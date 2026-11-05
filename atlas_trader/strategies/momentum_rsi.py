"""Ricardo's momentum model: RSI crosses 50 on an aggressive candle -> ride it with a trailing stop.

  ENTRY  : RSI(`rsi_period`) crosses 50 on this candle (from below for a long, from above for a
           short) AND the candle is aggressive: body >= `disp_atr` x ATR, body >= `body_frac` of
           its range, closing in the trade's direction. Optional trend filter: close above
           (long) / below (short) the `ema` EMA. Next bar's open, inside `hours` (NY time).
  STOP   : beyond the aggressive candle's wick + `stop_buffer_atr` x ATR (skipped if wider than
           `max_stop_atr` x ATR).
  TARGET : `tp: levels` = take profit at the nearest FVG 50% / resting liquidity / major S/R
           level paying >= `tp_min_r` x risk (atlas_trader/targets.py); none in reach = no
           trade. `tp: none` = no fixed target (`reward_risk` 0) and ride the trail.
  STAY IN: no fixed target by default (`reward_risk` 0). Candle trailing stop from
           `trail_start_r` x risk; on another aggressive candle in our favor (body >=
           `spike_atr` x ATR) the stop is pulled to the middle of that candle. The stop never
           moves backwards. One position, no adding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema, rsi
from .base import Strategy
from ..targets import TargetFinder, tp_params


class MomentumRSI(Strategy):
    name = "momentum_rsi"
    uses_session_window = False
    defaults = {
        "rsi_period": 14,
        "disp_atr": 1.2,
        "body_frac": 0.6,
        "ema": 50,
        "trend_filter": True,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 3.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "spike_atr": 2.0,
        "spike_min_r": 1.0,
        "spike_mode": 2,
        "reward_risk": 0.0,
        "tp": "none",              # none | levels
        "tp_min_r": 1.0,           # take-profit levels: nearest FVG 50% / liquidity / major S/R
        "tp_lookback_bars": 0,     # paying >= tp_min_r x risk (see atlas_trader/targets.py)
        "tp_sr_days": 10,
        "tp_front_atr": 0.1,
        "hours": "08:00-11:00",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        for k in ("trail_start_r", "trail_buf", "spike_pts", "spike_min_r", "spike_mode"):
            out[k] = np.nan
        if len(df) < 100:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        r = rsi(df["close"], int(p["rsi_period"])).to_numpy()
        e = ema(df["close"], int(p["ema"])).to_numpy()
        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        in_hours = np.zeros(len(df), bool)
        for rng_ in str(p["hours"]).split(","):
            s_, e_ = rng_.strip().split("-")
            in_hours |= (tod >= int(s_[:2]) * 60 + int(s_[3:])) & (tod < int(e_[:2]) * 60 + int(e_[3:]))

        body = c - o
        rng = h - l
        aggressive = (np.abs(body) >= p["disp_atr"] * a) & (np.abs(body) >= p["body_frac"] * np.where(rng > 0, rng, np.inf))
        r_prev = np.concatenate([[np.nan], r[:-1]])
        up = (r_prev < 50) & (r >= 50) & (body > 0) & aggressive
        dn = (r_prev > 50) & (r <= 50) & (body < 0) & aggressive
        if p["trend_filter"]:
            up &= c > e
            dn &= c < e
        side = np.where(up & in_hours, 1, np.where(dn & in_hours, -1, 0))
        stop = np.where(side == 1, l - p["stop_buffer_atr"] * a, np.where(side == -1, h + p["stop_buffer_atr"] * a, np.nan))
        risk = np.abs(c - stop)
        side = np.where((side != 0) & (risk > 0) & (risk <= p["max_stop_atr"] * a), side, 0)

        tgt = np.full(len(df), np.nan)
        kind = np.full(len(df), None, object)
        if p["tp"] == "levels" and (side != 0).any():
            finder = TargetFinder(df, self.session.timezone, tp_params(p))
            for i in np.flatnonzero(side):
                hit = finder.find(int(i), int(side[i]), float(c[i]), float(stop[i]))
                if hit is None:
                    side[i] = 0                     # nothing worth >= tp_min_r in the path: skip
                else:
                    tgt[i], kind[i] = hit
        out["signal"] = side
        out["stop"] = np.where(side != 0, stop, np.nan)
        out["target"] = np.where(side != 0, tgt, np.nan)
        out["rr"] = np.where((side != 0) & np.isnan(tgt),
                             float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0, np.nan)
        out = self._finalize(out, df["close"])
        out["tp_kind"] = np.where(out["signal"] != 0, kind, None)
        on = out["signal"] != 0
        out["trail_start_r"] = np.where(on, float(p["trail_start_r"]), np.nan)
        out["trail_buf"] = np.where(on, p["trail_buf_atr"] * a, np.nan)
        out["spike_pts"] = np.where(on & (p["spike_atr"] > 0), p["spike_atr"] * a, np.nan)
        out["spike_min_r"] = np.where(on, float(p["spike_min_r"]), np.nan)
        out["spike_mode"] = np.where(on, float(p["spike_mode"]), np.nan)
        return out
