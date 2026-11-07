"""Bitcoin weekend window: Fri 17:00 -> Sun 18:00 New York, DST-safe, no look-ahead."""
import pandas as pd
import pytest

from atlas_trader.weekend import in_weekend_window, weekend_close, weekend_id

NY = "America/New_York"


@pytest.mark.parametrize("local, inside", [
    ("2026-11-06 16:59", False),      # Friday, gold still open
    ("2026-11-06 17:00", True),       # window opens
    ("2026-11-07 00:00", True),       # Saturday
    ("2026-11-07 23:59", True),
    ("2026-11-08 17:59", True),       # Sunday, last minute
    ("2026-11-08 18:00", False),      # gold reopens: window closed
    ("2026-11-09 10:00", False),      # Monday
    ("2026-11-04 20:00", False),      # Wednesday night
])
def test_window_edges(local, inside):
    assert in_weekend_window(pd.Timestamp(local, tz=NY)) is inside


def test_window_is_new_york_time_not_utc():
    # Friday 21:30 UTC = 16:30 New York (EST): not yet open
    assert not in_weekend_window(pd.Timestamp("2026-11-06 21:30", tz="UTC"))
    assert in_weekend_window(pd.Timestamp("2026-11-06 22:00", tz="UTC"))


def test_close_time_and_id_across_dst_change():
    # DST ended on Sunday 2026-11-01: that Sunday's 18:00 NY is 23:00 UTC (EST)
    t = pd.Timestamp("2026-10-31 12:00", tz=NY)
    assert weekend_close(t) == pd.Timestamp("2026-11-01 23:00", tz="UTC")
    assert weekend_id(t) == "2026-10-30"
    # summer weekend: 18:00 EDT = 22:00 UTC
    s = pd.Timestamp("2026-07-10 18:00", tz=NY)
    assert weekend_close(s) == pd.Timestamp("2026-07-12 22:00", tz="UTC")
    assert weekend_id(pd.Timestamp("2026-07-12 17:59", tz=NY)) == "2026-07-10"


def test_outside_window_raises_and_naive_rejected():
    with pytest.raises(ValueError):
        weekend_close(pd.Timestamp("2026-11-09 10:00", tz=NY))
    with pytest.raises(ValueError):
        in_weekend_window(pd.Timestamp("2026-11-07 10:00"))


def test_every_minute_of_a_week():
    idx = pd.date_range("2026-11-02 00:00", "2026-11-08 23:59", freq="1min", tz=NY)
    inside = pd.Series([in_weekend_window(t) for t in idx], index=idx)
    assert inside.sum() == (7 + 24 + 18) * 60              # Fri 17-24, Sat, Sun 0-18 = 49 h
    assert inside[inside].index[0] == pd.Timestamp("2026-11-06 17:00", tz=NY)
    assert inside[inside].index[-1] == pd.Timestamp("2026-11-08 17:59", tz=NY)
