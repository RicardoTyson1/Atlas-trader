from datetime import datetime

import pytest

from atlas_trader.risk import RiskManager


def rm_for(cfg, **prop):
    for k, v in prop.items():
        setattr(cfg.prop, k, v)
    r = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, 50000)
    r.new_day("2025-01-06", 50000)
    return r


def ny(h, m, d=6):
    return datetime(2025, 1, d, h, m, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))


def test_floors(cfg):
    r = rm_for(cfg)
    assert r.daily_floor(real=True) == pytest.approx(48000)      # 4% of 50k
    assert r.daily_floor() == pytest.approx(50000 - 2000 * 0.7)  # buffer
    assert r.dd_floor(real=True) == pytest.approx(46000)
    assert r.dd_floor() == pytest.approx(46000 + 4000 * 0.3)


def test_trailing_floor_moves_up_and_locks(cfg):
    r = rm_for(cfg)
    r.update(52000, 52000)
    assert r.dd_floor(real=True) == pytest.approx(48000)
    r.update(60000, 60000)
    assert r.dd_floor(real=True) == pytest.approx(50000)  # locked at initial
    r.update(55000, 55000)
    assert r.dd_floor(real=True) == pytest.approx(50000)  # never moves down


def test_daily_buffer_halts_day_and_resets_next_day(cfg):
    r = rm_for(cfg)
    ev = r.update(48500, 50000)
    assert r.s.day_halt and ev
    assert not r.check_entry(ny(10, 0))[0]
    r.new_day("2025-01-07", 48500)
    assert r.s.day_halt is None
    assert r.check_entry(ny(10, 0, d=7))[0]


def test_real_breach_is_permanent(cfg):
    r = rm_for(cfg)
    r.update(47900, 47900)
    assert r.s.breached
    r.new_day("2025-01-07", 47900)
    assert not r.check_entry(ny(10, 0, d=7))[0]
    with pytest.raises(RuntimeError):
        r.reset_hard_halt()


def test_entry_window_and_limits(cfg):
    r = rm_for(cfg)
    assert not r.check_entry(ny(9, 35))[0]
    assert not r.check_entry(ny(15, 30))[0]
    assert r.check_entry(ny(10, 0))[0]
    for _ in range(3):
        r.record_entry()
    assert r.check_entry(ny(10, 0))[1] == "max trades per day reached"


def test_consecutive_losses_stop(cfg):
    r = rm_for(cfg)
    for _ in range(3):
        r.record_close(-100)
    assert r.check_entry(ny(10, 0))[1] == "max consecutive losses reached"


def test_size_respects_risk_and_room(cfg):
    r = rm_for(cfg)
    inst = cfg.instrument("NAS100")
    lots = r.size(20000, 19950, inst, 50000)  # 50pt stop + 1.5 costs
    assert lots * r.risk_per_lot(20000, 19950, inst) <= 250 + 1e-6
    assert lots == pytest.approx(4.8)
    # near the daily floor the budget shrinks
    r.update(48700, 48700)
    small = r.size(20000, 19950, inst, 48700)
    assert small * r.risk_per_lot(20000, 19950, inst) <= (48700 - r.daily_floor()) * 0.5 + 1e-6
    # stop too wide for min lot -> no trade
    assert r.size(20000, 10000, inst, 50000) == 0.0


def test_news_blackout(cfg):
    cfg.news.events = [ny(10, 0)]
    r = rm_for(cfg)
    assert r.check_entry(ny(10, 10))[1] == "news blackout"
    assert r.must_flatten(ny(9, 50)) == "news blackout"


def test_cooldown_after_loss(cfg):
    r = rm_for(cfg)
    r.record_close(-50, ny(10, 0))
    assert "cooldown" in r.check_entry(ny(10, 10))[1]
    assert r.check_entry(ny(10, 16))[0]
    r.record_close(+50, ny(10, 20))
    assert r.check_entry(ny(10, 21))[0]


def test_sizing_cap_ignores_bonus_money(cfg):
    cfg.risk.max_sizing_equity = 10000
    r = rm_for(cfg)
    inst = cfg.instrument("NAS100")
    capped = r.size(20000, 19950, inst, 50000)
    # 0.5% of the $10k cap = $50 max loss, even though equity shows $50k
    assert capped * r.risk_per_lot(20000, 19950, inst) <= 50 + 1e-9
    cfg.risk.max_sizing_equity = None
    assert r.size(20000, 19950, inst, 50000) > capped


def test_sizing_cap_does_not_block_trading_when_account_grows(cfg):
    """Regression: capping equity must not make the bot think it is past its daily floor."""
    cfg.risk.max_sizing_equity = 10000
    r = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, 10000)
    r.new_day("2025-01-06", 10250)  # account grew above the cap
    inst = cfg.instrument("NAS100")
    assert r.size(20000, 19990, inst, 10250) > 0


def test_eod_trailing_like_topstep(cfg):
    cfg.prop.trailing_on = "eod_balance"
    cfg.prop.max_drawdown_pct = 4.0          # $2,000 on $50k
    cfg.prop.trail_locks_at_initial = True
    r = rm_for(cfg)                           # day 1 starts at 50,000
    r.update(51500, 51000)                    # intraday highs do NOT move the floor
    assert r.dd_floor(real=True) == pytest.approx(48000)
    r.new_day("2025-01-07", 51000)            # closed day 1 at 51,000
    assert r.dd_floor(real=True) == pytest.approx(49000)
    r.new_day("2025-01-08", 53000)
    assert r.dd_floor(real=True) == pytest.approx(50000)  # locked at the starting balance


def test_off_hours_risk_is_smaller():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from atlas_trader.config import Instrument, NewsBlackouts, PropRules, RiskSettings, Session
    from atlas_trader.risk import RiskManager
    ny = ZoneInfo("America/New_York")
    rs = RiskSettings(risk_per_trade_pct=5.0, core_hours="08:00-11:00", off_hours_risk_pct=1.0,
                      max_lots=100, max_room_fraction_per_trade=1.0)
    rm = RiskManager(PropRules(daily_loss_pct=50, max_drawdown_pct=50), rs, Session(), NewsBlackouts(), 10_000)
    inst = Instrument(symbol="XAUUSD.R", value_per_point=100.0, min_lot=0.01, lot_step=0.01)
    core = rm.size(4100.0, 4090.0, inst, 10_000, when=datetime(2026, 11, 6, 9, 0, tzinfo=ny))
    off = rm.size(4100.0, 4090.0, inst, 10_000, when=datetime(2026, 11, 6, 20, 0, tzinfo=ny))
    assert rm.risk_pct_at(datetime(2026, 11, 6, 11, 0, tzinfo=ny)) == 1.0     # 11:00 is outside
    assert core == 0.5 and off == 0.1                                          # $500 vs $100 at 10-pt risk
