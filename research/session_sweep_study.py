"""Session sweep reversal study (pre-registered 2026-11-13, Ricardo's evening idea).

Setup (atlas_trader/strategies/session_sweep.py): price sweeps the last session's high or low, the
1H / 4H trend points the other way, a 5m or 15m candle confirms the reversal -> enter, stop beyond the
sweep, trail. Exits on the same entries: base (trail from 1R), half1_be (Ricardo's plan: half off at
+1R, stop to breakeven, trail the rest) and half0.5_be.

Windows (NY):
  evening : entries 20:00-24:00, levels = NY day session 08:00-17:00
  morning : entries 08:00-11:00, levels = overnight session 18:00-08:00
Grid (fixed BEFORE running, 12 configs per window): timeframe {5m, 15m} x bias {1h, 4h, both}
  x confirm {close_inside, choch}. Other settings = strategy defaults.
Costs: real spread + slippage + $7/lot per side, in R. Data: 5m 2023-03 -> 2026-11, halves A/B.
PASS (a config + exit): expectancy > 0 in BOTH halves and >= 100 trades.
To limit cherry-picking, the "pick" per window is the best total R on half A only; it must then be
positive on half B.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/session_sweep_study.py data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from exit_options_study import replay, research_cfg

WINDOWS = {"evening": ("20:00-24:00", "08:00-17:00"), "morning": ("08:00-11:00", "18:00-24:00,00:00-08:00")}
EXITS = ["base", "half1_be", "half0.5_be"]
GRID = [(tf, bias, conf) for tf in ("5m", "15m") for bias in ("1h", "4h", "both")
        for conf in ("close_inside", "choch")]
DATA: dict = {}


def run(job):
    wk, tf, bias, conf, part = job
    cfg, inst = research_cfg()
    df = DATA[(tf, part)]
    hours, ref = WINDOWS[wk]
    p = {"hours": hours, "ref_session": ref, "bias": bias, "confirm": conf}
    t = run_backtest(df, build_strategy("session_sweep", cfg.session, p), inst, cfg).trades
    out = {e: np.array([replay(df, tr, inst, e) for tr in t.itertuples()]) if len(t) else np.array([])
           for e in EXITS}
    print(f"done {wk} {tf} {bias} {conf} {part}: {len(t)} trades", flush=True)
    return (wk, tf, bias, conf), part, out


def main(path5):
    base = load_csv(path5)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        DATA[("5m", part)] = d
        DATA[("15m", part)] = resample(d, "15m")
    jobs = [(wk, *g, part) for wk in WINDOWS for g in GRID for part in ("A", "B")]
    with Pool() as pool:
        res = pool.map(run, jobs, chunksize=1)
    tab: dict = {}
    for key, part, out in res:
        tab.setdefault(key, {})[part] = out
    rows = []
    for (wk, tf, bias, conf), parts in tab.items():
        for e in EXITS:
            a, b = parts["A"][e], parts["B"][e]
            r = np.concatenate([a, b])
            rows.append({"window": wk, "tf": tf, "bias": bias, "confirm": conf, "exit": e, "n": len(r),
                         "win%": round(100 * (r > 0).mean(), 1) if len(r) else 0.0,
                         "expR": round(r.mean(), 3) if len(r) else 0.0, "totR": round(r.sum(), 1),
                         "A_totR": round(a.sum(), 1), "B_totR": round(b.sum(), 1),
                         "A_exp": round(a.mean(), 3) if len(a) else 0.0,
                         "B_exp": round(b.mean(), 3) if len(b) else 0.0})
    t = pd.DataFrame(rows)
    t["PASS"] = (t.A_exp > 0) & (t.B_exp > 0) & (t.n >= 100)
    t = t.sort_values(["window", "A_totR"], ascending=[True, False])
    t.to_csv("research/session_sweep_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))
    for wk in WINDOWS:
        pick = t[t.window == wk].iloc[0]
        print(f"PICK {wk}: {pick.tf} {pick.bias} {pick.confirm} {pick.exit} -> A {pick.A_totR}R, "
              f"B {pick.B_totR}R ({'holds' if pick.B_totR > 0 else 'FAILS'} on B), n {pick.n}, win {pick['win%']}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
