"""Strategies the bot can run.

Only momentum_rsi is kept: it is the one model that was profitable in both halves of the
replay (+0.23R/trade, research results in docs/). Every other model that failed testing
was deleted on 2026-11-05 (Ricardo); its results stay in docs/RESULTS_*.md and git history.
"""
from .base import Strategy
from .momentum_rsi import MomentumRSI
from .bb_reversion import BBReversion
from .st_confluence import STConfluence
from .smc_ob import SMCOrderBlock
from .scalp import Scalp

# bb_reversion / st_confluence / smc_ob: added 2026-11-06 for testing (research/archetypes_study.py);
# only the ones that pass go into config/default.yaml.
# scalp: 60-second scalper under test (research/scalp_study.py), not on the live bot.
REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (MomentumRSI, BBReversion, STConfluence, SMCOrderBlock, Scalp)
}


def build_strategy(name: str, session, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](session, **(params or {}))


__all__ = ["Strategy", "REGISTRY", "build_strategy"]
