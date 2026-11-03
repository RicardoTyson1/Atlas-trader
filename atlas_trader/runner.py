"""Live / demo trading loop.

Each `step(now)`:
  1. Kill switch file present?            -> flatten everything, stop.
  2. Read account, update risk engine.     -> halts if a buffer is hit.
  3. Session end / news / halt?            -> flatten.
  4. For each strategy+instrument with no open position, evaluate the latest
     COMPLETED bar; if it signals and risk allows, send a market order with a
     broker-side stop-loss and take-profit attached.
  5. Save state to disk (survives restarts).

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
        self.extra = {"last_signal_bar": {}, "open_ids": {}, "balance_at_open": {}}
        self.rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, cfg.starting_balance,
                              self._load_state())
        self.stopped = False

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
            self.notify.send("KILL SWITCH: all positions closed, bot stopped.", logging.WARNING)
            return ["killed"]

        acct = self.broker.account()
        self.rm.new_day(local.date(), acct.balance)
        for ev in self.rm.update(acct.equity, acct.balance, when=local.strftime("%Y-%m-%d %H:%M")):
            self.notify.send(ev, logging.WARNING)

        positions = self.broker.positions()
        self._detect_closed(positions, acct.balance)

        flat_reason = self.rm.must_flatten(local)
        if flat_reason and positions:
            self.broker.close_all()
            self.notify.send(f"Flattened all positions ({flat_reason}).")
            actions.append(f"flatten:{flat_reason}")
            positions = []
            acct = self.broker.account()
            self._detect_closed(positions, acct.balance)

        held = {p.symbol for p in positions}
        for spec, strat in self.strats:
            for sym in spec.instruments:
                if sym in held:
                    continue
                bars = self.broker.bars(sym, spec.timeframe, 400)
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

    def _detect_closed(self, positions, balance: float) -> None:
        open_syms = {p.symbol for p in positions}
        for sym in list(self.extra["open_ids"]):
            if sym not in open_syms:
                pnl = balance - self.extra["balance_at_open"].get(sym, balance)
                self.rm.record_close(pnl)
                self.notify.send(f"Closed {sym}: P/L ${pnl:,.2f}")
                self.extra["open_ids"].pop(sym, None)
                self.extra["balance_at_open"].pop(sym, None)

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
