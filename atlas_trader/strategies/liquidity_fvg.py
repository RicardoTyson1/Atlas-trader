"""Liquidity sweep -> fair value gap (FVG) retrace, with a daily bias filter.

Codifies a common smart-money gold model in exact, testable rules:

  Trading day: starts 18:00 New York (gold's daily reopen).
  BIAS (one of):
    discount : dealing range = high/low of the last `range_days` completed days.
               Longs only below its 50% level (discount), shorts only above (premium).
    trend    : longs only if the last completed day closed above the close
               `range_days` days earlier; shorts only if below.
    none     : both directions.
  LIQUIDITY: during a killzone (London 02:00-05:00, New York 07:00-10:00 NY time),
    a bar trades through a liquidity level and CLOSES back inside:
    - previous day's high/low ("pdhl"), and/or
    - Asian range high/low, 18:00-24:00 NY ("asian").
    A sweep of a low is a bullish setup; a sweep of a high is bearish.
  DISPLACEMENT + FVG: within `max_wait` bars after the sweep, a bullish 3-bar FVG
    forms (low[j] > high[j-2]) and the middle bar's body is >= `disp_atr` x ATR.
  ENTRY: when price retraces into the FVG (bar low <= top of gap) and closes
    above the bottom of the gap -> buy at the next bar's open.
  STOP: beyond the sweep extreme (+ `stop_buffer_atr` x ATR).
  TARGET: `reward_risk` x risk, or the opposite liquidity level ("liquidity").
  One trade per day. Everything is mirrored for shorts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes

KILLZONES = {"london": (2 * 60, 5 * 60), "ny": (7 * 60, 10 * 60)}


class LiquidityFVG(Strategy):
    name = "liquidity_fvg"
    uses_session_window = False
    defaults = {
        "bias": "discount",          # discount | trend | none
        "range_days": 20,
        "liquidity": "both",         # pdhl | asian | both
        "killzones": "both",         # london | ny | both
        "max_wait": 12,              # bars after the sweep to form the FVG
        "entry_deadline_bars": 24,   # bars after the FVG to get the retrace
        "disp_atr": 1.0,
        "atr_period": 14,
        "stop_buffer_atr": 0.1,
        "target": "rr",              # rr | liquidity
        "reward_risk": 2.0,
        "min_rr": 1.5,               # when target=liquidity, skip if reward < this x risk
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        if len(df) < 50:
            return out
        p = self.p
        tz = self.session.timezone
        local = df.index.tz_convert(tz)
        tf = _bar_minutes(pd.DataFrame(index=df.index))
        # trading day starting 18:00 NY
        shifted = local + pd.Timedelta(hours=6)
        tday = pd.Series(shifted.date, index=df.index)
        stod = shifted.hour * 60 + shifted.minute          # minutes since 18:00 NY
        tod = local.hour * 60 + local.minute
        close_tod = tod + tf

        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()

        daily = df.groupby(tday.values).agg(high=("high", "max"), low=("low", "min"),
                                            open=("open", "first"), close=("close", "last"))
        n_rd = int(p["range_days"])
        prev = daily.shift(1)
        rng_hi = daily["high"].rolling(n_rd).max().shift(1)
        rng_lo = daily["low"].rolling(n_rd).min().shift(1)
        trend_ref = daily["close"].shift(n_rd + 1)
        asian_mask = pd.Series(stod < 6 * 60, index=df.index)
        asian = df[asian_mask.values].groupby(tday[asian_mask.values].values).agg(ah=("high", "max"), al=("low", "min"))

        def per_bar(series):
            return tday.map(series).to_numpy(float)

        pdh, pdl = per_bar(prev["high"]), per_bar(prev["low"])
        pclose = per_bar(prev["close"])
        eq = (per_bar(rng_hi) + per_bar(rng_lo)) / 2
        tref = per_bar(trend_ref)
        ah, al = per_bar(asian["ah"]), per_bar(asian["al"])

        kz_names = ["london", "ny"] if p["killzones"] == "both" else [p["killzones"]]
        in_kz = np.zeros(len(df), bool)
        for k in kz_names:
            s, e = KILLZONES[k]
            in_kz |= (tod >= s) & (close_tod <= e)
        asian_done = stod >= 6 * 60

        sig = np.zeros(len(df), int)
        stop = np.full(len(df), np.nan)
        target = np.full(len(df), np.nan)
        rr = np.full(len(df), np.nan)

        traded_day = None
        setup = None  # dict(side, extreme, sweep_i, fvg=(lo, hi), fvg_i, opp)
        days_arr = tday.to_numpy()
        for i in range(2, len(df)):
            d = days_arr[i]
            if setup is not None and setup["day"] != d:
                setup = None
            if traded_day == d:
                continue
            ai = a[i]
            if np.isnan(ai):
                continue

            # bias for today
            if p["bias"] == "discount":
                allow_long, allow_short = c[i] < eq[i], c[i] > eq[i]
            elif p["bias"] == "trend":
                allow_long, allow_short = pclose[i] > tref[i], pclose[i] < tref[i]
            else:
                allow_long = allow_short = True

            # 1) look for a sweep (only in killzone, only if no live setup)
            if setup is None and in_kz[i]:
                lows, highs = [], []
                if p["liquidity"] in ("pdhl", "both"):
                    lows.append((pdl[i], pdh[i])); highs.append((pdh[i], pdl[i]))
                if p["liquidity"] in ("asian", "both") and asian_done[i]:
                    lows.append((al[i], ah[i])); highs.append((ah[i], al[i]))
                for lvl, opp in lows:
                    if allow_long and not np.isnan(lvl) and l[i] < lvl < c[i]:
                        setup = {"day": d, "side": 1, "extreme": l[i], "sweep_i": i, "fvg": None, "opp": opp}
                        break
                if setup is None:
                    for lvl, opp in highs:
                        if allow_short and not np.isnan(lvl) and h[i] > lvl > c[i]:
                            setup = {"day": d, "side": -1, "extreme": h[i], "sweep_i": i, "fvg": None, "opp": opp}
                            break
                continue

            if setup is None:
                continue
            side = setup["side"]
            # keep the sweep extreme updated until the FVG forms
            if setup["fvg"] is None:
                if side == 1:
                    setup["extreme"] = min(setup["extreme"], l[i])
                else:
                    setup["extreme"] = max(setup["extreme"], h[i])
                if i - setup["sweep_i"] > int(p["max_wait"]):
                    setup = None
                    continue
                body = abs(c[i - 1] - o[i - 1])
                disp_ok = body >= p["disp_atr"] * ai
                if side == 1 and l[i] > h[i - 2] and c[i - 1] > o[i - 1] and disp_ok:
                    setup["fvg"], setup["fvg_i"] = (h[i - 2], l[i]), i
                elif side == -1 and h[i] < l[i - 2] and c[i - 1] < o[i - 1] and disp_ok:
                    setup["fvg"], setup["fvg_i"] = (h[i], l[i - 2]), i
                continue

            # 2) wait for the retrace into the FVG
            if i - setup["fvg_i"] > int(p["entry_deadline_bars"]):
                setup = None
                continue
            gap_lo, gap_hi = setup["fvg"]
            buf = p["stop_buffer_atr"] * ai
            if side == 1 and l[i] <= gap_hi and c[i] >= gap_lo:
                st = setup["extreme"] - buf
            elif side == -1 and h[i] >= gap_lo and c[i] <= gap_hi:
                st = setup["extreme"] + buf
            else:
                continue
            risk = abs(c[i] - st)
            if risk <= 0 or side * (c[i] - st) <= 0:
                setup = None
                continue
            if p["target"] == "liquidity":
                tgt = setup["opp"]
                if np.isnan(tgt) or side * (tgt - c[i]) < p["min_rr"] * risk:
                    setup = None
                    continue
                target[i] = tgt
            else:
                rr[i] = float(p["reward_risk"])
            sig[i], stop[i] = side, st
            traded_day = d
            setup = None

        out["signal"] = sig
        out["stop"] = stop
        out["target"] = target
        out["rr"] = rr
        return self._finalize(out, df["close"])
