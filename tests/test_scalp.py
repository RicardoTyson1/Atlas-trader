import numpy as np
import pandas as pd
import pytest

from atlas_trader.backtest import run_backtest
from atlas_trader.data import resample, synthetic_intraday
from atlas_trader.strategies import build_strategy

MODES = ["cont", "fade", "rsi_fade", "breakout", "momentum"]


@pytest.fixture(scope="module")
def bars1():
    return synthetic_intraday(days=6, seed=4)  # 1-minute bars


@pytest.mark.parametrize("mode", MODES)
def test_scalp_no_lookahead(cfg, bars1, mode):
    s = build_strategy("scalp", cfg.session, {"mode": mode, "hours": "00:00-23:59", "k": 1.0})
    full = s.generate(bars1)
    for cut in (400, 1300, len(bars1) - 2):
        part = s.generate(bars1.iloc[:cut])
        assert (part.signal.values == full.signal.values[:cut]).all()
        np.testing.assert_allclose(part.stop.values, full.stop.values[:cut], equal_nan=True)


def test_scalp_holds_at_most_one_candle(cfg, bars1):
    cfg.risk.max_trades_per_day = 10**6
    cfg.risk.cooldown_after_loss_minutes = 0
    s = build_strategy("scalp", cfg.session, {"mode": "cont", "k": 1.0, "hours": "00:00-23:59", "target_r": 0})
    t = run_backtest(bars1, s, cfg.instrument("NAS100"), cfg).trades
    assert len(t) > 0
    held = (t.exit_time - t.entry_time)
    assert (held <= pd.Timedelta(minutes=1)).all()
