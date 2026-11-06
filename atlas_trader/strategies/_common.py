"""Small helpers shared by strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd


def hours_mask(index: pd.DatetimeIndex, tz: str, hours: str) -> np.ndarray:
    """True for bars whose NY-local start time is inside any "HH:MM-HH:MM" range (comma list)."""
    local = index.tz_convert(tz)
    tod = local.hour * 60 + local.minute
    m = np.zeros(len(index), bool)
    for rng in str(hours).split(","):
        s, e = rng.strip().split("-")
        m |= (tod >= int(s[:2]) * 60 + int(s[3:])) & (tod < int(e[:2]) * 60 + int(e[3:]))
    return m


def exit_columns(out: pd.DataFrame, on: np.ndarray, trail_start_r: float, trail_buf: np.ndarray,
                 spike_pts: np.ndarray | None = None) -> pd.DataFrame:
    """Fill the engine's trailing / big-candle columns for signal bars (NaN = feature off)."""
    out["trail_start_r"] = np.where(on, trail_start_r, np.nan) if trail_start_r > 0 else np.nan
    out["trail_buf"] = np.where(on, trail_buf, np.nan)
    out["spike_pts"] = np.where(on, spike_pts, np.nan) if spike_pts is not None else np.nan
    out["spike_min_r"] = np.where(on, 1.0, np.nan)
    out["spike_mode"] = np.where(on, 2.0, np.nan)
    return out
