"""Order-flow gates: correct features, gates, and no look-ahead."""
import numpy as np
import pandas as pd
import pytest

from atlas_trader import orderflow


def frames(n=200, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05 13:00", periods=n, freq="5min", tz="UTC")
    c = 2000 + np.cumsum(rng.normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    xau = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3,
                        "close": c, "volume": 1.0}, index=idx)
    gc = xau[["open", "high", "low", "close"]] + 20.0
    gc["volume"] = rng.integers(100, 200, n).astype(float)
    return xau, gc


def test_vol_ratio_uses_previous_bars_only():
    xau, gc = frames()
    gc.iloc[100, gc.columns.get_loc("volume")] = 10_000.0
    f = orderflow.features(xau, gc)
    assert f["vol_ratio"].iloc[100] > 30            # spike vs the previous 20 bars
    assert f["vol_ratio"].iloc[99] < 3              # the bar before is not affected
    assert f["vol_ratio"].iloc[:20].isna().all()    # warm-up


def test_missing_gc_fails_gates():
    xau, gc = frames()
    gc = gc.drop(gc.index[150:160])
    f = orderflow.features(xau, gc)
    sig = np.zeros(len(xau), int)
    sig[155] = -1
    for g in ("V1", "D1", "A1"):
        assert orderflow.gate(sig, f, g)[155] == 0


def test_volume_gates():
    xau, gc = frames()
    gc.iloc[100, gc.columns.get_loc("volume")] = 400.0     # ~2.7x
    f = orderflow.features(xau, gc)
    sig = np.zeros(len(xau), int)
    sig[100] = 1
    sig[103] = 1
    assert orderflow.gate(sig, f, "V2")[100] == 1
    assert orderflow.gate(sig, f, "V1")[103] == 0 or f["vol_ratio"].iloc[103] >= 1.5
    assert orderflow.gate(sig, f, "V4")[103] == 1           # spike inside the 6-bar window


def test_divergence_is_side_aware():
    xau, gc = frames()
    i = 120
    xau.iloc[i, xau.columns.get_loc("high")] = xau["high"].iloc[i - 20:i].max() + 5   # new high
    gc.iloc[i - 20:i + 1, gc.columns.get_loc("close")] = gc["open"].iloc[i - 20:i + 1] - 1  # selling
    f = orderflow.features(xau, gc)
    assert f["div_bear"].iloc[i]
    sig = np.zeros(len(xau), int)
    sig[i + 2] = -1
    assert orderflow.gate(sig, f, "D1")[i + 2] == -1
    sig[i + 2] = 1
    assert orderflow.gate(sig, f, "D1")[i + 2] == 0 or f["div_bull"].iloc[i - 4:i + 3].any()


def test_no_look_ahead():
    xau, gc = frames(300, seed=7)
    full = orderflow.features(xau, gc)
    cut = 180
    part = orderflow.features(xau.iloc[:cut], gc.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part)
    sig = np.random.default_rng(3).choice([-1, 0, 1], size=300, p=[0.05, 0.9, 0.05])
    for g in orderflow.GATES:
        assert (orderflow.gate(sig, full, g)[:cut] == orderflow.gate(sig[:cut], part, g)).all()


def test_unknown_gate():
    xau, gc = frames()
    with pytest.raises(ValueError):
        orderflow.gate(np.zeros(len(xau), int), orderflow.features(xau, gc), "X9")
