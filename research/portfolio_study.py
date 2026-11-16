"""Whole-lineup check: does the London-fix short add money once it competes for the ONE trade slot?

The bot holds one position at a time; strategies are checked in config order, so a trade from a
higher-priority strategy (or any open trade) blocks the others. Each strategy's trades come from
its own backtest with its live exit (research/exit_options_study.py replay, costs in R); then the
lineup is replayed on one timeline: a trade is taken only if no other trade is open at its entry
(ties at the same entry -> config order). Variants:
  base        : smc_ob, momentum_rsi, reversal_1m, break_retest           (the live morning lineup)
  fix_last    : base + London-fix short (08:00 -> 10:00) at the lowest priority
  fix_first   : London-fix short at the highest priority
Not modelled: the news blackout (no historical calendar in the research data). The fix short enters
at 08:00 and 08:30 releases would block some of its days live.
Data: 5m 2023-03 -> 2026-11 (the 1m reversal only exists for the last 13 months), halves A/B.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/portfolio_study.py data/raw/XAUUSD.R_5m.csv data/raw/XAUUSD.R_1m.csv
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
LINEUP = {
    "smc_ob": ("smc_ob", "5m", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}, "base"),
    "momentum_rsi": ("momentum_rsi", "5m", "5m",
                     {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0, "trail_buf_atr": 0.3,
                      "spike_atr": 2.0, "max_candle_atr": 2.5, "max_run_atr": 3.0, "run_bars": 12, "hours": AM},
                     "base"),
    "reversal_1m": ("reversal_trail", "1m", "1m",
                    {"move_atr": 5.0, "rsi_level": 25, "target": "half_move", "spike_atr": 0.0, "hours": AM},
                    "half0.5_be"),
    "break_retest": ("session_sweep", "5m", "5m",
                     {"mode": "retest", "bias": "4h", "ref_session": "18:00-24:00,00:00-08:00", "hours": AM,
                      "wick_frac": 0.4}, "base"),
    "fix_short": ("evening", "5m", "5m",
                  {"mode": "overnight", "hours": AM, "entry_time": "08:00", "direction": "short",
                   "hold_bars": 24}, "base"),
}
VARIANTS = {
    "base": ["smc_ob", "momentum_rsi", "reversal_1m", "break_retest"],
    "fix_last": ["smc_ob", "momentum_rsi", "reversal_1m", "break_retest", "fix_short"],
    "fix_first": ["fix_short", "smc_ob", "momentum_rsi", "reversal_1m", "break_retest"],
}
DATA: dict = {}


def trades_of(job):
    name, part = job
    model, tf, data, params, ex = LINEUP[name]
    cfg, inst = research_cfg()
    df = DATA[(data, tf, part)]
    t = run_backtest(df, build_strategy(model, cfg.session, params), inst, cfg).trades
    if not len(t):
        return name, part, pd.DataFrame(columns=["entry_time", "exit_time", "r"])
    r = [replay(df, tr, inst, ex) for tr in t.itertuples()]
    out = pd.DataFrame({"entry_time": t["entry_time"], "exit_time": t["exit_time"], "r": r})
    print(f"done {name} {part}: {len(out)} trades", flush=True)
    return name, part, out


def one_slot(lists: dict, order: list) -> pd.DataFrame:
    rows = []
    for prio, name in enumerate(order):
        d = lists[name].copy()
        d["strategy"], d["prio"] = name, prio
        rows.append(d)
    allt = pd.concat(rows).sort_values(["entry_time", "prio"])
    taken, busy_until = [], None
    for tr in allt.itertuples():
        if busy_until is not None and tr.entry_time < busy_until:
            continue
        taken.append(tr)
        busy_until = tr.exit_time
    return pd.DataFrame(taken)


def main(path5, path1):
    for key, path in (("5m", path5), ("1m", path1)):
        base = load_csv(path)
        mid5 = DATA.get("mid")
        if key == "5m":
            mid5 = base.index[0] + (base.index[-1] - base.index[0]) / 2
            DATA["mid"] = mid5
        for part, d in (("A", base[base.index < mid5]), ("B", base[base.index >= mid5])):
            for tf in {v[1] for v in LINEUP.values() if v[2] == key}:
                DATA[(key, tf, part)] = d if tf == key else resample(d, tf)
    with Pool() as pool:
        res = pool.map(trades_of, [(n, p) for n in LINEUP for p in ("A", "B")], chunksize=1)
    lists = {"A": {}, "B": {}}
    for name, part, d in res:
        lists[part][name] = d
    rows = []
    for v, order in VARIANTS.items():
        for part in ("A", "B"):
            t = one_slot(lists[part], order)
            for name in order + ["TOTAL"]:
                s = t if name == "TOTAL" else t[t.strategy == name]
                rows.append({"variant": v, "half": part, "strategy": name, "n": len(s),
                             "win%": round(100 * float((s.r > 0).mean()), 1) if len(s) else 0.0,
                             "totR": round(float(s.r.sum()), 1) if len(s) else 0.0})
    out = pd.DataFrame(rows)
    piv = out.pivot_table(index=["variant", "strategy"], columns="half", values=["n", "totR"], aggfunc="sum")
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv["totR"] = piv["totR_A"] + piv["totR_B"]
    out.to_csv("research/portfolio_results.csv", index=False)
    pd.set_option("display.width", 200)
    print(piv.to_string())
    tot = out[out.strategy == "TOTAL"]
    print(tot.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_1m.csv")
