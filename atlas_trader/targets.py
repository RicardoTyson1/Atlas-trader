"""Take-profit levels: where price is likely to be drawn to, Ricardo's way.

For a trade decided at the close of bar `i`, the candidate targets in the trade's path are:

  FVG 50%     the consequent encroachment (middle) of a fair value gap that price has not
              come back to since it formed (gap >= `fvg_min_atr` x ATR, formed in the last
              `lookback_bars` bars). For a long: gaps above price; for a short: below.
  LIQUIDITY   resting stops that have not been taken yet:
                previous trading day high / low (day starts 18:00 New York),
                today's Asian range (18:00-24:00) and London range (02:00-05:00) high / low,
                unswept swing highs / lows (fractal, `swing_k` bars each side) in the
                lookback - equal highs / lows show up here as the same level.
  MAJOR S/R   zones on the 1-hour chart: confirmed 1h swing highs / lows from the last
              `sr_days` days, clustered within `sr_tol_atr` x 1h-ATR; a zone touched at least
              `sr_min_touches` times is major. Its near edge (the side price reaches first)
              is the level.

The target is the NEAREST candidate that still pays at least `min_r` x the trade's risk,
placed `front_atr` x ATR in front of the level (so it fills before the crowd's orders).
No candidate that far -> no target (the strategy then skips the trade).

Only bars up to and including `i` are used (no look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr as _atr


@dataclass
class TargetParams:
    min_r: float = 1.0
    lookback_bars: int = 0            # how far back FVGs / swings count; 0 = one day of bars
    fvg_min_atr: float = 0.2
    swing_k: int = 3
    sr_days: int = 10
    sr_tol_atr: float = 0.5
    sr_min_touches: int = 2
    front_atr: float = 0.1
    atr_period: int = 14


class TargetFinder:
    """Precomputes everything once per bar series; `find(i, side, entry, stop)` per signal."""

    def __init__(self, df: pd.DataFrame, tz: str, p: TargetParams | None = None) -> None:
        self.p = p or TargetParams()
        self.df = df
        if self.p.lookback_bars <= 0:
            self.p.lookback_bars = max(10, int(pd.Timedelta(days=1) / _bar_delta(df)))
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.a = _atr(df, self.p.atr_period).to_numpy()
        n = len(df)
        local = df.index.tz_convert(tz)
        shifted = local + pd.Timedelta(hours=6)          # trading day starts 18:00 NY
        self.tday = np.asarray(shifted.year * 10000 + shifted.month * 100 + shifted.day)
        self.stod = np.asarray(shifted.hour * 60 + shifted.minute)   # minutes since 18:00
        self.tod = np.asarray(local.hour * 60 + local.minute)
        # first bar index of each bar's trading day
        new_day = np.r_[True, self.tday[1:] != self.tday[:-1]]
        self.day_start = np.maximum.accumulate(np.where(new_day, np.arange(n), 0))
        # previous trading day high / low per bar
        d = pd.DataFrame({"d": self.tday, "h": self.h, "l": self.l}).groupby("d", sort=False).agg(h=("h", "max"), l=("l", "min"))
        prev = d.shift(1)
        self.pdh = pd.Series(self.tday).map(prev["h"]).to_numpy(float)
        self.pdl = pd.Series(self.tday).map(prev["l"]).to_numpy(float)
        # FVGs: (formed at bar j, lo, hi)
        h, l = self.h, self.l
        j = np.arange(2, n)
        bull = l[2:] > h[:-2]
        bear = h[2:] < l[:-2]
        gap_lo = np.where(bull, h[:-2], np.where(bear, h[2:], np.nan))
        gap_hi = np.where(bull, l[2:], np.where(bear, l[:-2], np.nan))
        ok = (bull | bear) & ((gap_hi - gap_lo) >= self.p.fvg_min_atr * np.nan_to_num(self.a[2:], nan=np.inf))
        self.fvg_j, self.fvg_ce = j[ok], ((gap_lo + gap_hi) / 2)[ok]
        # confirmed fractal swings: (index of the swing bar, price)
        k = self.p.swing_k
        self.sw_hi, self.sw_lo = [], []
        for x in range(k, n - k):
            if h[x] == h[x - k:x + k + 1].max():
                self.sw_hi.append(x)
            if l[x] == l[x - k:x + k + 1].min():
                self.sw_lo.append(x)
        self.sw_hi, self.sw_lo = np.array(self.sw_hi, int), np.array(self.sw_lo, int)
        # 1h chart for major S/R
        h1 = df.resample("1h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        self.h1 = h1
        self.h1_end = (h1.index + pd.Timedelta(hours=1)).as_unit("ns").asi8
        self.h1_a = _atr(h1, 14).to_numpy() if len(h1) else np.array([])
        self.bar_end = (df.index + _bar_delta(df)).as_unit("ns").asi8

    # ------------------------------------------------------------------ candidates
    def candidates(self, i: int, side: int, entry: float) -> list[tuple[float, str]]:
        """All levels in the trade's path at bar i: [(price, kind)]."""
        p, h, l = self.p, self.h, self.l
        out: list[tuple[float, str]] = []
        lo_i = max(0, i - p.lookback_bars)

        def ahead(x: float) -> bool:
            return not np.isnan(x) and side * (x - entry) > 0

        # FVG 50% not revisited since it formed
        sel = (self.fvg_j >= lo_i) & (self.fvg_j < i)
        for j, ce in zip(self.fvg_j[sel], self.fvg_ce[sel]):
            if not ahead(ce):
                continue
            seg_h, seg_l = h[j + 1:i + 1], l[j + 1:i + 1]
            touched = (seg_h.max() >= ce) if side == 1 else (seg_l.min() <= ce)
            if not touched:
                out.append((float(ce), "FVG 50%"))
        # previous day high / low, not yet taken today
        ds = self.day_start[i]
        day_hi, day_lo = h[ds:i + 1].max(), l[ds:i + 1].min()
        lvl = self.pdh[i] if side == 1 else self.pdl[i]
        if ahead(lvl) and ((day_hi < lvl) if side == 1 else (day_lo > lvl)):
            out.append((float(lvl), "prev day high" if side == 1 else "prev day low"))
        # today's Asian / London range, once complete, not yet taken
        for name, mask_fn, done in (
            ("Asian", lambda x: self.stod[x] < 360, self.stod[i] >= 360),
            ("London", lambda x: (self.tod[x] >= 120) & (self.tod[x] < 300), self.tod[i] >= 300 and self.stod[i] >= 480),
        ):
            if not done:
                continue
            idx = np.arange(ds, i + 1)
            m = mask_fn(idx)
            if not m.any():
                continue
            last_in = idx[m].max()
            r_hi, r_lo = h[idx[m]].max(), l[idx[m]].min()
            after_h = h[last_in + 1:i + 1].max() if last_in < i else -np.inf
            after_l = l[last_in + 1:i + 1].min() if last_in < i else np.inf
            if side == 1 and ahead(r_hi) and after_h < r_hi:
                out.append((float(r_hi), f"{name} high"))
            if side == -1 and ahead(r_lo) and after_l > r_lo:
                out.append((float(r_lo), f"{name} low"))
        # unswept swing highs / lows (confirmed by bar i)
        k = p.swing_k
        sw = self.sw_hi if side == 1 else self.sw_lo
        sel = sw[(sw >= lo_i) & (sw + k <= i)]
        for x in sel:
            lvl = h[x] if side == 1 else l[x]
            if not ahead(lvl):
                continue
            later = h[x + 1:i + 1].max() if side == 1 else l[x + 1:i + 1].min()
            if (later < lvl) if side == 1 else (later > lvl):
                out.append((float(lvl), "swing liquidity"))
        # major S/R zones from completed 1h bars
        out.extend(self._sr(i, side, entry))
        return out

    def _sr(self, i: int, side: int, entry: float) -> list[tuple[float, str]]:
        p = self.p
        if not len(self.h1):
            return []
        n_done = int(np.searchsorted(self.h1_end, self.bar_end[i], side="right"))
        if n_done < 10:
            return []
        start = max(0, n_done - p.sr_days * 24)
        hh = self.h1["high"].to_numpy(float)[start:n_done]
        ll = self.h1["low"].to_numpy(float)[start:n_done]
        a1 = self.h1_a[n_done - 1]
        if np.isnan(a1) or a1 <= 0:
            return []
        k = 2
        pts = []
        for x in range(k, len(hh) - k):
            if hh[x] == hh[x - k:x + k + 1].max():
                pts.append(hh[x])
            if ll[x] == ll[x - k:x + k + 1].min():
                pts.append(ll[x])
        if not pts:
            return []
        pts = np.sort(np.array(pts))
        tol = p.sr_tol_atr * a1
        zones, cur = [], [pts[0]]
        for v in pts[1:]:
            if v - cur[-1] <= tol:
                cur.append(v)
            else:
                zones.append(cur)
                cur = [v]
        zones.append(cur)
        out = []
        for z in zones:
            if len(z) < p.sr_min_touches:
                continue
            edge = min(z) if side == 1 else max(z)       # the side price reaches first
            if side * (edge - entry) > 0:
                out.append((float(edge), f"major {'resistance' if side == 1 else 'support'}"))
        return out

    # ------------------------------------------------------------------ choice
    def find(self, i: int, side: int, entry: float, stop: float) -> tuple[float, str] | None:
        """Nearest level that pays >= min_r x risk, moved `front_atr` x ATR in front of it."""
        risk = abs(entry - stop)
        ai = self.a[i]
        if risk <= 0 or np.isnan(ai):
            return None
        best = None
        for lvl, kind in self.candidates(i, side, entry):
            tp = lvl - side * self.p.front_atr * ai
            if side * (tp - entry) < self.p.min_r * risk:
                continue
            if best is None or side * (tp - best[0]) < 0:
                best = (tp, kind)
        return best


def _bar_delta(df: pd.DataFrame) -> pd.Timedelta:
    if len(df) < 2:
        return pd.Timedelta(minutes=1)
    return pd.Series(df.index).diff().dropna().mode().iloc[0]


def tp_params(p: dict) -> TargetParams:
    """Strategy params `tp_*` -> TargetParams."""
    return TargetParams(
        min_r=float(p.get("tp_min_r", 1.0)),
        lookback_bars=int(p.get("tp_lookback_bars", 0)),
        sr_days=int(p.get("tp_sr_days", 10)),
        front_atr=float(p.get("tp_front_atr", 0.1)),
    )
