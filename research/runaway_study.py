"""Runaway lock study, pre-registered 2026-11-05.

Ricardo (after the 11/05 short gave back ~$140 from its best): "the trail should have been more
aggressive when price ran away in our favor." Rule tested (backtest.runaway_lock): once the best
open profit since entry reaches START x initial risk, the stop is kept at KEEP x that best profit.

Protocol (fixed BEFORE looking at results):
  * Models exactly as live in config/default.yaml (momentum_rsi 5m, reversal_trail 1m), their
    normal candle trail from 1R stays on. GatesFX 1m data, halves A/B, real costs.
  * Variants: off | (START 1.0, KEEP 0.5) | (1.5, 0.5) | (1.5, 0.7) | (2.0, 0.7).
  * A variant goes live only if its expectancy beats "off" in BOTH halves for that model; if
    several do, the one with the best total R. Otherwise the runaway lock stays off.

Run:  python research/runaway_study.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import sys

import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

VARIANTS = [None, (1.0, 0.5), (1.5, 0.5), (1.5, 0.7), (2.0, 0.7)]


def main(path: str) -> None:
    base = load_csv(path)
    a, b = split_in_out(base, 0.5)
    data = {("1m", "A"): a, ("1m", "B"): b, ("5m", "A"): resample(a, "5m"), ("5m", "B"): resample(b, "5m")}
    print(f"DATA {base.index[0].date()} -> {base.index[-1].date()} | A ends {a.index[-1].date()}\n")
    live = load_config("config/default.yaml")
    rows = []
    for spec in live.strategies:
        for v in VARIANTS:
            parts = {}
            for part in ("A", "B"):
                cfg, inst = g.research_cfg(False)
                cfg.exits = {} if v is None else {"runaway_start_r": v[0], "runaway_keep": v[1]}
                t = run_backtest(data[(spec.timeframe, part)], build_strategy(spec.name, cfg.session, dict(spec.params)),
                                 inst, cfg).trades
                parts[part] = t["r"].astype(float) if len(t) else pd.Series(dtype=float)
            allr = pd.concat([parts["A"], parts["B"]])
            s = g.r_stats(allr)
            rows.append({"model": spec.name, "runaway": "off" if v is None else f"start {v[0]}R keep {int(v[1]*100)}%",
                         "n": s["n"], "win%": round(s["win%"], 1), "expR": round(s["expR"], 3), "t": round(s["t"], 2),
                         "totalR": round(float(allr.sum()), 1), "A": round(g.r_stats(parts["A"])["expR"], 3),
                         "B": round(g.r_stats(parts["B"])["expR"], 3)})
            print("done", spec.name, v, flush=True)
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print()
    print(t.to_string(index=False))
    print("\nVERDICT:")
    for model, grp in t.groupby("model"):
        off = grp[grp.runaway == "off"].iloc[0]
        win = grp[(grp.runaway != "off") & (grp.A > off.A) & (grp.B > off.B)].sort_values("totalR", ascending=False)
        print(f"  {model}: " + ("keep it off (no variant beats off in both halves)" if win.empty else
              f"use {win.iloc[0].runaway} ({win.iloc[0].totalR:+.1f}R vs {off.totalR:+.1f}R)"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
