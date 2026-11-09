"""Strategies the bot can run.

Proven on 3.7 years of GatesFX gold (live first in config/default.yaml): smc_ob, st_confluence,
momentum_rsi. Every other setup Ricardo discussed was tested and failed or barely passed
(docs/RESULTS_*.md); on 2026-11-09 they were re-added for demo forward-testing, together with the
new chart_patterns library. Config order = priority (one trade at a time).
"""
from .base import Strategy
from .momentum_rsi import MomentumRSI
from .bb_reversion import BBReversion
from .st_confluence import STConfluence
from .smc_ob import SMCOrderBlock
from .scalp import Scalp
# Re-added 2026-11-09 (Ricardo): every setup that was tested and removed, so they can be
# re-tested and forward-tested on DEMO. Results of the original tests: docs/RESULTS_*.md.
from .orb import OpeningRangeBreakout
from .trend_pullback import TrendPullback
from .vwap_reversion import VwapReversion
from .session_breakout import SessionBreakout
from .donchian_trend import DonchianTrend
from .time_drift import TimeDrift
from .liquidity_fvg import LiquidityFVG
from .silver_bullet import SilverBullet
from .break_retest import BreakRetest
from .reversal_trail import ReversalTrail
from .trend_break import TrendBreakRetest
from .zone_retest import ZoneRetest
from .chart_patterns import ChartPatterns   # new 2026-11-09: 11 classic chart patterns

# bb_reversion / st_confluence / smc_ob: added 2026-11-06 for testing (research/archetypes_study.py);
# only the ones that pass go into config/default.yaml.
# scalp: 60-second scalper under test (research/scalp_study.py), not on the live bot.
REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (
        MomentumRSI, BBReversion, STConfluence, SMCOrderBlock, Scalp,
        OpeningRangeBreakout, TrendPullback, VwapReversion, SessionBreakout, DonchianTrend, TimeDrift,
        LiquidityFVG, SilverBullet, BreakRetest, ReversalTrail, TrendBreakRetest, ZoneRetest,
        ChartPatterns,
    )
}


def build_strategy(name: str, session, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](session, **(params or {}))


__all__ = ["Strategy", "REGISTRY", "build_strategy"]
