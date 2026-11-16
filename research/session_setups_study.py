"""Researched session setups for 20:00-24:00 and 08:00-11:00 NY (pre-registered 2026-11-15, Ricardo).

Ideas from the web research (sources in the chat / docs): the Asian session is quiet and mean-
reverting, the Tokyo open is the one burst of volatility, gold's returns come mostly overnight,
Shanghai Gold Exchange opens 09:00 Beijing; in the morning: London range fades, COMEX (08:20) and
equity (09:30) open drives, the historical "London bias" (gold weak between the AM and PM fix, PM fix
= 10:00 NY), and the initial-balance retracement.

Grid (fixed BEFORE running; strategy atlas_trader/strategies/evening.py, 5m candles):
EVENING (entries 20:00-24:00)
  E1 range_fade  : range 18:00-20:00, wick {0.3, 0.5} x target {mid, far}
  E2 tokyo_drive : first {30, 60} min from 20:00 moved >= 1.5 ATR, {cont, fade}, trailing
  E3 overnight   : enter 20:00 with the {4H, daily} trend, exit {03:00, trailing}; + long-only 4H to 03:00
  E4 sge_open    : 18:00 -> SGE open drift, first {15, 30} min after the open {fade, cont}, trailing
MORNING (entries 08:00-11:00)
  M1 range_fade  : London range 03:00-08:00, wick {0.3, 0.5} x target {mid, far}
  M2 open_drive  : first 30 min from {08:20 COMEX, 09:30 equities} moved >= 1.5 ATR, {cont, fade}
  M3 fix_bias    : enter 08:00 {short (London bias), with the 4H trend}, exit 10:00
  M4 pm_fix      : 08:00 -> 10:00 (PM fix) drift, first {15, 30} min after 10:00 {fade, cont}
  M5 ib_retrace  : initial balance {09:30-10:30, 08:00-09:00}, retrace 25% -> target 50% beyond
Costs: real spread + slippage + $7/lot per side, in R; also "x2 spread" (evening spreads are wider).
PASS: >= 100 trades, expectancy > 0 in BOTH halves (also at x2 spread for the evening).

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/session_setups_study.py data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv
from exit_options_study import research_cfg
from atlas_trader.strategies import build_strategy

EVE, AM = "20:00-24:00", "08:00-11:00"
CONFIGS = []
for w in (0.3, 0.5):
    for tg in ("mid", "far"):
        CONFIGS.append(("evening", "E1 range_fade", f"wick{w}/{tg}", {"mode": "range_fade", "hours": EVE,
                        "range_window": "18:00-20:00", "wick_frac": w, "target": tg}))
        CONFIGS.append(("morning", "M1 range_fade", f"wick{w}/{tg}", {"mode": "range_fade", "hours": AM,
                        "range_window": "03:00-08:00", "wick_frac": w, "target": tg}))
for nb in (6, 12):
    for v in ("cont", "fade"):
        CONFIGS.append(("evening", "E2 tokyo_drive", f"{nb * 5}m/{v}", {"mode": "open_drive", "hours": EVE,
                        "drive_start": "20:00", "drive_bars": nb, "variant": v}))
for b in ("4h", "1d"):
    for hold in (84, 0):
        CONFIGS.append(("evening", "E3 overnight", f"{b}/{'to03:00' if hold else 'trail'}",
                        {"mode": "overnight", "hours": EVE, "entry_time": "20:00", "bias": b, "hold_bars": hold}))
CONFIGS.append(("evening", "E3 overnight", "long-only 4h/to03:00",
                {"mode": "overnight", "hours": EVE, "entry_time": "20:00", "bias": "4h", "long_only": True,
                 "hold_bars": 84}))
for nb in (3, 6):
    for v in ("fade", "cont"):
        CONFIGS.append(("evening", "E4 sge_open", f"{nb * 5}m/{v}", {"mode": "china_open", "hours": EVE,
                        "anchor": "sge", "pre_from": "18:00", "drive_bars": nb, "variant": v}))
        CONFIGS.append(("morning", "M4 pm_fix", f"{nb * 5}m/{v}", {"mode": "china_open", "hours": AM,
                        "anchor": "10:00", "pre_from": "08:00", "drive_bars": nb, "variant": v}))
for st in ("08:20", "09:30"):
    for v in ("cont", "fade"):
        CONFIGS.append(("morning", "M2 open_drive", f"{st}/{v}", {"mode": "open_drive", "hours": AM,
                        "drive_start": st, "drive_bars": 6, "variant": v}))
for d in ("short", "trend"):
    CONFIGS.append(("morning", "M3 fix_bias", f"{d}/to10:00", {"mode": "overnight", "hours": AM,
                    "entry_time": "08:00", "direction": d, "bias": "4h", "hold_bars": 24}))
for ibw in ("09:30-10:30", "08:00-09:00"):
    CONFIGS.append(("morning", "M5 ib_retrace", ibw, {"mode": "ib_retrace", "hours": AM, "ib_window": ibw}))
DATA: dict = {}


def run(job):
    i, part = job
    window, family, var, params = CONFIGS[i]
    cfg, inst = research_cfg()
    df = DATA[part]
    t = run_backtest(df, build_strategy("evening", cfg.session, params), inst, cfg).trades
    if len(t):
        risk = (t["entry"] - t["init_stop"]).abs()
        comm = 2 * inst.commission_per_lot / (risk * inst.value_per_point)
        r = t["r"].to_numpy() - comm.to_numpy()
        r2 = r - (inst.spread_points / risk).to_numpy()          # one more full spread per trade
    else:
        r = r2 = np.array([])
    print(f"done {family} {var} {part}: {len(t)} trades", flush=True)
    return i, part, r, r2


def main(path5):
    base = load_csv(path5)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    DATA["A"], DATA["B"] = base[base.index < mid], base[base.index >= mid]
    with Pool() as pool:
        res = pool.map(run, [(i, part) for i in range(len(CONFIGS)) for part in ("A", "B")], chunksize=1)
    tab = {}
    for i, part, r, r2 in res:
        tab.setdefault(i, {})[part] = (r, r2)
    rows = []
    for i, (window, family, var, _) in enumerate(CONFIGS):
        (a, a2), (b, b2) = tab[i]["A"], tab[i]["B"]
        r, r2 = np.concatenate([a, b]), np.concatenate([a2, b2])
        m = lambda x: round(float(x.mean()), 3) if len(x) else 0.0      # noqa: E731
        rows.append({"window": window, "setup": family, "variant": var, "n": len(r),
                     "win%": round(100 * float((r > 0).mean()), 1) if len(r) else 0.0,
                     "expR": m(r), "totR": round(float(r.sum()), 1), "A_exp": m(a), "B_exp": m(b),
                     "x2spread_exp": m(r2), "x2_A": m(a2), "x2_B": m(b2)})
    t = pd.DataFrame(rows)
    t["PASS"] = (t.n >= 100) & (t.A_exp > 0) & (t.B_exp > 0) & ((t.window == "morning") | ((t.x2_A > 0) & (t.x2_B > 0)))
    t = t.sort_values(["window", "setup", "A_exp"], ascending=[True, True, False])
    t.to_csv("research/session_setups_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
