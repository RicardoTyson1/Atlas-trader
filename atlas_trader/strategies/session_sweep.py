"""Session sweep reversal (Ricardo, 2026-11-13).

  LEVELS : high / low of the last COMPLETED reference session (`ref_session`, NY time, e.g. the NY
           day "08:00-17:00" for evening entries, or overnight "18:00-24:00,00:00-08:00" for the morning).
  SWEEP  : price trades above that high (or below that low). Each level can be swept once.
  BIAS   : higher-timeframe direction from COMPLETED candles: close vs `bias_ema` EMA on 1H, 4H, or
           both agreeing (`bias` = "1h" | "4h" | "both" | "none"). A high sweep is only faded (short)
           when the bias is down; a low sweep (long) only when the bias is up.
  ENTRY  : within `max_wait` candles of the sweep, on this strategy's own candles (5m or 15m):
             confirm = "close_inside": a candle closes back inside the range (below the swept high);
             confirm = "choch"       : a candle closes beyond the last swing before the sweep extreme
                                       (lowest low of the `swing_bars` candles before it, for a short).
           Entry next candle's open, inside `hours`.
  STOP   : beyond the sweep extreme + `stop_buffer_atr` x ATR (skipped if wider than `max_stop_atr` x ATR).
  EXIT   : no fixed target; candle trailing from `trail_start_r` (partial / breakeven come from the
           strategy's `partial` setting in the config).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema
from ._common import exit_columns, hours_mask
from .base import Strategy, _bar_minutes


def _htf_bias(df: pd.DataFrame, rule: str, n_ema: int) -> np.ndarray:
    """+1 / -1 / 0 from the last completed `rule` candle's close vs its EMA (no look-ahead)."""
    htf = df["close"].resample(rule, label="left", closed="left").last().dropna()
    b = np.sign(htf - ema(htf, n_ema))
    avail = pd.Series(b.to_numpy(), index=htf.index + pd.Timedelta(rule))
    step = pd.Timedelta(minutes=_bar_minutes(pd.DataFrame(index=df.index)))
    return avail.reindex(df.index + step, method="ffill").fillna(0).to_numpy()


class SessionSweep(Strategy):
    name = "session_sweep"
    uses_session_window = False
    defaults = {
        "ref_session": "08:00-17:00",
        "bias": "both",
        "bias_ema": 50,
        "confirm": "close_inside",
        "max_wait": 6,
        "swing_bars": 6,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 4.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "hours": "20:00-24:00",
        "mode": "reversal",          # "retest": the sweep FAILS (a candle closes beyond the level) ->
        "retest_wait": 24,           #   wait up to this many candles for price to come back to the
        "retest_tol_atr": 0.3,       #   level (within tol x ATR) and close on the breakout side ->
                                     #   enter WITH the breakout, if the 1H/4H trend agrees
        # --- refinements (all OFF by default = the original rules), research/sweep_improve_study.py
        "extra_levels": "",          # more levels, "+"-joined: "prevday" (previous gold day, 18:00
                                     #   roll) and/or session windows like "02:00-08:00"
        "break_body_atr": 0.0,       # retest: the break candle's body must be >= this x ATR
        "break_close_atr": 0.0,      # retest: the break candle must close >= this x ATR past the level
        "first_touch_only": False,   # retest: the FIRST touch of the level decides (no second chances)
        "wick_frac": 0.0,            # retest: the retest candle's wick into the level >= this x its range
        "stop_mode": "candle",       # retest: "candle" = past the retest candle / level, "level" = just
    }                                #   past the level (tighter: more R per point)

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 200:
            return out
        p = self.p
        if p["mode"] == "retest":
            return self._retest(df, out)
        if p["confirm"] not in ("close_inside", "choch") or p["bias"] not in ("1h", "4h", "both", "none"):
            raise ValueError("session_sweep: confirm must be close_inside|choch, bias 1h|4h|both|none")
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        n = len(df)
        if p["bias"] == "none":
            bias = np.full(n, np.nan)       # NaN: both directions allowed
        else:
            b1 = _htf_bias(df, "1h", int(p["bias_ema"])) if p["bias"] in ("1h", "both") else None
            b4 = _htf_bias(df, "4h", int(p["bias_ema"])) if p["bias"] in ("4h", "both") else None
            if p["bias"] == "1h":
                bias = b1
            elif p["bias"] == "4h":
                bias = b4
            else:
                bias = np.where(b1 == b4, b1, 0)
        in_ref = hours_mask(df.index, self.session.timezone, p["ref_session"])
        ok_h = hours_mask(df.index, self.session.timezone, p["hours"])
        gap = np.timedelta64(2, "h")   # a data gap (weekend) also ends a reference session
        dt = df.index.to_series().diff().fillna(pd.Timedelta(0)).to_numpy()

        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        run_hi = run_lo = np.nan            # reference session being built
        lvl_hi = lvl_lo = np.nan            # last completed session's levels
        used_hi = used_lo = True
        st = None                           # pending sweep: dict(side, ext, i, swing)
        sb = int(p["swing_bars"])
        for i in range(n):
            # --- reference session bookkeeping (levels only change when a session completes)
            if in_ref[i]:
                if np.isnan(run_hi) or dt[i] > gap:
                    run_hi, run_lo = h[i], l[i]
                else:
                    run_hi, run_lo = max(run_hi, h[i]), min(run_lo, l[i])
            elif not np.isnan(run_hi):
                lvl_hi, lvl_lo = run_hi, run_lo
                used_hi = used_lo = False
                run_hi = run_lo = np.nan
                st = None
            ai = a[i]
            if np.isnan(ai) or np.isnan(lvl_hi) or in_ref[i]:
                continue
            # --- new sweep
            if st is None:
                if not used_hi and h[i] > lvl_hi and not bias[i] >= 0:
                    used_hi = True
                    st = {"side": -1, "ext": h[i], "i": i, "lvl": lvl_hi,
                          "swing": l[max(0, i - sb):i].min() if i > 0 else l[i]}
                elif not used_lo and l[i] < lvl_lo and not bias[i] <= 0:
                    used_lo = True
                    st = {"side": 1, "ext": l[i], "i": i, "lvl": lvl_lo,
                          "swing": h[max(0, i - sb):i].max() if i > 0 else h[i]}
                else:
                    if not used_hi and h[i] > lvl_hi:
                        used_hi = True      # swept against the bias: level spent
                    if not used_lo and l[i] < lvl_lo:
                        used_lo = True
                    continue
            s = st["side"]
            # extend the sweep extreme (and the swing before it) while price keeps pushing
            if (s == -1 and h[i] > st["ext"]) or (s == 1 and l[i] < st["ext"]):
                st["ext"] = h[i] if s == -1 else l[i]
                lo_ = max(0, i - sb)
                st["swing"] = l[lo_:i].min() if s == -1 else h[lo_:i].max()
            if i - st["i"] > int(p["max_wait"]):
                st = None
                continue
            if p["confirm"] == "close_inside":
                ok = c[i] < st["lvl"] if s == -1 else c[i] > st["lvl"]
            else:
                ok = c[i] < st["swing"] if s == -1 else c[i] > st["swing"]
            if not ok:
                continue
            stp = st["ext"] - s * p["stop_buffer_atr"] * ai
            risk = s * (c[i] - stp)
            if ok_h[i] and 0 < risk <= p["max_stop_atr"] * ai:
                sig[i], stop[i] = s, stp
            st = None
        for hi_, lo_ in self._extra_level_arrays(df):
            sig2, stop2 = self._reversal_on(df, hi_, lo_, bias, ok_h, a)
            add = (sig == 0) & (sig2 != 0)
            sig[add], stop[add] = sig2[add], stop2[add]
        out["signal"], out["stop"] = sig, stop
        out["rr"] = np.where(sig != 0, 1000.0, np.nan)      # trail only
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)


    def _extra_level_arrays(self, df: pd.DataFrame) -> list:
        return [level_arrays(df, self.session.timezone, spec)
                for spec in str(self.p["extra_levels"]).split("+") if spec.strip()]

    def _reversal_on(self, df, lvl_hi, lvl_lo, bias, ok_h, a):
        """The reversal rules on per-bar level arrays (levels change -> both sides re-armed)."""
        p = self.p
        h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        n = len(df)
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        sb = int(p["swing_bars"])
        used_hi = used_lo = True
        prev = (np.nan, np.nan)
        st = None
        for i in range(n):
            cur = (lvl_hi[i], lvl_lo[i])
            if np.isnan(cur[0]):
                st, prev = None, cur
                continue
            if cur != prev:
                used_hi = used_lo = False
                st, prev = None, cur
            if np.isnan(a[i]):
                continue
            if st is None:
                if not used_hi and h[i] > cur[0] and not bias[i] >= 0:
                    used_hi = True
                    st = {"side": -1, "ext": h[i], "i": i, "lvl": cur[0], "swing": l[max(0, i - sb):i].min() if i else l[i]}
                elif not used_lo and l[i] < cur[1] and not bias[i] <= 0:
                    used_lo = True
                    st = {"side": 1, "ext": l[i], "i": i, "lvl": cur[1], "swing": h[max(0, i - sb):i].max() if i else h[i]}
                else:
                    used_hi = used_hi or h[i] > cur[0]
                    used_lo = used_lo or l[i] < cur[1]
                    continue
            s = st["side"]
            if (s == -1 and h[i] > st["ext"]) or (s == 1 and l[i] < st["ext"]):
                st["ext"] = h[i] if s == -1 else l[i]
                st["swing"] = l[max(0, i - sb):i].min() if s == -1 else h[max(0, i - sb):i].max()
            if i - st["i"] > int(p["max_wait"]):
                st = None
                continue
            ref = st["lvl"] if p["confirm"] == "close_inside" else st["swing"]
            if not (c[i] < ref if s == -1 else c[i] > ref):
                continue
            stp = st["ext"] - s * p["stop_buffer_atr"] * a[i]
            risk = s * (c[i] - stp)
            if ok_h[i] and 0 < risk <= p["max_stop_atr"] * a[i]:
                sig[i], stop[i] = s, stp
            st = None
        return sig, stop

    def _retest(self, df: pd.DataFrame, out: pd.DataFrame) -> pd.DataFrame:
        """Break of the session high/low that HOLDS: the broken level is retested as new
        support (long) / resistance (short) and price closes back on the breakout side."""
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        n = len(df)
        ctx = sweep_context(df, self.session.timezone, p["ref_session"], p["bias"], int(p["bias_ema"]))
        bias = ctx["bias"]
        ok_h = hours_mask(df.index, self.session.timezone, p["hours"])
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        for lvl_hi, lvl_lo in [(ctx["lvl_hi"], ctx["lvl_lo"])] + self._extra_level_arrays(df):
            s2, st2 = self._retest_on(o, h, l, c, a, lvl_hi, lvl_lo, bias, ok_h)
            add = (sig == 0) & (s2 != 0)
            sig[add], stop[add] = s2[add], st2[add]
        out["signal"], out["stop"] = sig, stop
        out["rr"] = np.where(sig != 0, 1000.0, np.nan)
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)


    def _retest_on(self, o, h, l, c, a, lvl_hi, lvl_lo, bias, ok_h):
        p = self.p
        n = len(c)
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        used = set()                     # (level value, side) already traded or spent
        st = None                        # dict(side, lvl, i_break)
        for i in range(1, n):
            ai = a[i]
            if np.isnan(ai) or np.isnan(lvl_hi[i]):
                st = None
                continue
            if st is not None and st["lvl"] not in (lvl_hi[i], lvl_lo[i]):
                st = None                # new levels replaced these
            if st is None:
                for side, lvl in ((1, lvl_hi[i]), (-1, lvl_lo[i])):
                    if (lvl, side) in used:
                        continue
                    if side * (c[i] - lvl) > 0:              # closed beyond the level: a break
                        used.add((lvl, side))
                        strong = (side * (c[i] - o[i]) >= p["break_body_atr"] * ai
                                  and side * (c[i] - lvl) >= p["break_close_atr"] * ai)
                        if strong and (p["bias"] == "none" or side * bias[i] > 0):
                            st = {"side": side, "lvl": lvl, "i": i}
                        break
                continue
            s, lvl = st["side"], st["lvl"]
            if i - st["i"] > int(p["retest_wait"]):
                st = None
                continue
            tol = p["retest_tol_atr"] * ai
            if s * (c[i] - lvl) < -tol:                     # closed back through: the break failed
                st = None
                continue
            touched = (l[i] <= lvl + tol) if s == 1 else (h[i] >= lvl - tol)
            if not touched:
                continue
            rng = h[i] - l[i]
            wick = (min(o[i], c[i]) - l[i]) if s == 1 else (h[i] - max(o[i], c[i]))
            good = (s * (c[i] - lvl) > 0 and s * (c[i] - o[i]) > 0
                    and (p["wick_frac"] <= 0 or (rng > 0 and wick >= p["wick_frac"] * rng)))
            if good:
                if p["stop_mode"] == "level":
                    stp = lvl - s * p["stop_buffer_atr"] * ai
                else:
                    ext = min(l[i], lvl) if s == 1 else max(h[i], lvl)
                    stp = ext - s * p["stop_buffer_atr"] * ai
                risk = s * (c[i] - stp)
                if ok_h[i] and 0 < risk <= p["max_stop_atr"] * ai:
                    sig[i], stop[i] = s, stp
                st = None
            elif p["first_touch_only"]:
                st = None
        return sig, stop


def level_arrays(df: pd.DataFrame, tz: str, spec: str):
    """(high, low) per bar for one level source: "prevday" = previous gold trading day (the day
    rolls at 18:00 NY), else a session window -> its last completed run (sweep_context)."""
    spec = spec.strip()
    if spec == "prevday":
        day = (df.index.tz_convert(tz) + pd.Timedelta(hours=6)).date
        g = pd.DataFrame({"h": df["high"].to_numpy(), "l": df["low"].to_numpy(), "d": day})
        daily = g.groupby("d").agg(h=("h", "max"), l=("l", "min"))
        prev = daily.shift(1)
        return prev["h"].reindex(day).to_numpy(float), prev["l"].reindex(day).to_numpy(float)
    ctx = sweep_context(df, tz, spec, "none")
    return ctx["lvl_hi"], ctx["lvl_lo"]


def sweep_context(df: pd.DataFrame, tz: str, ref_session: str, bias: str = "both", bias_ema: int = 50) -> dict:
    """Per-bar context for gating OTHER strategies with the session-sweep idea (no look-ahead).

    lvl_hi / lvl_lo : last COMPLETED reference session's high / low (NaN until one completes)
    swept_hi / lo   : that high / low has been traded through since the session completed
    bias            : +1 / -1 / 0 from the 1H / 4H / both-agree EMA trend (NaN = "none")
    """
    n = len(df)
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    in_ref = hours_mask(df.index, tz, ref_session)
    dt = df.index.to_series().diff().fillna(pd.Timedelta(0)).to_numpy()
    gap = np.timedelta64(2, "h")
    lvl_hi = np.full(n, np.nan)
    lvl_lo = np.full(n, np.nan)
    s_hi = np.zeros(n, bool)
    s_lo = np.zeros(n, bool)
    run_hi = run_lo = cur_hi = cur_lo = np.nan
    hit_hi = hit_lo = False
    for i in range(n):
        if in_ref[i]:
            if np.isnan(run_hi) or dt[i] > gap:
                run_hi, run_lo = h[i], l[i]
            else:
                run_hi, run_lo = max(run_hi, h[i]), min(run_lo, l[i])
            cur_hi = cur_lo = np.nan                    # levels are refreshed when this run ends
            hit_hi = hit_lo = False
        else:
            if not np.isnan(run_hi):
                cur_hi, cur_lo = run_hi, run_lo
                run_hi = run_lo = np.nan
                hit_hi = hit_lo = False
            if not np.isnan(cur_hi):
                hit_hi = hit_hi or h[i] > cur_hi
                hit_lo = hit_lo or l[i] < cur_lo
        lvl_hi[i], lvl_lo[i], s_hi[i], s_lo[i] = cur_hi, cur_lo, hit_hi, hit_lo
    if bias == "none":
        b = np.full(n, np.nan)
    else:
        b1 = _htf_bias(df, "1h", bias_ema) if bias in ("1h", "both") else None
        b4 = _htf_bias(df, "4h", bias_ema) if bias in ("4h", "both") else None
        b = b1 if bias == "1h" else b4 if bias == "4h" else np.where(b1 == b4, b1, 0)
    return {"lvl_hi": lvl_hi, "lvl_lo": lvl_lo, "swept_hi": s_hi, "swept_lo": s_lo, "bias": b}


def sweep_gate(df: pd.DataFrame, sig: np.ndarray, ctx: dict, inside: bool) -> np.ndarray:
    """Keep a short only after the session HIGH was swept with a down bias (long: low + up bias).
    `inside`: the signal candle must also have closed back inside the swept range."""
    c = df["close"].to_numpy(float)
    b = ctx["bias"]
    short_ok = ctx["swept_hi"] & ~(b >= 0)
    long_ok = ctx["swept_lo"] & ~(b <= 0)
    if inside:
        short_ok &= c < ctx["lvl_hi"]
        long_ok &= c > ctx["lvl_lo"]
    return np.where(((sig == -1) & short_ok) | ((sig == 1) & long_ok), sig, 0)
