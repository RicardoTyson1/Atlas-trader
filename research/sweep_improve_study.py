"""Improving the two new setups (pre-registered 2026-11-14, Ricardo: "meticulous and super smart").

MORNING  break-and-retest (live: 5m, 4H trend, overnight 18:00-08:00 levels, 08:00-11:00, trailing).
EVENING  sweep reversal  (live: 15m, 4H trend, NY-day 08:00-17:00 levels, 15m swing break, 20:00-24:00,
         half off at +1R + breakeven + trail).

Method (fixed BEFORE running, to avoid fitting the past):
  1. Each refinement is tested ALONE against the live rules ("base").
  2. A refinement is KEPT only if, on HALF A (2023-03 -> 2025-01) and the setup's own exit,
     it raises BOTH expectancy (R/trade) and win rate, with >= 40 trades in half A.
  3. All kept refinements are combined ("combo"). Half B (2025-01 -> 2026-11) is then the
     out-of-sample check - nothing is chosen on it.
  4. Deploy bar: combo has >= 100 trades, expectancy > 0 in both halves and beats base in both halves.
Refinements (one change each):
  morning: break_body_atr 1.0 | break_close_atr 0.25 | retest_wait 6 (30 min) | retest_wait 12 (60 min)
           | first_touch_only | wick_frac 0.4 (rejection candle) | stop_mode level (tight stop)
           | extra_levels prevday | extra_levels 02:00-08:00 (London)
  evening: extra_levels prevday | 02:00-08:00 | prevday+02:00-08:00 | max_wait 12 | stop_buffer_atr 0.1
Costs: real spread + slippage + $7/lot per side, in R. Fills: the engine takes the STOP first when a
5m candle touches both stop and target, i.e. the worst case, so tight stops are not flattered.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/sweep_improve_study.py data/raw/XAUUSD.R_5m.csv
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

SETUPS = {
    "morning": {"tf": "5m", "exit": "base",
                "base": {"mode": "retest", "bias": "4h", "ref_session": "18:00-24:00,00:00-08:00",
                         "hours": "08:00-11:00"},
                "variants": {"body1.0": {"break_body_atr": 1.0}, "close0.25": {"break_close_atr": 0.25},
                             "wait30m": {"retest_wait": 6}, "wait60m": {"retest_wait": 12},
                             "first_touch": {"first_touch_only": True}, "wick0.4": {"wick_frac": 0.4},
                             "stop_level": {"stop_mode": "level"}, "lv_prevday": {"extra_levels": "prevday"},
                             "lv_london": {"extra_levels": "02:00-08:00"}}},
    "evening": {"tf": "15m", "exit": "half1_be",
                "base": {"mode": "reversal", "confirm": "choch", "bias": "4h", "ref_session": "08:00-17:00",
                         "hours": "20:00-24:00"},
                "variants": {"lv_prevday": {"extra_levels": "prevday"},
                             "lv_london": {"extra_levels": "02:00-08:00"},
                             "lv_both": {"extra_levels": "prevday+02:00-08:00"},
                             "wait12": {"max_wait": 12}, "buf0.1": {"stop_buffer_atr": 0.1}}},
}
EXITS = ["base", "half1_be", "half0.5_be"]
DATA: dict = {}


def merge_params(items):
    out = {}
    for d in items:
        for k, v in d.items():
            if k == "extra_levels" and out.get(k):
                v = "+".join(dict.fromkeys(out[k].split("+") + v.split("+")))
            out[k] = v
    return out


def run(job):
    setup, name, params, part = job
    cfg, inst = research_cfg()
    df = DATA[(SETUPS[setup]["tf"], part)]
    t = run_backtest(df, build_strategy("session_sweep", cfg.session, params), inst, cfg).trades
    out = {e: np.array([replay(df, tr, inst, e) for tr in t.itertuples()]) if len(t) else np.array([])
           for e in EXITS}
    print(f"done {setup} {name} {part}: {len(t)} trades", flush=True)
    return setup, name, part, out


def stats(a, b):
    r = np.concatenate([a, b])

    def m(x):
        return float(x.mean()) if len(x) else 0.0

    def w(x):
        return float(100 * (x > 0).mean()) if len(x) else 0.0
    t = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 and r.std() > 0 else 0.0
    return {"n": len(r), "nA": len(a), "nB": len(b), "win%": round(w(r), 1), "A_win%": round(w(a), 1),
            "B_win%": round(w(b), 1), "expR": round(m(r), 3), "A_exp": round(m(a), 3), "B_exp": round(m(b), 3),
            "totR": round(float(r.sum()), 1), "t": round(t, 2)}


def run_round(jobs):
    with Pool() as pool:
        res = pool.map(run, jobs, chunksize=1)
    tab = {}
    for setup, name, part, out in res:
        tab.setdefault((setup, name), {})[part] = out
    return tab


def main(path5):
    base = load_csv(path5)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        DATA[("5m", part)] = d
        DATA[("15m", part)] = resample(d, "15m")
    # round 1: base + each refinement alone
    jobs = []
    for s, cfg in SETUPS.items():
        jobs += [(s, "base", cfg["base"], part) for part in ("A", "B")]
        jobs += [(s, v, {**cfg["base"], **p}, part) for v, p in cfg["variants"].items() for part in ("A", "B")]
    tab = run_round(jobs)
    # selection on half A only
    combos = []
    kept = {}
    for s, cfg in SETUPS.items():
        e = cfg["exit"]
        b = tab[(s, "base")]
        bA = stats(b["A"][e], b["B"][e])
        kept[s] = [v for v in cfg["variants"]
                   if (st := stats(tab[(s, v)]["A"][e], tab[(s, v)]["B"][e]))["nA"] >= 40
                   and st["A_exp"] > bA["A_exp"] and st["A_win%"] >= bA["A_win%"]]
        if kept[s]:
            p = merge_params([cfg["base"]] + [cfg["variants"][v] for v in kept[s]])
            combos += [(s, "COMBO", p, part) for part in ("A", "B")]
    if combos:
        tab.update(run_round(combos))
    rows = []
    for (s, name), parts in tab.items():
        for e in EXITS:
            rows.append({"setup": s, "variant": name, "exit": e, "selection_exit": e == SETUPS[s]["exit"],
                         **stats(parts["A"][e], parts["B"][e])})
    t = pd.DataFrame(rows).sort_values(["setup", "selection_exit", "A_exp"], ascending=[True, False, False])
    t.to_csv("research/sweep_improve_results.csv", index=False)
    pd.set_option("display.width", 250)
    print(t[t.selection_exit].drop(columns="selection_exit").to_string(index=False))
    for s in SETUPS:
        e = SETUPS[s]["exit"]
        print(f"KEPT {s}: {kept[s] or 'none'}")
        sel = t[(t.setup == s) & (t.exit == e)].set_index("variant")
        if "COMBO" in sel.index:
            c, b = sel.loc["COMBO"], sel.loc["base"]
            ok = c.n >= 100 and c.A_exp > 0 and c.B_exp > 0 and c.A_exp > b.A_exp and c.B_exp > b.B_exp
            print(f"COMBO {s}: n {c.n} win {c['win%']}% exp {c.expR}R (A {c.A_exp} / B {c.B_exp}) vs base "
                  f"n {b.n} win {b['win%']}% exp {b.expR}R (A {b.A_exp} / B {b.B_exp}) -> "
                  f"{'DEPLOY' if ok else 'do not deploy'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
