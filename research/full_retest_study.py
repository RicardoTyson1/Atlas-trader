"""Pre-registered re-test of EVERY setup Ricardo discussed, plus the new chart patterns (2026-11-09).

Why: Ricardo asked to re-add every removed setup and build the chart pattern library. Before they go
on the demo bot, each one gets the same test on the longest data we have, so the weekly reports can be
read against a known baseline.

Protocol (fixed BEFORE running):
  * Data 5m : GatesFX XAUUSD.R 5m, 2023-03 -> 2026-11 (3.7 years), split at the date midpoint into
              halves A / B (same split as archetypes_study.py).
  * Data 1m : GatesFX XAUUSD.R 1m, 2025-10 -> 2026-11 (13 months), halves A / B at the midpoint.
              Only for the setups that were designed on 1-minute candles (break_retest, reversal_trail).
  * Costs   : real spread + slippage + $7/lot from config/default.yaml. Edge in R per trade. No risk halts.
  * Hours   : every model that has an `hours` setting runs twice, "08-11" NY and "all" (00:00-23:59).
              Models with built-in time windows (orb, session_breakout, silver_bullet, time_drift,
              liquidity_fvg killzones, trend_pullback / vwap_reversion session) run once in their own window.
  * Settings: each model's own defaults, or the settings it was last tested / deployed with.
              No parameter search here: this is a re-test, not a new optimisation.
  * PASS    : expectancy > 0 in BOTH halves after costs and >= 100 trades. STRONG: also t >= 2.5.
  * Chart patterns: each of the 11 patterns alone, plus all 11 together, at both hours settings.
Run:  nice -n 19 python research/full_retest_study.py data/raw/XAUUSD.R_5m.csv data/raw/XAUUSD.R_1m.csv
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
from atlas_trader.strategies import REGISTRY, build_strategy
from atlas_trader.strategies.chart_patterns import ALL_PATTERNS

SYM = "XAUUSD.R"
HOURS = {"08-11": "08:00-11:00", "all": "00:00-23:59"}

# (label, model, timeframe, data, params)  -- data "5m" = 3.7 years, "1m" = 13 months
BASE_MODELS = [
    ("orb", "orb", "5m", "5m", {}),
    ("trend_pullback", "trend_pullback", "5m", "5m", {}),
    ("vwap_reversion", "vwap_reversion", "5m", "5m", {}),
    ("session_breakout", "session_breakout", "5m", "5m", {}),
    ("donchian_trend_1h", "donchian_trend", "1H", "5m", {"entry_bars": 55, "exit_bars": 20}),
    ("time_drift", "time_drift", "1H", "5m", {"entry_time": "20:00", "hold_bars": 3}),
    ("liquidity_fvg", "liquidity_fvg", "5m", "5m", {}),
    ("silver_bullet", "silver_bullet", "5m", "5m", {}),
    ("trend_break", "trend_break", "5m", "5m", {}),
    ("zone_retest", "zone_retest", "5m", "5m", {}),
    ("bb_reversion", "bb_reversion", "5m", "5m", {}),
    ("reversal_trail_5m", "reversal_trail", "5m", "5m",
     {"move_atr": 3.0, "rsi_level": 25, "target": "half_move", "spike_atr": 2.5}),
    ("reversal_trail_1m", "reversal_trail", "1m", "1m",
     {"move_atr": 5.0, "rsi_level": 25, "target": "half_move", "spike_atr": 0.0}),
    ("break_retest_1m", "break_retest", "1m", "1m", {}),
]
PATTERN_MODELS = [(f"pattern:{p}", "chart_patterns", "5m", "5m", {"patterns": p}) for p in ALL_PATTERNS]
PATTERN_MODELS.append(("pattern:all", "chart_patterns", "5m", "5m", {"patterns": "all"}))

CONFIGS = []
for label, model, tf, data, params in BASE_MODELS + PATTERN_MODELS:
    if "hours" in REGISTRY[model].defaults:
        for hk in HOURS:
            CONFIGS.append({"label": label, "model": model, "tf": tf, "data": data, "hours": hk, **params})
    else:
        CONFIGS.append({"label": label, "model": model, "tf": tf, "data": data, "hours": "own", **params})

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
    sd = r.std()
    return {"n": n, "expR": float(r.mean()), "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0,
            "win%": float(100 * (r > 0).mean())}


def run(job):
    c, part = job
    p = {k: v for k, v in c.items() if k not in ("label", "model", "tf", "data", "hours")}
    if c["hours"] != "own":
        p["hours"] = HOURS[c["hours"]]
    cfg, inst = research_cfg()
    try:
        t = run_backtest(DATA[(c["data"], c["tf"], part)], build_strategy(c["model"], cfg.session, p), inst, cfg).trades
        r = t["r"] if len(t) else pd.Series(dtype=float)
        err = ""
    except Exception as e:  # one broken model must not stop the study
        r, err = pd.Series(dtype=float), f"{type(e).__name__}: {e}"
    print(f"done {c['label']} {c['hours']} {part}: {len(r)} trades {err}", flush=True)
    return json.dumps(c, sort_keys=True), part, r, err


def main(path5: str, path1: str) -> None:
    for key, path in (("5m", path5), ("1m", path1)):
        base = load_csv(path)
        mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
        for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
            for tf in {c["tf"] for c in CONFIGS if c["data"] == key}:
                DATA[(key, tf, part)] = d if tf == key else resample(d, tf)
        print(f"{key}: A {base.index[0]:%Y-%m-%d} -> {mid:%Y-%m-%d} -> B -> {base.index[-1]:%Y-%m-%d}", flush=True)
    print(f"{len(CONFIGS)} configs", flush=True)
    with Pool() as pool:
        res = pool.map(run, [(c, part) for c in CONFIGS for part in ("A", "B")], chunksize=1)
    tab: dict = {}
    errs: dict = {}
    for key, part, r, err in res:
        tab.setdefault(key, {})[part] = r
        if err:
            errs[key] = err
    rows = []
    for key, parts in tab.items():
        s = r_stats(pd.concat([parts["A"], parts["B"]]))
        sa, sb = r_stats(parts["A"]), r_stats(parts["B"])
        c = json.loads(key)
        rows.append({"label": c["label"], "hours": c["hours"], "data": c["data"], "n": s["n"],
                     "win%": round(s["win%"], 1), "expR": round(s["expR"], 3), "t": round(s["t"], 2),
                     "A": round(sa["expR"], 3), "nA": sa["n"], "B": round(sb["expR"], 3), "nB": sb["n"],
                     "totalR": round(s["expR"] * s["n"], 1), "error": errs.get(key, ""), "cfg": key})
    t = pd.DataFrame(rows)
    t["PASS"] = (t.A > 0) & (t.B > 0) & (t.n >= 100)
    t["STRONG"] = t.PASS & (t.t >= 2.5)
    t = t.sort_values(["PASS", "t"], ascending=False)
    t.to_csv("research/full_retest_results.csv", index=False)
    pd.set_option("display.width", 250)
    print(t.drop(columns="cfg").to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_1m.csv")
