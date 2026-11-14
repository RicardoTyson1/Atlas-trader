"""Session sweep reversal: fires after a sweep of the last session's high/low, with the HTF bias, once."""
import numpy as np
import pandas as pd

from atlas_trader.strategies import build_strategy


def bars(path_ny, start="2026-01-05 00:00"):
    """5m bars from (n_bars, target) legs, starting at NY midnight Monday."""
    closes = [2000.0]
    for nb, tgt in path_ny:
        closes += list(np.linspace(closes[-1], tgt, nb + 1)[1:])
    c = np.array(closes)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(pd.Timestamp(start, tz="America/New_York"), periods=len(c), freq="5min").tz_convert("UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.2, "low": np.minimum(o, c) - 0.2,
                         "close": c, "volume": 1.0}, index=idx)


# Monday: long down-trend for the bias (00:00-08:00), NY session 08:00-17:00 ranges 1960-1980,
# quiet 17:00-20:30, then at ~20:30 price spikes above the session high and closes back inside.
LEGS = ([(96, 1960)]                                 # 00:00-08:00 falling: 1H/4H bias down
        + [(18, 1980), (18, 1962), (18, 1979), (18, 1963), (18, 1975), (18, 1966)]   # 08:00-17:00 range
        + [(42, 1968)]                               # 17:00-20:30 drift
        + [(2, 1984), (2, 1976), (24, 1955)])        # sweep above 1980, close back inside, then drop
P = {"ref_session": "08:00-17:00", "bias": "none", "confirm": "close_inside", "hours": "20:00-24:00",
     "max_wait": 6}


def test_fires_short_after_high_sweep(cfg):
    df = bars(LEGS)
    out = build_strategy("session_sweep", cfg.session, P).generate(df)
    hits = out[out.signal != 0]
    assert len(hits) == 1
    t = hits.index[0].tz_convert("America/New_York")
    assert hits.signal.iloc[0] == -1 and "20:00" <= t.strftime("%H:%M") <= "21:00"
    assert hits.stop.iloc[0] > 1984.2                         # stop above the sweep extreme (1984.2)


def test_choch_confirm_also_fires(cfg):
    out = build_strategy("session_sweep", cfg.session, {**P, "confirm": "choch", "max_wait": 20, "max_stop_atr": 20.0}).generate(bars(LEGS))
    assert (out.signal == -1).sum() == 1


def test_bias_filter_blocks_counter_trend(cfg):
    out = build_strategy("session_sweep", cfg.session, {**P, "bias": "1h"}).generate(bars([(200, 2100), (20, 1970)] + LEGS[1:]))
    assert (out.signal == -1).sum() == 0


def test_outside_hours_no_signal(cfg):
    out = build_strategy("session_sweep", cfg.session, {**P, "hours": "08:00-11:00"}).generate(bars(LEGS))
    assert (out.signal == 0).all()


def test_no_lookahead(cfg):
    df = bars(LEGS + [(30, 1990), (30, 1950)])
    s = build_strategy("session_sweep", cfg.session, {**P, "bias": "both", "confirm": "choch"})
    full = s.generate(df)
    for cut in range(210, len(df), 9):
        part = s.generate(df.iloc[:cut])
        assert (part.signal.values == full.signal.values[:cut]).all(), cut
        np.testing.assert_allclose(part.stop.values, full.stop.values[:cut], equal_nan=True)


def test_sweep_gate_keeps_only_matching_side():
    from atlas_trader.strategies.session_sweep import sweep_context, sweep_gate
    df = bars(LEGS)
    ctx = sweep_context(df, "America/New_York", "08:00-17:00", "none")
    loc = df.index.tz_convert("America/New_York")
    hm = np.asarray(loc.strftime("%H:%M"))
    after = (hm >= "20:45") & (hm <= "21:30")     # before the later drop under the session low
    before = (hm > "17:00") & (hm < "20:40")
    assert ctx["swept_hi"][after].all() and not ctx["swept_hi"][before].any()
    assert not ctx["swept_lo"][after].any()
    sig = np.zeros(len(df), int)
    sig[after] = -1
    assert (sweep_gate(df, sig, ctx, inside=False)[after] == -1).all()     # short after a high sweep: kept
    sig[after] = 1
    assert (sweep_gate(df, sig, ctx, inside=False) == 0).all()             # long without a low sweep: dropped


def test_sweep_context_no_lookahead():
    from atlas_trader.strategies.session_sweep import sweep_context
    df = bars(LEGS + [(30, 1990), (30, 1950)])
    full = sweep_context(df, "America/New_York", "08:00-17:00", "both")
    for cut in range(210, len(df), 13):
        part = sweep_context(df.iloc[:cut], "America/New_York", "08:00-17:00", "both")
        for k in ("lvl_hi", "lvl_lo", "swept_hi", "swept_lo", "bias"):
            np.testing.assert_array_equal(part[k], full[k][:cut])


# NY session 08:00-17:00 tops at ~1980; at 20:30 price breaks out and CLOSES above, pulls back to
# 1980 (new support), then rallies -> long on the retest candle.
RETEST = ([(96, 1960)] + [(18, 1980), (18, 1962), (18, 1979), (18, 1963), (18, 1975), (18, 1966)]
          + [(42, 1972)] + [(3, 1986), (4, 1980.3), (1, 1982), (20, 1995)])


def test_retest_mode_goes_long_after_break_and_retest(cfg):
    p = {**P, "mode": "retest", "retest_tol_atr": 0.5}
    out = build_strategy("session_sweep", cfg.session, p).generate(bars(RETEST))
    hits = out[out.signal != 0]
    assert len(hits) == 1 and hits.signal.iloc[0] == 1
    assert hits.stop.iloc[0] < 1980.2                    # stop under the retested level


def test_retest_mode_needs_bias_in_breakout_direction(cfg):
    # the opening down-leg makes the 1H trend down: an upside break is not the favored direction
    p = {**P, "mode": "retest", "retest_tol_atr": 0.5, "bias": "1h"}
    out = build_strategy("session_sweep", cfg.session, p).generate(bars([(200, 2100), (20, 1970)] + RETEST[1:]))
    assert (out.signal == 1).sum() == 0
