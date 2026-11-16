"""Evening (20:00-24:00 NY) setups from the 2026-11-15 web research (Tokyo open / China morning).

mode = "range_fade"   : the pre-window range (`range_window`, default 18:00-20:00) is faded. A candle
                        pokes beyond the range high (low) and closes back inside with a rejection
                        wick >= `wick_frac` of its range -> short (long). Stop beyond the wick +
                        buffer; target = range middle ("mid") or the opposite edge ("far").
mode = "open_drive"   : the first `drive_bars` candles from `drive_start` (Tokyo open, 20:00) move
                        >= `drive_atr` x ATR. variant "cont" enters with the drive, "fade" against it.
                        Stop beyond the drive's far extreme; trailing exit (or `hold_bars` time exit).
mode = "overnight"    : at `entry_time` (20:00) enter in the direction of the HTF trend (`bias`
                        "4h" | "1d"; `long_only` = only when it is up). Stop below / above the lowest
                        low / highest high of the last `stop_bars` candles; exit after `hold_bars`
                        candles or by trailing (`hold_bars` 0).
mode = "china_open"   : Shanghai Gold Exchange opens 09:00 Beijing (20:00 NY in winter, 21:00 in
                        summer). Pre-open drift = 18:00 -> open. The first `drive_bars` candles after
                        the open: variant "fade" enters against the pre-open drift if they reverse
                        it, "cont" enters with it if they extend it. Stop beyond that move's extreme.
One trade per mode per evening; entries only inside `hours`. All decisions use completed candles.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema
from ._common import exit_columns, hours_mask
from .base import Strategy, _bar_minutes


def _bias(df: pd.DataFrame, rule: str, n_ema: int) -> np.ndarray:
    htf = df["close"].resample(rule, label="left", closed="left").last().dropna()
    b = np.sign(htf - ema(htf, n_ema))
    avail = pd.Series(b.to_numpy(), index=htf.index + pd.Timedelta(rule))
    step = pd.Timedelta(minutes=_bar_minutes(pd.DataFrame(index=df.index)))
    return avail.reindex(df.index + step, method="ffill").fillna(0).to_numpy()


class Evening(Strategy):
    name = "evening"
    uses_session_window = False
    defaults = {
        "mode": "range_fade",
        "variant": "cont",
        "hours": "20:00-24:00",
        "range_window": "18:00-20:00",
        "wick_frac": 0.4,
        "target": "mid",
        "drive_start": "20:00",
        "drive_bars": 6,
        "drive_atr": 1.5,
        "entry_time": "20:00",
        "direction": "trend",        # overnight/time mode: "trend" (HTF bias), "long" or "short"
        "anchor": "sge",             # china_open mode: "sge" (09:00 Beijing) or a NY time "HH:MM"
        "pre_from": "18:00",         # ... drift measured from this NY time to the anchor
        "ib_window": "09:30-10:30",  # ib_retrace mode: initial balance window
        "ib_retrace": 0.25, "ib_stop": 0.6, "ib_target": 0.5,
        "bias": "4h",
        "bias_ema": 50,
        "long_only": False,
        "stop_bars": 24,
        "hold_bars": 0,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 4.0,
        "min_rr": 1.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 300:
            return out
        p = self.p
        tz = self.session.timezone
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        n = len(df)
        local = df.index.tz_convert(tz)
        tod = (local.hour * 60 + local.minute).to_numpy()
        bm = _bar_minutes(pd.DataFrame(index=df.index))
        close_tod = (tod + bm) % 1440
        evening = (local + pd.Timedelta(hours=6)).date          # one "evening" per gold trading day
        ok_h = hours_mask(df.index, tz, p["hours"])
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        tgt = np.full(n, np.nan)
        mode = p["mode"]
        hm = lambda s: int(s[:2]) * 60 + int(s[3:])            # noqa: E731

        if mode == "range_fade":
            in_rng = hours_mask(df.index, tz, p["range_window"])
            hi = lo = np.nan
            cur = None
            done = set()
            for i in range(n):
                if evening[i] != cur:
                    cur, hi, lo = evening[i], np.nan, np.nan
                if in_rng[i]:
                    hi = h[i] if np.isnan(hi) else max(hi, h[i])
                    lo = l[i] if np.isnan(lo) else min(lo, l[i])
                    continue
                if np.isnan(hi) or np.isnan(a[i]) or not ok_h[i]:
                    continue
                rg = h[i] - l[i]
                mid = (hi + lo) / 2
                for s, poke, back, wick in ((-1, h[i] > hi, c[i] < hi, h[i] - max(o[i], c[i])),
                                            (1, l[i] < lo, c[i] > lo, min(o[i], c[i]) - l[i])):
                    if (cur, s) in done or not (poke and back) or rg <= 0 or wick < p["wick_frac"] * rg:
                        continue
                    done.add((cur, s))
                    stp = (h[i] if s == -1 else l[i]) - s * p["stop_buffer_atr"] * a[i]
                    t_ = mid if p["target"] == "mid" else (lo if s == -1 else hi)
                    risk = s * (c[i] - stp)
                    if 0 < risk <= p["max_stop_atr"] * a[i] and s * (t_ - c[i]) >= p["min_rr"] * risk:
                        sig[i], stop[i], tgt[i] = s, stp, t_
                    break
            out["signal"], out["stop"], out["target"] = sig, stop, tgt
            out["rr"] = np.nan
            return self._finalize(out, df["close"])

        if mode in ("open_drive", "china_open"):
            if mode == "open_drive":
                start_tod = np.full(n, hm(p["drive_start"]))
            elif p["anchor"] == "sge":   # SGE 09:00 Beijing, in NY minutes for each bar's date
                bj = pd.DatetimeIndex(local.normalize() + pd.Timedelta(hours=12)).tz_convert("Asia/Shanghai")
                opens = (bj.normalize() + pd.Timedelta(hours=9)).tz_convert(tz)
                start_tod = (opens.hour * 60 + opens.minute).to_numpy()
            else:
                start_tod = np.full(n, hm(p["anchor"]))
            pre_from = hm(p["pre_from"])
            nb = int(p["drive_bars"])
            pre_start = None
            cur = None
            st = None
            for i in range(n):
                if evening[i] != cur:
                    cur, st, pre_start = evening[i], None, None
                if pre_start is None and pre_from <= tod[i] < start_tod[i]:
                    pre_start = o[i]
                if st is None and tod[i] == start_tod[i]:
                    st = {"i0": i, "open": o[i], "pre": pre_start, "done": False}
                if st is None or st["done"] or np.isnan(a[i]):
                    continue
                if i - st["i0"] + 1 < nb:
                    continue
                st["done"] = True
                seg = slice(st["i0"], i + 1)
                move = c[i] - st["open"]
                if mode == "open_drive":
                    if abs(move) < p["drive_atr"] * a[i]:
                        continue
                    d = int(np.sign(move))
                else:
                    if st["pre"] is None:
                        continue
                    pre = st["open"] - st["pre"]
                    if abs(pre) < 0.5 * a[i] or np.sign(move) == 0:
                        continue
                    if p["variant"] == "fade" and np.sign(move) != -np.sign(pre):
                        continue
                    if p["variant"] == "cont" and np.sign(move) != np.sign(pre):
                        continue
                    d = int(np.sign(move))
                s = d if (mode == "china_open" or p["variant"] == "cont") else -d
                ext = l[seg].min() if s == 1 else h[seg].max()
                stp = ext - s * p["stop_buffer_atr"] * a[i]
                risk = s * (c[i] - stp)
                if ok_h[i] and 0 < risk <= p["max_stop_atr"] * a[i]:
                    sig[i], stop[i] = s, stp
        elif mode == "overnight":
            b = _bias(df, "4h" if p["bias"] == "4h" else "1D", int(p["bias_ema"]))
            et = hm(p["entry_time"])
            sb = int(p["stop_bars"])
            # the trade is entered at the NEXT candle's open = this candle's close time
            ok_c = hours_mask(df.index + pd.Timedelta(minutes=bm), tz, p["hours"])
            for i in range(sb, n):
                if close_tod[i] != et or np.isnan(a[i]) or not ok_c[i]:
                    continue
                s = {"long": 1, "short": -1}.get(p["direction"], int(b[i]))
                if s == 0:
                    continue
                if p["long_only"] and s < 0:
                    continue
                stp = (l[i - sb + 1:i + 1].min() if s == 1 else h[i - sb + 1:i + 1].max()) - s * p["stop_buffer_atr"] * a[i]
                risk = s * (c[i] - stp)
                if 0 < risk <= p["max_stop_atr"] * a[i]:
                    sig[i], stop[i] = s, stp
        elif mode == "ib_retrace":
            in_ib = hours_mask(df.index, tz, p["ib_window"])
            cur, hi, lo, st = None, np.nan, np.nan, None
            for i in range(n):
                if evening[i] != cur:
                    cur, hi, lo, st = evening[i], np.nan, np.nan, None
                if in_ib[i]:
                    hi = h[i] if np.isnan(hi) else max(hi, h[i])
                    lo = l[i] if np.isnan(lo) else min(lo, l[i])
                    continue
                if np.isnan(hi) or st == "done" or np.isnan(a[i]):
                    continue
                ib = hi - lo
                if ib <= 0:
                    continue
                if st is None:                          # first close outside the IB = the break
                    if c[i] > hi:
                        st = 1
                    elif c[i] < lo:
                        st = -1
                    continue
                s = st
                edge = hi if s == 1 else lo
                lvl = edge - s * p["ib_retrace"] * ib
                if (l[i] <= lvl) if s == 1 else (h[i] >= lvl):   # retraced 25% back inside
                    st = "done"
                    stp = edge - s * p["ib_stop"] * ib
                    t_ = edge + s * p["ib_target"] * ib
                    risk = s * (c[i] - stp)
                    if ok_h[i] and 0 < risk <= p["max_stop_atr"] * a[i] and s * (t_ - c[i]) >= p["min_rr"] * risk:
                        sig[i], stop[i], tgt[i] = s, stp, t_
            out["signal"], out["stop"], out["target"] = sig, stop, tgt
            out["rr"] = np.nan
            if int(p["hold_bars"]) > 0:
                out["max_bars"] = np.where(sig != 0, float(p["hold_bars"]), np.nan)
            return self._finalize(out, df["close"])
        else:
            raise ValueError(f"evening: unknown mode {mode!r}")
        out["signal"], out["stop"] = sig, stop
        out["rr"] = np.where(sig != 0, 1000.0, np.nan)
        if int(p["hold_bars"]) > 0:
            out["max_bars"] = np.where(sig != 0, float(p["hold_bars"]), np.nan)
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)
