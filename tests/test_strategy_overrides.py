"""Per-strategy overrides (donchian_trend_1h, 2026-11-17): hold_overnight, risk_pct, ignore_regime,
own_slot (its own position next to the intraday strategies), and the "no target" TP cap."""
from datetime import time

import pandas as pd

from atlas_trader.brokers.sim import SimBroker
from atlas_trader.config import StrategySpec
from atlas_trader.data import synthetic_intraday
from atlas_trader.notify import MemoryNotifier
from atlas_trader.runner import Runner

MAIN = "momentum_rsi"
DON = "donchian_1h"


def setup(cfg, hedging=True):
    cfg.strategies = [
        StrategySpec(name="momentum_rsi", instruments=["NAS100"], timeframe="5m"),
        StrategySpec(name="donchian_trend", label=DON, instruments=["NAS100"], timeframe="1H",
                     params={"entry_bars": 55, "exit_bars": 20}, hold_overnight=True, risk_pct=0.25,
                     ignore_regime=True, own_slot=True),
    ]
    cfg.risk.hedging_account = hedging
    cfg.risk.max_trades_per_day = 100
    s = cfg.session
    s.trade_start, s.last_entry, s.flat_by, s.reopen = time(0, 0), time(16, 30), time(16, 50), time(18, 5)
    base = synthetic_intraday(days=3, seed=11)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0}, multi=True)
    runner = Runner(cfg, broker, MemoryNotifier())
    t = base.index[0] + pd.Timedelta(minutes=120)          # 11:30 NY on day 1
    broker.set_time(t)
    return base, broker, runner, t


def enter(runner, broker, t, source, side=1, rr=2.0):
    local = t.tz_convert("America/New_York")
    px = broker.last_price("NAS100")
    stop = px - side * 300.0
    n = sum(1 for p in broker.positions() if runner._slot_of_pos(p) == runner._slot_of_spec(runner._spec(source)))
    return runner._try_enter("NAS100", side, stop, None, rr, local, broker.account().equity, n, source=source)


def test_own_slot_trades_next_to_intraday_and_holds_through_break(cfg):
    base, broker, runner, t = setup(cfg)
    assert enter(runner, broker, t, DON) == "entered"
    assert enter(runner, broker, t, MAIN) == "entered"           # its own slot: the intraday model still trades
    assert len(broker.positions()) == 2
    acts = runner.step(t + pd.Timedelta(minutes=1))               # guardian: no "adding to a position"
    assert not any("adding" in a for a in acts)
    assert len(broker.positions()) == 2
    brk = pd.Timestamp("2025-01-06 16:55", tz="America/New_York").tz_convert("UTC")
    broker.set_time(brk)
    runner.step(brk)
    left = broker.positions()
    assert len(left) == 1                                         # the intraday trade closed at the break
    assert runner.tracked[left[0].id]["strategy"] == DON          # donchian held


def test_netting_account_never_opens_a_second_position(cfg):
    base, broker, runner, t = setup(cfg, hedging=False)
    assert enter(runner, broker, t, DON) == "entered"
    assert enter(runner, broker, t, MAIN).startswith("skipped:a position on this symbol")
    assert len(broker.positions()) == 1


def test_risk_pct_override_sizes_smaller(cfg):
    base, broker, runner, t = setup(cfg)
    inst = cfg.instrument("NAS100")
    full = runner.rm.size(1000.0, 900.0, inst, 50000.0)                     # global 0.5%
    half = runner.rm.size(1000.0, 900.0, inst, 50000.0, risk_pct=0.25)
    assert half > 0 and abs(half - full / 2) <= inst.lot_step + 1e-9
    assert enter(runner, broker, t, DON) == "entered"
    assert runner.tracked[broker.positions()[0].id]["risk_usd"] <= 50000 * 0.0025 + 1e-6


def test_ignore_regime(cfg):
    base, broker, runner, t = setup(cfg)
    runner._quiet_market = lambda sym, local: "quiet market (test)"
    assert enter(runner, broker, t, MAIN) == "skipped:quiet market (test)"
    assert enter(runner, broker, t, DON) == "entered"


def test_no_target_short_keeps_tp_above_zero(cfg):
    base, broker, runner, t = setup(cfg)
    assert enter(runner, broker, t, DON, side=-1, rr=1000.0) == "entered"
    p = broker.positions()[0]
    assert 0 < p.target < p.entry


def test_defaults_unchanged(cfg):
    spec = StrategySpec(name="x", instruments=["NAS100"])
    assert not spec.hold_overnight and spec.risk_pct is None and not spec.ignore_regime and not spec.own_slot
    assert cfg.risk.hedging_account is False
