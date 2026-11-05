"""Risk engine: position sizing and prop-firm rule enforcement.

This is the most important file in the project. Every order, from any source
(strategy, TradingView alert, manual), goes through `RiskManager` first.

Two sets of limits are tracked:
  * REAL limits: the firm's rules. Touching one = account failed.
  * BOT limits:  real limits scaled by `safety_buffer` (e.g. 70%). The bot
    stops trading when it reaches these, so it should never touch a real limit.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .config import Instrument, NewsBlackouts, PropRules, RiskSettings, Session


def _mins(t) -> int:
    return t.hour * 60 + t.minute


@dataclass
class RiskState:
    initial_balance: float
    day: str | None = None
    sod_balance: float = 0.0
    high_water: float = 0.0
    trades_today: int = 0
    consec_losses: int = 0
    day_halt: str | None = None        # cleared next day
    hard_halt: str | None = None       # needs a human to clear
    breached: str | None = None        # a REAL firm limit was hit
    target_hit_on: str | None = None
    trading_days: list[str] = field(default_factory=list)
    last_equity: float = 0.0
    cooldown_until: str | None = None  # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RiskState":
        return cls(**d)


class RiskManager:
    def __init__(
        self,
        prop: PropRules,
        risk: RiskSettings,
        session: Session,
        news: NewsBlackouts,
        initial_balance: float,
        state: RiskState | None = None,
    ) -> None:
        self.prop = prop
        self.risk = risk
        self.session = session
        self.news = news
        self.s = state or RiskState(
            initial_balance=initial_balance,
            sod_balance=initial_balance,
            high_water=initial_balance,
            last_equity=initial_balance,
        )

    # ------------------------------------------------------------ limits
    @property
    def initial(self) -> float:
        return self.s.initial_balance

    def daily_limit(self) -> float:
        basis = self.initial if self.prop.daily_loss_basis == "initial" else self.s.sod_balance
        return basis * self.prop.daily_loss_pct / 100.0

    def max_dd_limit(self) -> float:
        return self.initial * self.prop.max_drawdown_pct / 100.0

    def daily_floor(self, real: bool = False) -> float:
        lim = self.daily_limit()
        return self.s.sod_balance - (lim if real else lim * self.prop.safety_buffer)

    def dd_floor(self, real: bool = False) -> float:
        lim = self.max_dd_limit()
        if self.prop.drawdown_type == "static":
            real_floor = self.initial - lim
        else:
            real_floor = self.s.high_water - lim
            if self.prop.trail_locks_at_initial:
                real_floor = min(real_floor, self.initial)
        return real_floor if real else real_floor + lim * (1 - self.prop.safety_buffer)

    def target_balance(self) -> float:
        return self.initial * (1 + self.prop.profit_target_pct / 100.0)

    # ------------------------------------------------------------ lifecycle
    def new_day(self, day: date | str, balance: float) -> None:
        key = str(day)
        if self.s.day == key:
            return
        self.s.day = key
        if self.prop.trailing_on == "eod_balance" and balance > self.s.high_water:
            self.s.high_water = balance  # trails the previous day's closing balance only
        self.s.sod_balance = balance
        self.s.trades_today = 0
        self.s.consec_losses = 0
        self.s.day_halt = None

    def update(self, equity: float, balance: float, when: str = "") -> list[str]:
        """Feed latest (worst-case) equity. Returns new alert messages, if any."""
        events: list[str] = []
        self.s.last_equity = equity
        if self.prop.trailing_on != "eod_balance":
            mark = equity if self.prop.trailing_on == "equity" else balance
            if mark > self.s.high_water:
                self.s.high_water = mark

        daily_hit = equity <= self.daily_floor(real=True)
        if daily_hit and not self.prop.daily_breach_permanent and self.s.day_halt is None:
            # personal account: the daily limit means "done for today", not "account over"
            self.s.day_halt = f"{when} DAILY LOSS LIMIT hit (equity {equity:,.2f}). Done for today."
            events.append(self.s.day_halt)
        if self.s.breached is None:
            if daily_hit and self.prop.daily_breach_permanent:
                self.s.breached = f"{when} DAILY LOSS LIMIT BREACHED (equity {equity:,.2f})"
            elif equity <= self.dd_floor(real=True):
                self.s.breached = f"{when} MAX DRAWDOWN BREACHED (equity {equity:,.2f})"
            if self.s.breached:
                self.s.hard_halt = self.s.breached
                events.append(self.s.breached)

        if self.s.hard_halt is None and equity <= self.dd_floor():
            self.s.hard_halt = (
                f"{when} Bot drawdown buffer reached (equity {equity:,.2f} <= {self.dd_floor():,.2f}). "
                "Trading stopped until you review and reset."
            )
            events.append(self.s.hard_halt)
        if self.s.day_halt is None and equity <= self.daily_floor():
            self.s.day_halt = (
                f"{when} Bot daily loss buffer reached (equity {equity:,.2f} <= {self.daily_floor():,.2f}). "
                "No more trades today."
            )
            events.append(self.s.day_halt)

        if (
            self.prop.phase == "evaluation"
            and self.s.target_hit_on is None
            and balance >= self.target_balance()
        ):
            self.s.target_hit_on = when or "yes"
            events.append(f"{when} Profit target reached (balance {balance:,.2f}).")
        return events

    @property
    def halted(self) -> str | None:
        return self.s.hard_halt or self.s.day_halt

    def reset_hard_halt(self) -> None:
        """Manual reset after a human has reviewed what happened. Breaches cannot be reset."""
        if self.s.breached:
            raise RuntimeError("A real firm limit was breached; this account cannot be reset by the bot.")
        self.s.hard_halt = None

    # ------------------------------------------------------------ permissions
    def check_entry(self, ts_local: datetime, open_positions: int = 0) -> tuple[bool, str]:
        if self.s.hard_halt:
            return False, f"hard halt: {self.s.hard_halt}"
        if self.s.day_halt:
            return False, f"day halt: {self.s.day_halt}"
        if self.prop.phase == "evaluation" and self.s.target_hit_on:
            return False, "profit target already reached; stop trading the evaluation"
        if ts_local.weekday() not in self.session.weekdays:
            return False, "not a trading weekday"
        m = ts_local.hour * 60 + ts_local.minute
        if self.session.reopen is not None:
            if self.session.in_break(m) or (_mins(self.session.last_entry) < m < _mins(self.session.flat_by)):
                return False, "outside entry window (daily break)"
        elif m < _mins(self.session.trade_start) or m > _mins(self.session.last_entry):
            return False, "outside entry window"
        if self.news.active(ts_local):
            return False, "news blackout"
        if self.s.cooldown_until and ts_local < datetime.fromisoformat(self.s.cooldown_until):
            return False, f"cooldown after loss until {self.s.cooldown_until[11:16]}"
        if self.s.trades_today >= self.risk.max_trades_per_day:
            return False, "max trades per day reached"
        if self.s.consec_losses >= self.risk.max_consecutive_losses:
            return False, "max consecutive losses reached"
        if open_positions >= self.risk.max_open_positions:
            return False, "max open positions reached"
        return True, "ok"

    def must_flatten(self, ts_local: datetime) -> str | None:
        m = ts_local.hour * 60 + ts_local.minute
        if not self.session.overnight and self.session.in_break(m):
            return "end of session"
        if self.news.active(ts_local):
            return "news blackout"
        if self.halted:
            return "risk halt"
        return None

    # ------------------------------------------------------------ sizing
    def risk_per_lot(self, entry: float, stop: float, inst: Instrument) -> float:
        dist = abs(entry - stop) + inst.spread_points + inst.slippage_points
        return dist * inst.value_per_point + 2 * inst.commission_per_lot

    def size(self, entry: float, stop: float, inst: Instrument, equity: float) -> float:
        """Lots to trade so a stop-out loses at most the allowed amount. 0 = don't trade."""
        if entry == stop or equity <= 0:
            return 0.0
        sizing_base = min(equity, self.risk.max_sizing_equity) if self.risk.max_sizing_equity else equity
        budget = sizing_base * self.risk.risk_per_trade_pct / 100.0
        # room to the halt levels is measured on the REAL equity
        room = min(equity - self.daily_floor(), equity - self.dd_floor())
        budget = min(budget, room * self.risk.max_room_fraction_per_trade)
        if budget <= 0:
            return 0.0
        per_lot = self.risk_per_lot(entry, stop, inst)
        if per_lot <= 0:
            return 0.0
        raw = budget / per_lot
        lots = math.floor(raw / inst.lot_step + 1e-9) * inst.lot_step
        lots = min(lots, self.risk.max_lots)
        lots = round(lots, 8)
        return lots if lots >= inst.min_lot - 1e-12 else 0.0

    # ------------------------------------------------------------ bookkeeping
    def record_entry(self) -> None:
        self.s.trades_today += 1
        if self.s.day and self.s.day not in self.s.trading_days:
            self.s.trading_days.append(self.s.day)

    def record_close(self, pnl: float, when: datetime | None = None) -> None:
        self.s.consec_losses = self.s.consec_losses + 1 if pnl < 0 else 0
        if pnl < 0 and when is not None and self.risk.cooldown_after_loss_minutes > 0:
            until = when + timedelta(minutes=self.risk.cooldown_after_loss_minutes)
            self.s.cooldown_until = until.isoformat()

    def summary(self) -> dict[str, Any]:
        return {
            "sod_balance": round(self.s.sod_balance, 2),
            "high_water": round(self.s.high_water, 2),
            "bot_daily_floor": round(self.daily_floor(), 2),
            "real_daily_floor": round(self.daily_floor(real=True), 2),
            "bot_dd_floor": round(self.dd_floor(), 2),
            "real_dd_floor": round(self.dd_floor(real=True), 2),
            "target_balance": round(self.target_balance(), 2),
            "trades_today": self.s.trades_today,
            "consec_losses": self.s.consec_losses,
            "cooldown_until": self.s.cooldown_until,
            "halt": self.halted,
            "breached": self.s.breached,
        }
