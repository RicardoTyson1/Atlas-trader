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
