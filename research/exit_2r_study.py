"""2R exits on the live lineup (Ricardo, 2026-11-14: "what about a 2R move?").

Same entries as the live bot (live settings and hours), replayed with:
  live     : each strategy's live exit (trailing; reversal = half at 0.5R + breakeven;
             evening sweep = half at 1R + breakeven + trail)
  tp2      : one fixed target at +2R, initial stop, no trailing
  half2    : close half at +2R, the rest keeps the live trailing exit
  half2_be : same, and the rest's stop moves to breakeven at +2R
Costs: real spread + slippage + $7/lot per side, in R. Halves A/B as every study.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/exit_2r_study.py data/raw/XAUUSD.R_5m.csv data/raw/XAUUSD.R_1m.csv
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

AM = "08:00-11:00"
LINEUP = [  # (label, model, tf, data, params, live exit)
    ("smc_ob", "smc_ob", "5m", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}, "base"),
    ("st_confluence", "st_confluence", "15m", "5m", {"st_mult": 3.0, "vol_mult": 0.0, "hours": AM}, "base"),
    ("momentum_rsi", "momentum_rsi", "5m", "5m",
     {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0, "trail_buf_atr": 0.3, "spike_atr": 2.0,
      "max_candle_atr": 2.5, "max_run_atr": 3.0, "run_bars": 12, "hours": AM}, "base"),
    ("reversal_1m", "reversal_trail", "1m", "1m",
     {"move_atr": 5.0, "rsi_level": 25, "target": "half_move", "spike_atr": 0.0, "hours": AM}, "half0.5_be"),
    ("break_retest", "session_sweep", "5m", "5m",
     {"mode": "retest", "bias": "4h", "ref_session": "18:00-24:00,00:00-08:00", "hours": AM, "wick_frac": 0.4}, "base"),
    ("evening_sweep", "session_sweep", "15m", "5m",
     {"mode": "reversal", "confirm": "choch", "bias": "4h", "ref_session": "08:00-17:00", "hours": "20:00-24:00"},
     "half1_be"),
]
EXITS = ["tp2", "half2", "half2_be"]
DATA: dict = {}


def run(job):
    label, model, tf, data, params, live, part = job
    cfg, inst = research_cfg()
    df = DATA[(data, tf, part)]
    t = run_backtest(df, build_strategy(model, cfg.session, params), inst, cfg).trades
    out = {name: np.array([replay(df, tr, inst, e) for tr in t.itertuples()]) if len(t) else np.array([])
           for name, e in [("live", live)] + [(e, e) for e in EXITS]}
    print(f"done {label} {part}: {len(t)} trades", flush=True)
    return label, part, out


def main(path5, path1):
    for key, path in (("5m", path5), ("1m", path1)):
        base = load_csv(path)
        mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
        for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
            for tf in {m[2] for m in LINEUP if m[3] == key}:
                DATA[(key, tf, part)] = d if tf == key else resample(d, tf)
    jobs = [(*m, part) for m in LINEUP for part in ("A", "B")]
    with Pool() as pool:
        res = pool.map(run, jobs, chunksize=1)
    tab = {}
    for label, part, out in res:
        tab.setdefault(label, {})[part] = out
    rows = []
    groups = {m[0]: [m[0]] for m in LINEUP}
    groups["ALL_MORNING"] = [m[0] for m in LINEUP if m[0] != "evening_sweep"]
    for g, members in groups.items():
        for e in ["live"] + EXITS:
            a = np.concatenate([tab[m]["A"][e] for m in members])
            b = np.concatenate([tab[m]["B"][e] for m in members])
            r = np.concatenate([a, b])
            rows.append({"strategy": g, "exit": e, "n": len(r),
                         "win%": round(100 * (r > 0).mean(), 1) if len(r) else 0.0,
                         "expR": round(r.mean(), 3) if len(r) else 0.0, "totR": round(r.sum(), 1),
                         "A_totR": round(a.sum(), 1), "B_totR": round(b.sum(), 1)})
    t = pd.DataFrame(rows)
    t.to_csv("research/exit_2r_results.csv", index=False)
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_1m.csv")
