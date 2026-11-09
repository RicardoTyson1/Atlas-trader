"""Time-of-day drift: enter at a fixed clock time in a fixed direction and hold
for `hold_bars` bars, with a protective ATR stop. Tests intraday seasonality
(e.g. the gold evening/Asian-session drift). May need session.overnight: true.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes


class TimeDrift(Strategy):
    name = "time_drift"
    uses_session_window = False
    defaults = {
        "entry_time": "19:00",  # bar CLOSE time, exchange-local
        "direction": 1,          # 1 long, -1 short
        "hold_bars": 12,
        "atr_period": 14,
        "stop_atr": 3.0,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        h, m = (int(x) for x in str(p["entry_time"]).split(":"))
        close_tod = d["tod"] + _bar_minutes(d)
        a = atr(d, int(p["atr_period"]))
        hit = (close_tod == h * 60 + m) & d["weekday"].isin(self.session.weekdays) & a.notna()
        side = int(p["direction"])
        sig = hit.astype(int) * side
        out["signal"] = sig.values
        out["stop"] = np.where(sig != 0, d["close"] - side * p["stop_atr"] * a, np.nan)
        out["rr"] = np.where(sig != 0, 1000.0, np.nan)
        out["max_bars"] = np.where(sig != 0, float(p["hold_bars"]), np.nan)
        return self._finalize(out, df["close"])
