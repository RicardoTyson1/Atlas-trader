"""Economic calendar -> news blackouts.

The bot stands aside around scheduled high-impact news (CPI, jobs report, Fed decisions...):
no new trades from `minutes_before` until `minutes_after` the release, and open trades are
closed (gold can jump straight through a stop on these releases).

The calendar comes from the free Forex Factory weekly feed. `refresh()` downloads it and keeps
the chosen countries / impact levels in a small JSON file the risk engine reads
(`news_blackouts.feed_file`). The runner refreshes it at start and every few hours; if the
download fails, the last saved file stays in use and you get one alert.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger("atlas")

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse_events(raw: list[dict], countries=("USD",), impacts=("High",)) -> list[dict]:
    out = []
    for e in raw:
        if e.get("country") not in countries or e.get("impact") not in impacts:
            continue
        try:
            ts = datetime.fromisoformat(str(e["date"]))
        except (KeyError, ValueError):
            continue
        if ts.tzinfo is None:
            continue
        out.append({"time": ts.isoformat(), "title": str(e.get("title", ""))[:80],
                    "country": e.get("country"), "impact": e.get("impact")})
    return sorted(out, key=lambda x: x["time"])


def refresh(path: str | Path, url: str = DEFAULT_URL, countries=("USD",), impacts=("High",),
            timeout: float = 20.0) -> list[dict]:
    """Download the calendar and save the matching events. Keeps earlier events this week."""
    req = urllib.request.Request(url, headers={"User-Agent": "atlas-trader/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read().decode("utf-8"))
    events = parse_events(raw, tuple(countries), tuple(impacts))
    path = Path(path)
    old = load(path)
    merged = {e["time"] + e["title"]: e for e in old + events}
    keep = sorted(merged.values(), key=lambda x: x["time"])[-200:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, indent=1))
    tmp.replace(path)
    log.info("news calendar: %d events saved to %s", len(keep), path)
    return keep


def load(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return list(json.loads(p.read_text()))
    except (ValueError, OSError):
        return []


def event_times(path: str | Path) -> list[datetime]:
    return [datetime.fromisoformat(e["time"]) for e in load(path)]
