"""Opening Range Breakout.

Build the high/low of the first `range_minutes` after the open. After the range
is complete, go long on the first close above the range high (short: below the
low). Stop on the other side of the range (or its midpoint). Skip days whose
range is abnormally wide or narrow relative to ATR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes, _mins


class OpeningRangeBreakout(Strategy):
    name = "orb"
    defaults = {
        "range_minutes": 15,
        "reward_risk": 2.0,
        "stop_mode": "opposite",  # opposite | mid
        "atr_period": 14,
        "max_range_atr": 2.5,     # skip if range > this many bar-ATRs
        "min_range_atr": 0.3,     # skip if range < this many bar-ATRs
        "one_trade_per_day": True,
        "allow_short": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        tf = _bar_minutes(d)
        open_m = _mins(self.session.open)
        rng_end = open_m + int(p["range_minutes"])
        day = d["day"]

        in_range = (d["tod"] >= open_m) & (d["tod"] + tf <= rng_end)
        orh = d["high"].where(in_range).groupby(day).cummax()
        orl = d["low"].where(in_range).groupby(day).cummin()
        orh = orh.groupby(day).ffill()
        orl = orl.groupby(day).ffill()

        a = atr(d, int(p["atr_period"]))
        width = orh - orl
        ok_width = (width <= p["max_range_atr"] * a) & (width >= p["min_range_atr"] * a)
        after = d["tod"] >= rng_end
        base = after & ok_width & self._entry_window(d) & orh.notna()

        long = base & (d["close"] > orh)
        short = base & (d["close"] < orl) & bool(p["allow_short"])
        sig = long.astype(int) - short.astype(int)

        # keep only the first breakout per day (per direction, or overall)
        if p["one_trade_per_day"]:
            first = (sig != 0).groupby(day).cumsum() == 1
            sig = sig.where(first & (sig != 0), 0)
        else:
            lf = long.groupby(day).cumsum() == 1
            sf = short.groupby(day).cumsum() == 1
            sig = (long & lf).astype(int) - (short & sf).astype(int)

        mid = (orh + orl) / 2
        if p["stop_mode"] == "mid":
            stop = mid
        else:
            stop = np.where(sig > 0, orl, orh)
        out["signal"] = sig.astype(int).values
        out["stop"] = np.where(sig != 0, stop, np.nan)
        out["rr"] = np.where(sig != 0, float(p["reward_risk"]), np.nan)
        return self._finalize(out, df["close"])
