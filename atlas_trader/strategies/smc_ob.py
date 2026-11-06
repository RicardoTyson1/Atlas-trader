"""Smart money: H4 bias + lower-timeframe liquidity sweep + displacement FVG + order-block entry.

  BIAS     : last COMPLETED H4 candle closed above (long) / below (short) its `h4_ema` EMA.
  SWEEP    : a candle trades below the lowest low of the previous `sweep_bars` candles and
             closes back above it (sell-side liquidity taken) for a long; mirror for a short.
  DISPLACE : within `max_wait` candles, a candle with body >= `disp_atr` x ATR in the bias
             direction that leaves a fair value gap (low[i] > high[i-2] for a long).
  ORDER BLK: the last opposite-colored candle before the displacement (within 5 candles):
             its high/low is the zone.
  ENTRY    : within `entry_bars` candles of the gap, price trades back into the order block
             (low <= OB high for a long) and closes above the OB low -> next bar's open,
             inside `hours` (NY time).
  STOP     : beyond the sweep extreme + `stop_buffer_atr` x ATR (skipped if wider than
             `max_stop_atr` x ATR).
  TARGET   : `reward_risk` x risk, or 0 = trail only (candle trailing from `trail_start_r`).
  One setup at a time; everything mirrored for shorts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema
from ._common import exit_columns, hours_mask
from .base import Strategy, _bar_minutes


class SMCOrderBlock(Strategy):
    name = "smc_ob"
    uses_session_window = False
    defaults = {
        "h4_ema": 50,
        "sweep_bars": 24,
        "max_wait": 12,
        "disp_atr": 1.0,
        "entry_bars": 24,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 4.0,
        "reward_risk": 2.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "hours": "08:00-11:00",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 200:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()

        # H4 bias from completed H4 candles only (available once the H4 candle has closed)
        h4 = df["close"].resample("4h", label="left", closed="left").last().dropna()
        bias4 = np.sign(h4 - ema(h4, int(p["h4_ema"])))
        avail = pd.Series(bias4.to_numpy(), index=h4.index + pd.Timedelta(hours=4))
        step = pd.Timedelta(minutes=_bar_minutes(pd.DataFrame(index=df.index)))
        bias = avail.reindex(df.index + step, method="ffill").fillna(0).to_numpy()

        nb = int(p["sweep_bars"])
        prior_lo = pd.Series(l).shift(1).rolling(nb, min_periods=nb).min().to_numpy()
        prior_hi = pd.Series(h).shift(1).rolling(nb, min_periods=nb).max().to_numpy()
        ok_h = hours_mask(df.index, self.session.timezone, p["hours"])

        n = len(df)
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        st = None   # dict(side, extreme, i_sweep, ob, i_gap)
        for i in range(3, n):
            ai = a[i]
            if np.isnan(ai):
                continue
            if st is None:
                if bias[i] > 0 and l[i] < prior_lo[i] < c[i]:
                    st = {"side": 1, "ext": l[i], "i": i, "ob": None, "ig": None}
                elif bias[i] < 0 and h[i] > prior_hi[i] > c[i]:
                    st = {"side": -1, "ext": h[i], "i": i, "ob": None, "ig": None}
                continue
            s = st["side"]
            if st["ob"] is None:
                st["ext"] = min(st["ext"], l[i]) if s == 1 else max(st["ext"], h[i])
                if i - st["i"] > int(p["max_wait"]) or bias[i] != s:
                    st = None
                    continue
                body = c[i - 1] - o[i - 1]
                gap = l[i] > h[i - 2] if s == 1 else h[i] < l[i - 2]
                if s * body >= p["disp_atr"] * ai and gap:
                    for j in range(i - 2, max(st["i"] - 1, i - 7), -1):      # last opposite candle
                        if s * (c[j] - o[j]) < 0:
                            st["ob"], st["ig"] = (l[j], h[j]), i
                            break
                    if st["ob"] is None:
                        st = None
                continue
            if i - st["ig"] > int(p["entry_bars"]):
                st = None
                continue
            ob_lo, ob_hi = st["ob"]
            touched = l[i] <= ob_hi and c[i] > ob_lo if s == 1 else h[i] >= ob_lo and c[i] < ob_hi
            if not touched:
                continue
            stp = st["ext"] - s * p["stop_buffer_atr"] * ai
            risk = s * (c[i] - stp)
            if ok_h[i] and 0 < risk <= p["max_stop_atr"] * ai:
                sig[i], stop[i] = s, stp
            st = None
        out["signal"], out["stop"] = sig, stop
        out["rr"] = np.where(sig != 0, float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0, np.nan)
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)
