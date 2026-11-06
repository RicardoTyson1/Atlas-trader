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


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(middle, upper, lower). Uses the population std of the last n closes."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Classic SuperTrend. Returns (line, direction) with direction +1 (up) / -1 (down).
    Bar i uses only bars <= i."""
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    a = atr(df, n).to_numpy()
    hl2 = (h + l) / 2
    up_b, dn_b = hl2 - mult * a, hl2 + mult * a
    line = np.full(len(df), np.nan)
    d = np.zeros(len(df))
    fu, fd = up_b.copy(), dn_b.copy()
    for i in range(len(df)):
        if np.isnan(a[i]):
            continue
        if i > 0 and not np.isnan(fu[i - 1]):
            fu[i] = max(up_b[i], fu[i - 1]) if c[i - 1] > fu[i - 1] else up_b[i]
            fd[i] = min(dn_b[i], fd[i - 1]) if c[i - 1] < fd[i - 1] else dn_b[i]
        prev = d[i - 1] if i > 0 else 0
        if prev >= 0:
            d[i] = -1 if c[i] < fu[i] else 1
        else:
            d[i] = 1 if c[i] > fd[i] else -1
        line[i] = fu[i] if d[i] == 1 else fd[i]
    return pd.Series(line, index=df.index), pd.Series(d, index=df.index)
