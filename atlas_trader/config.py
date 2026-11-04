"""Typed configuration loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class PropRules:
    firm: str = "unknown"
    phase: str = "evaluation"
    profit_target_pct: float = 8.0
    daily_loss_pct: float = 4.0
    daily_loss_basis: str = "start_of_day"
    max_drawdown_pct: float = 8.0
    drawdown_type: str = "trailing"
    trailing_on: str = "equity"
    trail_locks_at_initial: bool = True
    min_trading_days: int = 0
    safety_buffer: float = 0.70

    def __post_init__(self) -> None:
        if not 0 < self.safety_buffer <= 1:
            raise ValueError("safety_buffer must be in (0, 1]")
        if self.daily_loss_basis not in ("initial", "start_of_day"):
            raise ValueError("daily_loss_basis must be 'initial' or 'start_of_day'")
        if self.drawdown_type not in ("static", "trailing"):
            raise ValueError("drawdown_type must be 'static' or 'trailing'")
        if self.trailing_on not in ("balance", "equity"):
            raise ValueError("trailing_on must be 'balance' or 'equity'")


@dataclass
class RiskSettings:
    risk_per_trade_pct: float = 0.5
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 3
    max_open_positions: int = 1
    max_lots: float = 5.0
    max_room_fraction_per_trade: float = 0.5


@dataclass
class Session:
    timezone: str = "America/New_York"
    open: time = time(9, 30)
    trade_start: time = time(9, 45)
    last_entry: time = time(15, 0)
    flat_by: time = time(15, 50)
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    overnight: bool = False   # True = positions may be held past flat_by / overnight

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass
class Instrument:
    symbol: str
    value_per_point: float = 1.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    spread_points: float = 0.0
    slippage_points: float = 0.0
    commission_per_lot: float = 0.0


@dataclass
class StrategySpec:
    name: str
    instruments: list[str]
    timeframe: str = "5m"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsBlackouts:
    minutes_before: int = 15
    minutes_after: int = 15
    events: list[datetime] = field(default_factory=list)  # tz-aware

    def active(self, ts: datetime) -> bool:
        b = timedelta(minutes=self.minutes_before)
        a = timedelta(minutes=self.minutes_after)
        return any(ev - b <= ts <= ev + a for ev in self.events)


@dataclass
class Config:
    mode: str
    starting_balance: float
    prop: PropRules
    risk: RiskSettings
    session: Session
    news: NewsBlackouts
    instruments: dict[str, Instrument]
    strategies: list[StrategySpec]
    tradelocker: dict[str, str]
    webhook: dict[str, Any]
    paths: dict[str, str]

    def instrument(self, symbol: str) -> Instrument:
        if symbol not in self.instruments:
            raise KeyError(f"Instrument {symbol!r} not in config 'instruments'")
        return self.instruments[symbol]


def load_config(path: str | Path = "config/default.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return config_from_dict(raw)


def config_from_dict(raw: dict[str, Any]) -> Config:
    s = raw.get("session", {})
    session = Session(
        timezone=s.get("timezone", "America/New_York"),
        open=_t(s.get("open", "09:30")),
        trade_start=_t(s.get("trade_start", "09:45")),
        last_entry=_t(s.get("last_entry", "15:00")),
        flat_by=_t(s.get("flat_by", "15:50")),
        weekdays=tuple(s.get("weekdays", [0, 1, 2, 3, 4])),
        overnight=bool(s.get("overnight", False)),
    )
    nb = raw.get("news_blackouts", {}) or {}
    events = [
        datetime.strptime(e, "%Y-%m-%d %H:%M").replace(tzinfo=session.tz)
        for e in (nb.get("events") or [])
    ]
    news = NewsBlackouts(nb.get("minutes_before", 15), nb.get("minutes_after", 15), events)
    instruments = {
        sym: Instrument(symbol=sym, **(vals or {}))
        for sym, vals in (raw.get("instruments") or {}).items()
    }
    strategies = [StrategySpec(**spec) for spec in (raw.get("strategies") or [])]
    mode = raw.get("mode", "backtest")
    if mode not in ("backtest", "demo", "live"):
        raise ValueError("mode must be backtest | demo | live")
    return Config(
        mode=mode,
        starting_balance=float(raw.get("account", {}).get("starting_balance", 50000)),
        prop=PropRules(**(raw.get("prop_rules") or {})),
        risk=RiskSettings(**(raw.get("risk") or {})),
        session=session,
        news=news,
        instruments=instruments,
        strategies=strategies,
        tradelocker=raw.get("tradelocker") or {},
        webhook=raw.get("webhook") or {},
        paths=raw.get("paths") or {},
    )
