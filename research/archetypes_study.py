"""Pre-registered test of three "high win-rate" archetypes on gold (Ricardo, 2026-11-06).

Models (exact rules in each strategy's docstring):
  bb_reversion  : fade a close beyond the k-sigma Bollinger band back to the middle band.
  smc_ob        : H4 EMA bias -> 5m liquidity sweep -> displacement FVG -> order-block retest.
  st_confluence : SuperTrend trend + MACD confirmation + volatility/volume filter, pullback entry.

Protocol (fixed BEFORE running):
  * Data: GatesFX XAUUSD.R 5m, 2023-03 -> 2026-11 (3.7 years), split into halves A/B by date.
    15m = resampled from 5m. Real spread + slippage + $7/lot. Edge in R per trade, no halts.
  * 52 configs (26 below, each at 08-11 NY and at all hours).
  * PASS (goes on the demo bot): expectancy > 0 in BOTH halves after costs and >= 100 trades.
    STRONG: also t >= 2.5. Per model, the passing config with the best worst-half expectancy
    is the one deployed. A model with no passing config is not installed.

Run:  python research/archetypes_study.py data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

SYM = "XAUUSD.R"
HOURS = {"08-11": "08:00-11:00", "all": "00:00-23:59"}
CONFIGS = (
    [dict(model="bb_reversion", tf=tf, bb_k=k, max_slope_atr=sl)
     for tf in ("5m", "15m") for k in (2.0, 2.5, 3.0) for sl in (0.0, 1.5)]
    + [dict(model="smc_ob", tf="5m", sweep_bars=sb, reward_risk=rr)
       for sb in (24, 48) for rr in (2.0, 3.0, 0.0)]
    + [dict(model="st_confluence", tf=tf, st_mult=m, vol_mult=vm)
       for tf in ("5m", "15m") for m in (2.0, 3.0) for vm in (1.0, 0.0)]
)
CONFIGS = [dict(c, hours=h) for c in CONFIGS for h in HOURS]
DATA: dict = {}


def research_cfg():
    cfg = load_config("config/default.yaml")
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 10**6
    cfg.risk.max_lots = 1e12
    cfg.risk.max_room_fraction_per_trade = 1.0
    cfg.risk.cooldown_after_loss_minutes = 0
    cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(23, 59)
    cfg.session.overnight = False
    inst = copy.deepcopy(cfg.instrument(SYM))
    inst.min_lot = inst.lot_step = 1e-9
    return cfg, inst


def r_stats(r: pd.Series) -> dict:
    r = r.astype(float)
    n = len(r)
    if n < 3:
        return {"n": n, "expR": float(r.mean()) if n else 0.0, "t": 0.0, "win%": 0.0}
    return {"n": n, "expR": float(r.mean()), "t": float(r.mean() / (r.std() / np.sqrt(n))),
            "win%": float(100 * (r > 0).mean())}


def run(job):
    c, part = job
    p = dict(c)
    model, tf = p.pop("model"), p.pop("tf")
    p["hours"] = HOURS[p["hours"]]
    cfg, inst = research_cfg()
    t = run_backtest(DATA[(tf, part)], build_strategy(model, cfg.session, p), inst, cfg).trades
    return json.dumps(c, sort_keys=True), part, (t["r"] if len(t) else pd.Series(dtype=float))


def main(path: str) -> None:
    base = load_csv(path)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        DATA[("5m", part)] = d
        DATA[("15m", part)] = resample(d, "15m")
    a, b = DATA[("5m", "A")], DATA[("5m", "B")]
    print(f"A {a.index[0]:%Y-%m-%d} -> {a.index[-1]:%Y-%m-%d} | B {b.index[0]:%Y-%m-%d} -> "
          f"{b.index[-1]:%Y-%m-%d} | {len(CONFIGS)} configs", flush=True)
    with Pool() as pool:
        res = pool.map(run, [(c, part) for c in CONFIGS for part in ("A", "B")])
    tab: dict = {}
    for key, part, r in res:
        tab.setdefault(key, {})[part] = r
    rows = []
    for key, parts in tab.items():
        s = r_stats(pd.concat([parts["A"], parts["B"]]))
        sa, sb = r_stats(parts["A"]), r_stats(parts["B"])
        c = json.loads(key)
        rows.append({"model": c["model"], "cfg": key, "n": s["n"], "win%": s["win%"], "expR": s["expR"],
                     "t": s["t"], "A": sa["expR"], "B": sb["expR"], "totalR": s["expR"] * s["n"]})
    t = pd.DataFrame(rows)
    t["PASS"] = (t.A > 0) & (t.B > 0) & (t.n >= 100)
    t["STRONG"] = t.PASS & (t.t >= 2.5)
    t.to_csv("research/archetypes_results.csv", index=False)
    pd.set_option("display.width", 250, "display.max_colwidth", 140)
    print(t.groupby("model").agg(configs=("n", "size"), passed=("PASS", "sum"), strong=("STRONG", "sum"),
                                 median_win=("win%", "median"), median_expR=("expR", "median"),
                                 best_t=("t", "max")).round(3).to_string(), "\n")
    for m, g in t.groupby("model"):
        best = g[g.PASS].assign(worst=lambda x: x[["A", "B"]].min(axis=1)).sort_values("worst", ascending=False)
        print(f"{m}: " + ("NO passing config -> not installed" if best.empty else
                          "DEPLOY " + best.iloc[0].cfg + f"  n={best.iloc[0].n} win={best.iloc[0]['win%']:.1f}% "
                          f"expR={best.iloc[0].expR:+.3f} t={best.iloc[0].t:.2f} A={best.iloc[0].A:+.3f} B={best.iloc[0].B:+.3f}"))
    print("\nTop 12 by t:")
    print(t.sort_values("t", ascending=False).head(12).drop(columns="cfg").assign(
        cfg=t.sort_values("t", ascending=False).head(12).cfg.str.replace('"', "")).round(3).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
