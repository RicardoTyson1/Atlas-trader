"""ICT (Inner Circle Trader) models, codified as exact, testable rules.

ICT teaches discretionary concepts; these are ONE mechanical reading of the public
definitions, chosen before testing. Times are New York.

Building blocks
  Trading day        starts 18:00 NY (gold/index CFD daily reopen)
  Liquidity          previous day high/low (PDH/PDL), Asian range (18:00-24:00),
                     London range (02:00-05:00, usable from 05:00), last confirmed
                     fractal swing high/low ("swing", 3 bars each side)
  Sweep              a bar trades beyond a liquidity level and CLOSES back inside
  Displacement FVG   3-bar fair value gap (low[j] > high[j-2] bullish) whose middle
                     candle body is >= disp_atr x ATR
  MSS                market structure shift: after the sweep, a close beyond the last
                     confirmed swing on the other side (bullish: above the last swing high)
  Entry              LIMIT order at the FVG's consequent encroachment (50%, "ce") or its
                     near edge ("edge"); expires after `expire_bars`
  Stop               beyond the sweep extreme (+ buffer x ATR)
  Target             reward_risk x risk ("rr") or the opposing liquidity ("liquidity")
  Bias (optional)    "discount": longs only below the 50% of the last `range_days` days'
                     range, shorts only above. "daily_trend": longs only if yesterday
                     closed above the close `range_days` days earlier.
  Midnight open      Judas swing / power-of-3 filter: longs only if the sweep went below
                     the 00:00 NY open (shorts: above).
  SMT divergence     needs a correlated market in `self.other`: bullish only if THIS
                     market swept its level while the other market did NOT.

Models (presets of the generic engine below)
  silver_bullet      windows 03-04 / 10-11 / 14-15 NY, liquidity = swing, confirm = FVG
  ict2022            London 02-05 + NY 07-10 killzones, liquidity = PDH/PDL + sessions,
                     confirm = MSS + displacement FVG
  judas_po3          ict2022 + midnight-open filter + daily bias
  smt                ict2022 + SMT divergence vs a correlated market
  ote                (separate class) 62-79% retracement of a displacement leg
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes

WINDOWS = {
    "london": (2 * 60, 5 * 60),
    "ny_am": (7 * 60, 10 * 60),
    "sb_london": (3 * 60, 4 * 60),
    "sb_am": (10 * 60, 11 * 60),
    "sb_pm": (14 * 60, 15 * 60),
}


def _context(df: pd.DataFrame, tz: str, range_days: int):
    """Per-bar arrays shared by the ICT models (no look-ahead)."""
    local = df.index.tz_convert(tz)
    tf = _bar_minutes(pd.DataFrame(index=df.index))
    shifted = local + pd.Timedelta(hours=6)
    tday = pd.Series(shifted.date, index=df.index)
    stod = np.asarray(shifted.hour * 60 + shifted.minute)
    tod = np.asarray(local.hour * 60 + local.minute)
    daily = df.groupby(tday.values).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = daily.shift(1)
    rng_hi = daily["high"].rolling(range_days).max().shift(1)
    rng_lo = daily["low"].rolling(range_days).min().shift(1)
    tref = daily["close"].shift(range_days + 1)

    def per_bar(series):
        return tday.map(series).to_numpy(float)

    asian = pd.Series(stod < 6 * 60, index=df.index)
    a = df[asian.values].groupby(tday[asian.values].values).agg(h=("high", "max"), l=("low", "min"))
    lon = pd.Series((tod >= 120) & (tod + tf <= 300), index=df.index)
    lo = df[lon.values].groupby(tday[lon.values].values).agg(h=("high", "max"), l=("low", "min"))
    mid = pd.Series((tod == 0), index=df.index)
    mo = df[mid.values].groupby(tday[mid.values].values)["open"].first()
    return {
        "tday": tday.to_numpy(), "tod": tod, "stod": stod, "tf": tf,
        "pdh": per_bar(prev["high"]), "pdl": per_bar(prev["low"]), "pclose": per_bar(prev["close"]),
        "eq": (per_bar(rng_hi) + per_bar(rng_lo)) / 2, "tref": per_bar(tref),
        "ah": per_bar(a["h"]), "al": per_bar(a["l"]),
        "lh": per_bar(lo["h"]), "ll": per_bar(lo["l"]),
        "mo": per_bar(mo),
    }


def _swings(h: np.ndarray, l: np.ndarray, k: int = 3):
    """Last CONFIRMED fractal swing high/low as known at each bar (confirmed k bars later)."""
    n = len(h)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    last_h = last_l = np.nan
    for i in range(n):
        j = i - k
        if j - k >= 0:
            win_h = h[j - k:j + k + 1]
            win_l = l[j - k:j + k + 1]
            if h[j] == win_h.max():
                last_h = h[j]
            if l[j] == win_l.min():
                last_l = l[j]
        sh[i], sl[i] = last_h, last_l
    return sh, sl


class ICTSetup(Strategy):
    """Generic ICT engine: sweep -> (MSS) -> displacement FVG -> limit entry in the gap."""

    name = "ict_setup"
    uses_session_window = False
    defaults = {
        "windows": ["london", "ny_am"],
        "liquidity": ["pdhl", "asian", "london"],
        "confirm": "mss",            # mss | fvg
        "bias": "none",              # none | discount | daily_trend
        "range_days": 20,
        "mo_filter": False,
        "smt": False,
        "entry": "ce",               # ce | edge
        "target": "rr",              # rr | liquidity
        "reward_risk": 2.0,
        "min_rr": 1.5,
        "disp_atr": 1.0,
        "atr_period": 14,
        "stop_buffer_atr": 0.1,
        "max_wait": 12,              # bars from sweep to confirmation
        "expire_bars": 12,           # how long the limit order rests
        "one_per_day": True,
    }
    other: pd.DataFrame | None = None   # correlated market for SMT

    def _empty_limit(self, index):
        out = self._empty(index)
        out["entry"] = np.nan
        out["expire_bars"] = np.nan
        return out

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty_limit(df.index)
        if len(df) < 100:
            return out
        p = self.p
        cx = _context(df, self.session.timezone, int(p["range_days"]))
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        sh, sl = _swings(h, l)
        tod, tf, tday = cx["tod"], cx["tf"], cx["tday"]
        close_tod = tod + tf
        in_win = np.zeros(len(df), bool)
        for w in p["windows"]:
            s, e = WINDOWS[w]
            in_win |= (tod >= s) & (close_tod <= e)
        other = None
        if p["smt"]:
            if self.other is None:
                return out
            ot = self.other.reindex(df.index)
            ocx = _context(self.other, self.session.timezone, int(p["range_days"]))
            o_map = pd.DataFrame({k: ocx[k] for k in ("pdh", "pdl", "ah", "al", "lh", "ll")}, index=self.other.index)
            o_lvls = o_map.reindex(df.index)
            ohs, ols = _swings(self.other["high"].to_numpy(float), self.other["low"].to_numpy(float))
            o_sw = pd.DataFrame({"sh": ohs, "sl": ols}, index=self.other.index).reindex(df.index)
            other = {"h": ot["high"].to_numpy(float), "l": ot["low"].to_numpy(float),
                     **{k: o_lvls[k].to_numpy(float) for k in o_lvls}, "sh": o_sw["sh"].to_numpy(float),
                     "sl": o_sw["sl"].to_numpy(float)}

        sig = np.zeros(len(df), int)
        stop = np.full(len(df), np.nan)
        target = np.full(len(df), np.nan)
        rr = np.full(len(df), np.nan)
        entry = np.full(len(df), np.nan)
        expire = np.full(len(df), np.nan)
        traded_day = None
        setup = None

        def levels(i, side):
            """(level, opposing level) pairs for the liquidity sources, for a given side."""
            out_l = []
            src = p["liquidity"]
            if "pdhl" in src:
                out_l.append(("pdhl", cx["pdl"][i] if side == 1 else cx["pdh"][i],
                              cx["pdh"][i] if side == 1 else cx["pdl"][i]))
            if "asian" in src and cx["stod"][i] >= 6 * 60:
                out_l.append(("asian", cx["al"][i] if side == 1 else cx["ah"][i],
                              cx["ah"][i] if side == 1 else cx["al"][i]))
            if "london" in src and tod[i] >= 300:
                out_l.append(("london", cx["ll"][i] if side == 1 else cx["lh"][i],
                              cx["lh"][i] if side == 1 else cx["ll"][i]))
            if "swing" in src:
                out_l.append(("swing", sl[i] if side == 1 else sh[i], sh[i] if side == 1 else sl[i]))
            return out_l

        def other_swept(i, side, name):
            key = {"pdhl": ("pdl", "pdh"), "asian": ("al", "ah"), "london": ("ll", "lh"),
                   "swing": ("sl", "sh")}[name]
            lvl = other[key[0]][i] if side == 1 else other[key[1]][i]
            if np.isnan(lvl) or np.isnan(other["l"][i]):
                return True  # unknown -> no divergence
            return other["l"][i] < lvl if side == 1 else other["h"][i] > lvl

        for i in range(3, len(df)):
            d = tday[i]
            if setup is not None and setup["day"] != d:
                setup = None
            if p["one_per_day"] and traded_day == d:
                continue
            ai = a[i]
            if np.isnan(ai):
                continue
            if p["bias"] == "discount":
                allow = {1: c[i] < cx["eq"][i], -1: c[i] > cx["eq"][i]}
            elif p["bias"] == "daily_trend":
                allow = {1: cx["pclose"][i] > cx["tref"][i], -1: cx["pclose"][i] < cx["tref"][i]}
            else:
                allow = {1: True, -1: True}

            # 1) sweep inside a window
            if setup is None:
                if not in_win[i]:
                    continue
                for side in (1, -1):
                    if not allow[side] or setup is not None:
                        continue
                    for name, lvl, opp in levels(i, side):
                        if np.isnan(lvl):
                            continue
                        swept = (l[i] < lvl < c[i]) if side == 1 else (h[i] > lvl > c[i])
                        if not swept:
                            continue
                        ext = l[i] if side == 1 else h[i]
                        if p["mo_filter"]:
                            mo = cx["mo"][i]
                            if np.isnan(mo) or (side == 1 and not ext < mo) or (side == -1 and not ext > mo):
                                continue
                        if p["smt"] and other_swept(i, side, name):
                            continue
                        ref = sh[i] if side == 1 else sl[i]   # structure to break for MSS
                        setup = {"day": d, "side": side, "i": i, "ext": ext, "opp": opp,
                                 "ref": ref, "stage": "sweep", "fvg": None}
                        break
                continue

            side = setup["side"]
            if setup["stage"] == "sweep":
                setup["ext"] = min(setup["ext"], l[i]) if side == 1 else max(setup["ext"], h[i])
                if i - setup["i"] > p["max_wait"]:
                    setup = None
                    continue
                body_ok = abs(c[i - 1] - o[i - 1]) >= p["disp_atr"] * ai
                if side == 1 and l[i] > h[i - 2] and c[i - 1] > o[i - 1] and body_ok:
                    setup["fvg"] = (h[i - 2], l[i])
                elif side == -1 and h[i] < l[i - 2] and c[i - 1] < o[i - 1] and body_ok:
                    setup["fvg"] = (h[i], l[i - 2])
                if setup["fvg"] is None:
                    continue
                if p["confirm"] == "mss":
                    ref = setup["ref"]
                    if np.isnan(ref) or not ((side == 1 and c[i] > ref) or (side == -1 and c[i] < ref)):
                        continue  # keep waiting for the structure break (latest FVG kept)
                lo, hi = setup["fvg"]
                lim = (lo + hi) / 2 if p["entry"] == "ce" else (hi if side == 1 else lo)
                st = setup["ext"] - side * p["stop_buffer_atr"] * ai
                risk = abs(lim - st)
                if risk <= 0 or side * (lim - st) <= 0:
                    setup = None
                    continue
                if p["target"] == "liquidity":
                    tgt = setup["opp"]
                    if np.isnan(tgt) or side * (tgt - lim) < p["min_rr"] * risk:
                        setup = None
                        continue
                    target[i] = tgt
                else:
                    rr[i] = float(p["reward_risk"])
                sig[i], stop[i], entry[i], expire[i] = side, st, lim, p["expire_bars"]
                traded_day = d
                setup = None

        out["signal"], out["stop"], out["target"], out["rr"] = sig, stop, target, rr
        out["entry"], out["expire_bars"] = entry, expire
        out = self._finalize(out, df["close"])
        out.loc[out["signal"] == 0, ["entry", "expire_bars"]] = np.nan
        return out


class SilverBullet(ICTSetup):
    name = "silver_bullet"
    defaults = {**ICTSetup.defaults, "windows": ["sb_london", "sb_am", "sb_pm"],
                "liquidity": ["swing"], "confirm": "fvg", "expire_bars": 6}


class ICT2022(ICTSetup):
    name = "ict2022"


class JudasPO3(ICTSetup):
    name = "judas_po3"
    defaults = {**ICTSetup.defaults, "mo_filter": True, "bias": "daily_trend"}


class SMTDivergence(ICTSetup):
    name = "smt"
    defaults = {**ICTSetup.defaults, "smt": True}


class OTE(Strategy):
    """Optimal Trade Entry: after a displacement leg that breaks structure, buy the
    62-79% retracement (limit at `level`), stop beyond the leg's origin, target the leg
    extreme or an extension. Leg = last confirmed swing low -> new high that closes above
    the previous swing high with range >= leg_atr x ATR (mirror for shorts)."""

    name = "ote"
    uses_session_window = False
    defaults = {
        "level": 0.705, "leg_atr": 3.0, "atr_period": 14, "stop_buffer_atr": 0.1,
        "target": "extreme",   # extreme | ext27 | rr
        "reward_risk": 2.0, "expire_bars": 12, "windows": ["london", "ny_am"], "one_per_day": True,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out["entry"] = np.nan
        out["expire_bars"] = np.nan
        if len(df) < 100:
            return out
        p = self.p
        cx = _context(df, self.session.timezone, 20)
        h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        sh, sl = _swings(h, l)
        tod, tf, tday = cx["tod"], cx["tf"], cx["tday"]
        in_win = np.zeros(len(df), bool)
        for w in p["windows"]:
            s, e = WINDOWS[w]
            in_win |= (tod >= s) & (tod + tf <= e)
        sig = np.zeros(len(df), int)
        stop = np.full(len(df), np.nan)
        target = np.full(len(df), np.nan)
        rr = np.full(len(df), np.nan)
        entry = np.full(len(df), np.nan)
        expire = np.full(len(df), np.nan)
        traded = None
        for i in range(1, len(df)):
            if not in_win[i] or np.isnan(a[i]) or (p["one_per_day"] and traded == tday[i]):
                continue
            for side in (1, -1):
                prev_ref = sh[i - 1] if side == 1 else sl[i - 1]
                origin = sl[i] if side == 1 else sh[i]
                if np.isnan(prev_ref) or np.isnan(origin):
                    continue
                broke = (c[i] > prev_ref and c[i - 1] <= prev_ref) if side == 1 else \
                        (c[i] < prev_ref and c[i - 1] >= prev_ref)
                if not broke:
                    continue
                ext = h[i] if side == 1 else l[i]
                rng = abs(ext - origin)
                if rng < p["leg_atr"] * a[i] or side * (ext - origin) <= 0:
                    continue
                lim = ext - side * p["level"] * rng
                st = origin - side * p["stop_buffer_atr"] * a[i]
                if p["target"] == "extreme":
                    target[i] = ext
                elif p["target"] == "ext27":
                    target[i] = ext + side * 0.27 * rng
                else:
                    rr[i] = float(p["reward_risk"])
                sig[i], stop[i], entry[i], expire[i] = side, st, lim, p["expire_bars"]
                traded = tday[i]
                break
        out["signal"], out["stop"], out["target"], out["rr"] = sig, stop, target, rr
        out["entry"], out["expire_bars"] = entry, expire
        out = self._finalize(out, df["close"])
        out.loc[out["signal"] == 0, ["entry", "expire_bars"]] = np.nan
        return out
