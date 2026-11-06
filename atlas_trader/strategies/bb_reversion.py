"""Mean reversion: fade an extreme Bollinger-band move back to the middle band.

  SETUP  : the previous candle CLOSED beyond the `bb_k`-sigma band (`bb_period` SMA).
  ENTRY  : this candle closes back inside the band, in the reversal direction (bullish
           candle back above the lower band = long; bearish back below the upper band =
           short). Optional RSI filter: RSI(14) was <= `rsi_level` (long) / >= 100 - level
           (short) on the outside candle. Optional trend guard: skip when the 50 EMA moved
           more than `max_slope_atr` x ATR over the last 10 candles (strong trends don't
           revert). Next bar's open, inside `hours` (NY time).
  STOP   : beyond the extreme of the last 3 candles + `stop_buffer_atr` x ATR (skipped if
           wider than `max_stop_atr` x ATR).
  TARGET : the middle band at entry. Skipped if it pays less than `min_rr` x risk (the
           live bot refuses < 1R anyway). Optional trailing from `trail_start_r` (0 = off).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, bollinger, ema, rsi
from ._common import exit_columns, hours_mask
from .base import Strategy


class BBReversion(Strategy):
    name = "bb_reversion"
    uses_session_window = False
    defaults = {
        "bb_period": 20,
        "bb_k": 2.5,
        "rsi_level": 0.0,
        "max_slope_atr": 0.0,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 3.0,
        "min_rr": 1.0,
        "trail_start_r": 0.0,
        "trail_buf_atr": 0.3,
        "hours": "08:00-11:00",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 100:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        mid, up, lo = (x.to_numpy() for x in bollinger(df["close"], int(p["bb_period"]), float(p["bb_k"])))
        r = rsi(df["close"], 14).to_numpy()
        e = ema(df["close"], 50).to_numpy()
        prev = lambda x: np.concatenate([[np.nan], x[:-1]])
        long_ = (prev(c) < prev(lo)) & (c > lo) & (c > o)
        short = (prev(c) > prev(up)) & (c < up) & (c < o)
        if p["rsi_level"] > 0:
            long_ &= prev(r) <= p["rsi_level"]
            short &= prev(r) >= 100 - p["rsi_level"]
        if p["max_slope_atr"] > 0:
            slope = np.abs(e - np.concatenate([np.full(10, np.nan), e[:-10]]))
            calm = slope <= p["max_slope_atr"] * a
            long_ &= calm
            short &= calm
        ok = hours_mask(df.index, self.session.timezone, p["hours"])
        side = np.where(long_ & ok, 1, np.where(short & ok, -1, 0))
        lo3 = pd.Series(l).rolling(3, min_periods=1).min().to_numpy()
        hi3 = pd.Series(h).rolling(3, min_periods=1).max().to_numpy()
        buf = p["stop_buffer_atr"] * a
        stop = np.where(side == 1, lo3 - buf, np.where(side == -1, hi3 + buf, np.nan))
        risk = np.abs(c - stop)
        reward = side * (mid - c)
        good = (risk > 0) & (risk <= p["max_stop_atr"] * a) & (reward >= p["min_rr"] * risk)
        side = np.where((side != 0) & good, side, 0)
        out["signal"] = side
        out["stop"] = np.where(side != 0, stop, np.nan)
        out["target"] = np.where(side != 0, mid, np.nan)
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)
