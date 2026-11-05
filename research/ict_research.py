"""Pre-registered test of ICT models on GatesFX 5m data (XAUUSD, NAS100, US30, SPX500).

Protocol (fixed BEFORE running):
  * DEV   = 2024-11 -> 2026-11 (data/raw/<SYM>_5m.csv). Used for selection only.
  * FRESH = 2022-11 -> 2024-11 (gold: 2023-03 ->) (data/raw/<SYM>_5m_fresh.csv).
            Never looked at before this study. Each selected config is run on it ONCE.
  * Edge in R per trade (fine-grained sizing, no risk halts, real spread/slippage/fees).
  * DEV candidate: >= 40 trades, expectancy > 0 in BOTH halves of DEV, t-stat >= 1.5.
    Per (model, market) the candidate with the best worst-half expectancy goes to FRESH.
  * PASS on FRESH: expectancy > 0 and t-stat >= 1.5.
  * Also run once on FRESH: the earlier `liquidity_fvg` config from config/default.yaml.

Run:  PYTHONPATH=research python research/ict_research.py
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.config import load_config
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy
from gold_research import grid, r_stats, research_cfg as _gold_cfg

SYMS = ["XAUUSD.R", "NAS100.R", "US30.R", "SPX500.R"]
SMT_PAIR = {"NAS100.R": "SPX500.R", "SPX500.R": "NAS100.R", "US30.R": "SPX500.R"}

MODELS = {
    "silver_bullet": [dict(windows=w, entry=e, target=t, reward_risk=rr, bias=b)
                      for w in (["sb_am"], ["sb_london", "sb_am", "sb_pm"])
                      for e in ("ce", "edge")
                      for t, rr in (("rr", 2.0), ("rr", 3.0), ("liquidity", 2.0))
                      for b in ("none", "discount")],
    "ict2022": [dict(windows=w, target=t, reward_risk=2.0, bias=b)
                for w in (["london"], ["ny_am"], ["london", "ny_am"])
                for t in ("rr", "liquidity") for b in ("none", "discount")],
    "judas_po3": [dict(windows=w, target=t, reward_risk=2.0, bias=b)
                  for w in (["ny_am"], ["london", "ny_am"])
                  for t in ("rr", "liquidity") for b in ("daily_trend", "discount")],
    "smt": [dict(windows=w, target=t, reward_risk=2.0)
            for w in (["ny_am"], ["london", "ny_am"]) for t in ("rr", "liquidity")],
    "ote": [dict(level=lv, target=t, reward_risk=2.0)
            for lv in (0.62, 0.705, 0.79) for t in ("extreme", "ext27", "rr")],
}

DATA: dict[tuple[str, str], pd.DataFrame] = {}


def cfg_inst(sym):
    cfg = load_config("config/default.yaml")
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.prop.daily_breach_permanent = False
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 50
    cfg.risk.max_lots = 1e12
    cfg.risk.max_room_fraction_per_trade = 1.0
    cfg.risk.cooldown_after_loss_minutes = 0
    cfg.risk.max_sizing_equity = None
    from datetime import time
    cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(15, 0)
    import copy
    inst = copy.deepcopy(cfg.instrument(sym))
    inst.min_lot = inst.lot_step = 1e-9
    return cfg, inst


def run(job):
    model, params, sym, part = job
    cfg, inst = cfg_inst(sym)
    strat = build_strategy(model, cfg.session, params)
    if model == "smt":
        strat.other = DATA[(SMT_PAIR[sym], part)]
    t = run_backtest(DATA[(sym, part)], strat, inst, cfg).trades
    if t.empty:
        return model, json.dumps(params, sort_keys=True), sym, part, pd.DataFrame(columns=["entry_time", "r"])
    return model, json.dumps(params, sort_keys=True), sym, part, t[["entry_time", "r"]]


def main() -> None:
    for s in SYMS:
        DATA[(s, "dev")] = load_csv(f"data/raw/{s}_5m.csv")
        DATA[(s, "fresh")] = load_csv(f"data/raw/{s}_5m_fresh.csv")
    mid = {s: DATA[(s, "dev")].index[0] + (DATA[(s, "dev")].index[-1] - DATA[(s, "dev")].index[0]) / 2 for s in SYMS}
    jobs = [(m, p, s, "dev") for m, cfgs in MODELS.items() for p in cfgs for s in SYMS
            if not (m == "smt" and s not in SMT_PAIR)]
    print(f"{len(MODELS)} ICT models, {len(jobs)} pre-registered (config x market) runs on DEV", flush=True)
    with Pool() as pool:
        res = pool.map(run, jobs)
    rows = []
    for m, key, s, _, t in res:
        r = t["r"] if len(t) else pd.Series(dtype=float)
        h1 = r[t["entry_time"] < mid[s]] if len(t) else r
        h2 = r[t["entry_time"] >= mid[s]] if len(t) else r
        a, b1, b2 = r_stats(r), r_stats(h1), r_stats(h2)
        rows.append({"model": m, "sym": s, "params": key, "n": a["n"], "expR": a["expR"], "t": a["t"],
                     "win%": a["win%"], "h1": b1["expR"], "h2": b2["expR"], "worst": min(b1["expR"], b2["expR"])})
    dev = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_colwidth", 120)
    print("\nDEV family summary (all configs):")
    print(dev.groupby(["model", "sym"]).agg(configs=("expR", "size"), positive=("expR", lambda x: int((x > 0).sum())),
                                            median_expR=("expR", "median"), best_expR=("expR", "max"),
                                            avg_trades=("n", "mean")).round(3).to_string())
    cands = dev[(dev.n >= 40) & (dev.h1 > 0) & (dev.h2 > 0) & (dev.t >= 1.5)]
    picks = cands.sort_values("worst", ascending=False).groupby(["model", "sym"]).head(1)
    print(f"\n{len(cands)} of {len(dev)} runs meet the DEV bar; {len(picks)} (model, market) picks go to FRESH.")
    if len(picks):
        print(picks[["model", "sym", "params", "n", "expR", "t", "h1", "h2"]].round(3).to_string(index=False))
    fresh_jobs = [(r.model, json.loads(r.params), r.sym, "fresh") for r in picks.itertuples()]
    # the trader's own earlier model, as configured in config/default.yaml
    base = load_config("config/default.yaml").strategies[0]
    fresh_jobs.append(("liquidity_fvg", base.params, "XAUUSD.R", "fresh"))
    with Pool() as pool:
        fres = pool.map(run, fresh_jobs)
    print("\nFRESH 2022/23-2024 (evaluated once):")
    out = []
    for m, key, s, _, t in fres:
        st = r_stats(t["r"] if len(t) else pd.Series(dtype=float))
        verdict = "PASS" if st["expR"] > 0 and st["t"] >= 1.5 else "FAIL"
        out.append({"verdict": verdict, "model": m, "sym": s, "n": st["n"], "expR": round(st["expR"], 3),
                    "t": round(st["t"], 2), "win%": round(st["win%"], 1), "params": key})
    print(pd.DataFrame(out).to_string(index=False))
    dev.to_csv("research/ict_dev_results.csv", index=False)
    pd.DataFrame(out).to_csv("research/ict_fresh_results.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
