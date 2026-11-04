"""Broker interface. The runner only talks to this, so brokers are swappable."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class Account:
    balance: float
    equity: float


@dataclass
class Position:
    id: str
    symbol: str
    side: int          # +1 long, -1 short
    qty: float
    entry: float
    stop: float | None = None
    target: float | None = None
    has_stop: bool = True
    opened: pd.Timestamp | None = None


class Broker(ABC):
    @abstractmethod
    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Most recent COMPLETED bars, canonical format (UTC index, OHLCV)."""

    @abstractmethod
    def account(self) -> Account: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def market_bracket(self, symbol: str, side: int, qty: float, stop: float, target: float) -> str:
        """Market order with broker-side stop-loss and take-profit attached. Returns an id."""

    @abstractmethod
    def close_all(self, symbol: str | None = None) -> None: ...

    @abstractmethod
    def close_position(self, position_id: str) -> None: ...

    def last_price(self, symbol: str, timeframe: str = "1m") -> float:
        return float(self.bars(symbol, timeframe, 1)["close"].iloc[-1])
