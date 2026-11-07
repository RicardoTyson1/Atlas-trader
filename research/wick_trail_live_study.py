"""P1/P2 wick-trail study on the two LIVE models (smc_ob, st_confluence), pre-registered 2026-11-07.

Same exit rules, grid, costs and pass bar as research/wick_trail_study.py (read its docstring);
only the models and the data change:

  * smc_ob        : live settings (5m, sweep 24 candles, no fixed target, entries 08:00-11:00 NY).
  * st_confluence : live settings (15m, SuperTrend x3, no volume filter, entries 08:00-11:00 NY).
                    Its SuperTrend-flip exit and 2-ATR big-candle lock stay unchanged (judged on
                    15m candles even when the trail runs on a finer timeframe).
  Entries are generated with the same research settings as research/archetypes_study.py, the
  study that put these models live.

PRIMARY run (decides): GatesFX XAUUSD.R 5m, 2023-03 -> 2026-11 (3.7 years), halves A/B split at the
  date midpoint, exactly as the archetypes study. Trail timeframes: smc_ob 5m; st_confluence 15m
  and 5m. (No 1m data exists for this period.)
SECONDARY run (information only, cannot make a variant pass): the 13 months of 1m data used by
  the first wick-trail study (split by days as there), adding the 1m trail: smc_ob 5m and 1m;
  st_confluence 15m and 1m. Samples are small here (st_confluence ~35 trades).

Pass bar per model (primary run): best total R on half A -> on half B it must beat the baseline's
total R, have expectancy > 0 and max drawdown <= 1.2 x baseline's. STRONG = also n >= 100 and
paired t >= 2.5 on half B. Nothing passes -> the live trail stays.

Run (never in /opt/atlas-trader):
  PYTHONPATH=. python research/wick_trail_live_study.py data/raw/XAUUSD.R_5m.csv data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import copy
import sys
from datetime import time

import pandas as pd

import wick_trail_study as w
from atlas_trader.backtest import split_in_out
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample

LIVE = dict(
    smc_ob=dict(sig_tf="5m", params=dict(sweep_bars=24, reward_risk=0.0, hours="08:00-11:00")),
    st_confluence=dict(sig_tf="15m", params=dict(st_mult=3.0, vol_mult=0.0, hours="08:00-11:00")),
)
PRIMARY_TRAIL = {"smc_ob": ("5m",), "st_confluence": ("15m", "5m")}
SECONDARY_TRAIL = {"smc_ob": ("5m", "1m"), "st_confluence": ("15m", "1m")}


def archetypes_cfg():
    """research/archetypes_study.py's settings (the study that approved these models)."""
    cfg = load_config("config/default.yaml")                 # read only
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 10**6
    cfg.risk.max_lots = 1e12
    cfg.risk.max_room_fraction_per_trade = 1.0
    cfg.risk.cooldown_after_loss_minutes = 0
    cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(23, 59)
    cfg.session.overnight = False
    inst = copy.deepcopy(cfg.instrument(w.SYM))
    inst.min_lot = inst.lot_step = 1e-9
    return cfg, inst


def set_models(trail: dict) -> None:
    w.MODELS.clear()
    for m, spec in LIVE.items():
        w.MODELS[m] = dict(spec, trail_tfs=trail[m])


def main(path5: str, path1: str) -> None:
    w.research_cfg = archetypes_cfg

    print("=" * 30, "PRIMARY: 3.7 years of 5m (decides)", "=" * 30, flush=True)
    base = load_csv(path5)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    w.DATA.clear()
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        w.DATA[("5m", part)] = d
        w.DATA[("15m", part)] = resample(d, "15m")
    a, b = w.DATA[("5m", "A")], w.DATA[("5m", "B")]
    print(f"DATA {base.index[0]} -> {base.index[-1]} | A {a.index[0].date()} -> {a.index[-1].date()} | "
          f"B {b.index[0].date()} -> {b.index[-1].date()}", flush=True)
    set_models(PRIMARY_TRAIL)
    w.run_study("wick_trail_live")

    print("\n" + "=" * 30, "SECONDARY: 13 months of 1m (information only)", "=" * 30, flush=True)
    base = load_csv(path1)
    base = base[(base.index >= pd.Timestamp(w.START, tz="UTC")) &
                (base.index < pd.Timestamp(w.END, tz="UTC") + pd.Timedelta(days=1))]
    a, b = split_in_out(base, 0.5)
    w.DATA.clear()
    for part, d in (("A", a), ("B", b)):
        w.DATA[("1m", part)] = d
        w.DATA[("5m", part)] = resample(d, "5m")
        w.DATA[("15m", part)] = resample(d, "15m")
    print(f"DATA {base.index[0]} -> {base.index[-1]} | A {a.index[0].date()} -> {a.index[-1].date()} | "
          f"B {b.index[0].date()} -> {b.index[-1].date()}", flush=True)
    set_models(SECONDARY_TRAIL)
    w.run_study("wick_trail_live_1m")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_1m.csv")
