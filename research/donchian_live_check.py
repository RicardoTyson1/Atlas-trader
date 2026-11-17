"""Can the 1H Donchian trend (the only 16-year PASS, research/library_16y_study.py) join the live bot?
Pre-registered 2026-11-17 (Ricardo approved adding it "as recommended").

Two questions the 16-year test did not answer:
 1. The test HELD trades overnight and over weekends. The live bot is flat every day 16:50-18:05 NY.
    donchian_hold = as tested; donchian_flat = flat every day at the daily break (modelled with the
    gold day as the calendar day: Europe/Berlin = NY + 6h, flat 22:50, entries 00:05-22:30;
    US/EU daylight-saving dates differ ~3 weeks a year - 1 hour off in those weeks).
 2. The bot holds ONE trade at a time. A Donchian trade can block the morning setups (and vice versa).
    The new lineup (momentum_rsi removed) is replayed on one slot, config order = priority,
    with and without Donchian at the lowest priority. The volatility switch (volr >= 1.2) applies to
    every setup except Donchian (it made Donchian worse: +86.7R -> +19.8R).
    The 1m reversal is modelled on 5m (no 1m data before 2025).
Rule (fixed before running): Donchian goes live only in the version (hold or flat) that
 a) passes on its own (>= 200 trades, profit in both halves, >= 4 of 6 periods), and
 b) makes the one-slot lineup better in BOTH halves than the lineup without it.
Run in /opt/atlas-research, one job per process (1 GB server):
  for j in donchian_hold donchian_flat smc_ob_am break_retest_am reversal_5m_am sweep_eve; do
    PYTHONPATH=.:research nice -n 19 python research/donchian_live_check.py $j; done
  PYTHONPATH=.:research python research/donchian_live_check.py report
"""
from __future__ import annotations

import os
import sys
from datetime import time

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from exit_options_study import replay, research_cfg
from portfolio_study import one_slot
from regime_study import daily_features

AM = "08:00-11:00"
DON = {"entry_bars": 55, "exit_bars": 20}
JOBS = {  # name: (model, timeframe, params, exit mode)
    "donchian_hold": ("donchian_trend", "1H", DON, "base"),
    "donchian_flat": ("donchian_trend", "1H", DON, "base"),
    "smc_ob_am": ("smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}, "base"),
    "break_retest_am": ("session_sweep", "5m", {"mode": "retest", "bias": "4h", "wick_frac": 0.4,
                                                "ref_session": "18:00-24:00,00:00-08:00", "hours": AM}, "base"),
    "reversal_5m_am": ("reversal_trail", "5m", {"move_atr": 3.0, "rsi_level": 25, "target": "half_move",
                                                "spike_atr": 2.5, "hours": AM}, "half0.5_be"),
    "sweep_eve": ("session_sweep", "15m", {"mode": "reversal", "confirm": "choch", "bias": "4h",
                                           "ref_session": "08:00-17:00", "hours": "20:00-24:00"}, "half1_be"),
}
LINEUP = ["smc_ob_am", "reversal_5m_am", "break_retest_am", "sweep_eve"]   # live config order
PERIODS = [("2010-2012", 2012), ("2013-2015", 2015), ("2016-2018", 2018),
           ("2019-2021", 2021), ("2022-2024", 2024), ("2025-2026", 2026)]


def out(j):
    return f"research/dl_{j}.csv"


def run_job(name: str) -> None:
    model, tf, params, ex = JOBS[name]
    gc = load_csv("data/raw/GC_5m.csv")
    df = gc if tf == "5m" else resample(gc, tf)
    del gc
    cfg, inst = research_cfg()
    if name == "donchian_flat":
        s = cfg.session
        s.timezone, s.reopen, s.overnight = "Europe/Berlin", None, False
        s.trade_start, s.last_entry, s.flat_by = time(0, 5), time(22, 30), time(22, 50)
    t = run_backtest(df, build_strategy(model, cfg.session, params), inst, cfg).trades
    if ex == "base":
        r = t["r"] - 2 * inst.commission_per_lot / ((t["entry"] - t["init_stop"]).abs() * inst.value_per_point)
    else:
        r = [replay(df, tr, inst, ex) for tr in t.itertuples()]
    pd.DataFrame({"entry_time": t["entry_time"], "exit_time": t["exit_time"], "r": np.round(r, 4)}).to_csv(
        out(name), index=False)
    print(f"done {name}: {len(t)} trades, {float(np.sum(r)):.1f}R", flush=True)


def stats(x: pd.DataFrame) -> dict:
    if not len(x):
        x = pd.DataFrame({"entry_time": pd.to_datetime([], utc=True), "r": []})
    y = pd.to_datetime(x.entry_time, utc=True).dt.tz_convert("America/New_York").dt.year
    per = {p: round(float(x.r[(y <= hi) & (y > (PERIODS[k - 1][1] if k else 2009))].sum()), 1)
           for k, (p, hi) in enumerate(PERIODS)}
    old, new = float(x.r[y <= 2018].sum()), float(x.r[y >= 2019].sum())
    return {"n": len(x), "win%": round(100 * float((x.r > 0).mean()), 1), "totR": round(float(x.r.sum()), 1),
            "older_R": round(old, 1), "newer_R": round(new, 1),
            "pos_periods": sum(v > 0 for v in per.values()), **per}


def report() -> None:
    L = {}
    for j in JOBS:
        d = pd.read_csv(out(j))
        d["entry_time"] = pd.to_datetime(d.entry_time, utc=True)
        d["exit_time"] = pd.to_datetime(d.exit_time, utc=True)
        L[j] = d
    f = daily_features("data/raw/GC_5m.csv")[["volr"]]
    for j in LINEUP:                                  # live: volatility switch on these setups
        d = L[j]
        gday = pd.to_datetime((d.entry_time.dt.tz_convert("America/New_York") + pd.Timedelta(hours=6)).dt.date)
        L[j] = d[(gday.map(f.volr) >= 1.2).to_numpy()]
    pd.set_option("display.width", 250)
    rows = {j: stats(L[j]) for j in JOBS}
    print("EACH SETUP ALONE (morning/evening setups: volatility switch on; Donchian: off)")
    print(pd.DataFrame(rows).T.to_string())
    combos = {"lineup (no Donchian)": LINEUP,
              "lineup + Donchian hold": LINEUP + ["donchian_hold"],
              "lineup + Donchian flat daily": LINEUP + ["donchian_flat"]}
    res = {}
    for k, order in combos.items():
        t = one_slot(L, order)
        res[k] = stats(t)
        res[k]["by_setup"] = (t.groupby("strategy").r.agg(["count", "sum"]).round(1).to_dict("index")
                              if len(t) else {})
    print("\nONE TRADE AT A TIME (config order = priority)")
    print(pd.DataFrame({k: {a: b for a, b in v.items() if a != "by_setup"} for k, v in res.items()}).T.to_string())
    for k, v in res.items():
        print(f"  {k}: {v['by_setup']}")
    base = res["lineup (no Donchian)"]
    for v in ("hold", "flat"):
        alone = rows[f"donchian_{v}"]
        a = alone["n"] >= 200 and alone["older_R"] > 0 and alone["newer_R"] > 0 and alone["pos_periods"] >= 4
        c = res[f"lineup + Donchian {'hold' if v == 'hold' else 'flat daily'}"]
        b = c["older_R"] > base["older_R"] and c["newer_R"] > base["newer_R"]
        print(f"Donchian {v}: passes alone {a}, improves lineup in both halves {b} -> {'GO' if a and b else 'NO'}")
    # how often Donchian is in a trade when the morning window opens (blocks the 08-11 setups)
    for v in ("hold", "flat"):
        d = L[f"donchian_{v}"]
        days = pd.date_range("2010-07-01", "2026-11-13", freq="B", tz="America/New_York") + pd.Timedelta(hours=8)
        s = np.searchsorted(d.entry_time.values, days.tz_convert("UTC").values, side="right") - 1
        ok = s >= 0
        busy = np.zeros(len(days), bool)
        busy[ok] = d.exit_time.values[s[ok]] > days.tz_convert("UTC").values[ok]
        print(f"Donchian {v}: in a trade at 08:00 NY on {100 * busy.mean():.0f}% of weekdays")


if __name__ == "__main__":
    report() if sys.argv[1] == "report" else run_job(sys.argv[1])
