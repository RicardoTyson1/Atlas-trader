"""Ricardo's trendline break -> retest -> rejection model (1-3 day moves).

From his chart of 2026-11-05: gold built a rising trendline off the ~4096 low, a strong candle
closed through it (break / change of character), price came back up to the underside of the line
(retest), was rejected, and the sell held for the move down. Mirror for buys.

  TRENDLINE : drawn on the `tl_tf` chart (15m or 1h) through the last two CONFIRMED swing lows
              (rising line, for a sell setup) or swing highs (falling line, for a buy setup),
              `swing_k` bars each side. The second point must be higher (lows) / lower (highs),
              the two points `min_span_h`..`max_span_h` hours apart (1-3 day moves), and no
              `tl_tf` candle may have closed through the line since the first point (respected).
  BREAK     : a completed `tl_tf` candle closes through the line by >= `break_atr` x its ATR with
              a solid body (>= `body_frac` of its range) in the break direction. With
              `require_bos`, the close must also be beyond the last swing point on that side
              (break of structure, not just of the line).
  RETEST    : within `retest_hours` after the break, a candle on the entry chart (this strategy's
              timeframe, normally 5m) reaches the line (within `touch_atr` x ATR) and is REJECTED:
              it closes back on the break side of the line, in the break direction, in the far
              half of its range. A close back through the line by `fail_atr` x tl-ATR cancels the
              setup (failed break).
  ENTRY     : next bar's open. One trade per broken line.
  STOP      : beyond the retest candle's wick (and the line) + `stop_buffer_atr` x ATR; skipped if
              the risk is under `min_stop_atr` or over `max_stop_atr` x ATR.
  EXIT      : `target: trail` = no fixed target, candle trailing stop from `trail_start_r`, big
              candle in our favor pulls the stop to mid-candle; `target: rr` = `reward_risk` x risk.
Only completed candles are used (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy


def _pivots(h: np.ndarray, l: np.ndarray, k: int):
    """Confirmed fractal pivots: lists of (bar index, price), confirmed at index + k."""
    hi, lo = [], []
    for j in range(k, len(h) - k):
        if h[j] == h[j - k:j + k + 1].max():
            hi.append((j, h[j]))
        if l[j] == l[j - k:j + k + 1].min():
            lo.append((j, l[j]))
    return hi, lo


class TrendBreakRetest(Strategy):
    name = "trend_break"
    uses_session_window = False
    defaults = {
        "tl_tf": "15min",           # trendline chart: 15min | 1h
        "swing_k": 3,
        "min_span_h": 4.0,
        "max_span_h": 72.0,
        "break_atr": 0.25,
        "body_frac": 0.5,
        "require_bos": False,
        "retest_hours": 8.0,
        "touch_atr": 0.15,
        "fail_atr": 0.5,
        "atr_period": 14,
        "stop_buffer_atr": 0.3,
        "min_stop_atr": 0.5,
        "max_stop_atr": 4.0,
        "target": "trail",          # trail | rr
        "reward_risk": 2.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "spike_atr": 2.0,
        "spike_min_r": 1.0,
        "spike_mode": 2,
        "hours": "00:00-23:59",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        for k in ("trail_start_r", "trail_buf", "spike_pts", "spike_min_r", "spike_mode"):
            out[k] = np.nan
        if len(df) < 200:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        n = len(df)
        step = pd.Series(df.index).diff().dropna().mode().iloc[0]
        bar_end = (df.index + step).as_unit("ns").asi8
        t_sec = df.index.as_unit("ns").asi8 / 1e9

        htf = df.resample(p["tl_tf"], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(htf) < 30:
            return out
        H_o, H_h, H_l, H_c = (htf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        H_a = atr(htf, int(p["atr_period"])).to_numpy()
        H_step = pd.Timedelta(p["tl_tf"])
        H_end = (htf.index + H_step).as_unit("ns").asi8
        H_t = htf.index.as_unit("ns").asi8 / 1e9                     # line x-axis: candle open time (s)
        k = int(p["swing_k"])
        piv_hi, piv_lo = _pivots(H_h, H_l, k)

        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        in_hours = np.zeros(n, bool)
        for rng_ in str(p["hours"]).split(","):
            s_, e_ = rng_.strip().split("-")
            in_hours |= (tod >= int(s_[:2]) * 60 + int(s_[3:])) & (tod <= int(e_[:2]) * 60 + int(e_[3:]))

        def line_at(ln, ts):
            (t1, p1), (t2, p2) = ln
            return p1 + (p2 - p1) * (ts - t1) / (t2 - t1)

        conf = {id(piv_hi): np.array([j + k for j, _ in piv_hi], int),
                id(piv_lo): np.array([j + k for j, _ in piv_lo], int)}

        def last_two(pivs, upto):
            """Last two pivots confirmed by htf candle `upto` (inclusive)."""
            m = int(np.searchsorted(conf[id(pivs)], upto, side="right"))
            return pivs[m - 2:m] if m >= 2 else None

        def respected(ln, j1, upto, side_line):
            # side_line +1: rising support (closes must stay above); -1: falling resistance
            for j in range(j1 + 1, upto + 1):
                v = line_at(ln, H_t[j])
                if (side_line == 1 and H_c[j] < v) or (side_line == -1 and H_c[j] > v):
                    return False
            return True

        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        used: set = set()
        setups: list[dict] = []      # active broken lines waiting for a retest
        hj = -1                      # last completed htf candle index
        span_lo, span_hi = p["min_span_h"] * 3600, p["max_span_h"] * 3600
        for i in range(n):
            # 1) new completed trendline-chart candles -> check for breaks
            while hj + 1 < len(htf) and H_end[hj + 1] <= bar_end[i]:
                hj += 1
                if np.isnan(H_a[hj]):
                    continue
                for pivs, trade_side, line_side in ((piv_lo, -1, 1), (piv_hi, 1, -1)):
                    pts = last_two(pivs, hj - 1)
                    if pts is None:
                        continue
                    (j1, p1), (j2, p2) = pts
                    if line_side == 1 and not p2 > p1:
                        continue
                    if line_side == -1 and not p2 < p1:
                        continue
                    span = H_t[j2] - H_t[j1]
                    if not (span_lo <= span <= span_hi):
                        continue
                    key = (trade_side, j1, j2)
                    if key in used:
                        continue
                    ln = ((H_t[j1], p1), (H_t[j2], p2))
                    if not respected(ln, j1, hj - 1, line_side):
                        used.add(key)                       # already broken earlier: not a clean line
                        continue
                    v = line_at(ln, H_t[hj])
                    rng = H_h[hj] - H_l[hj]
                    body = H_c[hj] - H_o[hj]
                    solid = rng > 0 and trade_side * body >= p["body_frac"] * rng
                    through = trade_side * (H_c[hj] - v) >= p["break_atr"] * H_a[hj]
                    bos = (H_c[hj] < p2) if trade_side == -1 else (H_c[hj] > p2)
                    if solid and through and (bos or not p["require_bos"]):
                        used.add(key)
                        setups.append({"side": trade_side, "line": ln, "atr_h": H_a[hj],
                                       "until": t_sec[i] + p["retest_hours"] * 3600, "after": H_end[hj]})
            # 2) retest + rejection on the entry chart
            if not setups or np.isnan(a[i]):
                continue
            keep = []
            for s in setups:
                if t_sec[i] > s["until"]:
                    continue
                if bar_end[i] <= s["after"]:
                    keep.append(s)
                    continue
                side = s["side"]
                v = line_at(s["line"], t_sec[i])
                if -side * (c[i] - v) > p["fail_atr"] * s["atr_h"]:
                    continue                                # closed back through: failed break
                reach = (h[i] >= v - p["touch_atr"] * a[i]) if side == -1 else (l[i] <= v + p["touch_atr"] * a[i])
                rng = h[i] - l[i]
                rejected = rng > 0 and side * (c[i] - v) > 0 and side * (c[i] - o[i]) > 0 and \
                    ((c[i] - l[i]) <= 0.5 * rng if side == -1 else (h[i] - c[i]) <= 0.5 * rng)
                if reach and rejected and in_hours[i] and sig[i] == 0:
                    wick = max(h[i], v) if side == -1 else min(l[i], v)
                    st = wick - side * p["stop_buffer_atr"] * a[i]
                    risk = side * (c[i] - st)
                    if p["min_stop_atr"] * a[i] <= risk <= p["max_stop_atr"] * a[i]:
                        sig[i], stop[i] = side, st
                        continue                            # one trade per line
                keep.append(s)
            setups = keep

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
