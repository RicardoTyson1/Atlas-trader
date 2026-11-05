"""Ricardo's video model: fade a fast move after a change of character, trail the stop.

Codified from his recorded TopstepX trades (Aug 16 short after a rally broke its trendline,
Jul 6 long after a sell-off reversed): wait for a FAST move, then a change of character
(price closes through the last minor swing against that move with a solid candle), enter,
put the stop beyond the fast move's extreme, and trail it candle by candle.

  FAST MOVE : over the last `move_bars` bars, price travelled >= `move_atr` x ATR in one
              direction (a rally for a short setup, a sell-off for a long setup).
  CHoCH     : within `wait_bars` bars of the extreme, a solid candle (body >= `body_frac`
              of range) closes beyond the last confirmed swing low (short) / high (long).
  ENTRY     : next bar's open, only inside `hours` (New York time, comma-separated ranges).
  STOP      : beyond the fast move's extreme + `stop_buffer_atr` x ATR; skipped if wider than
              `max_stop_atr` x ATR (keeps risk sane).
  RSI       : if `rsi_level` > 0, the fast move must reach an EXTREME reading at its extreme:
              RSI(`rsi_period`) <= rsi_level for a long (oversold sell-off),
              >= 100 - rsi_level for a short (overbought rally).
  TARGET    : `target: half_move` = take profit at 50% of the fast move (retrace toward where it
              started); `target: rr` = fixed `reward_risk` x risk (0 = no target, trail only).
  BIG CANDLE: if `spike_atr` > 0 and a candle in the trade's favor has a body >= spike_atr x ATR
              while the trade is up >= `spike_min_r` x risk: `spike_mode` 2 (default) = stay in
              the move and pull the stop to the middle of that candle; 1 = take the profit.
  EXIT      : candle trailing stop from `trail_start_r` x risk (see backtest.trail_candidate),
              together with the target. One position, no adding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, rsi
from .base import Strategy
from .break_retest import _swings


class ReversalTrail(Strategy):
    name = "reversal_trail"
    uses_session_window = False
    defaults = {
        "move_bars": 15,
        "move_atr": 4.0,
        "wait_bars": 10,
        "swing_k": 2,
        "body_frac": 0.6,
        "atr_period": 14,
        "stop_buffer_atr": 0.2,
        "max_stop_atr": 4.0,
        "trail_start_r": 1.0,
        "trail_buf_atr": 0.3,
        "reward_risk": 0.0,
        "target": "rr",            # rr | half_move
        "spike_atr": 0.0,          # 0 = off; e.g. 2.5 = take profit on an aggressive candle
        "spike_min_r": 1.0,
        "spike_mode": 2,
        "rsi_period": 14,
        "rsi_level": 0.0,          # 0 = off; 20-25 = "extremely oversold/overbought"
        "hours": "08:00-11:00,19:00-23:00",
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        out["trail_start_r"] = np.nan
        out["trail_buf"] = np.nan
        out["spike_pts"] = np.nan
        out["spike_min_r"] = np.nan
        if len(df) < 100:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        r = rsi(df["close"], int(p["rsi_period"])).to_numpy()
        lvl = float(p["rsi_level"])
        sh, sl, _, _ = _swings(h, l, int(p["swing_k"]))
        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        in_hours = np.zeros(len(df), bool)
        for rng_ in str(p["hours"]).split(","):
            s_, e_ = rng_.strip().split("-")
            s_m, e_m = int(s_[:2]) * 60 + int(s_[3:]), int(e_[:2]) * 60 + int(e_[3:])
            in_hours |= (tod >= s_m) & (tod < e_m)

        mb = int(p["move_bars"])
        n = len(df)
        sig = np.zeros(n, int)
        stop = np.full(n, np.nan)
        tgt = np.full(n, np.nan)
        rr = np.full(n, np.nan)
        trs = np.full(n, np.nan)
        tbuf = np.full(n, np.nan)
        spk = np.full(n, np.nan)
        setup = None  # (side_to_trade, extreme, i_extreme, move_start_price, rsi_ok)
        for i in range(mb + 1, n):
            ai = a[i]
            if np.isnan(ai) or ai <= 0:
                continue
            # track / start a fast-move setup
            lo_w, hi_w = l[i - mb:i + 1].min(), h[i - mb:i + 1].max()
            if hi_w - lo_w >= p["move_atr"] * ai:
                i_hi = i - mb + int(np.argmax(h[i - mb:i + 1]))
                i_lo = i - mb + int(np.argmin(l[i - mb:i + 1]))
                if i_lo < i_hi and h[i] >= hi_w - 0.25 * ai:      # rally just made its high -> short setup
                    keep = setup is not None and setup[0] == -1
                    setup = (-1, hi_w, i_hi, setup[3] if keep else lo_w, (setup[4] if keep else False) or r[i] >= 100 - lvl)
                elif i_hi < i_lo and l[i] <= lo_w + 0.25 * ai:    # sell-off just made its low -> long setup
                    keep = setup is not None and setup[0] == 1
                    setup = (1, lo_w, i_lo, setup[3] if keep else hi_w, (setup[4] if keep else False) or r[i] <= lvl)
            if setup is None:
                continue
            side, ext, i_ext, start, rsi_ok = setup
            # keep the extreme current while the move extends
            if side == -1 and h[i] > ext:
                setup = (side, h[i], i, start, rsi_ok or r[i] >= 100 - lvl)
                continue
            if side == 1 and l[i] < ext:
                setup = (side, l[i], i, start, rsi_ok or r[i] <= lvl)
                continue
            if i - i_ext > int(p["wait_bars"]):
                setup = None
                continue
            rng = h[i] - l[i]
            solid = rng > 0 and abs(c[i] - o[i]) >= p["body_frac"] * rng
            choch = (side == -1 and c[i] < o[i] and not np.isnan(sl[i]) and c[i] < sl[i]) or \
                    (side == 1 and c[i] > o[i] and not np.isnan(sh[i]) and c[i] > sh[i])
            if not (solid and choch and in_hours[i]):
                continue
            if lvl > 0 and not rsi_ok:
                setup = None
                continue
            st = ext - side * p["stop_buffer_atr"] * ai
            risk = side * (c[i] - st)
            setup = None
            if risk <= 0 or risk > p["max_stop_atr"] * ai:
                continue
            if p["target"] == "half_move":
                t_px = ext + side * 0.5 * abs(start - ext)        # 50% retrace of the fast move
                if side * (t_px - c[i]) <= 0:
                    continue                                       # already past the halfway point
                tgt[i] = t_px
            else:
                rr[i] = float(p["reward_risk"]) if p["reward_risk"] > 0 else 1000.0
            sig[i], stop[i] = side, st
            trs[i] = float(p["trail_start_r"])
            tbuf[i] = p["trail_buf_atr"] * ai
            spk[i] = p["spike_atr"] * ai if p["spike_atr"] > 0 else np.nan

        out["signal"], out["stop"], out["target"], out["rr"] = sig, stop, tgt, rr
        out = self._finalize(out, df["close"])
        out["trail_start_r"] = np.where(out["signal"] != 0, trs, np.nan)
        out["trail_buf"] = np.where(out["signal"] != 0, tbuf, np.nan)
        out["spike_pts"] = np.where(out["signal"] != 0, spk, np.nan)
        out["spike_min_r"] = np.where(out["signal"] != 0, float(p["spike_min_r"]), np.nan)
        out["spike_mode"] = np.where(out["signal"] != 0, float(p["spike_mode"]), np.nan)
        return out
