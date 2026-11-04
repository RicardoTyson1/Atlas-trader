"""Session range breakout (e.g. Asian-range -> London breakout, COMEX open).

Build the high/low between `range_start` and `range_end` (exchange-local time,
same day). Between `range_end` and `trade_end`, take the first close beyond the
range. Stop on the other side (or midpoint); target = reward_risk x risk.
Any open trade is closed at the first bar closing at/after `exit_time`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes


def _m(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class SessionBreakout(Strategy):
    name = "session_breakout"
    uses_session_window = False
    defaults = {
        "range_start": "00:00",
        "range_end": "03:00",
        "trade_end": "08:00",
        "exit_time": "11:30",
        "reward_risk": 2.0,
        "stop_mode": "opposite",  # opposite | mid
        "atr_period": 14,
        "max_range_atr": 99.0,
        "min_range_atr": 0.0,
        "allow_long": True,
        "allow_short": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        tf = _bar_minutes(d)
        rs, re_, te, xt = (_m(p[k]) for k in ("range_start", "range_end", "trade_end", "exit_time"))
        day = d["day"]
        close_tod = d["tod"] + tf

        in_range = (d["tod"] >= rs) & (close_tod <= re_)
        rh = d["high"].where(in_range).groupby(day).cummax().groupby(day).ffill()
        rl = d["low"].where(in_range).groupby(day).cummin().groupby(day).ffill()
        a = atr(d, int(p["atr_period"]))
        width = rh - rl
        ok = (width <= p["max_range_atr"] * a) & (width >= p["min_range_atr"] * a) & rh.notna()
        window = (d["tod"] >= re_) & (close_tod <= te) & d["weekday"].isin(self.session.weekdays)
        base = ok & window
        long = base & (d["close"] > rh) & bool(p["allow_long"])
        short = base & (d["close"] < rl) & bool(p["allow_short"])
        sig = long.astype(int) - short.astype(int)
        first = (sig != 0).groupby(day).cumsum() == 1
        sig = sig.where(first & (sig != 0), 0)

        stop = (rh + rl) / 2 if p["stop_mode"] == "mid" else pd.Series(np.where(sig > 0, rl, rh), index=d.index)
        out["signal"] = sig.astype(int).values
        out["stop"] = np.where(sig != 0, stop, np.nan)
        out["rr"] = np.where(sig != 0, float(p["reward_risk"]), np.nan)
        out["exit"] = np.where(close_tod >= xt, 2, 0)
        return self._finalize(out, df["close"])
