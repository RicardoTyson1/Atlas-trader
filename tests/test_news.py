"""News calendar -> blackouts."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from atlas_trader.config import NewsBlackouts
from atlas_trader.news import load, parse_events

NY = ZoneInfo("America/New_York")
RAW = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
    {"title": "Retail Sales", "country": "USD", "date": "2026-11-06T10:00:00-05:00", "impact": "Medium"},
    {"title": "ECB Speech", "country": "EUR", "date": "2026-11-06T09:00:00-05:00", "impact": "High"},
    {"title": "broken", "country": "USD", "date": "soon", "impact": "High"},
]


def test_parse_keeps_high_usd_only():
    ev = parse_events(RAW)
    assert [e["title"] for e in ev] == ["Non-Farm Employment Change"]


def test_blackout_from_feed_file(tmp_path):
    f = tmp_path / "news.json"
    f.write_text(json.dumps(parse_events(RAW)))
    nb = NewsBlackouts(15, 15, [], feed_file=str(f))
    assert nb.active(datetime(2026, 11, 6, 8, 20, tzinfo=NY))
    assert nb.active(datetime(2026, 11, 6, 8, 45, tzinfo=NY))
    assert not nb.active(datetime(2026, 11, 6, 8, 10, tzinfo=NY))
    assert not nb.active(datetime(2026, 11, 6, 9, 0, tzinfo=NY))


def test_missing_or_bad_file_means_no_feed_events(tmp_path):
    nb = NewsBlackouts(15, 15, [], feed_file=str(tmp_path / "none.json"))
    assert not nb.active(datetime(2026, 11, 6, 8, 30, tzinfo=NY))
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert load(bad) == []
