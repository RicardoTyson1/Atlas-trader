"""Pre-registered test of ICT (Inner Circle Trader) models on gold, on FRESH data.

Models (rules in the strategy docstrings):
  * silver_bullet  - ICT Silver Bullet: FVG inside 03-04 / 10-11 / 14-15 NY windows.
  * ict_2022       - ICT 2022 model = killzone liquidity sweep -> displacement FVG ->
                     retrace entry (strategy `liquidity_fvg`), full 81-variant grid.

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX XAUUSD.R 5m. Only bars BEFORE 2024-11-04 are used. Everything from
    2024-11-04 on was already used by earlier studies, so it is excluded here.
  * The fresh period is split in two halves (A = older, B = newer).
  * Costs as in gold_research.py. Edge in R per trade, no risk halts.
  * A config PASSES only if: expectancy > 0 in BOTH halves, >= 100 trades in total,
    and t-stat >= 3.0 on the whole fresh period (strict, because 135 configs are tested
    at once; with that many tries, t ~ 2.5 shows up by luck alone).
  * Family view: share of configs with positive expectancy and the median expectancy.
    A real, robust edge should be positive for most variants, not 1 lucky config.

Run:  python research/ict_research.py data/raw/XAUUSD.R_5m_long.csv
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy

FRESH_END = pd.Timestamp("2024-11-04", tz="UTC")
MIN_TRADES, MIN_T = 100, 3.0

FAMILIES = {
    "silver_bullet": [dict(windows=w, bias=b, require_sweep=s, target=t, reward_risk=rr)
                      for w in ("am_london", "am_ny", "pm_ny") for b in ("trend", "discount", "none")
                      for s in (True, False)
                      for t, rr in (("rr", 2.0), ("rr", 3.0), ("liquidity", 2.0))],
    "ict_2022": [dict(reward_risk=rr, target=t, bias=b, liquidity=lq, killzones=k)
                 for b in ("discount", "trend", "none") for lq in ("pdhl", "asian", "both")
                 for k in ("london", "ny", "both")
                 for t, rr in (("rr", 2.0), ("rr", 3.0), ("liquidity", 2.0))],
}
STRATEGY = {"silver_bullet": "silver_bullet", "ict_2022": "liquidity_fvg"}
DATA: dict[str, pd.DataFrame] = {}


def _run(job):
    fam, params, part = job
    cfg, inst = g.research_cfg(False)
    t = run_backtest(DATA[part], build_strategy(STRATEGY[fam], cfg.session, params), inst, cfg).trades
    return fam, json.dumps(params, sort_keys=True), part, (t["r"] if len(t) else pd.Series(dtype=float))


def main(path: str) -> None:
    base = load_csv(path)
    fresh = base[base.index < FRESH_END]
    if len(fresh) == 0:
        print("No bars before 2024-11-04 in this file. Download a longer history first.")
        return
    days = fresh.index.normalize().nunique()
    a, b = split_in_out(fresh, 0.5)
    DATA["A"], DATA["B"] = a, b
    print(f"FRESH {fresh.index[0].date()} -> {fresh.index[-1].date()} ({days} days) | "
          f"A ends {a.index[-1].date()}, B starts {b.index[0].date()}")
    if days < 250:
        print("WARNING: under ~1 year of fresh data; treat any result as weak.")
    jobs = [(f, p, part) for f, cfgs in FAMILIES.items() for p in cfgs for part in ("A", "B")]
    print(f"{len(jobs) // 2} pre-registered configs\n")
    with Pool() as pool:
        res = pool.map(_run, jobs)
    tab: dict[tuple, dict] = {}
    for fam, key, part, r in res:
        tab.setdefault((fam, key), {})[part] = r
    rows = []
    for (fam, key), parts in tab.items():
        sa, sb = g.r_stats(parts["A"]), g.r_stats(parts["B"])
        s = g.r_stats(pd.concat([parts["A"], parts["B"]]))
        rows.append({"family": fam, "params": key, "n": s["n"], "expR": s["expR"], "t": s["t"],
                     "win%": s["win%"], "A_expR": sa["expR"], "B_expR": sb["expR"]})
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_colwidth", 110)
    for fam, grp in t.groupby("family"):
        pos = (grp.expR > 0).mean() * 100
        print(f"{fam}: {len(grp)} configs | positive {pos:.0f}% | median expR {grp.expR.median():+.3f} "
              f"| median win% {grp['win%'].median():.0f}")
        print(grp.sort_values("t", ascending=False).head(5).round(3).to_string(index=False), "\n")
    ok = t[(t.A_expR > 0) & (t.B_expR > 0) & (t.n >= MIN_TRADES) & (t.t >= MIN_T)]
    print(f"VERDICT: {len(ok)} of {len(t)} configs pass (both halves > 0, n >= {MIN_TRADES}, t >= {MIN_T}).")
    if len(ok):
        print(ok.round(3).to_string(index=False))
        print("Next step for any pass: demo forward test only, never straight to a real account.")
    else:
        print("No ICT variant shows a reliable edge on gold after costs in this period.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m_long.csv")
