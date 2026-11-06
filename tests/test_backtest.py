import numpy as np
import pandas as pd
import pytest

from atlas_trader.backtest import run_backtest
from atlas_trader.montecarlo import simulate_challenge
from atlas_trader.strategies import build_strategy
from atlas_trader.strategies.base import Strategy


class OneShot(Strategy):
    """Long at a fixed bar with a fixed stop/target, for testing the engine."""
    name = "oneshot"
    defaults = {"at": 0, "stop": 0.0, "target": 0.0, "side": 1}

    def generate(self, df):
        out = self._empty(df.index)
        i = self.p["at"]
        out.iloc[i, out.columns.get_loc("signal")] = self.p["side"]
        out.iloc[i, out.columns.get_loc("stop")] = self.p["stop"]
        out.iloc[i, out.columns.get_loc("target")] = self.p["target"]
        return out


def bars(prices, start="2025-01-06 10:00"):
    idx = pd.date_range(pd.Timestamp(start, tz="America/New_York"), periods=len(prices), freq="5min").tz_convert("UTC")
    rows = [dict(open=o, high=h, low=l, close=c, volume=1) for o, h, l, c in prices]
    return pd.DataFrame(rows, index=idx)


def zero_cost(cfg):
    inst = cfg.instrument("NAS100")
    inst.spread_points = inst.slippage_points = 0
    return inst


def test_target_hit(cfg):
    inst = zero_cost(cfg)
    df = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 111, 100, 110), (110, 110, 110, 110)])
    res = run_backtest(df, OneShot(cfg.session, at=0, stop=90, target=110), inst, cfg)
    t = res.trades.iloc[0]
    assert t.entry == 100 and t.exit == 110 and t.reason == "target"
    assert t.r == pytest.approx(1.0)
    # risk 0.5% of 50k = 250 over a 10pt stop -> 25 lots, capped by max_lots 5
    assert t.lots == pytest.approx(5.0)


def test_stop_first_when_both_hit(cfg):
    inst = zero_cost(cfg)
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 120, 80, 100), (100, 100, 100, 100)])
    res = run_backtest(df, OneShot(cfg.session, at=0, stop=90, target=110), inst, cfg)
    assert res.trades.iloc[0].reason == "stop"


def test_gap_through_stop_fills_at_open(cfg):
    inst = zero_cost(cfg)
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (85, 86, 84, 85), (85, 85, 85, 85)])
    res = run_backtest(df, OneShot(cfg.session, at=0, stop=90, target=110), inst, cfg)
    t = res.trades.iloc[0]
    assert t.exit == 85 and t.reason == "stop (gap)" and t.r == pytest.approx(-1.5)


def test_flat_by_session_end(cfg):
    inst = zero_cost(cfg)
    df = bars([(100, 100, 100, 100)] * 14, start="2025-01-06 14:50")
    res = run_backtest(df, OneShot(cfg.session, at=0, stop=90, target=200), inst, cfg)
    t = res.trades.iloc[0]
    assert t.reason == "end of session"
    assert t.exit_time.tz_convert("America/New_York").strftime("%H:%M") <= "15:50"


def test_costs_reduce_pnl(cfg, bars5):
    s = build_strategy("momentum_rsi", cfg.session, {"hours": "00:00-23:59"})
    inst = cfg.instrument("NAS100")
    with_cost = run_backtest(bars5, s, inst, cfg).stats["net_profit"]
    inst.spread_points = inst.slippage_points = 0
    without = run_backtest(bars5, s, inst, cfg).stats["net_profit"]
    assert without > with_cost


def test_never_breaches_on_random_data(cfg, bars5):
    for name in ("momentum_rsi",):
        res = run_backtest(bars5, build_strategy(name, cfg.session, {"hours": "00:00-23:59"}), cfg.instrument("NAS100"), cfg)
        assert res.stats["prop_breached"] is None


def test_montecarlo_probabilities(cfg):
    daily = pd.DataFrame({"pnl": [500.0, -200.0, 300.0], "worst_intraday": [-100.0, -300.0, -50.0]})
    mc = simulate_challenge(daily, cfg.prop, 50000, max_days=60, paths=500)
    assert mc.p_pass + mc.p_fail + mc.p_undecided == pytest.approx(1.0)
    assert mc.p_pass > 0.9
    bad = pd.DataFrame({"pnl": [-1500.0], "worst_intraday": [-1500.0]})
    assert simulate_challenge(bad, cfg.prop, 50000, paths=50).p_fail == 1.0


def test_exit_signal_and_overnight(cfg):
    import copy
    c2 = copy.deepcopy(cfg)
    c2.session.overnight = True
    from datetime import time as _time
    c2.session.last_entry = _time(23, 0)
    inst = zero_cost(c2)

    class EnterThenExit(Strategy):
        name = "ete"
        defaults = {}

        def generate(self, df):
            out = self._empty(df.index)
            out.iloc[0, out.columns.get_loc("signal")] = 1
            out.iloc[0, out.columns.get_loc("stop")] = 50.0
            out.iloc[0, out.columns.get_loc("rr")] = 1000.0
            out.iloc[3, out.columns.get_loc("exit")] = 1
            return out

    # bars run from 15:40 past the 15:50 flat time; overnight keeps the trade open
    df = bars([(100, 100, 100, 100)] * 2 + [(101, 101, 101, 101)] * 4, start="2025-01-06 15:40")
    t = run_backtest(df, EnterThenExit(c2.session), inst, c2).trades.iloc[0]
    assert t.reason == "exit signal" and t.exit == 101


def test_time_exit(cfg):
    inst = zero_cost(cfg)

    class Hold2(Strategy):
        name = "h2"
        defaults = {}

        def generate(self, df):
            out = self._empty(df.index)
            out.iloc[0, out.columns.get_loc("signal")] = 1
            out.iloc[0, out.columns.get_loc("stop")] = 50.0
            out.iloc[0, out.columns.get_loc("rr")] = 1000.0
            out.iloc[0, out.columns.get_loc("max_bars")] = 2
            return out

    df = bars([(100, 100, 100, 100)] * 6)
    t = run_backtest(df, Hold2(cfg.session), inst, cfg).trades.iloc[0]
    assert t.reason == "time exit" and t.bars == 2
