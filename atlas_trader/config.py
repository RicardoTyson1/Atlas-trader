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
    trailing_on: str = "equity"   # equity | balance | eod_balance (Topstep-style)
    trail_locks_at_initial: bool = True
    min_trading_days: int = 0
    safety_buffer: float = 0.70
    daily_breach_permanent: bool = True   # prop firms: True (account fails). Personal: False (done for today)

    def __post_init__(self) -> None:
        if not 0 < self.safety_buffer <= 1:
            raise ValueError("safety_buffer must be in (0, 1]")
        if self.daily_loss_basis not in ("initial", "start_of_day"):
            raise ValueError("daily_loss_basis must be 'initial' or 'start_of_day'")
        if self.drawdown_type not in ("static", "trailing"):
            raise ValueError("drawdown_type must be 'static' or 'trailing'")
        if self.trailing_on not in ("balance", "equity", "eod_balance"):
            raise ValueError("trailing_on must be 'balance', 'equity' or 'eod_balance'")


@dataclass
class RiskSettings:
    risk_per_trade_pct: float = 0.5
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 3
    max_open_positions: int = 1
    max_lots: float = 5.0
    max_room_fraction_per_trade: float = 0.5
    cooldown_after_loss_minutes: int = 15     # no new entry this long after a losing trade
    max_sizing_equity: float | None = None    # never size from more than this (e.g. own money, no bonus)
    guardian: bool = True                     # police ALL positions on the account, incl. manual ones
    core_hours: str | None = None             # e.g. "08:00-11:00" (NY): full risk only in these hours
    off_hours_risk_pct: float | None = None   # risk per trade outside core_hours (None = same as inside)


@dataclass
class Session:
    timezone: str = "America/New_York"
    open: time = time(9, 30)
    trade_start: time = time(9, 45)
    last_entry: time = time(15, 0)
    flat_by: time = time(15, 50)
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    overnight: bool = False   # True = positions may be held past flat_by / overnight
    # Around-the-clock mode: if set, the market is closed only between flat_by and reopen
    # (e.g. gold's daily break 16:50-18:05 NY). Entries are allowed from reopen to last_entry
    # (wrapping past midnight) and everything is flattened during the break, every day.
    reopen: time | None = None

    def in_break(self, m: int) -> bool:
        """True if `m` (minutes since local midnight) is inside the daily break."""
        f = self.flat_by.hour * 60 + self.flat_by.minute
        if self.reopen is None:
            return m >= f
        r = self.reopen.hour * 60 + self.reopen.minute
        return f <= m < r

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
    history_bars: int = 600   # bars fetched each cycle in live trading
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsBlackouts:
    minutes_before: int = 15
    minutes_after: int = 15
    events: list[datetime] = field(default_factory=list)  # tz-aware, from the config
    feed_file: str | None = None      # calendar events saved by atlas_trader.news.refresh
    feed: dict[str, Any] = field(default_factory=dict)    # url / countries / impacts / refresh_hours
    _feed_mtime: float = field(default=-1.0, repr=False)
    _feed_events: list[datetime] = field(default_factory=list, repr=False)

    def _reload(self) -> None:
        if not self.feed_file:
            return
        from .news import event_times
        try:
            m = Path(self.feed_file).stat().st_mtime
        except OSError:
            return
        if m != self._feed_mtime:
            self._feed_events = event_times(self.feed_file)
            self._feed_mtime = m

    def all_events(self) -> list[datetime]:
        self._reload()
        return list(self.events) + list(self._feed_events)

    def active(self, ts: datetime) -> bool:
        b = timedelta(minutes=self.minutes_before)
        a = timedelta(minutes=self.minutes_after)
        return any(ev - b <= ts <= ev + a for ev in self.all_events())


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
    monitoring: dict[str, Any] = field(default_factory=dict)
    exits: dict[str, Any] = field(default_factory=dict)   # runaway_start_r / runaway_keep

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
        reopen=_t(s["reopen"]) if s.get("reopen") else None,
    )
    nb = raw.get("news_blackouts", {}) or {}
    events = [
        datetime.strptime(e, "%Y-%m-%d %H:%M").replace(tzinfo=session.tz)
        for e in (nb.get("events") or [])
    ]
    news = NewsBlackouts(nb.get("minutes_before", 15), nb.get("minutes_after", 15), events,
                         feed_file=nb.get("feed_file"), feed=dict(nb.get("feed") or {}))
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
        exits=raw.get("exits") or {},
        paths=raw.get("paths") or {},
        monitoring=raw.get("monitoring") or {},
    )
