import pandas as pd

from atlas_trader.brokers.sim import SimBroker
from atlas_trader.data import synthetic_intraday
from atlas_trader.notify import MemoryNotifier
from atlas_trader.runner import Runner
from atlas_trader.webhook import handle_alert


def make(cfg, days=5):
    base = synthetic_intraday(days=days, seed=11)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    return base, broker, runner


def drive(base, broker, runner, step="5min"):
    t = base.index[0] + pd.Timedelta(step)
    while t <= base.index[-1] + pd.Timedelta(step) and not runner.stopped:
        broker.set_time(t)
        runner.step(t)
        t += pd.Timedelta(step)


def test_runner_trades_and_is_flat_overnight(cfg):
    base, broker, runner = make(cfg)
    drive(base, broker, runner)
    opens = [f for f in broker.fills if f["action"] == "open"]
    assert opens, "expected at least one trade"
    assert not broker.positions()
    for f in opens:
        loc = f["time"].tz_convert("America/New_York")
        assert "09:45" <= loc.strftime("%H:%M") <= "15:00"


def test_kill_switch(cfg):
    base, broker, runner = make(cfg, days=1)
    runner.kill_file.parent.mkdir(parents=True, exist_ok=True)
    runner.kill_file.write_text("stop")
    broker.set_time(base.index[10])
    assert runner.step(base.index[10]) == ["killed"]
    assert runner.stopped


def test_state_persists(cfg):
    base, broker, runner = make(cfg, days=2)
    drive(base, broker, runner)
    r2 = Runner(cfg, broker, MemoryNotifier())
    assert r2.rm.s.day == runner.rm.s.day
    assert r2.rm.s.trading_days == runner.rm.s.trading_days


def test_webhook_auth_and_validation(cfg):
    base, broker, runner = make(cfg, days=1)
    broker.set_time(base.index[60])
    assert handle_alert(runner, {"secret": "x"}, "")[0] == 500
    assert handle_alert(runner, {"secret": "bad"}, "s3cret")[0] == 401
    assert handle_alert(runner, {"secret": "s3cret", "symbol": "XYZ", "action": "buy"}, "s3cret")[0] == 400
    assert handle_alert(runner, {"secret": "s3cret", "symbol": "NAS100", "action": "buy"}, "s3cret")[0] == 400


def _guard_setup(cfg):
    base = synthetic_intraday(days=2, seed=5)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    # 10:30 New York on day 1: inside the entry window
    t = base.index[0] + pd.Timedelta(minutes=60)
    broker.set_time(t)
    return base, broker, runner, t


def test_guardian_closes_position_without_stop(cfg):
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    broker.add_manual("NAS100", 1, 1.0, stop=None)
    acts = runner.step(t)
    assert any("no stop-loss" in a for a in acts)
    assert not broker.positions()


def test_guardian_blocks_adding_to_position(cfg):
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    first = broker.add_manual("NAS100", 1, 1.0, stop=1.0)
    runner.step(t)  # first manual trade accepted
    assert [p.id for p in broker.positions()] == [first]
    # SimBroker keeps one position per symbol, so simulate the add on another key
    broker.data["NAS100_2"] = broker.data["NAS100"]
    p2 = broker.add_manual("NAS100_2", 1, 1.0, stop=1.0)
    broker._pos["NAS100_2"].symbol = "NAS100"
    acts = runner.step(t + pd.Timedelta(minutes=5))
    assert any("adding" in a for a in acts)
    assert p2 not in [p.id for p in broker.positions()]


def test_guardian_blocks_manual_trade_during_cooldown(cfg):
    from datetime import timedelta
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    loc = t.tz_convert("America/New_York").to_pydatetime()
    runner.rm.record_close(-100.0, loc)  # a loss just now -> 15 min cooldown
    broker.add_manual("NAS100", 1, 1.0, stop=1.0)
    acts = runner.step(t)
    assert any("cooldown" in a for a in acts)
    assert not broker.positions()
