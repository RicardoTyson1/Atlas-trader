"""Order-flow gates from COMEX gold futures volume (Databento GC), for XAUUSD entries.

Pre-registered 2026-11-16 (Ricardo): research/orderflow_gate_study.py. Research only, not in the bot.

GatesFX XAUUSD has no real volume. GC (CME gold futures, front month by volume, back-adjusted) does.
GC bars are joined to XAUUSD bars by timestamp (UTC); a bar with no GC data has NaN features and
fails every gate (conservative).

Features at bar i use ONLY bars <= i (no look-ahead):
  vol_ratio : GC volume / mean GC volume of the previous `avg_bars` bars (bar i excluded)
  delta     : proxy bar delta = GC volume x sign(GC close - GC open)   (no aggressor side in OHLCV)
  cvd       : cumulative proxy delta
  div_bear  : XAUUSD high > highest high of the previous `div_bars` bars, but CVD < highest CVD of
              those bars (new price high, no new buying) -> supports a SHORT
  div_bull  : mirror (new low, CVD above its lowest) -> supports a LONG
  absorb    : vol_ratio >= `absorb_vol` and GC candle body <= `absorb_body` x its range

Gates for a signal at bar i with side s (+1 long / -1 short), window W = bars i-`lookback`..i
(covers the sweep candle and the confirm candle; session_sweep's max_wait is 6):
  V1 vol_ratio[i] >= 1.5        V2 vol_ratio[i] >= 2.0
  V3 max vol_ratio over W >= 1.5   V4 max vol_ratio over W >= 2.0
  D1 divergence on the signal's side anywhere in W
  D2 D1 and V1                  A1 absorption anywhere in W          A2 A1 and D1
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GATES = ("V1", "V2", "V3", "V4", "D1", "D2", "A1", "A2")


def gc_volume_frame(gc: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """GC open/close/high/low/volume reindexed onto `index` (exact timestamp match, NaN if missing)."""
    g = gc[["open", "high", "low", "close", "volume"]]
    g = g[~g.index.duplicated(keep="last")].sort_index()
    return g.reindex(index)


def features(df: pd.DataFrame, gc: pd.DataFrame, avg_bars: int = 20, div_bars: int = 20,
             absorb_vol: float = 1.5, absorb_body: float = 0.35) -> pd.DataFrame:
    g = gc_volume_frame(gc, df.index)
    vol = g["volume"]
    avg = vol.rolling(avg_bars, min_periods=avg_bars).mean().shift(1)
    vol_ratio = vol / avg.replace(0, np.nan)
    delta = vol * np.sign(g["close"] - g["open"])
    cvd = delta.fillna(0.0).cumsum().where(vol.notna())
    prev_hi = df["high"].rolling(div_bars, min_periods=div_bars).max().shift(1)
    prev_lo = df["low"].rolling(div_bars, min_periods=div_bars).min().shift(1)
    prev_cvd_hi = cvd.rolling(div_bars, min_periods=div_bars).max().shift(1)
    prev_cvd_lo = cvd.rolling(div_bars, min_periods=div_bars).min().shift(1)
    div_bear = (df["high"] > prev_hi) & (cvd < prev_cvd_hi)
    div_bull = (df["low"] < prev_lo) & (cvd > prev_cvd_lo)
    rng = (g["high"] - g["low"]).replace(0, np.nan)
    body = (g["close"] - g["open"]).abs()
    absorb = (vol_ratio >= absorb_vol) & (body / rng <= absorb_body)
    return pd.DataFrame({"vol_ratio": vol_ratio, "cvd": cvd, "div_bear": div_bear.fillna(False),
                         "div_bull": div_bull.fillna(False), "absorb": absorb.fillna(False)},
                        index=df.index)


def _window_any(x: np.ndarray, lookback: int) -> np.ndarray:
    return pd.Series(x.astype(float)).rolling(lookback + 1, min_periods=1).max().to_numpy() > 0


def _window_max(x: np.ndarray, lookback: int) -> np.ndarray:
    return pd.Series(x).rolling(lookback + 1, min_periods=1).max().to_numpy()


def gate(signal: np.ndarray, f: pd.DataFrame, name: str, lookback: int = 6) -> np.ndarray:
    """Return `signal` with the entries that fail gate `name` set to 0."""
    if name not in GATES:
        raise ValueError(f"unknown gate {name}")
    sig = np.asarray(signal, int).copy()
    vr = f["vol_ratio"].to_numpy(float)
    vr0 = np.nan_to_num(vr, nan=0.0)
    vmax = np.nan_to_num(_window_max(vr, lookback), nan=0.0)
    bear = _window_any(f["div_bear"].to_numpy(bool), lookback)
    bull = _window_any(f["div_bull"].to_numpy(bool), lookback)
    div = np.where(sig == 1, bull, np.where(sig == -1, bear, False))
    absorb = _window_any(f["absorb"].to_numpy(bool), lookback)
    keep = {
        "V1": vr0 >= 1.5, "V2": vr0 >= 2.0, "V3": vmax >= 1.5, "V4": vmax >= 2.0,
        "D1": div, "D2": div & (vr0 >= 1.5), "A1": absorb, "A2": absorb & div,
    }[name]
    sig[~keep] = 0
    return sig
