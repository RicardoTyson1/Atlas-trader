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


# ---- regression tests for the 2026-11-04 review ---------------------------------

def test_close_and_reopen_between_polls_still_triggers_cooldown(cfg):
    """Review #2: closing a loser and opening a new trade on the same symbol between
    two polls must still record the loss, start the cooldown and block the new trade."""
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    first = broker.add_manual("NAS100", 1, 1.0, stop=1.0)
    runner.step(t)
    assert first in runner.tracked
    broker._close("NAS100", broker._pos["NAS100"].entry - 100, t)   # -$100 loss
    second = broker.add_manual("NAS100", 1, 1.0, stop=1.0)
    acts = runner.step(t + pd.Timedelta(minutes=1))
    assert runner.rm.s.cooldown_until is not None
    assert runner.rm.s.consec_losses == 1
    assert any("cooldown" in a for a in acts)
    assert second not in [p.id for p in broker.positions()]


def test_bot_position_not_yet_tracked_is_reattached_not_closed(cfg):
    """Review #4: a bot-tagged position the runner lost track of must be adopted, not killed."""
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    late = (base.index[0] + pd.Timedelta(hours=5, minutes=40)).tz_convert("UTC")   # 15:10: after last_entry, before flat_by
    broker.set_time(late)
    pid = broker.market_bracket("NAS100", 1, 1.0, stop=1.0, target=10**9)  # tagged "atlas"
    acts = runner.step(late)
    assert not any(a.startswith("guardian") for a in acts)
    assert pid in runner.tracked and runner.tracked[pid]["bot"]


def test_personal_daily_limit_is_a_day_stop_not_permanent(cfg):
    """Review #5: with daily_breach_permanent false, -2% = done for today only."""
    from atlas_trader.risk import RiskManager
    cfg.prop.daily_breach_permanent = False
    cfg.prop.safety_buffer = 1.0
    rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, 50000)
    rm.new_day("2025-01-06", 50000)
    rm.update(47000, 47000)              # below the -4% daily floor
    assert rm.s.day_halt and not rm.s.breached and not rm.s.hard_halt
    rm.new_day("2025-01-07", 47000)
    assert rm.halted is None


def test_close_failure_raises_loud_alert(cfg):
    """A close that never happens must produce a CRITICAL alert, not silence."""
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    runner.close_timeout_seconds = 0.05
    runner.close_poll_seconds = 0.01
    broker.close_position = lambda pid: None      # broker ignores closes
    broker.close_all = lambda symbol=None: None
    broker.add_manual("NAS100", 1, 1.0, stop=None)
    runner.step(t)
    assert any("COULD NOT CLOSE" in m for m in runner.notify.messages)


def test_daily_armed_and_end_of_day_reports(cfg):
    cfg.strategies = []
    base = synthetic_intraday(days=1, seed=5)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    for ts in (base.index[30], base.index[-1] + pd.Timedelta(minutes=1)):
        broker.set_time(ts)
        runner.step(ts)
    msgs = " ".join(runner.notify.messages)
    assert "Good morning" in msgs and "Day report" in msgs


def test_entry_uses_live_quote_not_bar_close(cfg):
    base, broker, runner = make(cfg, days=2)
    seen = {}
    real_quote = broker.quote
    def q(sym):
        seen["called"] = True
        return real_quote(sym)
    broker.quote = q
    drive(base, broker, runner)
    assert seen.get("called")


def test_same_strategy_twice_with_labels(cfg):
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec("orb", ["NAS100"], "5m"), StrategySpec("orb", ["NAS100"], "5m", label="orb_b")]
    assert [s.id for s in cfg.strategies] == ["orb", "orb_b"]


def test_off_hours_trailing_uses_1m_candles(cfg):
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec("orb", ["NAS100"], "5m")]
    cfg.risk.core_hours = "08:00-11:00"
    base, broker, runner = make(cfg, days=2)
    calls = []
    real = broker.bars
    broker.bars = lambda sym, tf, n: (calls.append(tf), real(sym, tf, n))[1]
    t = base.index[0] + pd.Timedelta(hours=3)            # 12:30 NY: outside core hours
    broker.set_time(t)
    runner.clock = lambda: broker.now
    _, ask = broker.quote("NAS100")
    runner.submit_external("NAS100", 1, ask - 20, source="orb")
    spec, strat = runner.strats[0]
    runner._manage_exit(spec, strat, "NAS100", t)
    assert "1m" in calls
