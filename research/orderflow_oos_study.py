"""One-shot out-of-sample check of order-flow gate V3 on COMEX gold 2010-2022 (Ricardo, 2026-11-17).

V3 PASSED on XAUUSD 2023-03 -> 2026-11 for smc_ob and momentum_rsi (docs/RESULTS_ORDERFLOW_2026-11-16.md).
This checks it ONCE, unchanged, on years that study never saw: COMEX GC 5m (Databento, back-adjusted)
2010-06 -> 2022-12, prices AND volume from GC itself. Costs in R as every study (XAUUSD.R spread,
slippage, $7/lot via research_cfg/replay, the same convention as research/long_history_study.py).
Live settings, live exits, 08:00-11:00 NY. No parameter changes, no retesting.

PASS (fixed BEFORE running), per model:
  1. V3 expectancy > 0 and > ungated expectancy over 2010-2022 combined;
  2. V3 expectancy > ungated in at least 3 of the 4 periods;
  3. V3 max drawdown (R) no more than 20% worse than ungated.
Only a model that passes all three goes forward as a proposal (smc_ob first). Report failures plainly.

Run in /opt/atlas-research (sequential, low memory):
  PYTHONPATH=.:research nice -n 19 python research/orderflow_oos_study.py data/raw/GC_5m.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from atlas_trader import orderflow
from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy
from exit_options_study import replay, research_cfg
from orderflow_gate_study import OFGated, max_dd, tstat

AM = "08:00-11:00"
MODELS = {
    "smc_ob": ("smc_ob", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}),
    "momentum_rsi": ("momentum_rsi", {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0,
                                      "trail_buf_atr": 0.3, "spike_atr": 2.0, "max_candle_atr": 2.5,
                                      "max_run_atr": 3.0, "run_bars": 12, "hours": AM}),
}
PERIODS = [("2010-2012", "2010", "2013"), ("2013-2015", "2013", "2016"),
           ("2016-2018", "2016", "2019"), ("2019-2022", "2019", "2023")]
GATES = [None, "V3"]


def run_period(gc: pd.DataFrame, a: str, b: str) -> list[dict]:
    start = pd.Timestamp(a, tz="UTC")
    d = gc[(gc.index >= start - pd.Timedelta(days=30)) & (gc.index < pd.Timestamp(b, tz="UTC"))]
    feats = orderflow.features(d, d)
    cfg, inst = research_cfg()
    out = []
    for name, (model, params) in MODELS.items():
        for g in GATES:
            strat = build_strategy(model, cfg.session, params)
            if g is not None:
                strat = OFGated(strat, feats, g)
            t = run_backtest(d, strat, inst, cfg).trades
            t = t[pd.to_datetime(t["entry_time"], utc=True) >= start] if len(t) else t
            r = np.array([replay(d, tr, inst, "base") for tr in t.itertuples()]) if len(t) else np.array([])
            out.append({"model": name, "gate": g or "ungated", "r": r})
            print(f"done {name} {g or 'ungated'} {a}-{b}: {len(r)} trades", flush=True)
    return out


def main(path_gc):
    gc = load_csv(path_gc)
    gc = gc[gc.index < pd.Timestamp("2023-01-01", tz="UTC")]
    print(f"GC {gc.index[0]} -> {gc.index[-1]}, {len(gc)} 5m bars (out-of-sample only)", flush=True)
    rows = []
    for label, a, b in PERIODS:
        for x in run_period(gc, a, b):
            r = x.pop("r")
            rows.append({**x, "period": label, "n": len(r), "expR": r.mean() if len(r) else 0.0,
                         "totR": r.sum(), "_r": r})
    T = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("\nBY PERIOD (expR / n):")
    print(T.pivot_table(index=["model", "gate"], columns="period", values="expR").round(3).to_string())
    print(T.pivot_table(index=["model", "gate"], columns="period", values="n").to_string())
    print("\nVERDICT:")
    summary = []
    for m in MODELS:
        s = {}
        for g in ("ungated", "V3"):
            rr = [x for x in T[(T.model == m) & (T.gate == g)].sort_values("period")["_r"]]
            r = np.concatenate(rr) if rr else np.array([])
            s[g] = {"n": len(r), "expR": r.mean() if len(r) else 0.0, "totR": r.sum(),
                    "dd": max_dd(r), "t": tstat(r)}
        per = T[T.model == m].pivot(index="period", columns="gate", values="expR")
        wins = int((per["V3"] > per["ungated"]).sum())
        c1 = s["V3"]["expR"] > 0 and s["V3"]["expR"] > s["ungated"]["expR"]
        c2 = wins >= 3
        c3 = s["V3"]["dd"] <= 1.2 * s["ungated"]["dd"]
        ok = c1 and c2 and c3
        for g in ("ungated", "V3"):
            summary.append({"model": m, "gate": g, "n": s[g]["n"], "expR": round(s[g]["expR"], 3),
                            "totR": round(s[g]["totR"], 1), "maxDD_R": round(s[g]["dd"], 1),
                            "t": round(s[g]["t"], 2)})
        print(f"  {m}: V3 {s['V3']['expR']:+.3f}R (n {s['V3']['n']}) vs ungated {s['ungated']['expR']:+.3f}R "
              f"(n {s['ungated']['n']}); beats ungated in {wins}/4 periods; DD {s['V3']['dd']:.1f}R vs "
              f"{s['ungated']['dd']:.1f}R -> {'PASS' if ok else 'FAIL'} (c1 {c1}, c2 {c2}, c3 {c3})", flush=True)
    S = pd.DataFrame(summary)
    print(S.to_string(index=False))
    T.drop(columns="_r").to_csv("research/orderflow_oos_periods.csv", index=False)
    S.to_csv("research/orderflow_oos_summary.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/GC_5m.csv")
