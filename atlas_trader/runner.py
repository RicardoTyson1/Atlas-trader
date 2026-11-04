"""Live / demo trading loop.

Each `step(now)`:
  1. Kill switch file present?            -> flatten everything, stop.
  2. Read account, update risk engine.     -> halts if a buffer is hit.
  3. GUARDIAN: police every position on the account, including ones opened by
     hand: close any position with no stop-loss, any position added on top of
     an open one, anything opened during a halt/cooldown/outside hours, and
     anything bigger than max_lots.
  4. Session end / news / halt?            -> flatten.
  5. Strategy exits (exit signal / time limit) for the bot's own positions.
  6. For each strategy+instrument with no open position, evaluate the latest
     COMPLETED bar; if it signals and risk allows, send a market order with a
     broker-side stop-loss and take-profit attached.
  7. Save state to disk (survives restarts).

The broker-side stop is the real protection: if this process dies, every open
trade still has its stop at the broker.
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
from .risk import RiskManager, RiskState
from .strategies import build_strategy

log = logging.getLogger("atlas")


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
        self.strats = [
            (spec, build_strategy(spec.name, cfg.session, spec.params)) for spec in cfg.strategies
        ]
        for spec, _ in self.strats:
            for sym in spec.instruments:
                cfg.instrument(sym)  # fail fast on unknown instruments
        self.extra = {"last_signal_bar": {}, "open_ids": {}, "balance_at_open": {},
                      "known_ids": [], "entry_time": {}, "max_bars": {}}
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
        raw = json.loads(self._state_file.read_text())
        self.extra.update(raw.get("extra", {}))
        return RiskState.from_dict(raw["risk"])

    def save(self) -> None:
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"risk": self.rm.s.to_dict(), "extra": self.extra}, indent=2, default=str))
        tmp.replace(self._state_file)

    # ------------------------------------------------------------ main step
    def step(self, now: pd.Timestamp | None = None) -> list[str]:
        with self.lock:
            return self._step(now or pd.Timestamp.now(tz="UTC"))

    def _step(self, now: pd.Timestamp) -> list[str]:
        actions: list[str] = []
        local = now.tz_convert(self.cfg.session.tz)

        if self.kill_file.exists():
            self.broker.close_all()
            self.stopped = True
            self.stop_reason = "kill switch"
            self.notify.send("KILL SWITCH: all positions closed, bot stopped.", logging.WARNING)
            return ["killed"]

        acct = self.broker.account()
        self.rm.new_day(local.date(), acct.balance)
        for ev in self.rm.update(acct.equity, acct.balance, when=local.strftime("%Y-%m-%d %H:%M")):
            self.notify.send(ev, logging.WARNING)

        positions = self.broker.positions()
        self._detect_closed(positions, acct.balance, local)
        if self.cfg.risk.guardian:
            closed = self._guard(positions, local)
            if closed:
                actions += closed
                positions = self.broker.positions()
                acct = self.broker.account()

        flat_reason = self.rm.must_flatten(local)
        if flat_reason and positions:
            self.broker.close_all()
            self.notify.send(f"Flattened all positions ({flat_reason}).")
            actions.append(f"flatten:{flat_reason}")
            positions = []
            acct = self.broker.account()
            self._detect_closed(positions, acct.balance, local)

        held = {p.symbol for p in positions}
        for spec, strat in self.strats:
            for sym in spec.instruments:
                if sym in held:
                    if sym in self.extra["open_ids"]:
                        res = self._manage_exit(spec, strat, sym, now)
                        if res:
                            actions.append(res)
                    continue
                bars = self.broker.bars(sym, spec.timeframe, spec.history_bars)
                if bars.empty:
                    continue
                tf = pd.Timedelta(timeframe_to_offset(spec.timeframe))
                last_ts = bars.index[-1]
                if last_ts + 2 * tf < now:
                    continue  # stale data: market closed or feed problem
                key = f"{spec.name}:{sym}"
                if self.extra["last_signal_bar"].get(key) == str(last_ts):
                    continue  # already evaluated this bar
                self.extra["last_signal_bar"][key] = str(last_ts)
                row = strat.generate(bars).iloc[-1]
                if int(row["signal"]) == 0:
                    continue
                res = self._try_enter(
                    sym, int(row["signal"]), float(row["stop"]),
                    None if np.isnan(row["target"]) else float(row["target"]),
                    None if np.isnan(row["rr"]) else float(row["rr"]),
                    float(bars["close"].iloc[-1]), local, acct.equity, len(positions), source=spec.name,
                )
                actions.append(res)
                if res.startswith("entered"):
                    held.add(sym)
                    mb = row.get("max_bars", np.nan)
                    self.extra["entry_time"][sym] = str(now)
                    self.extra["max_bars"][sym] = None if pd.isna(mb) else float(mb)
                    positions = self.broker.positions()
        self.save()
        return actions

    # ------------------------------------------------------------ entries
    def _try_enter(self, sym, side, stop, target, rr, price, local, equity, n_open, source) -> str:
        ok, why = self.rm.check_entry(local.to_pydatetime() if hasattr(local, "to_pydatetime") else local, n_open)
        if not ok:
            log.info("%s %s signal skipped: %s", source, sym, why)
            return f"skipped:{why}"
        if side * (price - stop) <= 0:
            return "skipped:stop on wrong side of price"
        if target is None:
            target = price + side * (rr or 2.0) * abs(price - stop)
        if side * (target - price) <= 0:
            return "skipped:target on wrong side of price"
        inst = self.cfg.instrument(sym)
        qty = self.rm.size(price, stop, inst, equity)
        if qty <= 0:
            return "skipped:size below minimum lot (stop too wide for risk budget)"
        oid = self.broker.market_bracket(sym, side, qty, stop, target)
        self.rm.record_entry()
        self.extra["open_ids"][sym] = oid
        self.extra["known_ids"] = list({*self.extra["known_ids"], *(p.id for p in self.broker.positions())})
        self.extra["balance_at_open"][sym] = self.broker.account().balance
        risk_usd = self.rm.risk_per_lot(price, stop, inst) * qty
        self.notify.send(
            f"{'BUY' if side > 0 else 'SELL'} {qty} {sym} @~{price:.2f} SL {stop:.2f} TP {target:.2f} "
            f"(risk ~${risk_usd:,.0f}, source {source})"
        )
        return "entered"

    def submit_external(self, symbol: str, side: int, stop: float, target: float | None = None,
                        rr: float | None = None, source: str = "tradingview") -> str:
        """Entry from outside (e.g. TradingView alert). Same risk checks as strategies."""
        with self.lock:
            now = pd.Timestamp.now(tz="UTC")
            local = now.tz_convert(self.cfg.session.tz)
            acct = self.broker.account()
            self.rm.new_day(local.date(), acct.balance)
            self.rm.update(acct.equity, acct.balance)
            positions = self.broker.positions()
            if any(p.symbol == symbol for p in positions):
                return "skipped:position already open"
            price = self.broker.last_price(symbol)
            res = self._try_enter(symbol, side, stop, target, rr, price, local, acct.equity,
                                  len(positions), source)
            self.save()
            return res

    def _detect_closed(self, positions, balance: float, local=None) -> None:
        open_syms = {p.symbol for p in positions}
        when = local.to_pydatetime() if hasattr(local, "to_pydatetime") else local
        for sym in list(self.extra["open_ids"]):
            if sym not in open_syms:
                pnl = balance - self.extra["balance_at_open"].get(sym, balance)
                self.rm.record_close(pnl, when)
                self.notify.send(f"Closed {sym}: P/L ${pnl:,.2f}")
                for k in ("open_ids", "balance_at_open", "entry_time", "max_bars"):
                    self.extra[k].pop(sym, None)
        live_ids = {p.id for p in positions}
        self.extra["known_ids"] = [i for i in self.extra["known_ids"] if i in live_ids]

    # ------------------------------------------------------------ guardian
    def _guard(self, positions, local) -> list[str]:
        """Enforce the rules on EVERY position, including trades placed by hand."""
        actions: list[str] = []
        known = set(self.extra["known_ids"])
        by_symbol: dict[str, list] = {}
        for p in positions:
            by_symbol.setdefault(p.symbol, []).append(p)
        closed_ids: set[str] = set()

        def kill(p, why: str) -> None:
            if p.id in closed_ids:
                return
            self.broker.close_position(p.id)
            closed_ids.add(p.id)
            self.notify.send(f"GUARDIAN closed {p.symbol} {p.qty} lots ({why}).", logging.WARNING)
            actions.append(f"guardian:{why}")

        for p in positions:
            if not p.has_stop:
                kill(p, "no stop-loss")
            elif p.qty > self.cfg.risk.max_lots + 1e-9:
                kill(p, f"size {p.qty} > max {self.cfg.risk.max_lots} lots")
        # no adding: only the oldest position per symbol may stay
        for sym, ps in by_symbol.items():
            ps = sorted(ps, key=lambda x: (x.opened or pd.Timestamp.min.tz_localize("UTC")))
            for extra in ps[1:]:
                kill(extra, "adding to an open position")
        # new manual trades must pass the same entry rules as the bot
        survivors = [p for p in positions if p.id not in closed_ids]
        for p in survivors:
            if p.id in known:
                continue
            others = len([q for q in survivors if q.id != p.id and q.id in known])
            ok, why = self.rm.check_entry(local.to_pydatetime(), others)
            if ok:
                self.rm.record_entry()
                known.add(p.id)
                self.extra["open_ids"].setdefault(p.symbol, p.id)
                self.extra["balance_at_open"].setdefault(p.symbol, self.broker.account().balance)
                self.notify.send(f"Manual trade accepted: {p.symbol} {p.qty} lots (counts toward today's limits).")
            else:
                kill(p, f"manual trade blocked: {why}")
        self.extra["known_ids"] = sorted(known - closed_ids)
        return actions

    # ------------------------------------------------------------ exits
    def _manage_exit(self, spec, strat, sym: str, now: pd.Timestamp) -> str | None:
        if sym not in self.extra["entry_time"]:
            return None  # not a bot entry (e.g. an accepted manual trade): leave it to the trader
        positions = [p for p in self.broker.positions() if p.symbol == sym]
        if not positions:
            return None
        pos = positions[0]
        tf = pd.Timedelta(timeframe_to_offset(spec.timeframe))
        mb = self.extra["max_bars"].get(sym)
        et = self.extra["entry_time"].get(sym)
        if mb and et and now - pd.Timestamp(et) >= tf * mb:
            self.broker.close_position(pos.id)
            self.notify.send(f"Time exit {sym} after {mb:.0f} bars.")
            return "exit:time"
        bars = self.broker.bars(sym, spec.timeframe, spec.history_bars)
        if bars.empty:
            return None
        ex = int(strat.generate(bars)["exit"].iloc[-1])
        if ex != 0 and ex in (2, pos.side):
            self.broker.close_position(pos.id)
            self.notify.send(f"Strategy exit {sym} ({spec.name}).")
            return "exit:signal"
        return None

    # ------------------------------------------------------------ loop
    def run_forever(self, poll_seconds: int = 5, max_errors: int = 5) -> None:
        tfs = [pd.Timedelta(timeframe_to_offset(s.timeframe)) for s, _ in self.strats] or [pd.Timedelta("1min")]
        step_td = min(tfs)
        errors = 0
        self.notify.send(f"Atlas started in {self.cfg.mode.upper()} mode. Risk: {self.rm.summary()}")
        while not self.stopped:
            now = pd.Timestamp.now(tz="UTC")
            nxt = now.floor(step_td) + step_td + pd.Timedelta(seconds=poll_seconds)
            _time.sleep(max(0.0, (nxt - now).total_seconds()))
            try:
                self.step()
                errors = 0
            except Exception as e:
                errors += 1
                self.notify.send(f"Error ({errors}/{max_errors}): {e!r}", logging.ERROR)
                if errors >= max_errors:
                    try:
                        self.broker.close_all()
                    finally:
                        self.notify.send("Too many errors: positions closed, bot stopped.", logging.CRITICAL)
                        self.stopped = True
                        self.stop_reason = "errors"
