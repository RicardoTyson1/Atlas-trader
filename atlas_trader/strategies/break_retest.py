"""Ricardo's model: consolidation -> solid breakout candle (BOS/CHoCH) -> verified retest,
with the 5-minute structure as the direction filter and a candle-by-candle trailing stop.

Built for 1-minute bars (works on any timeframe). Rules:

  DIRECTION (higher timeframe, `htf` = 5min by default, completed bars only):
    bullish after a 5m close above the last 5m swing high (break of structure),
    bearish after a 5m close below the last 5m swing low. Longs only when bullish,
    shorts only when bearish (`bias: none` disables this).
  CONSOLIDATION: the `cons_bars` bars before the breakout span no more than
    `cons_atr` x ATR (a tight range).
  BREAKOUT (BOS): a solid candle - body >= `body_frac` of its range and
    >= `disp_atr` x ATR - CLOSES beyond the consolidation high (long) / low (short).
    `require_choch`: the 1m swings before it were against the trade (lower highs for a
    long), so the breakout is a change of character, not a continuation.
  VERIFIED RETEST: within `retest_bars` bars, price comes back to the broken level
    (within `retest_tol_atr` x ATR), does NOT close back inside the range, and the
    retest bar closes in the trade direction -> enter at the next bar's open.
  STOP: beyond the retest bar's wick + `stop_buffer_atr` x ATR.
  EXIT: trailing stop. Once open profit >= `trail_start_r` x risk, each time a pullback
    candle is followed by a strong candle in the trade's direction, the stop moves to
    just beyond the pullback wick (+ `trail_buf_atr` x ATR of room). Optional fixed
    target `reward_risk` (0 = trail only). One position at a time; no adding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr
from .base import Strategy, _bar_minutes


def _bar_delta(df: pd.DataFrame) -> pd.Timedelta:
    return pd.Timedelta(minutes=_bar_minutes(pd.DataFrame(index=df.index)))


def _swings(h: np.ndarray, l: np.ndarray, k: int):
    """Last and previous CONFIRMED swing high/low known at each bar.

    A pivot at bar j is confirmed at bar j + k (k bars on each side), so nothing here
    looks ahead. Returns (swing_high, swing_low, prev_swing_high, prev_swing_low).
    """
    n = len(h)
    sh, sl, psh, psl = (np.full(n, np.nan) for _ in range(4))
    last_h = last_l = prev_h = prev_l = np.nan
    for i in range(n):
        j = i - k
        if j - k >= 0:
            if h[j] == h[j - k:i + 1].max():
                prev_h, last_h = last_h, h[j]
            if l[j] == l[j - k:i + 1].min():
                prev_l, last_l = last_l, l[j]
        sh[i], sl[i], psh[i], psl[i] = last_h, last_l, prev_h, prev_l
    return sh, sl, psh, psl


class BreakRetest(Strategy):
    name = "break_retest"
    uses_session_window = False
    defaults = {
        "htf": "5min",
        "bias": "bos",              # bos | none
        "swing_k": 2,
        "cons_bars": 10,
        "cons_atr": 2.5,
        "body_frac": 0.6,
        "disp_atr": 0.8,
        "require_choch": False,
        "retest_bars": 15,
        "retest_tol_atr": 0.3,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "reward_risk": 0.0,         # 0 = no fixed target, ride the trailing stop
        "hours": "02:00-11:00",     # New York time entry window
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out["trail_start_r"] = np.nan
        out["trail_buf"] = np.nan
        if len(df) < 100:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        k = int(p["swing_k"])

        # --- higher-timeframe direction from COMPLETED htf bars only
        bias = np.zeros(len(df))
        if p["bias"] == "bos":
            hb = df.resample(p["htf"], label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            hh, hl, hc = hb["high"].to_numpy(), hb["low"].to_numpy(), hb["close"].to_numpy()
            hsh, hsl, _, _ = _swings(hh, hl, k)
            state, hdir = 0, np.zeros(len(hb))
            for j in range(1, len(hb)):
                if not np.isnan(hsh[j - 1]) and hc[j] > hsh[j - 1]:
                    state = 1
                elif not np.isnan(hsl[j - 1]) and hc[j] < hsl[j - 1]:
                    state = -1
                hdir[j] = state
            # an htf bar is usable only after it closes: shift labels by one htf period
            avail = pd.Series(hdir, index=hb.index + pd.tseries.frequencies.to_offset(p["htf"]))
            bias = avail.reindex(df.index + _bar_delta(df), method="ffill").fillna(0).to_numpy()
        else:
            bias[:] = 1  # placeholder: both sides allowed below

        # --- 1m structure for CHoCH
        sh, sl, psh, psl = _swings(h, l, k)

        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        s_h, e_h = (int(x[:2]) * 60 + int(x[3:]) for x in str(p["hours"]).split("-"))
        in_hours = (tod >= s_h) & (tod < e_h)

        n = len(df)
        cb = int(p["cons_bars"])
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        rr = np.full(n, np.nan)
        trs = np.full(n, np.nan)
        tbuf = np.full(n, np.nan)
        setup = None  # (side, level, range_other_side, i_break)
        for i in range(cb + 1, n):
            ai = a[i]
            if np.isnan(ai) or ai <= 0:
                continue
            if setup is not None:
                side, lvl, other, ib = setup
                if i - ib > int(p["retest_bars"]) or side * (c[i] - lvl) < 0:
                    setup = None          # too slow, or closed back inside the range: failed retest
                else:
                    tol = p["retest_tol_atr"] * ai
                    touched = (l[i] <= lvl + tol) if side == 1 else (h[i] >= lvl - tol)
                    holds = side * (c[i] - o[i]) > 0 and side * (c[i] - lvl) > 0
                    if touched and holds and in_hours[i]:
                        st = (l[i] if side == 1 else h[i]) - side * p["stop_buffer_atr"] * ai
                        if side * (c[i] - st) > 0:
                            sig[i], stop[i] = side, st
                            rr[i] = float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0
                            trs[i] = float(p["trail_start_r"])
                            tbuf[i] = p["trail_buf_atr"] * ai
                        setup = None
                    continue
            # look for consolidation -> solid breakout candle closing beyond it
            hi = h[i - cb:i].max()
            lo = l[i - cb:i].min()
            if hi - lo > p["cons_atr"] * ai:
                continue
            rng = h[i] - l[i]
            body = abs(c[i] - o[i])
            if rng <= 0 or body < p["body_frac"] * rng or body < p["disp_atr"] * ai:
                continue
            side = 1 if (c[i] > hi and c[i] > o[i]) else (-1 if (c[i] < lo and c[i] < o[i]) else 0)
            if side == 0:
                continue
            if p["bias"] == "bos" and bias[i] != side:
                continue
            if p["require_choch"]:
                # change of character: the swings before the break pointed the other way
                if side == 1 and not (sh[i] < psh[i]):      # lower highs before a bullish break
                    continue
                if side == -1 and not (sl[i] > psl[i]):     # higher lows before a bearish break
                    continue
            setup = (side, hi if side == 1 else lo, lo if side == 1 else hi, i)

        out["signal"], out["stop"], out["rr"] = sig, stop, rr
        out = self._finalize(out, df["close"])
        out["trail_start_r"] = np.where(out["signal"] != 0, trs, np.nan)
        out["trail_buf"] = np.where(out["signal"] != 0, tbuf, np.nan)
        return out
