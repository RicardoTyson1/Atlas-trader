"""Monte Carlo prop-challenge simulator.

Resamples the backtest's trading days (with replacement) into thousands of
alternative histories and asks: how often would this strategy have passed
the evaluation before breaking a rule?

Limits: days are treated as independent and dollar results are not rescaled
with account size. It answers "how fragile is this?", not "what will happen".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import PropRules


@dataclass
class MCResult:
    paths: int
    max_days: int
    p_pass: float
    p_fail: float
    p_undecided: float
    median_days_to_pass: float | None
    fail_reasons: dict[str, int]

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def simulate_challenge(
    daily: pd.DataFrame,
    prop: PropRules,
    initial: float,
    max_days: int = 60,
    paths: int = 5000,
    seed: int = 1,
) -> MCResult:
    """`daily` needs columns `pnl` and `worst_intraday` (<= 0), as produced by the backtester."""
    if daily.empty:
        raise ValueError("No daily results to simulate")
    rng = np.random.default_rng(seed)
    pnl = daily["pnl"].to_numpy(float)
    worst = np.minimum(daily["worst_intraday"].to_numpy(float), 0.0)
    target = initial * (1 + prop.profit_target_pct / 100)
    dd_lim = initial * prop.max_drawdown_pct / 100
    passed = failed = 0
    days_to_pass: list[int] = []
    reasons = {"daily_loss": 0, "max_drawdown": 0}
    for _ in range(paths):
        bal = hw = initial
        picks = rng.integers(0, len(pnl), max_days)
        outcome = None
        for k, j in enumerate(picks, start=1):
            basis = initial if prop.daily_loss_basis == "initial" else bal
            daily_floor = bal - basis * prop.daily_loss_pct / 100
            if prop.drawdown_type == "static":
                dd_floor = initial - dd_lim
            else:
                dd_floor = hw - dd_lim
                if prop.trail_locks_at_initial:
                    dd_floor = min(dd_floor, initial)
            low = bal + worst[j]
            if low <= daily_floor:
                outcome, why = "fail", "daily_loss"
            elif low <= dd_floor:
                outcome, why = "fail", "max_drawdown"
            bal += pnl[j]
            hw = max(hw, bal)
            if outcome is None and bal <= dd_floor:
                outcome, why = "fail", "max_drawdown"
            if outcome == "fail":
                failed += 1
                reasons[why] += 1
                break
            if bal >= target and k >= prop.min_trading_days:
                passed += 1
                days_to_pass.append(k)
                break
    return MCResult(
        paths=paths,
        max_days=max_days,
        p_pass=passed / paths,
        p_fail=failed / paths,
        p_undecided=(paths - passed - failed) / paths,
        median_days_to_pass=float(np.median(days_to_pass)) if days_to_pass else None,
        fail_reasons=reasons,
    )
