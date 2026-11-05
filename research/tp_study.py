"""Take-profit study: does the levels TP (FVG 50% / liquidity / major S/R) help the live models?

Protocol (fixed BEFORE looking at results):
  * Models exactly as they run live in config/default.yaml (momentum_rsi 5m, reversal_trail 1m),
    08:00-11:00 NY, real GatesFX costs, no account limits (research_cfg).
  * Variants per model (2 x 2):
      TP     : the current exit (momentum_rsi: no target, ride the trail;
               reversal_trail: 50% of the fast move)  vs  `levels` (nearest level >= 1R)
      trail  : start at 0R (first profit, live since 2026-11-05)  vs  1R (as tested before)
  * Data split in halves A/B. Levels TP is BETTER only if its expectancy beats the current
    exit in BOTH halves with the same trail start. Otherwise the live config stays.

Run:  python research/tp_study.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool

import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

LIVE = {s.name: (s.timeframe, dict(s.params)) for s in load_config("config/default.yaml").strategies}
JOBS = []
for name, (tf, params) in LIVE.items():
    for tp in ("current", "levels"):
        for ts in (0.0, 1.0):
            p = {**params, "trail_start_r": ts}
            if tp == "levels":
                if name == "momentum_rsi":
                    p["tp"] = "levels"
                else:
                    p["target"] = "levels"
            JOBS.append({"model": name, "tf": tf, "tp": tp, "trail_start": ts, "params": p})
DATA: dict = {}


def _run(job):
    spec, part = job
    cfg, inst = g.research_cfg(False)
    res = run_backtest(DATA[(spec["tf"], part)], build_strategy(spec["model"], cfg.session, spec["params"]), inst, cfg)
    t = res.trades
    r = t["r"].astype(float) if len(t) else pd.Series(dtype=float)
    reasons = t["reason"].value_counts().to_dict() if len(t) and "reason" in t else {}
    return spec, part, r, reasons


def main(path: str) -> None:
    base = load_csv(path)
    a, b = split_in_out(base, 0.5)
    for part, d in (("A", a), ("B", b)):
        DATA[("1m", part)] = d
        DATA[("5m", part)] = resample(d, "5m")
    print(f"DATA {base.index[0].date()} -> {base.index[-1].date()} | A ends {a.index[-1].date()}")
    with Pool(2) as pool:
        out = pool.map(_run, [(j, part) for j in JOBS for part in ("A", "B")])
    rows = {}
    for spec, part, r, reasons in out:
        key = (spec["model"], spec["tp"], spec["trail_start"])
        rows.setdefault(key, {"A": None, "B": None, "reasons": {}})
        rows[key][part] = r
        for k, v in reasons.items():
            rows[key]["reasons"][k] = rows[key]["reasons"].get(k, 0) + v
    tab = []
    for (model, tp, ts), d in rows.items():
        allr = pd.concat([d["A"], d["B"]])
        s = g.r_stats(allr)
        top = sorted(d["reasons"].items(), key=lambda x: -x[1])[:3]
        tab.append({"model": model, "TP": tp, "trail@": ts, "n": s["n"], "win%": round(s["win%"], 1),
                    "expR": round(s["expR"], 3), "t": round(s["t"], 2), "totalR": round(float(allr.sum()), 1),
                    "A": round(g.r_stats(d["A"])["expR"], 3), "B": round(g.r_stats(d["B"])["expR"], 3),
                    "exits": ", ".join(f"{k} {v}" for k, v in top)})
    t = pd.DataFrame(tab).sort_values(["model", "trail@", "TP"])
    pd.set_option("display.width", 250, "display.max_colwidth", 80)
    print(t.to_string(index=False))
    print("\nVERDICT (levels must beat current in BOTH halves, same trail start):")
    for (model, ts), grp in t.groupby(["model", "trail@"]):
        cur = grp[grp.TP == "current"].iloc[0]
        lev = grp[grp.TP == "levels"].iloc[0]
        better = lev.A > cur.A and lev.B > cur.B
        print(f"  {model:15s} trail@{ts:.0f}R: levels {'BETTER' if better else 'not better'} "
              f"(A {lev.A:+.3f} vs {cur.A:+.3f}, B {lev.B:+.3f} vs {cur.B:+.3f})")
    print("\nJSON", json.dumps(tab))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
