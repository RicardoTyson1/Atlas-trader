"""VWAP mean reversion: fade overstretched moves back toward session VWAP.

Long when price is more than `dev_atr` ATRs below VWAP, RSI is oversold and
the bar closes up (first sign of reversal). Target is VWAP at signal time,
stop below the recent low. Requires at least `min_rr` reward/risk.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, rsi, session_vwap
from .base import Strategy


class VwapReversion(Strategy):
    name = "vwap_reversion"
    defaults = {
        "dev_atr": 2.0,
        "rsi_period": 14,
        "rsi_low": 30,
        "rsi_high": 70,
        "atr_period": 14,
        "stop_atr": 0.5,
        "swing_bars": 3,
        "min_rr": 1.0,
        "allow_short": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        d = self._prep(df)
        out = self._empty(df.index)
        if d.empty:
            return out
        p = self.p
        vwap = session_vwap(d, d["day"])
        a = atr(d, int(p["atr_period"]))
        r = rsi(d["close"], int(p["rsi_period"]))
        dev = (d["close"] - vwap) / a
        win = self._entry_window(d)
        n = int(p["swing_bars"])
        swing_low = d["low"].rolling(n, min_periods=1).min() - p["stop_atr"] * a
        swing_high = d["high"].rolling(n, min_periods=1).max() + p["stop_atr"] * a

        long = win & (dev < -p["dev_atr"]) & (r < p["rsi_low"]) & (d["close"] > d["open"])
        short = (
            win & (dev > p["dev_atr"]) & (r > p["rsi_high"]) & (d["close"] < d["open"])
            & bool(p["allow_short"])
        )
        sig = long.astype(int) - short.astype(int)
        stop = np.where(sig > 0, swing_low, swing_high)
        risk = np.abs(d["close"] - stop)
        reward = np.abs(vwap - d["close"])
        enough = reward >= p["min_rr"] * risk
        sig = sig.where(enough, 0)
        out["signal"] = sig.values
        out["stop"] = np.where(sig != 0, stop, np.nan)
        out["target"] = np.where(sig != 0, vwap, np.nan)
        return self._finalize(out, df["close"])
