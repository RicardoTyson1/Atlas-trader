"""Take-profit levels (atlas_trader/targets.py): correct levels, nearest >= min R, no look-ahead."""
import numpy as np
import pandas as pd

from atlas_trader.config import Session
from atlas_trader.data import resample, synthetic_intraday
from atlas_trader.strategies import build_strategy
from atlas_trader.targets import TargetFinder, TargetParams

TZ = "America/New_York"


def bars(prices, start="2026-11-04 09:00", tz=TZ):
    """5m bars from a list of (open, high, low, close)."""
    idx = pd.date_range(pd.Timestamp(start, tz=tz), periods=len(prices), freq="5min").tz_convert("UTC")
    o, h, l, c = zip(*prices)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


def flat(n, px=100.0, w=0.5):
    return [(px, px + w, px - w, px)] * n


def test_unfilled_bearish_fvg_ce_is_a_long_target():
    p = flat(30, 110)
    p += [(110, 110.5, 108, 108.2), (108.2, 108.3, 104, 104.2), (104.2, 105, 103.5, 104)]  # gap 105..108
    p += flat(20, 100)
    df = bars(p)
    f = TargetFinder(df, TZ, TargetParams(front_atr=0.0, min_r=0.1))
    ces = [v for v, k in f.candidates(len(df) - 1, 1, 100.0) if k == "FVG 50%"]
    assert any(abs(v - 106.5) < 1e-9 for v in ces)


def test_filled_fvg_is_not_a_target():
    p = flat(30, 110)
    p += [(110, 110.5, 108, 108.2), (108.2, 108.3, 104, 104.2), (104.2, 105, 103.5, 104)]
    p += [(104, 107, 103.8, 104)]            # traded back up through the gap's middle
    p += flat(20, 100)
    df = bars(p)
    f = TargetFinder(df, TZ, TargetParams(front_atr=0.0, min_r=0.1))
    ces = [v for v, k in f.candidates(len(df) - 1, 1, 100.0) if k == "FVG 50%"]
    assert not any(abs(v - 106.5) < 1e-9 for v in ces)       # the 105-108 gap was revisited


def test_nearest_level_paying_min_r_wins():
    df = bars(flat(40, 100))
    f = TargetFinder(df, TZ, TargetParams(front_atr=0.0, min_r=1.0))
    f.candidates = lambda i, side, entry: [(101.0, "a"), (103.0, "b"), (106.0, "c")]
    assert f.find(len(df) - 1, 1, 100.0, 98.0) == (103.0, "b")     # 101 is only 0.5R
    assert f.find(len(df) - 1, 1, 100.0, 90.0) is None              # nothing pays 1R


def test_no_look_ahead():
    df = resample(synthetic_intraday(days=12, seed=7), "5m")
    i = len(df) - 60
    full = TargetFinder(df, TZ).candidates(i, 1, float(df["close"].iloc[i]))
    cut = TargetFinder(df.iloc[: i + 1], TZ).candidates(i, 1, float(df["close"].iloc[i]))
    assert sorted(full) == sorted(cut)


def test_strategies_place_targets_at_levels():
    df = resample(synthetic_intraday(days=40, seed=3), "5m")
    s = Session()
    for name, extra in (("momentum_rsi", {"tp": "levels", "hours": "00:00-23:59", "trend_filter": False}),):
        out = build_strategy(name, s, extra).generate(df)
        sig = out[out.signal != 0]
        assert len(sig), name
        assert sig["target"].notna().all() and sig["tp_kind"].notna().all()
        c = df["close"].reindex(sig.index)
        risk = (c - sig["stop"]).abs()
        assert ((sig["target"] - c) * sig["signal"] >= 0.999 * risk).all()   # >= 1R in the trade's favor
