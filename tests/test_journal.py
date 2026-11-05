"""Trade journal and weekly keep/drop review."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from atlas_trader.brokers.sim import SimBroker
from atlas_trader.data import synthetic_intraday
from atlas_trader.journal import MIN_TRADES, append, load, session_of, weekly_report
from atlas_trader.notify import MemoryNotifier
from atlas_trader.runner import Runner

NY = ZoneInfo("America/New_York")


def test_sessions():
    assert session_of(datetime(2026, 11, 5, 9, 0, tzinfo=NY)) == "NY morning 08-11"
    assert session_of(datetime(2026, 11, 5, 20, 0, tzinfo=NY)) == "Asia 18-02"
    assert session_of(datetime(2026, 11, 5, 1, 0, tzinfo=NY)) == "Asia 18-02"
    assert session_of(datetime(2026, 11, 5, 3, 0, tzinfo=NY)) == "London 02-08"


def test_weekly_report_verdicts(tmp_path):
    f = tmp_path / "journal.csv"
    for i in range(MIN_TRADES):
        append(f, {"close_time": "2026-11-05T10:00:00-05:00", "strategy": "momentum_rsi", "symbol": "XAUUSD.R",
                   "side": 1, "entry_time": "2026-11-05T09:00:00-05:00", "pnl": 100 if i % 2 else -40,
                   "risk_usd": 50, "r": 2.0 if i % 2 else -0.8})
        append(f, {"close_time": "2026-11-05T21:00:00-05:00", "strategy": "reversal_trail", "symbol": "XAUUSD.R",
                   "side": -1, "entry_time": "2026-11-05T20:00:00-05:00", "pnl": -10, "risk_usd": 10, "r": -1.0})
    assert len(load(f)) == 2 * MIN_TRADES
    txt = weekly_report(f, datetime(2026, 11, 6, 17, 10, tzinfo=NY))
    assert "momentum_rsi | NY morning 08-11" in txt and "KEEP" in txt
    assert "reversal_trail | Asia 18-02" in txt and "DROP?" in txt


def test_runner_journals_closed_trades_and_sends_weekly_review(cfg):
    cfg.strategies = []
    base = synthetic_intraday(days=3, seed=11)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    runner.clock = lambda: broker.now
    t = base.index[0] + pd.Timedelta(minutes=60)
    broker.set_time(t)
    _, ask = broker.quote("NAS100")
    assert runner.submit_external("NAS100", 1, ask - 20) == "entered"
    pid = next(iter(runner.tracked))
    broker.close_position(pid)
    runner.step(t + pd.Timedelta(minutes=1))
    rows = load(runner.state_dir / "journal.csv")
    assert len(rows) == 1 and rows[0]["strategy"] == "tradingview" and rows[0]["r"] != ""
    fri = pd.Timestamp("2026-11-06 17:10", tz="America/New_York")
    runner._weekly_review(fri)
    runner._weekly_review(fri)                     # only once per Friday
    sent = [m for m in runner.notify.messages if "WEEKLY REVIEW" in m]
    assert len(sent) == 1
