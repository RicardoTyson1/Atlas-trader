"""ICT Silver Bullet, in exact testable rules.

Source: The Inner Circle Trader's Silver Bullet (time-boxed FVG model), as commonly
documented: three one-hour windows (New York time) 03:00-04:00, 10:00-11:00 and
14:00-15:00; trade the first fair value gap that forms inside the window in the
direction of the higher-timeframe bias, aiming at the nearest liquidity.

  WINDOW : `windows` = any of "am_london" (03-04), "am_ny" (10-11), "pm_ny" (14-15).
  BIAS   : trend    -> longs only if yesterday closed above the close `bias_days` ago.
           discount -> longs only below the 50% of the last `bias_days` days' range.
           none     -> both directions.
  SWEEP  : if `require_sweep`, before the FVG the window must have traded through the
           high (for shorts) or low (for longs) of the hour BEFORE the window.
  FVG    : 3-bar gap whose middle bar closes in the trade direction with a body of at
           least `disp_atr` x ATR; the gap's last bar must close inside the window.
  ENTRY  : first later bar (inside the window) that trades back into the gap and closes
           beyond its far edge -> market entry at the next bar's open.
  STOP   : beyond the FVG's first candle (+ `stop_buffer_atr` x ATR).
  TARGET : `reward_risk` x risk, or "liquidity" = the pre-window hour's opposite
           extreme (skipped if under `min_rr` x risk).
  One trade per window per day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes

WINDOWS = {"am_london": (3 * 60, 4 * 60), "am_ny": (10 * 60, 11 * 60), "pm_ny": (14 * 60, 15 * 60)}


class SilverBullet(Strategy):
    name = "silver_bullet"
    uses_session_window = False
    defaults = {
        "windows": "am_ny",            # comma list of WINDOWS keys
        "bias": "trend",               # trend | discount | none
        "bias_days": 20,
        "require_sweep": True,
        "disp_atr": 0.5,
        "atr_period": 14,
        "stop_buffer_atr": 0.1,
        "target": "rr",                # rr | liquidity
        "reward_risk": 2.0,
        "min_rr": 1.5,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        if len(df) < 50:
            return out
        p = self.p
        local = df.index.tz_convert(self.session.timezone)
        tf = _bar_minutes(pd.DataFrame(index=df.index))
        tod = local.hour * 60 + local.minute
        close_tod = tod + tf
        shifted = local + pd.Timedelta(hours=6)                 # gold trading day starts 18:00 NY
        tday = pd.Series(shifted.date, index=df.index)
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()

        daily = df.groupby(tday.values).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
        n = int(p["bias_days"])
        pclose = tday.map(daily["close"].shift(1)).to_numpy(float)
        tref = tday.map(daily["close"].shift(n + 1)).to_numpy(float)
        eq = tday.map((daily["high"].rolling(n).max() + daily["low"].rolling(n).min()).shift(1) / 2).to_numpy(float)

        wins = [WINDOWS[w.strip()] for w in str(p["windows"]).split(",")]
        days = tday.to_numpy()
        sig = np.zeros(len(df), int)
        stop = np.full(len(df), np.nan)
        target = np.full(len(df), np.nan)
        rr = np.full(len(df), np.nan)

        for ws, we in wins:
            pre = (tod >= ws - 60) & (close_tod <= ws)
            pre_hi = pd.Series(np.where(pre, h, np.nan)).groupby(days).transform("max").to_numpy()
            pre_lo = pd.Series(np.where(pre, l, np.nan)).groupby(days).transform("min").to_numpy()
            inwin = (tod >= ws) & (close_tod <= we)
            state: dict | None = None
            for i in range(2, len(df)):
                if not inwin[i]:
                    state = None
                    continue
                if state is None or state["day"] != days[i]:
                    state = {"day": days[i], "swept_lo": False, "swept_hi": False, "fvg": None, "done": False}
                if state["done"] or np.isnan(a[i]):
                    continue
                if p["bias"] == "trend":
                    allow_l, allow_s = pclose[i] > tref[i], pclose[i] < tref[i]
                elif p["bias"] == "discount":
                    allow_l, allow_s = c[i] < eq[i], c[i] > eq[i]
                else:
                    allow_l = allow_s = True
                state["swept_lo"] |= l[i] < pre_lo[i]
                state["swept_hi"] |= h[i] > pre_hi[i]

                if state["fvg"] is None:
                    disp = abs(c[i - 1] - o[i - 1]) >= p["disp_atr"] * a[i]
                    ok_l = allow_l and (state["swept_lo"] or not p["require_sweep"])
                    ok_s = allow_s and (state["swept_hi"] or not p["require_sweep"])
                    if ok_l and disp and l[i] > h[i - 2] and c[i - 1] > o[i - 1]:
                        state["fvg"] = (1, h[i - 2], l[i], l[i - 2])      # side, gap_lo, gap_hi, candle-1 extreme
                    elif ok_s and disp and h[i] < l[i - 2] and c[i - 1] < o[i - 1]:
                        state["fvg"] = (-1, h[i], l[i - 2], h[i - 2])
                    continue

                side, glo, ghi, ext = state["fvg"]
                buf = p["stop_buffer_atr"] * a[i]
                if side == 1 and l[i] <= ghi and c[i] >= glo:
                    st, liq = ext - buf, pre_hi[i]
                elif side == -1 and h[i] >= glo and c[i] <= ghi:
                    st, liq = ext + buf, pre_lo[i]
                else:
                    continue
                state["done"] = True
                risk = side * (c[i] - st)
                if risk <= 0:
                    continue
                if p["target"] == "liquidity":
                    if np.isnan(liq) or side * (liq - c[i]) < p["min_rr"] * risk:
                        continue
                    target[i] = liq
                else:
                    rr[i] = float(p["reward_risk"])
                sig[i], stop[i] = side, st

        out["signal"], out["stop"], out["target"], out["rr"] = sig, stop, target, rr
        return self._finalize(out, df["close"])
