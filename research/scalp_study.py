"""Scalper study: can 60-second gold scalps beat GatesFX costs? (Ricardo's theory, 2026-11-06)

Protocol (fixed BEFORE running):
  * Data: GatesFX XAUUSD.R 1m, 2024-06 -> 2026-11, halves A/B by date.
  * Every trade: signal at a 1m close, market entry next open, exit at stop / target / that
    candle's close (hold <= ~60 s). Stop 1 x ATR(14, 1m). Stop first if both hit in one candle.
  * Costs as live: spread 0.14 + slippage 0.10 per side + $7/lot. Also run with ZERO costs to
    show how much of the edge the costs take ("gross").
  * 44 configs: cont k{1,1.5,2} | fade k{1.5,2,3} | rsi_fade 20/80, 10/90 | breakout n{15,30} |
    momentum k1 ; hours {all, 08-11 NY} ; target {time exit only, 1R}.
  * PASS (demo): expectancy > 0 in BOTH halves after costs, >= 100 trades per half.
    STRONG: also t >= 2.5 in both halves.

Run:  python research/scalp_study.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import copy
import json
import sys
from multiprocessing import Pool

from datetime import time

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.config import load_config
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy

SYM = "XAUUSD.R"


def research_cfg():
    """Live config with every account limit lifted, so only the strategy is measured (in R)."""
    cfg = load_config("config/default.yaml")
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 10**7
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

RULES = ([dict(mode="cont", k=k) for k in (1.0, 1.5, 2.0)]
         + [dict(mode="fade", k=k) for k in (1.5, 2.0, 3.0)]
         + [dict(mode="rsi_fade", rsi_lo=20.0, rsi_hi=80.0), dict(mode="rsi_fade", rsi_lo=10.0, rsi_hi=90.0)]
         + [dict(mode="breakout", n=n) for n in (15, 30)]
         + [dict(mode="momentum", k=1.0)])
CONFIGS = [dict(r, hours=h, target_r=t, stop_atr=1.0, hold_bars=1)
           for r in RULES for h in ("00:00-23:59", "08:00-11:00") for t in (0.0, 1.0)]
DATA: dict = {}


def run(job):
    params, part, costs = job
    cfg, inst = research_cfg()
    if not costs:
        inst = copy.deepcopy(inst)
        inst.spread_points = inst.slippage_points = inst.commission_per_lot = 0.0
    t = run_backtest(DATA[part], build_strategy("scalp", cfg.session, params), inst, cfg).trades
    return json.dumps(params, sort_keys=True), part, costs, (t["r"] if len(t) else pd.Series(dtype=float))


def main(path):
    base = load_csv(path)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    DATA["A"], DATA["B"] = base[base.index < mid], base[base.index >= mid]
    print(f"A {DATA['A'].index[0]:%Y-%m-%d} -> {DATA['A'].index[-1]:%Y-%m-%d} | B -> {DATA['B'].index[-1]:%Y-%m-%d} "
          f"| {len(base):,} 1m bars | {len(CONFIGS)} configs", flush=True)
    jobs = [(c, part, costs) for c in CONFIGS for part in ("A", "B") for costs in (True, False)]
    with Pool() as pool:
        res = pool.map(run, jobs)
    rows = {}
    for key, part, costs, r in res:
        st = r_stats(r)
        d = rows.setdefault(key, json.loads(key))
        tag = part if costs else f"{part}_gross"
        d[f"{tag}_n"], d[f"{tag}_expR"], d[f"{tag}_t"] = st["n"], st["expR"], st["t"]
        if costs:
            d[f"{part}_win%"] = st["win%"]
    df = pd.DataFrame(rows.values())
    df["hours"] = df.hours.map({"00:00-23:59": "all", "08:00-11:00": "08-11"})
    df["rule"] = df.apply(lambda x: x["mode"] + (f" k{x['k']}" if x["mode"] in ("cont", "fade", "momentum") else
                                                 f" n{int(x['n'])}" if x["mode"] == "breakout" else
                                                 f" {int(x['rsi_lo'])}/{int(x['rsi_hi'])}"), axis=1)
    df["verdict"] = df.apply(lambda x: "STRONG" if min(x.A_expR, x.B_expR) > 0 and min(x.A_n, x.B_n) >= 100
                             and min(x.A_t, x.B_t) >= 2.5 else "PASS" if min(x.A_expR, x.B_expR) > 0
                             and min(x.A_n, x.B_n) >= 100 else "fail", axis=1)
    cols = ["verdict", "rule", "hours", "target_r", "A_n", "A_win%", "A_expR", "A_gross_expR", "B_n", "B_win%",
            "B_expR", "B_gross_expR", "A_t", "B_t"]
    pd.set_option("display.width", 250)
    print(df.sort_values(["verdict", "B_expR"], ascending=[True, False])[cols].round(3).to_string(index=False))
    cost_r = ((df.A_gross_expR - df.A_expR) + (df.B_gross_expR - df.B_expR)) / 2
    print(f"\nCosts take {cost_r.median():.2f}R per trade (median over configs).")
    print(f"PASS: {(df.verdict != 'fail').sum()} of {len(df)} | STRONG: {(df.verdict == 'STRONG').sum()}")
    df.to_csv("research/scalp_results.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
