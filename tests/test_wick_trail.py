"""P1 wick trail / P2 break-even: no look-ahead, forward-only stop, closed strong-candle confirmation."""
import numpy as np
import pytest

from atlas_trader.wick_trail import WickTrail, strong_body

ATR = 1.0


def feed(wt, candles):
    """candles: list of (o, h, l, c). Returns the stop after each candle."""
    out = []
    for o, h, l, c in candles:
        wt.update(o, h, l, c, ATR)
        out.append(wt.stop)
    return out


# long from 100, stop 99 (risk 1): +1R = 101
LONG_SETUP = [
    (100.0, 101.2, 99.9, 101.1),    # activation (high 101.2 >= 101); strong bull candle -> S, S high 101.2
    (101.1, 101.15, 100.5, 100.6),  # pullback, low 100.5 < previous low 99.9? no
    (100.6, 100.7, 100.3, 100.4),   # pullback, low 100.3 < 100.5 -> dip; pullback low 100.3
]


def test_strong_body_definition():
    assert strong_body(1, 100, 101, 99.9, 100.9, 1.0)          # body .9 of 1.1 range, >= .5 ATR
    assert not strong_body(1, 100, 101, 99, 100.6, 1.0)        # body 30% of range
    assert not strong_body(1, 100, 100.45, 100, 100.4, 1.0)    # body .4 < 0.5 x ATR
    assert not strong_body(1, 101, 101.1, 99.9, 100.0, 1.0)    # bearish
    assert strong_body(-1, 101, 101.1, 99.9, 100.0, 1.0)


def test_confirmation_moves_stop_to_pullback_low_minus_buffer():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.25)
    feed(wt, LONG_SETUP)
    new = wt.update(100.4, 101.6, 100.35, 101.5, ATR)          # strong, closes above S high 101.2
    assert new == pytest.approx(100.3 - 0.25)
    assert wt.stop == pytest.approx(100.05)


def test_confirmation_needs_a_closed_strong_candle():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.25)
    feed(wt, LONG_SETUP)
    # wick pokes above S's high but the candle CLOSES below it -> nothing
    assert wt.update(100.4, 101.5, 100.35, 101.0, ATR) is None
    # closes above S's high but the body is weak (< 60% of range) -> nothing
    assert wt.update(101.0, 102.5, 100.4, 101.3, ATR) is None
    # strong body but below 0.5 x ATR -> nothing
    assert WickTrail(1, 100.0, 99.0, 0.25).update(100, 101.0, 99.95, 100.0, 5.0) is None
    assert wt.stop == 99.0
    # a strong candle that closes above S's high -> stop moves
    assert wt.update(101.3, 102.6, 101.2, 102.5, ATR) == pytest.approx(100.3 - 0.25)


def test_pullback_needs_a_lower_low():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.1)
    feed(wt, [(100.0, 101.2, 99.9, 101.1),        # S
              (101.1, 101.15, 100.95, 101.0)])    # pause, low not below S's low 99.9
    assert wt.update(101.0, 101.9, 100.98, 101.8, ATR) is None  # confirmation but no real pullback
    assert wt.stop == 99.0


def test_nothing_before_plus_1r():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, breakeven=True)
    stops = feed(wt, [(100.0, 100.9, 99.8, 100.8),     # strong, but high 100.9 < 101
                      (100.8, 100.85, 100.2, 100.3),
                      (100.3, 100.95, 100.25, 100.9)])
    assert stops == [99.0, 99.0, 99.0] and not wt.active


def test_breakeven_at_plus_1r_includes_costs():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, breakeven=True, be_offset=0.24)
    assert wt.update(100.0, 101.1, 99.9, 101.0, ATR) == pytest.approx(100.24)
    off = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, breakeven=False)
    assert off.update(100.0, 101.1, 99.9, 101.0, ATR) is None


def test_breakeven_waits_for_a_close_above_it():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, breakeven=True, be_offset=0.24)
    assert wt.update(100.0, 101.1, 99.9, 100.1, ATR) is None     # touched +1R, closed below 100.24
    assert wt.update(100.1, 100.6, 100.0, 100.5, ATR) == pytest.approx(100.24)


def test_short_is_the_mirror():
    long_wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.25)
    short_wt = WickTrail(-1, -100.0, -99.0, buffer_atr=0.25)
    seq = LONG_SETUP + [(100.4, 101.6, 100.35, 101.5)]
    for o, h, l, c in seq:
        a = long_wt.update(o, h, l, c, ATR)
        b = short_wt.update(-o, -l, -h, -c, ATR)
        assert (a is None and b is None) or a == pytest.approx(-b)
    assert short_wt.stop == pytest.approx(-long_wt.stop)


def _random_walk(seed, n=400):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0.02, 0.4, n))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) + rng.exponential(0.15, n)
    l = np.minimum(o, c) - rng.exponential(0.15, n)
    return o, h, l, c


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("seed", range(8))
def test_stop_only_moves_forward(side, seed):
    o, h, l, c = _random_walk(seed)
    if side == -1:
        o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
    wt = WickTrail(side, o[0], o[0] - side * 0.5, buffer_atr=0.1, breakeven=bool(seed % 2), be_offset=0.24)
    prev = wt.stop
    for i in range(len(c)):
        new = wt.update(o[i], h[i], l[i], c[i], 0.5)
        assert side * (wt.stop - prev) >= 0                    # never backwards
        if new is not None:
            assert side * (c[i] - new) > 0                     # stays on the right side of price
        prev = wt.stop


def test_random_walks_exercise_the_trail():
    moves = 0
    for seed in range(8):
        o, h, l, c = _random_walk(seed)
        wt = WickTrail(1, o[0], o[0] - 0.5, buffer_atr=0.1)
        for i in range(len(c)):
            wt.update(o[i], h[i], l[i], c[i], 0.5)
        moves += wt.moves
    assert moves >= 8                                          # the forward-only test is not vacuous


@pytest.mark.parametrize("seed", range(5))
def test_no_look_ahead(seed):
    """The stop after candle k must not depend on any candle after k."""
    o, h, l, c = _random_walk(seed)
    full = WickTrail(1, o[0], o[0] - 0.5, buffer_atr=0.25, breakeven=True, be_offset=0.24)
    stops_full = [None] * len(c)
    for i in range(len(c)):
        full.update(o[i], h[i], l[i], c[i], 0.5)
        stops_full[i] = full.stop
    rng = np.random.default_rng(99)
    for k in (50, 150, 300):
        # same history up to k, scrambled future
        o2, h2, l2, c2 = (x.copy() for x in (o, h, l, c))
        o2[k + 1:] += rng.normal(0, 3, len(c) - k - 1)
        c2[k + 1:] = o2[k + 1:] + rng.normal(0, 1, len(c) - k - 1)
        h2[k + 1:] = np.maximum(o2[k + 1:], c2[k + 1:]) + 0.2
        l2[k + 1:] = np.minimum(o2[k + 1:], c2[k + 1:]) - 0.2
        wt = WickTrail(1, o[0], o[0] - 0.5, buffer_atr=0.25, breakeven=True, be_offset=0.24)
        for i in range(k + 1):
            wt.update(o2[i], h2[i], l2[i], c2[i], 0.5)
        assert wt.stop == stops_full[k]


def test_option_confirm_on_close():
    """confirm_on='close': a strong candle closing above S's close (but below S's high) confirms."""
    seq = LONG_SETUP
    conf = (100.4, 101.2, 100.35, 101.15)          # closes 101.15: above S close 101.1, below S high 101.2
    strict = WickTrail(1, 100.0, 99.0, buffer_atr=0.25)
    loose = WickTrail(1, 100.0, 99.0, buffer_atr=0.25, confirm_on="close")
    feed(strict, seq)
    feed(loose, seq)
    assert strict.update(*conf, ATR) is None
    assert loose.update(*conf, ATR) == pytest.approx(100.3 - 0.25)


def test_option_no_dip_required():
    seq = [(100.0, 101.2, 99.9, 101.1),            # S
           (101.1, 101.15, 100.95, 101.0)]         # pause, no lower low
    conf = (101.0, 101.9, 100.98, 101.8)
    strict = WickTrail(1, 100.0, 99.0, buffer_atr=0.1)
    loose = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, require_dip=False)
    feed(strict, seq)
    feed(loose, seq)
    assert strict.update(*conf, ATR) is None
    assert loose.update(*conf, ATR) == pytest.approx(100.95 - 0.1)


def test_option_later_activation():
    wt = WickTrail(1, 100.0, 99.0, buffer_atr=0.1, activate_r=2.0)
    feed(wt, LONG_SETUP + [(100.4, 101.6, 100.35, 101.5)])     # reaches 1.6R only
    assert wt.stop == 99.0 and not wt.active
