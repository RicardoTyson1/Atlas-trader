"""Confluence trend entries: SuperTrend direction + MACD confirmation + volatility/volume filter.

  TREND   : SuperTrend(`st_period`, `st_mult`) direction (primary trend).
  CONFIRM : MACD(12,26,9) histogram on the same side of zero (a momentum read that is not
            computed from the SuperTrend band).
  FILTER  : volatility - ATR(14) >= `vol_mult` x its 100-bar median (0 = off);
            volume - candle volume >= its 20-bar average (`volume_filter`).
  TRIGGER : price pulled back to within `pb_atr` x ATR of the SuperTrend line in the last 3
            candles, and this candle closes in the trend direction beyond the previous
            candle's high (long) / low (short). Next bar's open, inside `hours` (NY time).
  STOP    : beyond the SuperTrend line + `stop_buffer_atr` x ATR (skipped if wider than
            `max_stop_atr` x ATR).
  EXIT    : candle trailing stop from `trail_start_r` x risk, big-candle stop lock, optional
            fixed target `reward_risk` (0 = none), and a SuperTrend flip closes the trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, macd_hist, supertrend
from ._common import exit_columns, hours_mask
from .base import Strategy


class STConfluence(Strategy):
    name = "st_confluence"
    uses_session_window = False
    defaults = {
        "st_period": 10,
        "st_mult": 3.0,
        "vol_mult": 1.0,
        "volume_filter": True,
        "pb_atr": 0.5,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 3.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "spike_atr": 2.0,
        "reward_risk": 0.0,
        "hours": "08:00-11:00",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 150:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        line, d = supertrend(df, int(p["st_period"]), float(p["st_mult"]))
        line, d = line.to_numpy(), d.to_numpy()
        mh = macd_hist(df["close"]).to_numpy()
        ok = hours_mask(df.index, self.session.timezone, p["hours"])
        if p["vol_mult"] > 0:
            med = pd.Series(a).rolling(100, min_periods=100).median().to_numpy()
            ok &= a >= p["vol_mult"] * med
        if p["volume_filter"] and "volume" in df:
            v = df["volume"].to_numpy(float)
            ok &= v >= pd.Series(v).rolling(20, min_periods=20).mean().to_numpy()
        near_l = pd.Series(l - line).rolling(3, min_periods=1).min().to_numpy() <= p["pb_atr"] * a
        near_h = pd.Series(line - h).rolling(3, min_periods=1).min().to_numpy() <= p["pb_atr"] * a
        ph, pl = np.concatenate([[np.nan], h[:-1]]), np.concatenate([[np.nan], l[:-1]])
        long_ = (d == 1) & (mh > 0) & near_l & (c > o) & (c > ph)
        short = (d == -1) & (mh < 0) & near_h & (c < o) & (c < pl)
        side = np.where(long_ & ok, 1, np.where(short & ok, -1, 0))
        buf = p["stop_buffer_atr"] * a
        stop = np.where(side == 1, line - buf, np.where(side == -1, line + buf, np.nan))
        risk = side * (c - stop)
        side = np.where((side != 0) & (risk > 0) & (risk <= p["max_stop_atr"] * a), side, 0)
        out["signal"] = side
        out["stop"] = np.where(side != 0, stop, np.nan)
        out["rr"] = np.where(side != 0, float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0, np.nan)
        flip = np.concatenate([[0], np.diff(d)]) != 0
        out["exit"] = np.where(flip & (d == -1), 1, np.where(flip & (d == 1), -1, 0))
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        spk = p["spike_atr"] * a if p["spike_atr"] > 0 else None
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a, spk)
