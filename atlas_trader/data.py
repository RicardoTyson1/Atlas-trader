"""Market data loading, cleaning and resampling.

Canonical bar format everywhere in the system:
    DataFrame indexed by a tz-aware UTC DatetimeIndex (bar OPEN time),
    columns: open, high, low, close, volume (float).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]

_TF = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1H": "1h", "4H": "4h", "1D": "1D"}


def timeframe_to_offset(tf: str) -> str:
    if tf not in _TF:
        raise ValueError(f"Unsupported timeframe {tf!r}; use one of {list(_TF)}")
    return _TF[tf]


def timeframe_minutes(tf: str) -> int:
    return int(pd.Timedelta(timeframe_to_offset(tf)).total_seconds() // 60)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case columns, ensure UTC index, sort, drop duplicates/bad rows."""
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    if "volume" not in df.columns:
        df["volume"] = 0.0
    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError("Index must be a DatetimeIndex")
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx.tz_convert("UTC")
    df = df[COLS].astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])
    # Repair bars where high/low don't contain open/close
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    return df


def load_csv(path: str | Path, tz: str = "UTC") -> pd.DataFrame:
    """Load OHLCV CSV.

    Supports TradingView 'Export chart data' files (column `time` as unix seconds
    or ISO string) and generic files with a `time`/`date`/`datetime`/`timestamp` column.
    `tz` is used only when timestamps have no timezone.
    """
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    tcol = next((c for c in ("time", "datetime", "timestamp", "date") if c in raw.columns), None)
    if tcol is None:
        raise ValueError("CSV needs a time/datetime/timestamp/date column")
    t = raw[tcol]
    if pd.api.types.is_numeric_dtype(t):
        unit = "ms" if t.iloc[0] > 1e11 else "s"
        idx = pd.to_datetime(t, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(t, utc=False, format="mixed")
        if getattr(idx.dt, "tz", None) is None:
            idx = idx.dt.tz_localize(tz)
        idx = idx.dt.tz_convert("UTC")
    raw.index = pd.DatetimeIndex(idx)
    raw = raw.drop(columns=[tcol])
    return normalize(raw)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample bars to a higher timeframe (bar open time labels)."""
    rule = timeframe_to_offset(tf)
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def add_session_columns(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Add local-time helpers: `local` (tz-aware), `day` (local date), `tod` (minutes since midnight)."""
    out = df.copy()
    local = out.index.tz_convert(tz)
    out["day"] = local.date
    out["tod"] = local.hour * 60 + local.minute
    out["weekday"] = local.day_of_week
    return out


def synthetic_intraday(
    days: int = 60,
    tf_minutes: int = 1,
    start: str = "2025-01-06",
    tz: str = "America/New_York",
    start_price: float = 20000.0,
    daily_vol: float = 0.012,
    drift: float = 0.0,
    seed: int = 7,
) -> pd.DataFrame:
    """Random-walk regular-session bars for tests and demos.

    NOT market data. Results on synthetic data say nothing about real edge.
    """
    rng = np.random.default_rng(seed)
    per_day = int(390 / tf_minutes)
    step_vol = daily_vol / np.sqrt(per_day)
    frames = []
    price = start_price
    d = pd.Timestamp(start)
    made = 0
    while made < days:
        if d.weekday() < 5:
            t0 = pd.Timestamp(f"{d.date()} 09:30", tz=tz)
            idx = pd.date_range(t0, periods=per_day, freq=f"{tf_minutes}min")
            gap = rng.normal(0, daily_vol / 3)
            price *= 1 + gap
            rets = rng.normal(drift / per_day, step_vol, per_day)
            closes = price * np.cumprod(1 + rets)
            opens = np.concatenate([[price], closes[:-1]])
            wick = np.abs(rng.normal(0, step_vol * 0.6, per_day)) * closes
            highs = np.maximum(opens, closes) + wick
            lows = np.minimum(opens, closes) - np.abs(rng.normal(0, step_vol * 0.6, per_day)) * closes
            vol = rng.integers(100, 1000, per_day).astype(float)
            frames.append(
                pd.DataFrame(
                    {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol},
                    index=idx,
                )
            )
            price = closes[-1]
            made += 1
        d += pd.Timedelta(days=1)
    return normalize(pd.concat(frames))
