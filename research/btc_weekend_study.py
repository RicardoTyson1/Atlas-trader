"""P4 Bitcoin weekends study, pre-registered 2026-11-07 (Ricardo). Research only; nothing is wired in.

Window: Friday 17:00 New York (gold closes) -> Sunday 18:00 (gold reopens). Entries only inside it;
every open trade is closed at the close of the bar that ends at (or first passes) Sunday 18:00.

Models (no 1-minute entries; same rules as on gold, all ATR-scaled; trail from +1R):
  smc_ob        5m   sweep 24 candles, no fixed target        (live gold settings, any hour)
  st_confluence 15m  SuperTrend x3, no volume filter          (live gold settings, any hour)
  momentum_rsi  5m   disp 1.0 ATR, EMA-50 trend, big-candle lock 2 ATR (last live gold settings)
  Indicators run on the continuous 24/7 BTC series (causal); only the entry time is restricted.

Filters at the signal candle's close (known at decision time):
  * skip if the spread is above $80;
  * cost filter: skip if the first target is < 5x the spread. These models have no fixed target;
    the first profit point is +1R (where the trail starts), so: skip if 1R < 5 x spread.

Costs: TradeLocker's history has NO bid/ask (only one price series), so historical spread cannot be
  used. Agreed with Ricardo: a FIXED spread = the 90th percentile of a live weekend sample taken
  2026-11-07 (research/btc_weekend_data.json), plus $5 slippage per side. Bars are treated as
  bid- or mid-priced per that file's check. Longs buy the ask and exit on the bid; shorts the
  reverse; stops trigger on the side you would exit on. Commission: none (GatesFX quotes BTC
  spread-only; spec shows no commission).
Risk: 50% of the gold setting (2.5% per trade). Results are in R (1R = 2.5% of the account).

Pass bar (fixed before running):
  1. Weekends split into halves A and B by date.
  2. Pick the model with the best total R on half A only.
  3. PASS only if on half B: expectancy > 0, at least 30 trades, max drawdown no worse than 20R.
  4. Report trades, win %, expectancy, total R, max drawdown, t per model per half. Under 100
     trades = "promising, not proven". STRONG = n >= 100 and t >= 2.5.
  5. Nothing passes -> say so. No retesting with new settings.

Run (never in /opt/atlas-trader):
  PYTHONPATH=. python research/btc_weekend_study.py data/raw/BTCUSD_5m.csv [--spread X --basis bid|mid]
  (the 5m file is the 1m download resampled; 15m is resampled from it)
"""
from __future__ import annotations

import argparse
import json
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from atlas_trader.backtest import spike_lock, trail_candidate
from atlas_trader.config import Session
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from atlas_trader.weekend import in_weekend_window, weekend_close, weekend_id

MAX_SPREAD = 80.0
COST_MULT = 5.0
SLIP = 5.0
RISK_NOTE = "2.5% per trade (50% of gold's 5%)"
MODELS = {
    "smc_ob": ("5m", dict(sweep_bars=24, reward_risk=0.0, hours="00:00-23:59")),
    "st_confluence": ("15m", dict(st_mult=3.0, vol_mult=0.0, hours="00:00-23:59")),
    "momentum_rsi": ("5m", dict(disp_atr=1.0, trend_filter=True, trail_start_r=1.0, trail_buf_atr=0.3,
                                spike_atr=2.0, hours="00:00-23:59")),
}
SESSION = Session(timezone="America/New_York", trade_start=time(0, 0), last_entry=time(23, 59),
                  flat_by=time(23, 59), overnight=True)


def simulate(df: pd.DataFrame, sig: pd.DataFrame, spread: float, basis: str = "mid",
             slip: float = SLIP, max_spread: float = MAX_SPREAD, cost_mult: float = COST_MULT) -> list[dict]:
    """Bar-by-bar weekend simulation, one position at a time. `df` = OHLC on the signal timeframe
    (bid- or mid-priced), `sig` = the strategy's signal frame on the same index."""
    if basis not in ("bid", "mid"):
        raise ValueError("basis must be 'bid' or 'mid'")
    n = len(df)
    idx = df.index
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up = spread if basis == "bid" else spread / 2           # price -> ask
    dn = 0.0 if basis == "bid" else spread / 2              # price -> bid
    step = pd.Series(idx).diff().mode().iloc[0] if n > 1 else pd.Timedelta(minutes=5)
    close_t = idx + step
    win_open = np.array([in_weekend_window(t) for t in idx])
    win_close = np.array([in_weekend_window(t) for t in close_t])
    s_sig = sig["signal"].to_numpy(int)
    s_stop = sig["stop"].to_numpy(float)
    s_exit = sig["exit"].to_numpy(int) if "exit" in sig else np.zeros(n, int)
    cols = {k: (sig[k].to_numpy(float) if k in sig else np.full(n, np.nan))
            for k in ("trail_start_r", "trail_buf", "spike_pts", "spike_min_r", "spike_mode")}
    trades: list[dict] = []
    pos = None
    pending = None
    for i in range(n):
        # 1) entry at this bar's open
        if pending is not None and pos is None:
            side, stop, j = pending
            entry = (o[i] + up + slip) if side == 1 else (o[i] - dn - slip)
            if side * (entry - stop) > 0:
                pos = dict(side=side, entry=entry, stop=stop, init_stop=stop, i0=i, entry_time=idx[i],
                           close_at=weekend_close(idx[i]), weekend=weekend_id(idx[i]),
                           trs=cols["trail_start_r"][j], tbuf=cols["trail_buf"][j],
                           spk=cols["spike_pts"][j], spr=cols["spike_min_r"][j], spm=cols["spike_mode"][j])
        pending = None
        # 2) stop inside this bar (stops trigger on the price you exit at)
        if pos is not None:
            s, st = pos["side"], pos["stop"]
            hit = None
            if s == 1:
                bo, bl = o[i] - dn, l[i] - dn
                if bo <= st:
                    hit = (bo - slip, "stop (gap)")
                elif bl <= st:
                    hit = (st - slip, "stop")
            else:
                ao, ah = o[i] + up, h[i] + up
                if ao >= st:
                    hit = (ao + slip, "stop (gap)")
                elif ah >= st:
                    hit = (st + slip, "stop")
            if hit is not None:
                _close(pos, i, hit[0], hit[1], trades)
                pos = None
        # 3) Sunday 18:00 close / strategy exit signal at this bar's close
        if pos is not None:
            s = pos["side"]
            exit_px = (c[i] - dn - slip) if s == 1 else (c[i] + up + slip)
            if close_t[i] >= pos["close_at"]:
                _close(pos, i, exit_px, "sunday close", trades)
                pos = None
            elif s_exit[i] != 0 and s_exit[i] in (2, s):
                _close(pos, i, exit_px, "exit signal", trades)
                pos = None
        # 4) trail from +1R and the big-candle lock, decided at this close, live next bar
        if pos is not None and i >= 2 and not np.isnan(pos["trs"]):
            new = trail_candidate(pos["side"], pos["entry"], pos["init_stop"], pos["stop"], pos["trs"],
                                  0.0 if np.isnan(pos["tbuf"]) else pos["tbuf"],
                                  o[i - 2:i + 1], h[i - 2:i + 1], l[i - 2:i + 1], c[i - 2:i + 1])
            if new is not None:
                pos["stop"] = new
        if pos is not None and pos["spm"] == 2:
            new = spike_lock(pos["side"], pos["entry"], pos["init_stop"], pos["stop"], pos["spk"],
                             1.0 if np.isnan(pos["spr"]) else pos["spr"], o[i], c[i])
            if new is not None:
                pos["stop"] = new
        # 5) new signal at this bar's close: inside the window, filters known now
        if pos is None and s_sig[i] != 0 and win_close[i] and i + 1 < n and win_open[i + 1]:
            one_r = abs(c[i] - s_stop[i])
            if spread <= max_spread and one_r >= cost_mult * spread:
                pending = (int(s_sig[i]), float(s_stop[i]), i)
    if pos is not None:                                      # data ends inside a weekend
        s = pos["side"]
        _close(pos, n - 1, (c[-1] - dn - slip) if s == 1 else (c[-1] + up + slip), "end of data", trades)
    return trades


def _close(pos: dict, i: int, fill: float, reason: str, trades: list) -> None:
    risk = abs(pos["entry"] - pos["init_stop"])
    trades.append({"entry_time": pos["entry_time"], "weekend": pos["weekend"], "side": pos["side"],
                   "entry": pos["entry"], "exit": fill, "exit_bar": i, "reason": reason,
                   "r": pos["side"] * (fill - pos["entry"]) / risk})


def stats(r: np.ndarray) -> dict:
    n = len(r)
    cum = np.concatenate([[0.0], np.cumsum(r)])
    t = float(r.mean() / (r.std(ddof=1) / np.sqrt(n))) if n >= 3 and r.std(ddof=1) > 0 else 0.0
    return {"n": n, "win%": float(100 * (r > 0).mean()) if n else 0.0, "expR": float(r.mean()) if n else 0.0,
            "totalR": float(r.sum()), "maxDD": float(np.max(np.maximum.accumulate(cum) - cum)), "t": t}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default="data/raw/BTCUSD_5m.csv")
    ap.add_argument("--meta", default="research/btc_weekend_data.json")
    ap.add_argument("--spread", type=float, default=None, help="override: fixed spread in $")
    ap.add_argument("--basis", choices=("bid", "mid"), default=None)
    args = ap.parse_args()
    meta = json.loads(Path(args.meta).read_text()) if Path(args.meta).exists() else {}
    spread = args.spread if args.spread is not None else float(meta["live_spread"]["p90"])
    basis = args.basis or meta.get("bar_price_basis", {}).get("verdict", "mid")
    basis = basis if basis in ("bid", "mid") else "mid"
    base = load_csv(args.data)
    print(f"DATA {base.index[0]} -> {base.index[-1]} ({len(base):,} bars) | spread ${spread:.2f} fixed "
          f"(p90 live weekend sample) + ${SLIP:.0f} slippage/side | bars = {basis} prices | risk {RISK_NOTE}",
          flush=True)
    frames = {"5m": resample(base, "5m"), "15m": resample(base, "15m")}
    rows, all_trades = [], []
    for name, (tf, params) in MODELS.items():
        df = frames[tf]
        sig = build_strategy(name, SESSION, dict(params)).generate(df)
        tr = pd.DataFrame(simulate(df, sig, spread, basis))
        if not tr.empty:
            tr.insert(0, "model", name)
            all_trades.append(tr)
        print(f"{name}: {len(tr)} trades", flush=True)
    trades = pd.concat(all_trades) if all_trades else pd.DataFrame(columns=["model", "weekend", "r"])
    last = base.index[-1]
    weekends = sorted({weekend_id(t) for t in frames["5m"].index if in_weekend_window(t)
                       and weekend_close(t) <= last})           # complete weekends only
    trades = trades[trades["weekend"].isin(weekends)]
    cut = weekends[len(weekends) // 2]
    print(f"weekends: {len(weekends)} ({weekends[0]} -> {weekends[-1]}); half B starts {cut}")
    trades["part"] = np.where(trades["weekend"] < cut, "A", "B")
    trades.to_csv("research/btc_weekend_trades.csv", index=False)
    for name in MODELS:
        for part in ("A", "B"):
            r = trades[(trades.model == name) & (trades.part == part)]["r"].to_numpy(float)
            s = stats(r)
            s["label"] = ("STRONG" if s["n"] >= 100 and s["t"] >= 2.5 else
                          "promising, not proven" if s["n"] < 100 else "")
            rows.append({"model": name, "half": part, **s})
    t = pd.DataFrame(rows)
    t.to_csv("research/btc_weekend_results.csv", index=False)
    pd.set_option("display.width", 200)
    print(t.round(3).to_string(index=False))
    A = t[t.half == "A"].sort_values("totalR", ascending=False).iloc[0]
    B = t[(t.half == "B") & (t.model == A.model)].iloc[0]
    ok = (B.expR > 0, B.n >= 30, B.maxDD <= 20.0)
    verdict = "STRONG" if all(ok) and B.n >= 100 and B.t >= 2.5 else "PASS" if all(ok) else "NOT PASSED"
    print(f"\nVERDICT: picked on A = {A.model} (A {A.totalR:+.1f}R, n={int(A.n)}). Half B: exp {B.expR:+.3f}R "
          f"[{'ok' if ok[0] else 'FAIL'}], n={int(B.n)} [{'ok' if ok[1] else 'FAIL'}], maxDD {B.maxDD:.1f}R "
          f"[{'ok' if ok[2] else 'FAIL'}], t={B.t:.2f} -> {verdict}"
          + (" (promising, not proven: < 100 trades)" if verdict == "PASS" and B.n < 100 else ""))


if __name__ == "__main__":
    main()
