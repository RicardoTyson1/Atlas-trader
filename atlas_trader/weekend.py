"""Bitcoin weekend window (P4 study). Times are New York local.

The window opens Friday 17:00 (after gold closes) and closes Sunday 18:00 (gold reopens).
Every open trade is closed at Sunday 18:00. All functions look only at the time they are given,
so they never use future data.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

TZ = "America/New_York"
OPEN_DAY, OPEN_TIME = 4, time(17, 0)       # Friday 17:00
CLOSE_DAY, CLOSE_TIME = 6, time(18, 0)     # Sunday 18:00


def _local(ts) -> datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return t.tz_convert(TZ).to_pydatetime()


def in_weekend_window(ts) -> bool:
    """True from Friday 17:00 (inclusive) to Sunday 18:00 (exclusive), New York time."""
    t = _local(ts)
    d, tod = t.weekday(), t.time()
    if d == OPEN_DAY:
        return tod >= OPEN_TIME
    if d == 5:
        return True
    if d == CLOSE_DAY:
        return tod < CLOSE_TIME
    return False


def weekend_close(ts) -> pd.Timestamp:
    """The Sunday 18:00 New York time that ends the weekend window containing `ts` (UTC)."""
    t = _local(ts)
    if not in_weekend_window(t):
        raise ValueError(f"{t} is not inside a weekend window")
    days = (CLOSE_DAY - t.weekday()) % 7
    sunday = (t + timedelta(days=days)).date()
    return pd.Timestamp(datetime.combine(sunday, CLOSE_TIME)).tz_localize(TZ).tz_convert("UTC")


def weekend_id(ts) -> str:
    """The Friday date (YYYY-MM-DD) that opens the weekend window containing `ts`."""
    t = _local(ts)
    if not in_weekend_window(t):
        raise ValueError(f"{t} is not inside a weekend window")
    back = (t.weekday() - OPEN_DAY) % 7
    return (t - timedelta(days=back)).date().isoformat()
