from .base import Strategy
from .orb import OpeningRangeBreakout
from .trend_pullback import TrendPullback
from .vwap_reversion import VwapReversion
from .session_breakout import SessionBreakout
from .donchian_trend import DonchianTrend
from .time_drift import TimeDrift
from .liquidity_fvg import LiquidityFVG
from .ict import ICT2022, OTE, SMTDivergence, JudasPO3, SilverBullet

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (OpeningRangeBreakout, TrendPullback, VwapReversion, SessionBreakout, DonchianTrend, TimeDrift, LiquidityFVG,
                                SilverBullet, ICT2022, JudasPO3, SMTDivergence, OTE)
}


def build_strategy(name: str, session, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](session, **(params or {}))


__all__ = ["Strategy", "REGISTRY", "build_strategy"]
