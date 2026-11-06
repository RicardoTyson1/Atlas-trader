"""Does a 'no-chase' filter stop momentum_rsi buying exhausted spikes? And do off-hours
entries pay? Prompted by live demo trades on 2026-11-05/06 (e.g. 09:35 NY buy after a
2.7-ATR spike candle and a 4.1-ATR run, stopped out in 3 minutes for -$456).

Protocol (fixed BEFORE running):
  * Data: GatesFX XAUUSD.R 5m, 2023-03 -> 2026-11 (3.7 years), split into halves A/B by
    date. Real spread, slippage, $7/lot. Edge in R per trade. Base = the LIVE config
    (disp 1.0, EMA-50 trend, trail from 1R, trail buffer 0.3 ATR, spike 2.0).
  * Grid: hours {all (live), 08-11} x max_candle_atr {off, 2.0, 2.5} x max_run_atr {off, 3, 4}.
  * A filter is ADOPTED only if, versus no filter at the same hours, in BOTH halves:
    expectancy per trade is higher AND total R is not lower (the skipped trades lost net).
    Among adopted filters pick the one with the largest worst-half gain in total R.
  * Off-hours entries are kept only if, in BOTH halves, all-hours total R >= 08-11 total R
    (with the chosen filter).

Run:  python research/no_chase_study.py <5m csv> [<5m csv> ...]
"""
from __future__ import annotations

import itertools
import json
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy

LIVE = dict(disp_atr=1.0, trend_filter=True, trail_start_r=1.0, trail_buf_atr=0.3, spike_atr=2.0)
HOURS = {"all": "00:00-23:59", "08-11": "08:00-11:00"}
GRID = [dict(hours=h, max_candle_atr=mc, max_run_atr=mr)
        for h, mc, mr in itertools.product(HOURS, (0.0, 2.0, 2.5), (0.0, 3.0, 4.0))]
DATA: dict = {}


def run(job):
    cfgd, part = job
    p = {**LIVE, **cfgd, "hours": HOURS[cfgd["hours"]]}
    cfg, inst = g.research_cfg(False)
    t = run_backtest(DATA[part], build_strategy("momentum_rsi", cfg.session, p), inst, cfg).trades
    r = t["r"] if len(t) else pd.Series(dtype=float)
    return json.dumps(cfgd, sort_keys=True), part, r


def main(paths):
    base = pd.concat([load_csv(p) for p in paths]).sort_index()
    base = base[~base.index.duplicated()]
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    DATA["A"], DATA["B"] = base[base.index < mid], base[base.index >= mid]
    print(f"A {DATA['A'].index[0]:%Y-%m-%d} -> {DATA['A'].index[-1]:%Y-%m-%d} | "
          f"B {DATA['B'].index[0]:%Y-%m-%d} -> {DATA['B'].index[-1]:%Y-%m-%d} | {len(GRID)} configs", flush=True)
    with Pool() as pool:
        res = pool.map(run, [(c, part) for c in GRID for part in ("A", "B")])
    rows = {}
    for key, part, r in res:
        st = g.r_stats(r)
        d = rows.setdefault(key, json.loads(key))
        d[f"{part}_n"], d[f"{part}_expR"], d[f"{part}_totR"], d[f"{part}_t"] = st["n"], st["expR"], float(r.sum()), st["t"]
    df = pd.DataFrame(rows.values())
    pd.set_option("display.width", 220)
    print(df.round(3).to_string(index=False))

    print("\nFILTER VERDICTS (vs no filter at the same hours):")
    adopted = []
    for h in HOURS:
        b = df[(df.hours == h) & (df.max_candle_atr == 0) & (df.max_run_atr == 0)].iloc[0]
        for _, f in df[(df.hours == h) & ((df.max_candle_atr > 0) | (df.max_run_atr > 0))].iterrows():
            ok = all(f[f"{p}_expR"] > b[f"{p}_expR"] and f[f"{p}_totR"] >= b[f"{p}_totR"] for p in "AB")
            gain = min(f[f"{p}_totR"] - b[f"{p}_totR"] for p in "AB")
            tag = "ADOPT" if ok else "no"
            print(f"  {tag:<5} hours {h:<5} candle<= {f.max_candle_atr or '-':<4} run<= {f.max_run_atr or '-':<4} "
                  f"| totR A {f.A_totR:+.1f} (base {b.A_totR:+.1f}), B {f.B_totR:+.1f} (base {b.B_totR:+.1f}) "
                  f"| expR A {f.A_expR:+.3f}/{b.A_expR:+.3f}, B {f.B_expR:+.3f}/{b.B_expR:+.3f}")
            if ok:
                adopted.append((gain, h, f.max_candle_atr, f.max_run_atr))
    if adopted:
        adopted.sort(reverse=True)
        print(f"\nBEST ADOPTED FILTER per hours: {adopted}")
    for h in HOURS:
        pass
    # off-hours check, per filter setting
    print("\nOFF-HOURS CHECK (all-hours vs 08-11 total R, same filter):")
    for (mc, mr), grp in df.groupby(["max_candle_atr", "max_run_atr"]):
        a = grp[grp.hours == "all"].iloc[0]; o = grp[grp.hours == "08-11"].iloc[0]
        keep = all(a[f"{p}_totR"] >= o[f"{p}_totR"] for p in "AB")
        print(f"  candle<= {mc or '-':<4} run<= {mr or '-':<4} | off-hours adds A {a.A_totR - o.A_totR:+.1f}R, "
              f"B {a.B_totR - o.B_totR:+.1f}R -> {'KEEP off-hours' if keep else 'DROP off-hours'}")
    df.to_csv("research/no_chase_results.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1:])
