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
