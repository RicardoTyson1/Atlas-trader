"""Ricardo's supply / demand zone retest (his chart of 2026-11-05).

Price bases in a tight box, then leaves it with a big displacement candle (a sell-off for a
supply zone, a rally for a demand zone). When price later comes back into the box, the box is
resistance (supply) / support (demand): a rejection or reversal candle there is the entry.

  BASE        : `base_min`..`base_max` consecutive candles on the `zone_tf` chart whose whole range
                (highest high - lowest low) is <= `base_atr` x ATR (a tight consolidation).
  DISPLACEMENT: the next candle closes beyond the base with a body >= `disp_atr` x ATR.
                Down -> SUPPLY zone (sell setups); up -> DEMAND zone (buy setups).
                Zone = the base's low..high.
  FRESH       : only the FIRST return to the zone counts, within `max_age_h` hours, and only
                after price has been away for `min_away` entry-chart candles. A close through the
                far side of the zone by `fail_atr` x ATR kills it.
  ENTRY       : an entry-chart candle (this strategy's timeframe, normally 5m) trades into the
                zone and is rejected: it closes in the trade's direction, in the far half of its
                range, and back outside the zone's far edge or at least below (above) its middle.
                Next bar's open.
  STOP        : beyond the zone (or the rejection wick, whichever is further) + `stop_buffer_atr`
                x ATR; skipped if the risk is under `min_stop_atr` or over `max_stop_atr` x ATR.
  EXIT        : `target: trail` = candle trailing stop from `trail_start_r`, big candle in our
                favor pulls the stop to mid-candle; `target: rr` = `reward_risk` x risk.
Only completed candles are used (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy


class ZoneRetest(Strategy):
    name = "zone_retest"
    uses_session_window = False
    defaults = {
        "zone_tf": "5min",
        "base_min": 3,
        "base_max": 12,
        "base_atr": 1.5,
        "disp_atr": 2.0,
        "max_age_h": 72.0,
        "min_away": 3,
        "fail_atr": 0.3,
        "atr_period": 14,
        "stop_buffer_atr": 0.3,
        "min_stop_atr": 0.5,
        "max_stop_atr": 5.0,
        "target": "trail",          # trail | rr
        "reward_risk": 2.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "spike_atr": 2.0,
        "spike_min_r": 1.0,
        "spike_mode": 2,
        "hours": "00:00-23:59",
    }

    def _zones(self, z: pd.DataFrame) -> list[dict]:
        """All supply / demand zones on the zone chart, with the time they became known."""
        p = self.p
        o, h, l, c = (z[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(z, int(p["atr_period"])).to_numpy()
        step = pd.Series(z.index).diff().dropna().mode().iloc[0]
        end = (z.index + step).as_unit("ns").asi8
        out = []
        bmin, bmax = int(p["base_min"]), int(p["base_max"])
        for d in range(bmin, len(z)):
            ad = a[d - 1]
            if np.isnan(ad) or ad <= 0:
                continue
            body = c[d] - o[d]
            if abs(body) < p["disp_atr"] * ad:
                continue
            # the longest tight base right before the displacement candle
            best = None
            for nb in range(bmin, min(bmax, d) + 1):
                hi, lo = h[d - nb:d].max(), l[d - nb:d].min()
                if hi - lo <= p["base_atr"] * ad:
                    best = (hi, lo)
                else:
                    break
            if best is None:
                continue
            hi, lo = best
            if body < 0 and c[d] < lo:
                out.append({"side": -1, "hi": hi, "lo": lo, "known": end[d], "atr": ad})
            elif body > 0 and c[d] > hi:
                out.append({"side": 1, "hi": hi, "lo": lo, "known": end[d], "atr": ad})
        return out

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        for k in ("trail_start_r", "trail_buf", "spike_pts", "spike_min_r", "spike_mode"):
            out[k] = np.nan
        if len(df) < 100:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        n = len(df)
        step = pd.Series(df.index).diff().dropna().mode().iloc[0]
        bar_end = (df.index + step).as_unit("ns").asi8
        zdf = df if p["zone_tf"] in (None, "", "same") else df.resample(
            p["zone_tf"], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        zones = self._zones(zdf) if len(zdf) >= 30 else []
        zones.sort(key=lambda zz: zz["known"])
        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        in_hours = np.zeros(n, bool)
        for rng_ in str(p["hours"]).split(","):
            s_, e_ = rng_.strip().split("-")
            in_hours |= (tod >= int(s_[:2]) * 60 + int(s_[3:])) & (tod <= int(e_[:2]) * 60 + int(e_[3:]))

        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        active: list[dict] = []
        zi = 0
        max_age = int(p["max_age_h"] * 3600 * 1e9)
        for i in range(n):
            while zi < len(zones) and zones[zi]["known"] <= bar_end[i]:
                zz = dict(zones[zi])
                zz["away"] = 0
                zz["start_i"] = i
                active.append(zz)
                zi += 1
            if not active or np.isnan(a[i]):
                continue
            keep = []
            for zz in active:
                if bar_end[i] - zz["known"] > max_age:
                    continue
                side, hi, lo = zz["side"], zz["hi"], zz["lo"]
                if i == zz["start_i"]:
                    keep.append(zz)
                    continue
                # killed if price closes through the far side of the zone
                if (side == -1 and c[i] > hi + p["fail_atr"] * zz["atr"]) or \
                   (side == 1 and c[i] < lo - p["fail_atr"] * zz["atr"]):
                    continue
                inside = (h[i] >= lo) if side == -1 else (l[i] <= hi)
                if not inside:
                    zz["away"] += 1
                    keep.append(zz)
                    continue
                if zz["away"] < int(p["min_away"]):
                    keep.append(zz)          # still leaving the zone: not a return yet
                    continue
                # FIRST return: rejection or nothing (zone used up either way)
                rng = h[i] - l[i]
                mid = (hi + lo) / 2
                rejected = rng > 0 and side * (c[i] - o[i]) > 0 and (
                    (c[i] - l[i]) <= 0.5 * rng and c[i] < mid if side == -1 else
                    (h[i] - c[i]) <= 0.5 * rng and c[i] > mid)
                if rejected and in_hours[i] and sig[i] == 0:
                    edge = max(hi, h[i]) if side == -1 else min(lo, l[i])
                    st = edge - side * p["stop_buffer_atr"] * a[i]
                    risk = side * (c[i] - st)
                    if p["min_stop_atr"] * a[i] <= risk <= p["max_stop_atr"] * a[i]:
                        sig[i], stop[i] = side, st
            active = keep

        out["signal"], out["stop"] = sig, stop
        if p["target"] == "rr":
            out["rr"] = np.where(sig != 0, float(p["reward_risk"]), np.nan)
        else:
            out["rr"] = np.where(sig != 0, 1000.0, np.nan)
        out = self._finalize(out, df["close"])
        on = out["signal"] != 0
        out["trail_start_r"] = np.where(on, float(p["trail_start_r"]), np.nan)
        out["trail_buf"] = np.where(on, p["trail_buf_atr"] * a, np.nan)
        out["spike_pts"] = np.where(on & (p["spike_atr"] > 0), p["spike_atr"] * a, np.nan)
        out["spike_min_r"] = np.where(on, float(p["spike_min_r"]), np.nan)
        out["spike_mode"] = np.where(on, float(p["spike_mode"]), np.nan)
        return out
