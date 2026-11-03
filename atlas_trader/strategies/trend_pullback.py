"""Trend pullback: trade in the direction of the intraday trend after a dip
into the fast EMA.

Long when fast EMA > slow EMA, price is above session VWAP, the bar dips to
the fast EMA and closes back above it as a bullish bar. Stop below the bar's
low minus a fraction of ATR. Shorts mirror it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema, session_vwap
from .base import Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"
    defaults = {
        "fast": 20,
        "slow": 50,
        "atr_period": 14,
        "stop_atr": 0.5,
        "reward_risk": 2.0,
        "allow_short": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        fast = ema(d["close"], int(p["fast"]))
        slow = ema(d["close"], int(p["slow"]))
        vwap = session_vwap(d, d["day"])
        a = atr(d, int(p["atr_period"]))
        win = self._entry_window(d)
        warm = pd.Series(np.arange(len(d)) >= int(p["slow"]), index=d.index)

        long = (
            win & warm & (fast > slow) & (d["close"] > vwap)
            & (d["low"] <= fast) & (d["close"] > fast) & (d["close"] > d["open"])
        )
        short = (
            win & warm & (fast < slow) & (d["close"] < vwap)
            & (d["high"] >= fast) & (d["close"] < fast) & (d["close"] < d["open"])
            & bool(p["allow_short"])
        )
        sig = long.astype(int) - short.astype(int)
        stop = np.where(sig > 0, d["low"] - p["stop_atr"] * a, d["high"] + p["stop_atr"] * a)
        out["signal"] = sig.values
        out["stop"] = np.where(sig != 0, stop, np.nan)
        out["rr"] = np.where(sig != 0, float(p["reward_risk"]), np.nan)
        return self._finalize(out, df["close"])
