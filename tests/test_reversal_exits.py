"""reversal_trail target study exits: fib targets, partial-close sizing, no look-ahead."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from atlas_trader.config import Session
from atlas_trader.reversal_exits import SwitchTrail, fib_target, split_qty


def test_fib_target_long_and_short():
    # long after a sell-off from 110 to 100: 50% retrace = 105, 100% = back to 110
    assert fib_target(1, 100.0, 110.0, 0.5) == pytest.approx(105.0)
    assert fib_target(1, 100.0, 110.0, 0.382) == pytest.approx(103.82)
    assert fib_target(1, 100.0, 110.0, 1.0) == pytest.approx(110.0)
    # short after a rally from 100 to 110
    assert fib_target(-1, 110.0, 100.0, 0.618) == pytest.approx(103.82)
    with pytest.raises(ValueError):
        fib_target(1, 100.0, 110.0, 0.0)


@pytest.mark.parametrize("lots, want", [
    (1.00, (0.50, 0.50)),
    (0.37, (0.18, 0.19)),      # odd number of steps: the smaller half is closed, the bigger trails
    (0.03, (0.01, 0.02)),
    (0.02, (0.01, 0.01)),
    (0.01, (0.01, 0.0)),       # too small to split: all of it goes at the target
    (2.00, (1.00, 1.00)),
])
def test_split_qty(lots, want):
    first, rest = split_qty(lots, 0.01, 0.01)
    assert (first, rest) == pytest.approx(want)
    assert first + rest == pytest.approx(lots)


def test_split_qty_respects_min_lot_and_grid():
    assert split_qty(0.15, 0.01, 0.1) == pytest.approx((0.15, 0.0))      # 0.07 / 0.08 both < min 0.1
    assert split_qty(0.40, 0.01, 0.1) == pytest.approx((0.20, 0.20))
    first, rest = split_qty(0.333, 0.01, 0.01)                            # snapped to 0.33 first
    assert first + rest == pytest.approx(0.33)
    for lots in np.round(np.arange(0.01, 3.0, 0.01), 2):
        f, r = split_qty(float(lots), 0.01, 0.01)
        assert f >= 0.01 and r >= 0 and f + r == pytest.approx(lots)
        assert abs(round(f / 0.01) - f / 0.01) < 1e-6 and abs(round(r / 0.01) - r / 0.01) < 1e-6


def test_switch_trail_waits_for_1r_until_the_level_is_reached():
    # long 100, stop 99 (1R = 101), 50% level at 100.6
    st = SwitchTrail(1, 100.0, 99.0, level=100.6, trail_start_r=1.0, trail_buf=0.05)
    o = [100.2, 100.4, 100.25]
    h = [100.5, 100.45, 100.55]
    l = [100.1, 100.2, 100.2]
    c = [100.4, 100.25, 100.5]                     # pullback then strong candle, but < 1R and < level
    assert st.update(o, h, l, c, 99.0) is None and not st.reached
    o2, h2, l2, c2 = [100.4, 100.25, 100.3], [100.45, 100.35, 100.7], [100.2, 100.15, 100.28], [100.25, 100.3, 100.65]
    new = st.update(o2, h2, l2, c2, 99.0)          # closed candle reached 100.6 -> trail from 0R
    assert st.reached
    assert new == pytest.approx(100.15 - 0.05)     # stop under the pullback candle's low


def test_switch_trail_short_mirror():
    st = SwitchTrail(-1, 100.0, 101.0, level=99.4, trail_start_r=1.0, trail_buf=0.05)
    o, h, l, c = [99.6, 99.75, 99.7], [99.8, 99.85, 99.72], [99.55, 99.65, 99.3], [99.75, 99.7, 99.35]
    new = st.update(o, h, l, c, 101.0)
    assert st.reached and new == pytest.approx(99.85 + 0.05)


def test_switch_trail_no_look_ahead():
    """The stop decided at bar k depends only on bars up to k."""
    rng = np.random.default_rng(3)
    n = 300
    c = 100 + np.cumsum(rng.normal(0.03, 0.3, n))
    o = np.concatenate([[100.0], c[:-1]])
    h, l = np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1

    def run(o, h, l, c, upto):
        st, stop, out = SwitchTrail(1, 100.0, 99.0, 100.8, 1.0, 0.05), 99.0, []
        for i in range(2, upto + 1):
            new = st.update(o[i - 2:i + 1], h[i - 2:i + 1], l[i - 2:i + 1], c[i - 2:i + 1], stop)
            stop = new if new is not None else stop
            out.append(stop)
        return out

    full = run(o, h, l, c, n - 1)
    for k in (40, 120, 250):
        o2, h2, l2, c2 = (x.copy() for x in (o, h, l, c))
        c2[k + 1:] += 50.0                         # wildly different future
        h2[k + 1:] += 50.0
        assert run(o2, h2, l2, c2, k) == full[:k - 1]


def test_model_move_columns_have_no_look_ahead():
    """move_ext / move_start (used to place the fib targets) are known at the signal bar."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
    from reversal_trail_model import ReversalTrail
    from atlas_trader.data import synthetic_intraday

    df = synthetic_intraday(days=8, tf_minutes=1, start="2025-10-06", start_price=2000.0, daily_vol=0.03, seed=4)
    sess = Session(timezone="America/New_York")
    m = ReversalTrail(sess, move_atr=3.0, rsi_level=0, target="half_move", hours="00:00-23:59")
    full = m.generate(df)
    sig_rows = np.flatnonzero(full["signal"].to_numpy() != 0)
    sig_rows = sig_rows[sig_rows >= 100]          # the model needs 100 bars before it signals at all
    assert len(sig_rows) >= 3
    for i in sig_rows[:5]:
        part = m.generate(df.iloc[: i + 1])
        for col in ("signal", "stop", "target", "move_ext", "move_start"):
            assert part[col].iloc[-1] == pytest.approx(full[col].iloc[i], nan_ok=True)
        # the live 50% target is exactly the 0.5 fib of the recorded move
        side = int(full["signal"].iloc[i])
        assert full["target"].iloc[i] == pytest.approx(
            fib_target(side, full["move_ext"].iloc[i], full["move_start"].iloc[i], 0.5))
