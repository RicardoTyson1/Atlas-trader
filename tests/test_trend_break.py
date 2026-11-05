"""Trendline break -> retest -> rejection (strategies/trend_break.py)."""
import numpy as np

from atlas_trader.config import Session
from atlas_trader.data import resample, synthetic_intraday
from atlas_trader.strategies import build_strategy


def _df():
    return resample(synthetic_intraday(days=120, seed=3), "5m")


def test_signals_have_stops_on_the_right_side_and_trail_settings():
    df = _df()
    out = build_strategy("trend_break", Session(), {"tl_tf": "15min"}).generate(df)
    sig = out[out.signal != 0]
    assert len(sig) > 0
    c = df["close"].reindex(sig.index)
    assert ((sig["signal"] == 1) & (sig["stop"] < c) | (sig["signal"] == -1) & (sig["stop"] > c)).all()
    assert sig["trail_start_r"].eq(1.0).all() and sig["trail_buf"].notna().all()


def test_rr_target_mode():
    out = build_strategy("trend_break", Session(), {"tl_tf": "15min", "target": "rr"}).generate(_df())
    assert out.loc[out.signal != 0, "rr"].eq(2.0).all()


def test_no_look_ahead():
    df = _df()
    s = build_strategy("trend_break", Session(), {"tl_tf": "15min"})
    full = s.generate(df)
    for cut in (len(df) // 2, len(df) - 300, len(df) - 37):
        part = s.generate(df.iloc[:cut])
        a = full["signal"].iloc[:cut].to_numpy()
        b = part["signal"].to_numpy()
        # every signal seen with the full history must be seen at the time, and vice versa
        assert np.array_equal(a[:-1], b[:-1]), cut
