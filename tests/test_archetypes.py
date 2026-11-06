"""New archetype strategies: they must fire on test data and never look ahead."""
import numpy as np
import pytest

from atlas_trader.data import resample, synthetic_intraday
from atlas_trader.indicators import supertrend
from atlas_trader.strategies import build_strategy

ALL = "00:00-23:59"
CASES = [("bb_reversion", {"hours": ALL, "bb_k": 2.0}),
         ("st_confluence", {"hours": ALL}),
         ("smc_ob", {"hours": ALL})]


@pytest.fixture(scope="module")
def bars():
    return resample(synthetic_intraday(days=120, tf_minutes=1, start_price=4000, daily_vol=0.012, seed=4), "5m")


@pytest.mark.parametrize("name,params", CASES)
def test_fires_and_no_lookahead(name, params, cfg, bars):
    strat = build_strategy(name, cfg.session, params)
    full = strat.generate(bars)
    assert (full["signal"] != 0).sum() > 5, f"{name} produced (almost) no signals"
    for cut in (1000, 2500, len(bars) - 7):
        part = strat.generate(bars.iloc[:cut])
        assert (part["signal"].values == full["signal"].values[:cut]).all(), f"lookahead in {name} at {cut}"
        np.testing.assert_allclose(part["stop"].values, full["stop"].values[:cut], equal_nan=True)


def test_supertrend_is_causal(bars):
    full = supertrend(bars)[1].to_numpy()
    for cut in (500, 1500, 3000):
        assert (supertrend(bars.iloc[:cut])[1].to_numpy() == full[:cut]).all()
