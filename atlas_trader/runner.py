"""Live / demo trading loop.

Each `step(now)` (every `poll_seconds`, default 30s):
  1. Kill switch file present?           -> flatten everything (verified), stop.
  2. Read positions, then the account.    Detect closed trades BY POSITION ID and
     record their P/L (fees included) -> streaks, cooldowns.
  3. Update the risk engine               -> day / hard halts.
  4. GUARDIAN: police every position on the account, including ones opened by
     hand: no stop-loss, too big, added on top of an open position, or opened
     while the rules say "no new trades" -> closed (and the close is verified).
  5. Session end / news / halt?           -> flatten (verified).
  6. The bot's own positions: strategy exit / time exit.
  7. On each newly COMPLETED bar: evaluate the strategy; if it signals and risk
     allows, send ONE market order with broker-side stop-loss and take-profit.
  8. Save state (survives restarts); ping the dead-man's switch.

The broker-side stop is the real protection: if this process or its server dies,
every open trade still has its stop at the broker.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

from .brokers.base import Broker
from .config import Config
from .data import timeframe_to_offset
from .notify import Notifier
from .backtest import spike_exit, spike_lock, trail_candidate
from .risk import RiskManager, RiskState
from .strategies import build_strategy

log = logging.getLogger("atlas")
BOT_TAG = "atlas"


def _mins(t) -> int:
    return t.hour * 60 + t.minute


class Runner:
    def __init__(self, cfg: Config, broker: Broker, notifier: Notifier | None = None,
                 state_dir: str | Path | None = None) -> None:
        self.cfg = cfg
        self.broker = broker
        self.notify = notifier or Notifier()
        self.state_dir = Path(state_dir or cfg.paths.get("state_dir", "state"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.kill_file = Path(cfg.paths.get("kill_switch_file", self.state_dir / "KILL"))
        self.lock = threading.RLock()
        self.clock = lambda: pd.Timestamp.now(tz="UTC")   # wall clock; tests swap in the sim clock
        self.close_poll_seconds = 1.5   # how often a close is re-checked (positions API: 1 call/s)
        self.close_timeout_seconds = 12.0
        self.strats = [
            (spec, build_strategy(spec.name, cfg.session, spec.params)) for spec in cfg.strategies
        ]
        for spec, _ in self.strats:
            for sym in spec.instruments:
                cfg.instrument(sym)  # fail fast on unknown instruments
        # tracked: position id -> {symbol, bot, bal_before, entry_time, max_bars, strategy}
        self.extra: dict = {"tracked": {}, "last_signal_bar": {}, "report": {}, "alerts": {}}
        self.rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, cfg.starting_balance,
                              self._load_state())
        self.stopped = False
        self.stop_reason: str | None = None

    # ------------------------------------------------------------ state
    @property
    def _state_file(self) -> Path:
        return self.state_dir / "runner_state.json"

    def _load_state(self) -> RiskState | None:
        if not self._state_file.exists():
            return None
        try:
            raw = json.loads(self._state_file.read_text())
            for k in self.extra:
                if isinstance(raw.get("extra", {}).get(k), dict):
                    self.extra[k] = raw["extra"][k]
            return RiskState.from_dict(raw["risk"])
        except Exception as e:
            bad = self._state_file.with_suffix(".corrupt")
            self._state_file.replace(bad)
            self.notify.send(f"State file was unreadable ({e!r}); moved to {bad.name}. "
                             "Starting HALTED until you review and run reset-halt.", logging.CRITICAL)
            st = RiskState(initial_balance=self.cfg.starting_balance,
                           sod_balance=self.cfg.starting_balance, high_water=self.cfg.starting_balance,
                           last_equity=self.cfg.starting_balance)
            st.hard_halt = "state file was corrupt"
            return st

    def save(self) -> None:
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"risk": self.rm.s.to_dict(), "extra": self.extra}, indent=2, default=str))
        tmp.replace(self._state_file)

    @property
    def tracked(self) -> dict:
        return self.extra["tracked"]

    def _alert_once(self, key: str, msg: str, level: int = logging.WARNING) -> None:
        """Send `msg` at most once per `key` (keys usually include the date)."""
        if self.extra["alerts"].get(key):
            return
        self.extra["alerts"][key] = True
        self.notify.send(msg, level)

    # ------------------------------------------------------------ main step
    def step(self, now: pd.Timestamp | None = None) -> list[str]:
        with self.lock:
            return self._step(now or pd.Timestamp.now(tz="UTC"))

    def _step(self, now: pd.Timestamp) -> list[str]:
        actions: list[str] = []
        local = now.tz_convert(self.cfg.session.tz)

        if self.kill_file.exists():
            ok = self._flatten("kill switch")
            self.stopped = True
            self.stop_reason = "kill switch"
            self.notify.send("KILL SWITCH: " + ("all positions closed" if ok else "CLOSE FAILED, check the account")
                             + ", bot stopped.", logging.WARNING)
            self.save()
            return ["killed"]

        # positions FIRST, then the account, so a close in between is never mis-booked
        positions = self.broker.positions()
        acct = self.broker.account()
        self.rm.new_day(local.date(), acct.balance)
        self._detect_closed(positions, acct.balance, local)
        for ev in self.rm.update(acct.equity, acct.balance, when=local.strftime("%Y-%m-%d %H:%M")):
            self.notify.send(ev, logging.WARNING)
        self._daily_reports(local, acct)

        if self.cfg.risk.guardian:
            g = self._guard(positions, acct.balance, local)
            if g:
                actions += g
                positions = self.broker.positions()
                acct = self.broker.account()
                self._detect_closed(positions, acct.balance, local)

        flat_reason = self.rm.must_flatten(local)
        if flat_reason and positions:
            self._flatten(flat_reason)
            actions.append(f"flatten:{flat_reason}")
            positions = self.broker.positions()
            acct = self.broker.account()
            self._detect_closed(positions, acct.balance, local)

        held = {p.symbol for p in positions}
        for spec, strat in self.strats:
            for sym in spec.instruments:
                try:
                    res = self._run_strategy(spec, strat, sym, held, positions, now, local, acct)
                except Exception as e:  # one bad symbol must not stop the guardian / other symbols
                    log.exception("strategy %s on %s failed", spec.name, sym)
                    self._alert_once(f"err:{spec.name}:{sym}:{local.date()}:{type(e).__name__}",
                                     f"Strategy {spec.name} on {sym} error: {e!r}", logging.ERROR)
                    res = f"error:{sym}"
                if res:
                    actions.append(res)
                    if res == "entered":
                        held.add(sym)
                        positions = self.broker.positions()
        self.save()
        return actions

    def _run_strategy(self, spec, strat, sym, held, positions, now, local, acct) -> str | None:
        tf = pd.Timedelta(timeframe_to_offset(spec.timeframe))
        if sym in held:
            return self._manage_exit(spec, strat, sym, now)
        key = f"{spec.name}:{sym}"
        expected_last = now.floor(tf) - tf          # newest bar that should be complete
        if self.extra["last_signal_bar"].get(key) == str(expected_last):
            return None                              # nothing new since last evaluation
        bars = self.broker.bars(sym, spec.timeframe, spec.history_bars)
        m = local.hour * 60 + local.minute
        sess = self.cfg.session
        if sess.reopen is not None:
            fri_close = local.weekday() == 4 and m >= _mins(sess.flat_by)   # weekend: market shut
            in_window = local.weekday() in sess.weekdays and not sess.in_break(m) and not fri_close
        else:
            in_window = (local.weekday() in sess.weekdays
                         and _mins(sess.trade_start) <= m <= _mins(sess.last_entry))
        if bars.empty or bars.index[-1] + 2 * tf < now:
            if in_window:
                self._alert_once(f"stale:{key}:{local.date()}",
                                 f"No fresh {sym} price data during trading hours (last bar: "
                                 f"{bars.index[-1] if len(bars) else 'none'}). Bot cannot trade {sym}.")
            return None
        last_ts = bars.index[-1]
        if self.extra["last_signal_bar"].get(key) == str(last_ts):
            return None
        self.extra["last_signal_bar"][key] = str(last_ts)
        row = strat.generate(bars).iloc[-1]
        if int(row["signal"]) == 0:
            return None
        mb = row.get("max_bars", np.nan)
        trs = row.get("trail_start_r", np.nan)
        tbuf = row.get("trail_buf", np.nan)
        spk = row.get("spike_pts", np.nan)
        spr = row.get("spike_min_r", np.nan)
        spm = row.get("spike_mode", np.nan)
        return self._try_enter(
            sym, int(row["signal"]), float(row["stop"]),
            None if pd.isna(row["target"]) else float(row["target"]),
            None if pd.isna(row["rr"]) else float(row["rr"]),
            local, acct.equity, len(positions), source=spec.name,
            max_bars=None if pd.isna(mb) else float(mb),
            trail=None if pd.isna(trs) else (float(trs), 0.0 if pd.isna(tbuf) else float(tbuf)),
            spike=None if pd.isna(spk) else (float(spk), 1.0 if pd.isna(spr) else float(spr),
                                             2 if pd.isna(spm) else int(spm)),
            tp_note=row.get("tp_kind") if isinstance(row.get("tp_kind"), str) else None,
        )

    # ------------------------------------------------------------ entries
    def _try_enter(self, sym, side, stop, target, rr, local, equity, n_open, source,
                   max_bars: float | None = None, trail: tuple[float, float] | None = None,
                   spike: tuple[float, float, int] | None = None, worst_price: float | None = None,
                   tp_note: str | None = None) -> str:
        ok, why = self.rm.check_entry(local.to_pydatetime() if hasattr(local, "to_pydatetime") else local, n_open)
        if not ok:
            log.info("%s %s signal skipped: %s", source, sym, why)
            return f"skipped:{why}"
        bid, ask = self.broker.quote(sym)
        price = ask if side > 0 else bid               # the price we will actually pay
        if side * (price - stop) <= 0:
            return "skipped:price already through the stop"
        if worst_price is not None and side * (price - worst_price) > 0:
            return f"skipped:price {price:.2f} moved too far past the entry (limit {worst_price:.2f})"
        if target is None:
            target = price + side * (rr or 2.0) * abs(price - stop)
        if side * (target - price) <= 0:
            return "skipped:price already past the target"
        inst = self.cfg.instrument(sym)
        qty = self.rm.size(price, stop, inst, equity)
        if qty <= 0:
            return "skipped:size below minimum lot (stop too wide for risk budget)"
        bal_before = self.broker.account().balance     # before the opening fee is charged
        pid = self.broker.market_bracket(sym, side, qty, stop, target)
        self.rm.record_entry()
        self.tracked[str(pid)] = {"symbol": sym, "bot": True, "bal_before": bal_before,
                                  "entry_time": str(pd.Timestamp.now(tz="UTC")), "max_bars": max_bars,
                                  "strategy": source, "side": side, "init_stop": stop, "stop": stop,
                                  "target": target,
                                  "trail_start_r": trail[0] if trail else None,
                                  "trail_buf": trail[1] if trail else None,
                                  "spike_pts": spike[0] if spike else None,
                                  "spike_min_r": spike[1] if spike else None,
                                  "spike_mode": spike[2] if spike else None}
        self.save()                                    # never lose track of a live order
        risk_usd = self.rm.risk_per_lot(price, stop, inst) * qty
        self.notify.send(
            f"{'BUY' if side > 0 else 'SELL'} {qty} {sym} @~{price:.2f} SL {stop:.2f} TP {target:.2f}"
            f"{f' ({tp_note})' if tp_note else ''} (risk ~${risk_usd:,.0f}, {source})"
        )
        return "entered"

    def submit_external(self, symbol: str, side: int, stop: float, target: float | None = None,
                        rr: float | None = None, source: str = "tradingview",
                        worst_price: float | None = None) -> str:
        """Entry from outside (e.g. TradingView alert). Same checks as the strategies."""
        with self.lock:
            local = self.clock().tz_convert(self.cfg.session.tz)
            positions = self.broker.positions()
            acct = self.broker.account()
            self.rm.new_day(local.date(), acct.balance)
            self._detect_closed(positions, acct.balance, local)
            for ev in self.rm.update(acct.equity, acct.balance, when=local.strftime("%Y-%m-%d %H:%M")):
                self.notify.send(ev, logging.WARNING)
            if any(p.symbol == symbol for p in positions):
                return "skipped:position already open"
            res = self._try_enter(symbol, side, stop, target, rr, local, acct.equity, len(positions), source,
                                  worst_price=worst_price)
            self.save()
            return res

    # ------------------------------------------------------------ bookkeeping
    def _detect_closed(self, positions, balance: float, local) -> None:
        live = {p.id for p in positions}
        gone = [pid for pid in self.tracked if pid not in live]
        if not gone:
            return
        when = local.to_pydatetime() if hasattr(local, "to_pydatetime") else local
        # P/L = balance now vs balance before the earliest of the closed trades
        baseline = min(self.tracked[pid]["bal_before"] for pid in gone)
        pnl = balance - baseline
        syms = sorted({self.tracked[pid]["symbol"] for pid in gone})
        for pid in gone:
            self.tracked.pop(pid)
        self.rm.record_close(pnl, when)
        rep = self._report(local)
        rep["closed"] = rep.get("closed", 0) + len(gone)
        self.notify.send(f"Closed {', '.join(syms)}: P/L ${pnl:,.2f}"
                         + (f" | cooldown until {self.rm.s.cooldown_until[11:16]}" if pnl < 0 and self.rm.s.cooldown_until else ""))

    # ------------------------------------------------------------ guardian
    def _guard(self, positions, balance: float, local) -> list[str]:
        """Enforce the rules on EVERY position, including trades placed by hand."""
        actions: list[str] = []
        to_close: dict[str, str] = {}
        for p in positions:
            if p.id not in self.tracked and p.tag == BOT_TAG:
                # the bot's own trade that we lost track of (e.g. fill shown late, restart)
                self.tracked[p.id] = {"symbol": p.symbol, "bot": True, "bal_before": balance,
                                      "entry_time": str(p.opened or pd.Timestamp.now(tz="UTC")),
                                      "max_bars": None, "strategy": "recovered"}
                self.notify.send(f"Re-attached bot position {p.symbol} {p.qty} lots.")
        for p in positions:
            if not p.has_stop:
                to_close[p.id] = "no stop-loss"
            elif p.qty > self.cfg.risk.max_lots + 1e-9:
                to_close[p.id] = f"size {p.qty} > max {self.cfg.risk.max_lots} lots"
        by_symbol: dict[str, list] = {}
        for p in positions:
            by_symbol.setdefault(p.symbol, []).append(p)
        for ps in by_symbol.values():
            ps = sorted(ps, key=lambda x: (x.id not in self.tracked, x.opened or pd.Timestamp.max.tz_localize("UTC")))
            for extra in ps[1:]:
                to_close.setdefault(extra.id, "adding to an open position")
        for p in positions:
            if p.id in self.tracked or p.id in to_close:
                continue
            others = len([q for q in positions if q.id != p.id and q.id not in to_close])
            opened_local = (p.opened.tz_convert(self.cfg.session.tz).to_pydatetime()
                            if p.opened is not None else local.to_pydatetime())
            ok, why = self.rm.check_entry(opened_local, others)
            if ok:
                self.rm.record_entry()
                fee = 2 * self.cfg.instruments.get(p.symbol, None).commission_per_lot * p.qty \
                    if p.symbol in self.cfg.instruments else 0.0
                self.tracked[p.id] = {"symbol": p.symbol, "bot": False, "bal_before": balance + fee,
                                      "entry_time": str(p.opened or pd.Timestamp.now(tz="UTC")),
                                      "max_bars": None, "strategy": None}
                self.notify.send(f"Manual trade accepted: {p.symbol} {p.qty} lots "
                                 f"({self.rm.s.trades_today}/{self.cfg.risk.max_trades_per_day} trades today).")
            else:
                to_close[p.id] = f"manual trade blocked: {why}"
        if to_close:
            by_id = {p.id: p for p in positions}
            for pid, why in to_close.items():
                p = by_id[pid]
                self.notify.send(f"GUARDIAN closing {p.symbol} {p.qty} lots ({why}).", logging.WARNING)
                actions.append(f"guardian:{why}")
            self._close_verified(list(to_close))
            rep = self._report(local)
            rep["guardian"] = rep.get("guardian", 0) + len(to_close)
        return actions

    # ------------------------------------------------------------ closing
    def _close_verified(self, ids: list[str], symbol: str | None = None) -> bool:
        """Close positions and CONFIRM they are gone; escalate and alert if not."""
        for pid in ids:
            try:
                self.broker.close_position(pid)
            except Exception as e:
                log.warning("close_position %s failed: %r", pid, e)

        def remaining() -> list[str]:
            live = {p.id for p in self.broker.positions()}
            return [pid for pid in ids if pid in live]

        def wait() -> list[str]:
            left = remaining()
            deadline = _time.monotonic() + self.close_timeout_seconds
            while left and _time.monotonic() < deadline:
                _time.sleep(self.close_poll_seconds)
                left = remaining()
            return left

        left = wait()
        if left:
            syms = {p.symbol for p in self.broker.positions() if p.id in left}
            for s in syms:
                try:
                    self.broker.close_all(s)
                except Exception as e:
                    log.warning("close_all %s failed: %r", s, e)
            left = wait()
        if left:
            self.notify.send(f"COULD NOT CLOSE positions {left}. Their broker-side stops are still in place. "
                             "CHECK THE ACCOUNT NOW.", logging.CRITICAL)
            return False
        return True

    def _flatten(self, reason: str) -> bool:
        ids = [p.id for p in self.broker.positions()]
        if not ids:
            return True
        ok = self._close_verified(ids)
        self.notify.send(f"Flattened {len(ids)} position(s) ({reason})." if ok
                         else f"Flatten ({reason}) did not fully complete.", logging.WARNING)
        return ok

    # ------------------------------------------------------------ exits
    def _manage_exit(self, spec, strat, sym: str, now: pd.Timestamp) -> str | None:
        mine = [(pid, t) for pid, t in self.tracked.items()
                if t["symbol"] == sym and t["bot"] and t.get("strategy") == spec.name]
        if not mine:
            return None  # not this strategy's trade (e.g. a manual one): leave it to the trader
        pid, t = mine[0]
        pos = next((p for p in self.broker.positions() if p.id == pid), None)
        if pos is None:
            return None
        tf = pd.Timedelta(timeframe_to_offset(spec.timeframe))
        if t.get("max_bars") and now - pd.Timestamp(t["entry_time"]) >= tf * t["max_bars"]:
            self._close_verified([pid])
            self.notify.send(f"Time exit {sym} after {t['max_bars']:.0f} bars.")
            return "exit:time"
        bars = self.broker.bars(sym, spec.timeframe, spec.history_bars)
        if bars.empty:
            return None
        ex = int(strat.generate(bars)["exit"].iloc[-1])
        if ex != 0 and ex in (2, pos.side):
            self._close_verified([pid])
            self.notify.send(f"Strategy exit {sym} ({spec.name}).")
            return "exit:signal"
        return self._trail(pid, t, pos, sym, bars)

    def _trail(self, pid: str, t: dict, pos, sym: str, bars: pd.DataFrame) -> str | None:
        """Candle trailing stop on the live trade (same rule as the backtester).

        Evaluated once per completed bar. Only ever tightens the broker-side stop; if the
        broker refuses, the previous stop stays in place and we alert once.
        """
        if len(bars) < 3:
            return None
        last = str(bars.index[-1])
        if t.get("trail_bar") == last:
            return None
        t["trail_bar"] = last
        o_, c_ = float(bars["open"].iloc[-1]), float(bars["close"].iloc[-1])
        if t.get("spike_pts") and int(t.get("spike_mode") or 2) == 1 and spike_exit(
                pos.side, pos.entry, float(t["init_stop"]), float(t["spike_pts"]),
                float(t["spike_min_r"] or 1.0), o_, c_):
            self._close_verified([pid])
            self.notify.send(f"Took profit on {sym}: big candle in our favor.")
            return "exit:big candle"
        candidates = []
        if t.get("spike_pts") and int(t.get("spike_mode") or 2) == 2:
            candidates.append(spike_lock(pos.side, pos.entry, float(t["init_stop"]), float(t["stop"]),
                                         float(t["spike_pts"]), float(t["spike_min_r"] or 1.0), o_, c_))
        if t.get("trail_start_r") is None and not candidates:
            return None
        if t.get("trail_start_r") is not None:
            candidates.append(trail_candidate(pos.side, pos.entry, float(t["init_stop"]), float(t["stop"]),
                                              float(t["trail_start_r"]), float(t["trail_buf"] or 0.0),
                                              *(bars[k].to_numpy(float)[-3:] for k in ("open", "high", "low", "close"))))
        candidates = [x for x in candidates if x is not None and pos.side * (x - float(t["stop"])) > 0]
        if not candidates:
            return None
        new = max(candidates) if pos.side == 1 else min(candidates)   # the tightest, never backwards
        if not self.broker.modify_stop(pid, new, t.get("target")):
            self._alert_once(f"trailfail:{pid}", f"Could not move the stop on {sym}; the previous stop "
                             f"({float(t['stop']):.2f}) is still in place.", logging.ERROR)
            return None
        t["stop"] = new
        self.save()
        inst = self.cfg.instrument(sym)
        locked = pos.side * (new - pos.entry) * pos.qty * inst.value_per_point
        self.notify.send(f"Trailed {sym} stop to {new:.2f} ({'locks' if locked >= 0 else 'risk now'} "
                         f"${abs(locked):,.0f}).")
        return "trail"

    # ------------------------------------------------------------ reports
    def _report(self, local) -> dict:
        rep = self.extra["report"]
        day = str(local.date())
        if rep.get("day") != day:
            rep.clear()
            rep["day"] = day
        return rep

    def _daily_reports(self, local, acct) -> None:
        if not self.cfg.monitoring.get("daily_report", True):
            return
        if local.weekday() not in self.cfg.session.weekdays:
            return
        rep = self._report(local)
        m = local.hour * 60 + local.minute
        s = self.rm.s
        if not rep.get("armed") and m >= _mins(self.cfg.session.trade_start) - 5:
            rep["armed"] = True
            status = f"HALTED: {self.rm.halted}" if self.rm.halted else "ready"
            self.notify.send(
                f"Good morning. {status}. Balance ${acct.balance:,.2f}. "
                f"Day stop at ${self.rm.daily_floor():,.0f}, account stop at ${self.rm.dd_floor():,.0f}. "
                f"Max {self.cfg.risk.max_trades_per_day} trades, {self.cfg.risk.risk_per_trade_pct}% risk each. "
                + (f"Entries around the clock; flat {self.cfg.session.flat_by:%H:%M}-{self.cfg.session.reopen:%H:%M} NY daily break."
                 if self.cfg.session.reopen is not None else
                 f"Entries {self.cfg.session.trade_start:%H:%M}-{self.cfg.session.last_entry:%H:%M} NY.")
            )
        if not rep.get("eod") and m >= _mins(self.cfg.session.flat_by) + 5:
            rep["eod"] = True
            day_pl = acct.balance - s.sod_balance
            self.notify.send(
                f"Day report: P/L ${day_pl:,.2f} | balance ${acct.balance:,.2f} | trades {s.trades_today} | "
                f"closed {rep.get('closed', 0)} | guardian actions {rep.get('guardian', 0)} | "
                f"halt: {self.rm.halted or 'none'}"
            )

    # ------------------------------------------------------------ loop
    def run_forever(self, poll_seconds: int | None = None) -> None:
        poll = int(poll_seconds or self.cfg.monitoring.get("poll_seconds", 30))
        errors = 0
        last_err_alert = 0.0
        self.notify.send(f"Atlas started in {self.cfg.mode.upper()} mode. Risk: {self.rm.summary()}")
        while not self.stopped:
            try:
                self.step()
                if errors:
                    self.notify.send(f"Recovered after {errors} failed cycle(s).")
                errors = 0
                self.notify.heartbeat(True)
            except Exception as e:
                errors += 1
                log.exception("step failed")
                # broker-side stops protect open trades; do NOT panic-flatten on API trouble
                if errors == 3 or (errors > 3 and _time.monotonic() - last_err_alert > 1800):
                    last_err_alert = _time.monotonic()
                    self.notify.send(f"Bot cannot complete its checks ({errors} failures in a row): {e!r}. "
                                     "Open trades keep their broker stops. Retrying.", logging.ERROR)
                    self.notify.heartbeat(False, repr(e))
            if self.stopped:
                break
            delay = poll if errors == 0 else min(300, poll * (2 ** min(errors, 4)))
            _time.sleep(delay)
