"""BTC weekend study engine: entries only in the window, Sunday 18:00 close, costs, no look-ahead."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from btc_weekend_study import simulate  # noqa: E402

NY = "America/New_York"


def bars(start_local, n, price=70000.0, step=5, drift=0.0):
    idx = pd.date_range(pd.Timestamp(start_local, tz=NY), periods=n, freq=f"{step}min").tz_convert("UTC")
    c = price + drift * np.arange(n)
    return pd.DataFrame({"open": c, "high": c + 10, "low": c - 10, "close": c, "volume": 1.0}, index=idx)


def signal_at(df, i, side=1, stop_dist=1000.0):
    sig = pd.DataFrame({"signal": 0, "stop": np.nan, "exit": 0, "trail_start_r": np.nan, "trail_buf": np.nan,
                        "spike_pts": np.nan, "spike_min_r": np.nan, "spike_mode": np.nan}, index=df.index)
    sig.iloc[i, sig.columns.get_loc("signal")] = side
    sig.iloc[i, sig.columns.get_loc("stop")] = df["close"].iloc[i] - side * stop_dist
    return sig


def test_trade_open_on_sunday_is_closed_at_18_00():
    df = bars("2026-11-08 16:00", 48)                 # Sunday 16:00 -> 19:55, 5m bars
    tr = simulate(df, signal_at(df, 3), spread=20.0, basis="mid")
    assert len(tr) == 1 and tr[0]["reason"] == "sunday close"
    # closed on the bar 17:55-18:00
    assert df.index[tr[0]["exit_bar"]].tz_convert(NY) == pd.Timestamp("2026-11-08 17:55", tz=NY)


def test_sunday_close_ignores_prices_after_18_00():
    df = bars("2026-11-08 16:00", 48)
    base = simulate(df, signal_at(df, 3), spread=20.0)
    df2 = df.copy()
    after = df2.index.tz_convert(NY) >= pd.Timestamp("2026-11-08 18:00", tz=NY)
    df2.loc[after, ["open", "high", "low", "close"]] += 50000.0      # wildly different future
    again = simulate(df2, signal_at(df2, 3), spread=20.0)
    assert again[0]["r"] == pytest.approx(base[0]["r"]) and again[0]["exit_bar"] == base[0]["exit_bar"]


def test_no_entries_outside_the_window():
    df = bars("2026-11-06 15:00", 60)                 # Friday 15:00 -> 19:55
    # signal at 16:50 close (still gold hours) -> not taken
    i_early = list(df.index.tz_convert(NY)).index(pd.Timestamp("2026-11-06 16:45", tz=NY))
    assert simulate(df, signal_at(df, i_early), spread=20.0) == []
    # signal on the bar closing 17:05 -> taken, filled at 17:05
    i_ok = list(df.index.tz_convert(NY)).index(pd.Timestamp("2026-11-06 17:00", tz=NY))
    tr = simulate(df, signal_at(df, i_ok), spread=20.0)
    assert len(tr) == 1 and tr[0]["entry_time"].tz_convert(NY) == pd.Timestamp("2026-11-06 17:05", tz=NY)
    # no new entry once the next bar would open at/after Sunday 18:00
    sun = bars("2026-11-08 17:40", 10)
    last = list(sun.index.tz_convert(NY)).index(pd.Timestamp("2026-11-08 17:55", tz=NY))
    assert simulate(sun, signal_at(sun, last), spread=20.0) == []


def test_spread_and_cost_filters():
    df = bars("2026-11-07 10:00", 30)
    assert simulate(df, signal_at(df, 3), spread=81.0) == []                    # spread > $80
    assert simulate(df, signal_at(df, 3, stop_dist=300.0), spread=67.0) == []   # 1R 300 < 5 x 67
    assert len(simulate(df, signal_at(df, 3, stop_dist=400.0), spread=67.0)) == 1


def test_costs_buy_ask_sell_bid():
    df = bars("2026-11-08 17:00", 20)                 # flat price 70,000, closes at 18:00
    tr = simulate(df, signal_at(df, 1), spread=60.0, basis="mid", slip=5.0)[0]
    assert tr["entry"] == pytest.approx(70000 + 30 + 5)          # ask + slippage
    assert tr["exit"] == pytest.approx(70000 - 30 - 5)           # bid - slippage
    sh = simulate(df, signal_at(df, 1, side=-1), spread=60.0, basis="bid", slip=5.0)[0]
    assert sh["entry"] == pytest.approx(70000 - 5)               # sell the bid
    assert sh["exit"] == pytest.approx(70000 + 60 + 5)           # buy back the ask


def test_long_stop_triggers_on_the_bid():
    df = bars("2026-11-07 10:00", 30)
    sig = signal_at(df, 3, stop_dist=1000.0)
    stop = sig["stop"].iloc[3]
    df2 = df.copy()
    df2.iloc[10, df2.columns.get_loc("low")] = stop + 20       # mid low above stop, bid low (mid-30) below
    tr = simulate(df2, sig, spread=60.0, basis="mid", slip=5.0)
    assert tr[0]["reason"] == "stop" and tr[0]["exit"] == pytest.approx(stop - 5.0)
