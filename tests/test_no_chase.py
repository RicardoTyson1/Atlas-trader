"""No-chase filter on momentum_rsi: removes only signals, never adds; no look-ahead."""
import numpy as np

from atlas_trader.strategies import build_strategy


def test_no_chase_only_removes_signals(cfg, bars5):
    base = build_strategy("momentum_rsi", cfg.session, {"hours": "00:00-23:59", "disp_atr": 0.5})
    filt = build_strategy("momentum_rsi", cfg.session, {"hours": "00:00-23:59", "disp_atr": 0.5,
                                                        "max_candle_atr": 1.5, "max_run_atr": 2.0})
    a, b = base.generate(bars5), filt.generate(bars5)
    assert (a.signal != 0).sum() > 0
    assert ((b.signal != 0) <= (a.signal != 0)).all()        # subset
    assert (b.signal != 0).sum() < (a.signal != 0).sum()      # and it does remove some


def test_no_chase_no_lookahead(cfg, bars5):
    s = build_strategy("momentum_rsi", cfg.session, {"hours": "00:00-23:59", "disp_atr": 0.5,
                                                     "max_candle_atr": 1.5, "max_run_atr": 2.0})
    full = s.generate(bars5)
    for cut in (500, 1200, len(bars5) - 3):
        part = s.generate(bars5.iloc[:cut])
        assert (part.signal.values == full.signal.values[:cut]).all()
        np.testing.assert_allclose(part.stop.values, full.stop.values[:cut], equal_nan=True)
