"""Donchian channel trend following (multi-day; needs session.overnight: true).

Long on a close above the highest high of the previous `entry_bars` bars;
initial stop `stop_atr` ATRs away; exit on a close below the lowest low of the
previous `exit_bars` bars (trailing exit). Shorts mirror it. No fixed target
unless reward_risk > 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy


class DonchianTrend(Strategy):
    name = "donchian_trend"
    uses_session_window = False
    defaults = {
        "entry_bars": 20,
        "exit_bars": 10,
        "atr_period": 14,
        "stop_atr": 2.0,
        "reward_risk": 0.0,  # 0 = no target, ride the trend until the exit channel
        "allow_long": True,
        "allow_short": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        hi_e = d["high"].shift(1).rolling(int(p["entry_bars"])).max()
        lo_e = d["low"].shift(1).rolling(int(p["entry_bars"])).min()
        hi_x = d["high"].shift(1).rolling(int(p["exit_bars"])).max()
        lo_x = d["low"].shift(1).rolling(int(p["exit_bars"])).min()
        a = atr(d, int(p["atr_period"]))
        wd = d["weekday"].isin(self.session.weekdays)
        long = wd & (d["close"] > hi_e) & bool(p["allow_long"])
        short = wd & (d["close"] < lo_e) & bool(p["allow_short"])
        sig = long.astype(int) - short.astype(int)
        stop = np.where(sig > 0, d["close"] - p["stop_atr"] * a, d["close"] + p["stop_atr"] * a)
        rr = float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0
        out["signal"] = sig.values
        out["stop"] = np.where(sig != 0, stop, np.nan)
        out["rr"] = np.where(sig != 0, rr, np.nan)
        out["exit"] = np.where(d["close"] < lo_x, 1, np.where(d["close"] > hi_x, -1, 0))
        return self._finalize(out, df["close"])
