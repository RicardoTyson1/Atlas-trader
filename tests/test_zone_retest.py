"""Supply / demand zone retest (strategies/zone_retest.py)."""
import numpy as np
import pandas as pd

from atlas_trader.config import Session
from atlas_trader.data import resample, synthetic_intraday
from atlas_trader.strategies import build_strategy


def _bars(rows, start="2026-11-04 09:00"):
    idx = pd.date_range(pd.Timestamp(start, tz="America/New_York"), periods=len(rows), freq="5min").tz_convert("UTC")
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


def test_supply_zone_rejection_gives_a_sell():
    rows = [(100 + (i % 2), 101.5 + (i % 2), 99.5 + (i % 2), 100.5 + (i % 2)) for i in range(90)]  # noisy warm-up
    rows += [(110, 110.6, 109.6, 110.2), (110.2, 110.7, 109.7, 110.0), (110.0, 110.5, 109.6, 110.3),
             (110.3, 110.6, 109.8, 110.1)]                                   # tight base 109.6-110.7
    rows += [(110.1, 110.2, 101.0, 101.5)]                                   # displacement down
    rows += [(101.5, 102.5, 100.5, 102.0), (102.0, 104.0, 101.8, 103.8), (103.8, 106.0, 103.5, 105.8),
             (105.8, 108.0, 105.5, 107.9)]                                   # rally back, away from zone
    rows += [(108.5, 110.4, 107.5, 107.8)]                                   # into the zone, rejected (bearish)
    rows += [(108.0, 108.2, 106.0, 106.5)] * 5
    df = _bars(rows)
    out = build_strategy("zone_retest", Session(), {"zone_tf": "same", "disp_atr": 1.5}).generate(df)
    sig = out[out.signal != 0]
    assert list(sig.signal) == [-1]
    assert sig.index[0] == df.index[99]
    assert sig.stop.iloc[0] > 110.7                                          # above the zone


def test_no_look_ahead_and_valid_stops():
    df = resample(synthetic_intraday(days=120, seed=3), "5m")
    s = build_strategy("zone_retest", Session(), {"zone_tf": "15min"})
    full = s.generate(df)
    sig = full[full.signal != 0]
    c = df["close"].reindex(sig.index)
    assert ((sig.signal == 1) & (sig.stop < c) | (sig.signal == -1) & (sig.stop > c)).all()
    for cut in (len(df) // 2, len(df) - 200):
        part = s.generate(df.iloc[:cut])
        assert np.array_equal(full["signal"].iloc[:cut - 1].to_numpy(), part["signal"].iloc[:-1].to_numpy())
