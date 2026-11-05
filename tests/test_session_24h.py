"""Around-the-clock session: entries any time except the daily break; flat during the break."""
from datetime import datetime, time

from atlas_trader.risk import RiskManager


def test_24h_session_windows(cfg):
    s = cfg.session
    s.trade_start, s.last_entry, s.flat_by, s.reopen = time(0, 0), time(16, 30), time(16, 50), time(18, 5)
    rm = RiskManager(cfg.prop, cfg.risk, s, cfg.news, 50000)
    rm.new_day("2026-11-04", 50000)
    ok = lambda h, m: rm.check_entry(datetime(2026, 11, 4, h, m))[0]   # a Wednesday
    assert ok(3, 0) and ok(9, 35) and ok(16, 30) and ok(18, 5) and ok(21, 30) and ok(23, 59)
    assert not ok(16, 40) and not ok(17, 0) and not ok(18, 0)
    fl = lambda h, m: rm.must_flatten(datetime(2026, 11, 4, h, m))
    assert fl(16, 50) == "end of session" and fl(17, 30) == "end of session"
    assert fl(18, 5) is None and fl(21, 0) is None and fl(2, 0) is None
