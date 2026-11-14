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
    base, broker, runner = make(cfg, days=5)
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
    cfg.strategies = [StrategySpec("momentum_rsi", ["NAS100"], "5m"), StrategySpec("momentum_rsi", ["NAS100"], "1m", label="momentum_1m")]
    assert [s.id for s in cfg.strategies] == ["momentum_rsi", "momentum_1m"]


def test_off_hours_trailing_uses_1m_candles(cfg):
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec("momentum_rsi", ["NAS100"], "5m")]
    cfg.risk.core_hours = "08:00-11:00"
    base, broker, runner = make(cfg, days=2)
    calls = []
    real = broker.bars
    broker.bars = lambda sym, tf, n: (calls.append(tf), real(sym, tf, n))[1]
    t = base.index[0] + pd.Timedelta(hours=3)            # 12:30 NY: outside core hours
    broker.set_time(t)
    runner.clock = lambda: broker.now
    _, ask = broker.quote("NAS100")
    runner.submit_external("NAS100", 1, ask - 20, source="momentum_rsi")
    spec, strat = runner.strats[0]
    runner._manage_exit(spec, strat, "NAS100", t)
    assert "1m" in calls


def _auto_stop_runner(cfg):
    cfg.strategies = []
    cfg.risk.guardian_auto_stop = True
    cfg.risk.guardian_stop_grace_seconds = 30
    base, broker, runner, t = _guard_setup(cfg)
    return base, broker, runner, t


def test_guardian_gives_time_then_adds_stop_at_max_loss(cfg):
    base, broker, runner, t = _auto_stop_runner(cfg)
    pid = broker.add_manual("NAS100", 1, 1.0, stop=None)
    runner.step(t + pd.Timedelta(seconds=10))           # inside the 30 s grace: nothing happens
    pos = broker.positions()[0]
    assert not pos.has_stop and pos.id == pid
    acts = runner.step(t + pd.Timedelta(seconds=40))    # grace over: stop attached, trade kept
    pos = broker.positions()[0]
    assert "guardian:stop added" in acts and pos.has_stop and pos.id == pid
    max_loss = runner._max_loss_usd(cfg.starting_balance, t)
    assert abs((pos.entry - pos.stop) * pos.qty * 1.0 - max_loss) < 0.02
    assert any("GUARDIAN added a stop" in m for m in runner.notify.messages)


def test_guardian_closes_if_already_past_max_loss(cfg):
    cfg.risk.max_lots = 100.0                             # so only the max-loss rule can close it
    base, broker, runner, t = _auto_stop_runner(cfg)
    broker.add_manual("NAS100", 1, 50.0, stop=None)       # big size: max loss is only a few points away
    broker.positions()[0].entry += 100                    # entered far above the current price
    runner.step(t + pd.Timedelta(seconds=40))
    assert not broker.positions()
    assert any("past the max loss" in m for m in runner.notify.messages)


def test_rejects_trade_whose_target_pays_less_than_min_r(cfg):
    """2026-11-05 20:36 NY: a TradingView alert filled at a price where the target paid
    $2.78 against an $18 stop (0.15R). Such trades must be skipped."""
    cfg.strategies = []
    cfg.risk.min_target_r = 1.0
    base, broker, runner, t = _guard_setup(cfg)
    bid, ask = broker.quote("NAS100")
    res = runner._try_enter("NAS100", 1, ask - 18.0, ask + 2.78, None,
                            t.tz_convert("America/New_York"), broker.account().equity, 0, "test")
    assert res.startswith("skipped:target pays only")
    assert not broker.positions()
    ok = runner._try_enter("NAS100", 1, ask - 10.0, ask + 20.0, None,
                           t.tz_convert("America/New_York"), broker.account().equity, 0, "test")
    assert ok == "entered"


def test_spread_guard_skips_entries_when_spread_blows_out(cfg):
    cfg.strategies = []
    cfg.risk.max_spread_mult = 3.0                  # conftest NAS100 spread_points = 1.0
    base, broker, runner, t = _guard_setup(cfg)
    mid = broker.last_price("NAS100")
    local, eq = t.tz_convert("America/New_York"), broker.account().equity
    broker.quote = lambda s: (mid - 2.0, mid + 2.0)  # 4.0 wide = 4x normal
    res = runner._try_enter("NAS100", 1, mid - 20, mid + 40, None, local, eq, 0, "test")
    assert res.startswith("skipped:spread") and not broker.positions()
    broker.quote = lambda s: (mid - 1.0, mid + 1.0)  # 2.0 wide = 2x normal: allowed
    assert runner._try_enter("NAS100", 1, mid - 20, mid + 40, None, local, eq, 0, "test") == "entered"


def test_spread_guard_off_by_default(cfg):
    cfg.strategies = []
    base, broker, runner, t = _guard_setup(cfg)
    mid = broker.last_price("NAS100")
    broker.quote = lambda s: (mid - 50.0, mid + 50.0)
    res = runner._try_enter("NAS100", 1, mid - 200, mid + 400, None, t.tz_convert("America/New_York"),
                            broker.account().equity, 0, "test")
    assert not res.startswith("skipped:spread")


# ---- 50k guardian corrections (2026-11-07) ----------------------------------------------
def _guard50(cfg, allow_add=True, max_lots=0.5):
    cfg.strategies = []
    cfg.risk.guardian = True
    cfg.risk.guardian_auto_stop = False
    cfg.risk.guardian_allow_adding = allow_add
    cfg.risk.max_lots = max_lots
    cfg.risk.max_open_positions = 1
    return _guard_setup(cfg)


class _Pos:
    def __init__(self, pid, symbol, qty, opened, has_stop=True):
        self.id, self.symbol, self.qty, self.opened, self.has_stop = pid, symbol, qty, opened, has_stop
        self.tag, self.side, self.entry, self.target = None, 1, 100.0, None


def test_add_on_within_cap_is_allowed(cfg):
    base, broker, runner, t = _guard50(cfg)
    local = t.tz_convert("America/New_York")
    closed = []
    runner._close_verified = lambda ids, symbol=None: closed.extend(ids) or True
    a, b = _Pos("a", "NAS100", 0.2, t), _Pos("b", "NAS100", 0.2, t + pd.Timedelta(seconds=5))
    runner._guard([a, b], broker.account().balance, local)
    assert closed == [] and "a" in runner.tracked and "b" in runner.tracked
    assert runner.rm.s.trades_today == 1                      # the add-on is not a new trade
    c = _Pos("c", "NAS100", 0.2, t + pd.Timedelta(seconds=9))  # 0.6 total > 0.5 cap
    runner._guard([a, b, c], broker.account().balance, local)
    assert closed == ["c"]


def test_add_on_blocked_when_not_allowed(cfg):
    base, broker, runner, t = _guard50(cfg, allow_add=False)
    closed = []
    runner._close_verified = lambda ids, symbol=None: closed.extend(ids) or True
    a, b = _Pos("a", "NAS100", 0.1, t), _Pos("b", "NAS100", 0.1, t + pd.Timedelta(seconds=5))
    runner._guard([a, b], broker.account().balance, t.tz_convert("America/New_York"))
    assert closed == ["b"]


def test_always_open_symbol_trades_on_weekends_and_daily_break(cfg):
    from atlas_trader.config import Instrument
    base, broker, runner, t = _guard50(cfg)
    cfg.instruments["BTCUSD"] = Instrument("BTCUSD", value_per_point=1.0, always_open=True)
    sat = pd.Timestamp("2026-11-07 11:00", tz="America/New_York")
    brk = pd.Timestamp("2026-11-09 17:15", tz="America/New_York")
    for when in (sat, brk):
        ok, why = runner.rm.check_entry(when.to_pydatetime(), 0, always_open=True)
        assert ok, why
        assert not runner.rm.check_entry(when.to_pydatetime(), 0)[0]   # gold stays blocked
    closed = []
    runner._close_verified = lambda ids, symbol=None: closed.extend(ids) or True
    runner._guard([_Pos("btc", "BTCUSD", 0.1, sat.tz_convert("UTC"))], broker.account().balance, sat)
    assert closed == [] and "btc" in runner.tracked


def test_unknown_symbol_without_stop_says_why(cfg):
    base, broker, runner, t = _guard50(cfg)
    cfg.risk.guardian_auto_stop = True
    cfg.risk.guardian_stop_grace_seconds = 0
    closed = []
    runner._close_verified = lambda ids, symbol=None: closed.extend(ids) or True
    acts = runner._guard([_Pos("x", "ETHUSD", 0.1, t, has_stop=False)], broker.account().balance,
                         t.tz_convert("America/New_York"))
    assert closed == ["x"] and any("not set up in the config" in a for a in acts)


def test_topstep50k_config_values():
    from atlas_trader.config import load_config
    c = load_config("config/topstep50k.yaml")
    assert c.risk.max_lots == 0.5 and c.risk.cooldown_after_loss_minutes == 5
    assert c.risk.guardian_allow_adding is True
    assert c.prop.daily_loss_pct * c.prop.safety_buffer * c.starting_balance / 100 == 500
    btc = c.instrument("BTCUSD")
    assert btc.always_open and btc.value_per_point == 1.0
    assert not c.instrument("XAUUSD.R").always_open


def test_bars_fetched_once_per_step_for_shared_timeframe(cfg):
    """Many strategies on the same symbol/timeframe share one bar download per step (API limits)."""
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec("momentum_rsi", ["NAS100"], "5m", label=f"m{i}") for i in range(5)]
    base, broker, runner = make(cfg, days=2)
    calls = []
    real = broker.bars
    broker.bars = lambda sym, tf, n: (calls.append((sym, tf)), real(sym, tf, n))[1]
    t = base.index[0] + pd.Timedelta(hours=2)
    broker.set_time(t)
    runner.step(t)
    assert calls.count(("NAS100", "5m")) == 1
    t2 = t + pd.Timedelta("5min")
    broker.set_time(t2)
    runner.step(t2)                                   # next step downloads fresh bars again
    assert calls.count(("NAS100", "5m")) == 2


def test_config_order_is_priority_when_two_strategies_signal_together(cfg):
    """'Proven first' (Ricardo, 2026-11-09): the first strategy in the config that signals takes the slot."""
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec("momentum_rsi", ["NAS100"], "5m", label="proven"),
                      StrategySpec("momentum_rsi", ["NAS100"], "5m", label="test_only")]
    base, broker, runner = make(cfg)
    drive(base, broker, runner)
    entries = [m for m in runner.notify.messages if m.startswith(("BUY", "SELL"))]
    assert entries, "expected at least one entry"
    assert all("(proven)" in m or "proven)" in m for m in entries)
    assert not any("test_only" in m for m in entries)


def test_no_cooldown_when_turned_off_and_old_one_is_cleared(cfg):
    """cooldown_after_loss_minutes = 0 (Ricardo, 2026-11-10): a loss sets no cooldown, an old saved one is
    wiped, and the close message never shows a stale 'cooldown until'."""
    from datetime import datetime, timedelta
    cfg.risk.cooldown_after_loss_minutes = 0
    base, broker, runner = make(cfg, days=1)
    now = datetime(2026, 11, 10, 10, 15)   # inside the test session window
    runner.rm.s.cooldown_until = (now - timedelta(days=5)).isoformat()   # stale value from an old day
    runner.rm.record_close(-54.74, now)
    assert runner.rm.s.cooldown_until is None
    ok, why = runner.rm.check_entry(now + timedelta(minutes=1), 0)
    assert ok, why


def test_cooldown_still_works_when_set(cfg):
    from datetime import datetime, timedelta
    cfg.risk.cooldown_after_loss_minutes = 15
    base, broker, runner = make(cfg, days=1)
    now = datetime(2026, 11, 10, 10, 0)
    runner.rm.record_close(-10.0, now)
    ok, _ = runner.rm.check_entry(now + timedelta(minutes=5), 0)
    assert not ok


def test_order_error_that_fills_late_keeps_its_setup(cfg):
    """2026-11-09 22:45: the broker answered an order with an error, then the position showed up.
    The bot must not send a second order, and must track the late fill with its own stop/strategy."""
    base, broker, runner = make(cfg)
    real = broker.market_bracket
    calls = []

    def flaky(*a, **k):
        calls.append(a)
        if len(calls) == 1:
            real(*a, **k)                       # the order goes through...
            raise RuntimeError("HTTP 500")      # ...but the broker reports an error
        return real(*a, **k)

    broker.market_bracket = flaky
    t = base.index[0] + pd.Timedelta("5min")
    while not calls and t <= base.index[-1]:
        broker.set_time(t)
        runner.step(t)
        t += pd.Timedelta("5min")
    assert calls, "expected an entry attempt"
    assert "NAS100" in runner.pending
    assert any("broker error" in m for m in runner.notify.messages)
    broker.set_time(t)
    runner.step(t)                              # next poll sees the position
    assert len(calls) == 1                      # no duplicate order
    assert not runner.pending
    (rec,) = runner.tracked.values()
    assert rec["strategy"] == "momentum_rsi" and rec["init_stop"] is not None
    assert any("confirmed late" in m for m in runner.notify.messages)


def test_order_error_that_never_fills_frees_the_symbol(cfg):
    base, broker, runner = make(cfg)
    runner.pending["NAS100"] = {"symbol": "NAS100", "side": 1, "strategy": "x",
                                "sent": str(pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=11))}
    assert runner._pending_for("NAS100") is None and not runner.pending


def _partial_runner(cfg, rule):
    from atlas_trader.config import StrategySpec
    cfg.strategies = [StrategySpec(name="momentum_rsi", instruments=["NAS100"], partial=rule)]
    base, broker, runner, t = _guard_setup(cfg)
    px = broker._mark("NAS100")
    pid = broker.market_bracket("NAS100", 1, 1.0, stop=px - 10, target=px + 100)
    runner.tracked[pid] = {"symbol": "NAS100", "bot": True, "bal_before": broker.balance, "entry_time": str(t),
                           "max_bars": None, "strategy": "momentum_rsi", "side": 1, "init_stop": px - 10,
                           "stop": px - 10, "target": px + 100, "trail_start_r": None, "trail_buf": None,
                           "spike_pts": None, "spike_min_r": None, "spike_mode": None, "risk_usd": 10.0,
                           "partial": runner._partial_rule("momentum_rsi")}
    return broker, runner, pid, px


def test_partial_exit_banks_half_and_moves_stop_to_breakeven(cfg):
    broker, runner, pid, px = _partial_runner(cfg, {"at_r": 0.5, "fraction": 0.5, "breakeven": True})
    pos = broker.positions()[0]
    broker.quote = lambda s: (px + 6, px + 6.5)          # +0.6R for a long
    assert runner._partial_exit(pid, runner.tracked[pid], pos, "NAS100") == "partial"
    assert broker.positions()[0].qty == 0.5
    assert broker.positions()[0].stop == pos.entry and runner.tracked[pid]["stop"] == pos.entry
    assert any("Banked 0.5 of 1 lots" in m and "breakeven" in m for m in runner.notify.messages), runner.notify.messages
    # never twice
    assert runner._partial_exit(pid, runner.tracked[pid], broker.positions()[0], "NAS100") is None


def test_partial_exit_waits_for_the_level(cfg):
    broker, runner, pid, px = _partial_runner(cfg, {"at_r": 0.5, "fraction": 0.5, "breakeven": True})
    broker.quote = lambda s: (px + 4, px + 4.5)          # +0.4R: not yet
    assert runner._partial_exit(pid, runner.tracked[pid], broker.positions()[0], "NAS100") is None
    assert broker.positions()[0].qty == 1.0


def test_no_partial_rule_means_no_partial(cfg):
    broker, runner, pid, px = _partial_runner(cfg, None)
    broker.quote = lambda s: (px + 9, px + 9.5)
    assert runner.tracked[pid]["partial"] is None
    assert runner._partial_exit(pid, runner.tracked[pid], broker.positions()[0], "NAS100") is None


def test_live_config_partial_only_on_reversal():
    from atlas_trader.config import load_config
    c = load_config("config/default.yaml")
    rules = {s.id: s.partial for s in c.strategies}
    assert rules["reversal_trail_1m"] == {"at_r": 0.5, "fraction": 0.5, "breakeven": True}
    assert all(v is None for k, v in rules.items() if k != "reversal_trail_1m")
