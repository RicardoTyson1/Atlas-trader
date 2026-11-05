"""Nasdaq shock study: what does gold do when stocks drop fast?

Question (from 2026-11-05: Nasdaq into correction, Dow -1,000, gold fell with stocks):
when the Nasdaq drops hard and fast, which way does gold go next, and should the bot pause,
lean short, or lean long?

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX NAS100.R and XAUUSD.R 1-minute bars, aligned (same timestamps).
  * SHOCK = the first minute of a trading day (day starts 18:00 NY) when NAS100 is down
    >= `drop` % versus its price 60 minutes earlier. drop in {1.0, 1.5}. One event per day.
  * For each shock: gold's move over the same 60 minutes ("during"), and after the shock:
    +30 min, +2 h, to 16:50 NY (end of the bot's day), all in US dollars per ounce.
  * Baseline: gold's move over the same horizons from the same minute of day on ALL days
    (so time-of-day drift is not mistaken for a shock effect).
  * Split: shocks where gold FELL during the drop (liquidation) vs ROSE (safe haven).
  * The bot's live models: their replayed trades opened within 2 h after a shock vs all others.
  * A rule goes into the bot only if the effect has the same sign in both halves of the data
    and |t| >= 2 overall. Otherwise the bot does not trade off it (pause rule considered from
    the bot-trades comparison only).

Run:  python research/shock_study.py data/raw/NAS100.R_1m.csv data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

TZ = "America/New_York"
HORIZONS = {"30m": 30, "2h": 120}


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std() / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else 0.0


def shocks(nas: pd.DataFrame, drop: float) -> pd.DatetimeIndex:
    c = nas["close"]
    prev = c.shift(60, freq="min").reindex(c.index)
    chg = (c / prev - 1) * 100
    local = c.index.tz_convert(TZ)
    tday = (local + pd.Timedelta(hours=6)).date
    hit = pd.Series(chg <= -drop, index=c.index)
    first = hit[hit].groupby(pd.Series(tday, index=c.index)[hit]).head(1)
    return first.index


def gold_moves(gold: pd.DataFrame, t: pd.Timestamp) -> dict:
    c = gold["close"]
    def at(ts):
        s = c.loc[:ts]
        return float(s.iloc[-1]) if len(s) else np.nan
    p0 = at(t)
    out = {"during": p0 - at(t - pd.Timedelta(minutes=60))}
    for k, m in HORIZONS.items():
        out[k] = at(t + pd.Timedelta(minutes=m)) - p0
    loc = t.tz_convert(TZ)
    eod = loc.normalize() + pd.Timedelta(hours=16, minutes=50)
    if loc >= eod:
        eod += pd.Timedelta(days=1)
    out["to_16:50"] = at(eod.tz_convert("UTC")) - p0
    return out


def baseline(gold: pd.DataFrame, minute_of_day: int) -> dict:
    """Gold's average move over each horizon from this minute of day, all days."""
    c = gold["close"]
    loc = c.index.tz_convert(TZ)
    m = (loc.hour * 60 + loc.minute) == minute_of_day
    starts = c[m]
    res = {}
    for k, mins in HORIZONS.items():
        fut = c.reindex(starts.index + pd.Timedelta(minutes=mins))
        d = fut.to_numpy() - starts.to_numpy()
        res[k] = float(np.nanmean(d)) if np.isfinite(d).any() else 0.0
    return res


def main(nas_path: str, gold_path: str) -> None:
    nas, gold = load_csv(nas_path), load_csv(gold_path)
    common = nas.index.intersection(gold.index)
    nas, gold = nas.loc[common], gold.loc[common]
    mid = common[len(common) // 2]
    print(f"DATA {common[0].date()} -> {common[-1].date()} ({len(common):,} shared minutes), halves split {mid.date()}\n")

    for drop in (1.0, 1.5):
        ev = shocks(nas, drop)
        rows = []
        for t in ev:
            r = gold_moves(gold, t)
            r["time"] = t
            r["half"] = "A" if t < mid else "B"
            loc = t.tz_convert(TZ)
            b = baseline(gold, loc.hour * 60 + loc.minute)
            for k in HORIZONS:
                r[k + "_vs_base"] = r[k] - b[k]
            rows.append(r)
        df = pd.DataFrame(rows)
        print(f"=== SHOCK: Nasdaq -{drop}% in 60 min  ({len(df)} days) ===")
        if df.empty:
            continue
        print(f"Gold during the drop: fell on {int((df.during < 0).sum())} of {len(df)} shocks "
              f"(avg {df.during.mean():+.2f} $/oz)")
        for col in ("30m_vs_base", "2h_vs_base", "to_16:50"):
            a, b_ = df[df.half == "A"][col], df[df.half == "B"][col]
            print(f"  gold {col:12s}: avg {df[col].mean():+7.2f} $/oz  up {100 * (df[col] > 0).mean():4.0f}%  "
                  f"t {tstat(df[col]):+5.2f}  | A {a.mean():+7.2f}  B {b_.mean():+7.2f}")
        for name, grp in (("gold FELL during drop", df[df.during < 0]), ("gold ROSE during drop", df[df.during >= 0])):
            if len(grp) < 3:
                continue
            print(f"  {name} ({len(grp)}): next 2h {grp['2h_vs_base'].mean():+.2f} $/oz (t {tstat(grp['2h_vs_base']):+.2f}), "
                  f"to 16:50 {grp['to_16:50'].mean():+.2f} (t {tstat(grp['to_16:50']):+.2f})")
        print("  events:", ", ".join(f"{t.tz_convert(TZ):%Y-%m-%d %H:%M}" for t in df.time[:40]))
        print()

    # live models' replayed trades: opened within 2 h after a -1% shock vs the rest
    ev = shocks(nas, 1.0)
    cfg, inst = g.research_cfg(False)
    live = load_config("config/default.yaml")
    print("=== Bot's live models: trades opened within 2 h after a -1% shock vs all others ===")
    for spec in live.strategies:
        data = gold if spec.timeframe == "1m" else resample(gold, spec.timeframe)
        t = run_backtest(data, build_strategy(spec.name, cfg.session, dict(spec.params)), inst, cfg).trades
        if t.empty:
            continue
        et = pd.to_datetime(t["entry_time"], utc=True)
        near = np.zeros(len(t), bool)
        for s in ev:
            near |= ((et >= s) & (et <= s + pd.Timedelta(hours=2))).to_numpy()
        r = t["r"].astype(float)
        print(f"  {spec.name:15s} after shock: n {near.sum():3d} avg {r[near].mean():+.3f}R | "
              f"other: n {(~near).sum():4d} avg {r[~near].mean():+.3f}R")


if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("data/raw/NAS100.R_1m.csv", "data/raw/XAUUSD.R_1m.csv")))
