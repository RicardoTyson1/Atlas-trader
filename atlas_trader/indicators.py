"""Indicators. Every function uses only current and past bars (no look-ahead)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR."""
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def session_vwap(df: pd.DataFrame, day: pd.Series) -> pd.Series:
    """VWAP that resets each session `day`. Falls back to equal weights if volume is 0."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].where(df["volume"] > 0, 1.0)
    pv = (tp * vol).groupby(day.values).cumsum()
    vv = vol.groupby(day.values).cumsum()
    return pv / vv
