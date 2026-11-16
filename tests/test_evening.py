"""Researched session setups (evening + morning): each mode fires on random data, no look-ahead."""
import numpy as np
import pandas as pd
import pytest

from atlas_trader.strategies import build_strategy


def walk(days=40, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-05 23:00", periods=days * 24 * 12, freq="5min", tz="UTC")
    idx = idx[idx.tz_convert("America/New_York").dayofweek < 5]
    c = 2000 + np.cumsum(rng.normal(0, 0.9, len(idx)))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.6, (2, len(c))))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + w[0], "low": np.minimum(o, c) - w[1],
                         "close": c, "volume": 1.0}, index=idx)


CASES = {
    "fade_eve": {"mode": "range_fade", "wick_frac": 0.3, "target": "far"},
    "drive_eve": {"mode": "open_drive", "drive_atr": 1.0, "variant": "fade"},
    "overnight": {"mode": "overnight", "bias": "4h"},
    "sge": {"mode": "china_open", "variant": "fade"},
    "fade_am": {"mode": "range_fade", "range_window": "03:00-08:00", "hours": "08:00-11:00", "wick_frac": 0.3,
                "target": "far"},
    "fix": {"mode": "china_open", "anchor": "10:00", "pre_from": "08:00", "hours": "08:00-11:00", "variant": "cont"},
    "short_am": {"mode": "overnight", "entry_time": "08:00", "direction": "short", "hold_bars": 24,
                 "hours": "08:00-11:00"},
    "ib": {"mode": "ib_retrace", "hours": "08:00-11:00", "ib_window": "08:00-09:00", "min_rr": 0.5},
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_mode_fires_inside_hours_without_lookahead(case, cfg):
    df = walk()
    s = build_strategy("evening", cfg.session, CASES[case])
    full = s.generate(df)
    hits = full[full.signal != 0]
    assert len(hits) >= 1, case
    assert ((hits.signal * (df.close[hits.index] - hits.stop)) > 0).all()
    for cut in range(2000, len(df), 1500):
        part = s.generate(df.iloc[:cut])
        assert (part.signal.values == full.signal.values[:cut]).all(), (case, cut)
        np.testing.assert_allclose(part.stop.values, full.stop.values[:cut], equal_nan=True)
