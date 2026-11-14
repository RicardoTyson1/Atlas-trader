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
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out = exit_columns(out, np.zeros(len(df), bool), 0.0, np.nan)
        if len(df) < 200:
            return out
        p = self.p
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
        out["signal"], out["stop"] = sig, stop
        out["rr"] = np.where(sig != 0, 1000.0, np.nan)      # trail only
        out = self._finalize(out, df["close"])
        on = out["signal"].to_numpy() != 0
        return exit_columns(out, on, float(p["trail_start_r"]), p["trail_buf_atr"] * a)
