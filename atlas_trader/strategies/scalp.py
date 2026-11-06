"""1-minute scalper: classic scalping entries, held at most `hold_bars` candles (1 = ~60 s).

Signal at a 1m candle's close -> market entry at the next candle's open -> exit at the stop,
the target, or that candle's close (time exit), whichever comes first (stop first if both).

  mode "cont"      aggressive candle (body >= k x ATR and >= 60% of its range) -> same way
  mode "fade"      aggressive candle -> the other way (snap-back)
  mode "rsi_fade"  RSI(`rsi_fast`) <= `rsi_lo` -> long, >= `rsi_hi` -> short
  mode "breakout"  close above the highest high of the last `n` candles -> long (mirror short)
  mode "momentum"  Ricardo's rule on 1m: RSI(14) crosses 50 on an aggressive candle, EMA-50 trend

  STOP    `stop_atr` x ATR(14) from the signal close
  TARGET  `target_r` x risk (0 = none: time exit or stop only)
  HOURS   NY time, e.g. "08:00-11:00" or "00:00-23:59"
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema, rsi
from .base import Strategy


class Scalp(Strategy):
    name = "scalp"
    uses_session_window = False
    defaults = {
        "mode": "cont", "k": 1.5, "n": 15, "rsi_fast": 7, "rsi_lo": 20.0, "rsi_hi": 80.0,
        "stop_atr": 1.0, "target_r": 1.0, "hold_bars": 1, "hours": "08:00-11:00",
        "atr_period": 14, "body_frac": 0.6,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(df.index)
        if len(df) < 60:
            return out
        p = self.p
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a = atr(df, int(p["atr_period"])).to_numpy()
        body, rng = c - o, h - l
        aggr = (np.abs(body) >= p["k"] * a) & (np.abs(body) >= p["body_frac"] * np.where(rng > 0, rng, np.inf))
        mode = p["mode"]
        if mode == "cont":
            side = np.where(aggr & (body > 0), 1, np.where(aggr & (body < 0), -1, 0))
        elif mode == "fade":
            side = np.where(aggr & (body > 0), -1, np.where(aggr & (body < 0), 1, 0))
        elif mode == "rsi_fade":
            r = rsi(df["close"], int(p["rsi_fast"])).to_numpy()
            side = np.where(r <= p["rsi_lo"], 1, np.where(r >= p["rsi_hi"], -1, 0))
        elif mode == "breakout":
            n = int(p["n"])
            hh = pd.Series(h).shift(1).rolling(n).max().to_numpy()
            ll = pd.Series(l).shift(1).rolling(n).min().to_numpy()
            side = np.where(c > hh, 1, np.where(c < ll, -1, 0))
        elif mode == "momentum":
            r = rsi(df["close"], 14).to_numpy()
            rp = np.concatenate([[np.nan], r[:-1]])
            e = ema(df["close"], 50).to_numpy()
            up = (rp < 50) & (r >= 50) & aggr & (body > 0) & (c > e)
            dn = (rp > 50) & (r <= 50) & aggr & (body < 0) & (c < e)
            side = np.where(up, 1, np.where(dn, -1, 0))
        else:
            raise ValueError(f"unknown scalp mode {mode!r}")
        local = df.index.tz_convert(self.session.timezone)
        tod = local.hour * 60 + local.minute
        inh = np.zeros(len(df), bool)
        for part in str(p["hours"]).split(","):
            s_, e_ = part.strip().split("-")
            inh |= (tod >= int(s_[:2]) * 60 + int(s_[3:])) & (tod < int(e_[:2]) * 60 + int(e_[3:]))
        side = np.where(inh & ~np.isnan(a), side, 0)
        stop = c - side * p["stop_atr"] * a
        out["signal"] = side
        out["stop"] = np.where(side != 0, stop, np.nan)
        out["rr"] = np.where(side != 0, p["target_r"] if p["target_r"] > 0 else 1000.0, np.nan)
        out["max_bars"] = np.where(side != 0, float(p["hold_bars"]), np.nan)
        return self._finalize(out, df["close"])
