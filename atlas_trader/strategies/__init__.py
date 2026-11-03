from .base import Strategy
from .orb import OpeningRangeBreakout
from .trend_pullback import TrendPullback
from .vwap_reversion import VwapReversion

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (OpeningRangeBreakout, TrendPullback, VwapReversion)
}


def build_strategy(name: str, session, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](session, **(params or {}))


__all__ = ["Strategy", "REGISTRY", "build_strategy"]
