"""Candle trailing stop: moves only in the trade's favor, only after the start threshold,
and the trade's R is measured against the INITIAL stop."""
import numpy as np

from atlas_trader.backtest import run_backtest
from atlas_trader.strategies.base import Strategy
from test_backtest import bars


class TrailShot(Strategy):
    name = "trailshot"
    defaults = {}

    def generate(self, df):
        out = self._empty(df.index)
        out["trail_start_r"] = np.nan
        out["trail_buf"] = np.nan
        out.iloc[0, out.columns.get_loc("signal")] = 1
        out.iloc[0, out.columns.get_loc("stop")] = 98.0
        out.iloc[0, out.columns.get_loc("rr")] = 1000.0
        out.iloc[0, out.columns.get_loc("trail_start_r")] = 1.0
        out.iloc[0, out.columns.get_loc("trail_buf")] = 0.1
        return out


def test_trailing_stop_locks_profit(cfg):
    inst = cfg.instrument("NAS100")
    inst.spread_points = inst.slippage_points = 0.0
    prices = [
        (100, 100.5, 99.5, 100),    # 0 signal bar (risk = 2.0 from 100 to 98)
        (100, 101, 99.8, 100.8),    # 1 entry at 100
        (100.8, 102.5, 100.7, 102.4),  # 2 +2.4 >= 1R, strong, but prior bar not a pullback
        (102.4, 102.5, 101.6, 101.8),  # 3 pullback (red), low 101.6
        (101.8, 103.5, 101.7, 103.4),  # 4 strong green after pullback -> stop to 101.6-0.1 = 101.5
        (103.4, 103.5, 101.0, 101.2),  # 5 drops through 101.5 -> stopped in profit
        (101.2, 101.3, 95.0, 95.5),    # 6 would have hit the original stop
    ]
    r = run_backtest(bars(prices), TrailShot(cfg.session), inst, cfg)
    t = r.trades.iloc[0]
    assert t.stop_moves == 1
    assert abs(t.exit - 101.5) < 1e-9
    assert abs(t.r - (101.5 - 100) / 2.0) < 1e-9   # R vs the initial stop
    assert t.pnl > 0
