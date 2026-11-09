"""Classic chart patterns (Ricardo's Chart Pattern Library, added 2026-11-09).

Eleven patterns, each traded on the BREAKOUT CLOSE with a measured-move target:

  double_top / double_bottom
      Two swing highs (lows) within `tol_atr` x ATR of each other, at least `min_sep` bars
      apart, with a valley (peak) between them at least `min_height_atr` x ATR deep.
      Entry: a close through the valley (the neckline). Stop: beyond the higher top (lower
      bottom). Target: neckline -/+ the pattern height.
  head_shoulders / inverse_head_shoulders
      Three swing highs (lows): the middle one (head) beyond both shoulders by > tol, the
      shoulders within 2 x tol of each other. Neckline = line through the two troughs (peaks)
      between them. Entry: a close through the neckline. Stop: beyond the right shoulder.
      Target: neckline -/+ (head - neckline at the head).
  ascending_triangle   flat highs, rising lows      -> long on a close above the highs
  descending_triangle  flat lows, falling highs     -> short on a close below the lows
  symmetrical_triangle falling highs, rising lows   -> trade the side it breaks
  rising_wedge         highs and lows rising, lows faster (converging)  -> short below the lower line
  falling_wedge        highs and lows falling, highs faster (converging) -> long above the upper line
      Lines run through the last two swing highs / two swing lows. "Flat" = changes by no
      more than tol. Stop: beyond the last opposite swing. Target: breakout line -/+ the
      pattern height where it started.
  bull_flag / bear_flag
      Pole: price ran >= `pole_atr` x ATR within `pole_bars` candles. Flag: the next
      `flag_min`..`flag_max` candles hold a range no wider than `flag_frac` of the pole and give
      back no more than `flag_retrace` of it. Entry: a close beyond the flag. Stop: other side of
      the flag. Target: breakout + pole height.

Swing points use `pivot` candles on each side, so a swing is only KNOWN `pivot` candles after it
happened (no look-ahead). A pattern instance trades at most once, the breakout must come within
`max_wait` candles of the last swing, and a trade is skipped when its stop is wider than
`max_stop_atr` x ATR or its target pays less than `min_rr` x risk. Entries inside `hours` (NY).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from ._common import exit_columns, hours_mask
from .base import Strategy

ALL_PATTERNS = (
    "head_shoulders", "inverse_head_shoulders", "double_top", "double_bottom",
    "ascending_triangle", "descending_triangle", "symmetrical_triangle",
    "rising_wedge", "falling_wedge", "bull_flag", "bear_flag",
)


class ChartPatterns(Strategy):
    name = "chart_patterns"
    uses_session_window = False
    defaults = {
        "patterns": "all",          # "all" or a comma list from ALL_PATTERNS
        "pivot": 3,                 # candles each side that confirm a swing point
        "atr_period": 14,
        "tol_atr": 0.5,             # "equal" / "flat" tolerance
        "min_height_atr": 2.0,      # minimum pattern height
        "min_sep": 5,               # minimum candles between the two tops/bottoms
        "max_len": 120,             # pattern must have started within this many candles
        "max_wait": 30,             # breakout must come within this many candles of the last swing
        "pole_bars": 10,
        "pole_atr": 3.0,
        "flag_min": 3,
        "flag_max": 15,
        "flag_frac": 0.5,
        "flag_retrace": 0.5,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 4.0,
        "min_rr": 1.0,
        "trail_start_r": 0.0,       # 0 = no trailing, exit at the measured-move target or stop
        "trail_buf_atr": 0.3,
        "hours": "00:00-23:59",
    }

    def _enabled(self) -> list[str]:
        raw = str(self.p["patterns"]).strip()
        if raw == "all":
            return list(ALL_PATTERNS)
        pats = [x.strip() for x in raw.split(",") if x.strip()]
        bad = [x for x in pats if x not in ALL_PATTERNS]
        if bad:
            raise ValueError(f"chart_patterns: unknown patterns {bad}")
        return pats

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out["pattern"] = ""
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        n = len(df)
        p = self.p
        k = int(p["pivot"])
        if n < max(60, 4 * k + 10):
            return out
        enabled = set(self._enabled())
        h, l, c = (df[x].to_numpy(float) for x in ("high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        ok_h = hours_mask(df.index, self.session.timezone, p["hours"])

        # swing points, each usable only from bar j + k on
        win = 2 * k + 1
        hmax = pd.Series(h).rolling(win, center=True, min_periods=win).max().to_numpy()
        lmin = pd.Series(l).rolling(win, center=True, min_periods=win).min().to_numpy()
        is_ph = h == hmax
        is_pl = l == lmin

        # flag helpers (rolling extremes ending at each bar)
        P = int(p["pole_bars"])
        roll_lo = pd.Series(l).rolling(P + 1, min_periods=P + 1).min().to_numpy()
        roll_hi = pd.Series(h).rolling(P + 1, min_periods=P + 1).max().to_numpy()

        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        tgt = np.full(n, np.nan)
        pat = np.array([""] * n, dtype=object)
        ph: list[tuple[int, float]] = []
        pl: list[tuple[int, float]] = []
        used: set = set()
        tol_k, hgt_k = p["tol_atr"], p["min_height_atr"]
        mx_wait, mx_len, msep = int(p["max_wait"]), int(p["max_len"]), int(p["min_sep"])
        buf_k = p["stop_buffer_atr"]

        def line(i1, y1, i2, y2, t):
            return y1 + (y2 - y1) * (t - i1) / (i2 - i1)

        for t in range(win, n):
            j = t - k
            if is_ph[j]:                         # swings closer than `pivot` bars = one swing
                if ph and j - ph[-1][0] <= k:
                    if h[j] > ph[-1][1]:
                        ph[-1] = (j, h[j])
                else:
                    ph.append((j, h[j]))
            if is_pl[j]:
                if pl and j - pl[-1][0] <= k:
                    if l[j] < pl[-1][1]:
                        pl[-1] = (j, l[j])
                else:
                    pl.append((j, l[j]))
            at = a[t]
            if np.isnan(at) or at <= 0 or not ok_h[t]:
                continue
            tol, buf = tol_k * at, buf_k * at
            cands: list[tuple[str, int, float, float, tuple]] = []  # (name, side, stop, target, key)

            # ---- double top / bottom
            if "double_top" in enabled and len(ph) >= 2:
                (i1, h1), (i2, h2) = ph[-2], ph[-1]
                if i2 - i1 >= msep and abs(h1 - h2) <= tol and t - i2 <= mx_wait and i1 >= t - mx_len:
                    neck = l[i1:i2 + 1].min()
                    top = max(h1, h2)
                    if top - neck >= hgt_k * at and c[t] < neck <= c[t - 1] and h[i2 + 1:t + 1].max() <= top + tol:
                        cands.append(("double_top", -1, top + buf, neck - (top - neck), ("dt", i1, i2)))
            if "double_bottom" in enabled and len(pl) >= 2:
                (i1, l1), (i2, l2) = pl[-2], pl[-1]
                if i2 - i1 >= msep and abs(l1 - l2) <= tol and t - i2 <= mx_wait and i1 >= t - mx_len:
                    neck = h[i1:i2 + 1].max()
                    bot = min(l1, l2)
                    if neck - bot >= hgt_k * at and c[t] > neck >= c[t - 1] and l[i2 + 1:t + 1].min() >= bot - tol:
                        cands.append(("double_bottom", 1, bot - buf, neck + (neck - bot), ("db", i1, i2)))

            # ---- head & shoulders / inverse
            if "head_shoulders" in enabled and len(ph) >= 3:
                (ia, ha), (ib, hb), (ic, hc) = ph[-3], ph[-2], ph[-1]
                if (hb > ha + tol and hb > hc + tol and abs(ha - hc) <= 2 * tol
                        and t - ic <= mx_wait and ia >= t - mx_len):
                    t1 = ia + int(np.argmin(l[ia:ib + 1]))
                    t2 = ib + int(np.argmin(l[ib:ic + 1]))
                    if t2 > t1:
                        nk, nk_prev = line(t1, l[t1], t2, l[t2], t), line(t1, l[t1], t2, l[t2], t - 1)
                        height = hb - line(t1, l[t1], t2, l[t2], ib)
                        if height >= hgt_k * at and c[t] < nk and c[t - 1] >= nk_prev and h[ic + 1:t + 1].max() <= hc + tol:
                            cands.append(("head_shoulders", -1, hc + buf, nk - height, ("hs", ia, ib, ic)))
            if "inverse_head_shoulders" in enabled and len(pl) >= 3:
                (ia, la), (ib, lb), (ic, lc) = pl[-3], pl[-2], pl[-1]
                if (lb < la - tol and lb < lc - tol and abs(la - lc) <= 2 * tol
                        and t - ic <= mx_wait and ia >= t - mx_len):
                    t1 = ia + int(np.argmax(h[ia:ib + 1]))
                    t2 = ib + int(np.argmax(h[ib:ic + 1]))
                    if t2 > t1:
                        nk, nk_prev = line(t1, h[t1], t2, h[t2], t), line(t1, h[t1], t2, h[t2], t - 1)
                        height = line(t1, h[t1], t2, h[t2], ib) - lb
                        if height >= hgt_k * at and c[t] > nk and c[t - 1] <= nk_prev and l[ic + 1:t + 1].min() >= lc - tol:
                            cands.append(("inverse_head_shoulders", 1, lc - buf, nk + height, ("ihs", ia, ib, ic)))

            # ---- triangles and wedges (last two swing highs and two swing lows)
            tri = {"ascending_triangle", "descending_triangle", "symmetrical_triangle",
                   "rising_wedge", "falling_wedge"} & enabled
            if tri and len(ph) >= 2 and len(pl) >= 2:
                (h1i, h1), (h2i, h2) = ph[-2], ph[-1]
                (l1i, l1), (l2i, l2) = pl[-2], pl[-1]
                start = min(h1i, l1i)
                if start >= t - mx_len and t - max(h2i, l2i) <= mx_wait:
                    dh, dl = h2 - h1, l2 - l1
                    up_t, up_p = line(h1i, h1, h2i, h2, t), line(h1i, h1, h2i, h2, t - 1)
                    lo_t, lo_p = line(l1i, l1, l2i, l2, t), line(l1i, l1, l2i, l2, t - 1)
                    height = line(h1i, h1, h2i, h2, start) - line(l1i, l1, l2i, l2, start)
                    flat_h, flat_l = abs(dh) <= tol, abs(dl) <= tol
                    key = (h1i, h2i, l1i, l2i)
                    if up_p > lo_p and height >= hgt_k * at:
                        brk_up = c[t] > up_t and c[t - 1] <= up_p
                        brk_dn = c[t] < lo_t and c[t - 1] >= lo_p
                        kind = None
                        if flat_h and dl > tol:
                            kind = ("ascending_triangle", 1) if brk_up else None
                        elif flat_l and dh < -tol:
                            kind = ("descending_triangle", -1) if brk_dn else None
                        elif dh < -tol and dl > tol:
                            kind = ("symmetrical_triangle", 1 if brk_up else -1) if (brk_up or brk_dn) else None
                        elif dh > tol and dl > tol and dl > dh:
                            kind = ("rising_wedge", -1) if brk_dn else None
                        elif dh < -tol and dl < -tol and dh < dl:
                            kind = ("falling_wedge", 1) if brk_up else None
                        if kind and kind[0] in enabled:
                            nm, s = kind
                            if s == 1:
                                cands.append((nm, 1, l2 - buf, up_t + height, ("tri",) + key))
                            else:
                                cands.append((nm, -1, h2 + buf, lo_t - height, ("tri",) + key))

            # ---- flags
            if {"bull_flag", "bear_flag"} & enabled:
                for f in range(int(p["flag_min"]), int(p["flag_max"]) + 1):
                    e = t - f - 1                       # last candle of the pole
                    if e - P < 0:
                        break
                    fh, fl = h[e + 1:t].max(), l[e + 1:t].min()
                    if "bull_flag" in enabled:
                        pole = roll_hi[e] - roll_lo[e]
                        if (c[e] - roll_lo[e] >= p["pole_atr"] * at and pole > 0
                                and fh - fl <= p["flag_frac"] * pole and roll_hi[e] - fl <= p["flag_retrace"] * pole
                                and fh <= roll_hi[e] + tol and c[t] > fh >= c[t - 1]):
                            cands.append(("bull_flag", 1, fl - buf, c[t] + pole, ("bf", e)))
                            break
                    if "bear_flag" in enabled:
                        pole = roll_hi[e] - roll_lo[e]
                        if (roll_hi[e] - c[e] >= p["pole_atr"] * at and pole > 0
                                and fh - fl <= p["flag_frac"] * pole and fh - roll_lo[e] <= p["flag_retrace"] * pole
                                and fl >= roll_lo[e] - tol and c[t] < fl <= c[t - 1]):
                            cands.append(("bear_flag", -1, fh + buf, c[t] - pole, ("bfl", e)))
                            break

            for nm, s, stp, tg, key in cands:
                if key in used:
                    continue
                risk = s * (c[t] - stp)
                if risk <= 0 or risk > p["max_stop_atr"] * at:
                    continue
                if s * (tg - c[t]) < p["min_rr"] * risk:
                    continue
                used.add(key)
                sig[t], stop[t], tgt[t], pat[t] = s, stp, tg, nm
                break

        out["signal"], out["stop"], out["target"] = sig, stop, tgt
        out["pattern"] = pat
        out = self._finalize(out, df["close"])
        out.loc[out["signal"] == 0, "pattern"] = ""
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)
