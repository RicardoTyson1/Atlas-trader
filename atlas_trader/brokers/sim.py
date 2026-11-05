"""Simulated broker that replays historical bars.

Used for dry-running the live runner end to end (no network, no money) and
in tests. Fills: market orders at the current bar close; broker-side
stop/target are checked on each subsequent bar, stop first.
"""
from __future__ import annotations

import itertools

import pandas as pd

from ..data import resample, timeframe_to_offset
from .base import Account, Broker, Position


class SimBroker(Broker):
    def __init__(self, data: dict[str, pd.DataFrame], balance: float, value_per_point: dict[str, float] | None = None):
        self.data = data                    # symbol -> 1m (or base) bars
        self.vpp = value_per_point or {}
        self.balance = balance
        self.now: pd.Timestamp | None = None  # current time (UTC); bars closed before this are visible
        self._pos: dict[str, Position] = {}
        self._ids = itertools.count(1)
        self.fills: list[dict] = []

    # --- clock -----------------------------------------------------------
    def set_time(self, now: pd.Timestamp) -> None:
        """Advance time and process stop/target hits on bars that completed meanwhile."""
        prev = self.now
        self.now = now
        if prev is None:
            return
        for sym, p in list(self._pos.items()):
            df = self.data[sym]
            step = df.index[1] - df.index[0]
            window = df[(df.index >= prev) & (df.index + step <= now)]
            for ts, bar in window.iterrows():
                hit = None
                if p.side == 1:
                    if p.stop is not None and bar.low <= p.stop:
                        hit = p.stop
                    elif p.target is not None and bar.high >= p.target:
                        hit = p.target
                else:
                    if p.stop is not None and bar.high >= p.stop:
                        hit = p.stop
                    elif p.target is not None and bar.low <= p.target:
                        hit = p.target
                if hit is not None:
                    self._close(sym, hit, ts)
                    break

    def _visible(self, symbol: str) -> pd.DataFrame:
        df = self.data[symbol]
        step = df.index[1] - df.index[0]
        return df[df.index + step <= self.now]

    # --- Broker API ------------------------------------------------------
    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        base = self._visible(symbol)
        out = resample(base, timeframe)
        # drop a trailing bar that is not complete yet
        if len(out) and out.index[-1] + pd.Timedelta(timeframe_to_offset(timeframe)) > self.now:
            out = out.iloc[:-1]
        return out.tail(count)

    def _mark(self, sym: str) -> float:
        return float(self._visible(sym)["close"].iloc[-1])

    def account(self) -> Account:
        upnl = sum(
            p.side * (self._mark(s) - p.entry) * p.qty * self.vpp.get(s, 1.0) for s, p in self._pos.items()
        )
        return Account(balance=self.balance, equity=self.balance + upnl)

    def positions(self) -> list[Position]:
        return list(self._pos.values())

    def market_bracket(self, symbol, side, qty, stop, target) -> str:
        if symbol in self._pos:
            raise RuntimeError("SimBroker supports one position per symbol")
        px = self._mark(symbol)
        pid = str(next(self._ids))
        self._pos[symbol] = Position(pid, symbol, side, qty, px, stop, target,
                                     has_stop=stop is not None, opened=self.now, tag="atlas")
        self.fills.append({"time": self.now, "symbol": symbol, "side": side, "qty": qty, "price": px, "action": "open"})
        return pid

    def _close(self, symbol: str, price: float, ts) -> None:
        p = self._pos.pop(symbol)
        pnl = p.side * (price - p.entry) * p.qty * self.vpp.get(symbol, 1.0)
        self.balance += pnl
        self.fills.append({"time": ts, "symbol": symbol, "side": -p.side, "qty": p.qty, "price": price, "action": "close", "pnl": pnl})

    def close_position(self, position_id: str) -> None:
        for s, p in list(self._pos.items()):
            if p.id == position_id:
                self._close(s, self._mark(s), self.now)

    def modify_stop(self, position_id: str, stop: float, target: float | None = None) -> bool:
        for p in self._pos.values():
            if p.id == position_id:
                p.stop = stop
                if target is not None:
                    p.target = target
                return True
        return False

    def add_manual(self, symbol: str, side: int, qty: float, stop: float | None = None) -> str:
        """Test helper: a position opened by hand (outside the bot)."""
        pid = f"m{next(self._ids)}"
        self._pos[symbol] = Position(pid, symbol, side, qty, self._mark(symbol), stop, None,
                                     has_stop=stop is not None, opened=self.now)
        return pid

    def close_all(self, symbol: str | None = None) -> None:
        for s in list(self._pos):
            if symbol is None or s == symbol:
                self._close(s, self._mark(s), self.now)
