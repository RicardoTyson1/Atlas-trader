"""Chart pattern detector: each pattern fires on its breakout, on the right side, once, with no look-ahead."""
import numpy as np
import pandas as pd
import pytest

from atlas_trader.strategies import build_strategy


def path_bars(points, noise=0.0, seed=0, start="2026-01-05 13:00"):
    """Bars along a piecewise-linear close path. points = [(n_bars, target_price), ...]."""
    closes = [100.0]
    for nb, tgt in points:
        closes += list(np.linspace(closes[-1], tgt, nb + 1)[1:])
    c = np.array(closes)
    rng = np.random.default_rng(seed)
    c = c + rng.normal(0, noise, len(c)) if noise else c
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.3
    l = np.minimum(o, c) - 0.3
    idx = pd.date_range(start, periods=len(c), freq="5min", tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


PARAMS = {"pivot": 2, "tol_atr": 1.0, "min_height_atr": 2.0, "max_stop_atr": 20.0, "min_rr": 0.5,
          "max_len": 200, "max_wait": 40}

# warm-up so ATR exists, then the pattern
WARM = [(30, 100.0)]

CASES = {
    "double_top": (WARM + [(10, 110), (8, 104), (8, 110), (12, 100)], -1),
    "double_bottom": (WARM + [(10, 90), (8, 96), (8, 90), (12, 100)], 1),
    "head_shoulders": (WARM + [(8, 108), (6, 104), (8, 114), (8, 104), (6, 108), (12, 98)], -1),
    "inverse_head_shoulders": (WARM + [(8, 92), (6, 96), (8, 86), (8, 96), (6, 92), (12, 102)], 1),
    "ascending_triangle": (WARM + [(8, 110), (6, 100), (6, 110), (6, 104), (6, 110), (5, 106), (6, 116)], 1),
    "descending_triangle": (WARM + [(8, 90), (6, 100), (6, 90), (6, 96), (6, 90), (5, 94), (6, 84)], -1),
    "rising_wedge": (WARM + [(8, 106), (6, 100), (6, 109), (6, 106), (6, 111), (5, 109.5), (8, 100)], -1),
    "falling_wedge": (WARM + [(8, 94), (6, 100), (6, 91), (6, 94), (6, 89), (5, 90.5), (8, 100)], 1),
    "bull_flag": ([(50, 100.0)] + [(6, 115), (5, 112), (2, 120)], 1),
    "bear_flag": ([(50, 100.0)] + [(6, 85), (5, 88), (2, 80)], -1),
}


@pytest.mark.parametrize("pattern", sorted(CASES))
def test_pattern_fires_on_breakout(pattern, cfg):
    pts, side = CASES[pattern]
    df = path_bars(pts)
    strat = build_strategy("chart_patterns", cfg.session, {**PARAMS, "patterns": pattern})
    out = strat.generate(df)
    hits = out[out.signal != 0]
    assert len(hits) >= 1, f"{pattern} never fired"
    first = hits.iloc[0]
    assert first.signal == side
    assert first["pattern"] == pattern
    c = df.close.loc[hits.index[0]]
    assert side * (c - first.stop) > 0            # stop on the correct side
    assert side * (first.target - c) > 0          # target on the profit side


def test_symmetrical_triangle_either_side(cfg):
    up = path_bars(WARM + [(8, 110), (6, 90), (6, 106), (6, 94), (6, 103), (5, 98), (8, 112)])
    dn = path_bars(WARM + [(8, 110), (6, 90), (6, 106), (6, 94), (6, 103), (5, 98), (8, 86)])
    for df, side in ((up, 1), (dn, -1)):
        out = build_strategy("chart_patterns", cfg.session,
                             {**PARAMS, "patterns": "symmetrical_triangle"}).generate(df)
        hits = out[out.signal != 0]
        # an earlier, wider triangle inside the path can break first; the final break decides
        assert len(hits) >= 1 and hits.iloc[-1].signal == side


def test_flat_market_no_signals(cfg):
    df = path_bars([(300, 100.0)], noise=0.01, seed=1)
    out = build_strategy("chart_patterns", cfg.session).generate(df)
    assert (out.signal == 0).all()


def test_each_instance_trades_once(cfg):
    pts, _ = CASES["double_top"]
    df = path_bars(pts + [(4, 101), (6, 97)])   # wiggles around the neckline after the break
    out = build_strategy("chart_patterns", cfg.session, {**PARAMS, "patterns": "double_top"}).generate(df)
    assert (out.signal != 0).sum() == 1


def test_no_lookahead_on_patterns(cfg):
    pts = (WARM + [(10, 110), (8, 104), (8, 110), (12, 100)] + [(8, 92), (6, 96), (8, 86), (8, 96), (6, 92), (12, 102)]
           + [(6, 115), (5, 112), (2, 120)])
    df = path_bars(pts, noise=0.05, seed=4)
    strat = build_strategy("chart_patterns", cfg.session, PARAMS)
    full = strat.generate(df)
    for cut in range(60, len(df), 7):
        part = strat.generate(df.iloc[:cut])
        assert (part.signal.values == full.signal.values[:cut]).all(), cut
        np.testing.assert_allclose(part.stop.values, full.stop.values[:cut], equal_nan=True)
        np.testing.assert_allclose(part.target.values, full.target.values[:cut], equal_nan=True)


def test_unknown_pattern_rejected(cfg):
    with pytest.raises(ValueError):
        build_strategy("chart_patterns", cfg.session, {"patterns": "cup_and_handle"}).generate(path_bars([(100, 100)]))


def test_hours_filter(cfg):
    pts, _ = CASES["double_top"]
    df = path_bars(pts)   # starts 13:00 UTC = 08:00 NY; break lands mid-morning NY
    out = build_strategy("chart_patterns", cfg.session,
                         {**PARAMS, "patterns": "double_top", "hours": "20:00-21:00"}).generate(df)
    assert (out.signal == 0).all()
