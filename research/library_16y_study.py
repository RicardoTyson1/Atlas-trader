"""Every strategy in the library on 16 years of COMEX gold (2010-06 -> 2026-11), pre-registered 2026-11-16.

Goal: find setups that hold up across MARKET REGIMES, not just 2023-2026.
Data: data/raw/GC_5m.csv (Databento, roll-adjusted). Costs: real spread + slippage + $7/lot, in R
(the engine's own exits; no exit replay). 1-minute setups are not included (5m data only).
Each config is run once on the full 16 years; trades are then split into 6 periods.
Also reported with the volatility switch (only days with volr >= 1.2, research/regime_study.py).
PASS (fixed before running): >= 200 trades, total R > 0, profit in BOTH halves (2010-2018 / 2019-2026)
and in at least 4 of the 6 periods. STRONG: also t >= 2.5.
Resumable: each finished config is appended to research/library_16y_trades.csv.
Run in /opt/atlas-research (single process, low memory, a couple of hours):
  PYTHONPATH=.:research nice -n 19 python research/library_16y_study.py
"""
from __future__ import annotations

import json
import os
from datetime import time

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from atlas_trader.strategies.chart_patterns import ALL_PATTERNS
from exit_options_study import research_cfg
from regime_study import daily_features

AM, ALL = "08:00-11:00", "00:00-23:59"
CONFIGS = [  # (label, model, timeframe, params)
    ("orb", "orb", "5m", {}),
    ("trend_pullback", "trend_pullback", "5m", {}),
    ("vwap_reversion", "vwap_reversion", "5m", {}),
    ("session_breakout", "session_breakout", "5m", {}),
    ("donchian_trend_1h", "donchian_trend", "1H", {"entry_bars": 55, "exit_bars": 20}),
    ("time_drift_20h", "time_drift", "1H", {"entry_time": "20:00", "hold_bars": 3}),
    ("liquidity_fvg", "liquidity_fvg", "5m", {}),
    ("silver_bullet", "silver_bullet", "5m", {}),
    ("smc_ob_am", "smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}),
    ("smc_ob_all", "smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": ALL}),
    ("st_confluence_am", "st_confluence", "15m", {"st_mult": 3.0, "vol_mult": 0.0, "hours": AM}),
    ("momentum_rsi_am", "momentum_rsi", "5m", {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0,
                                               "trail_buf_atr": 0.3, "spike_atr": 2.0, "max_candle_atr": 2.5,
                                               "max_run_atr": 3.0, "run_bars": 12, "hours": AM}),
    ("momentum_rsi_all", "momentum_rsi", "5m", {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0,
                                                "trail_buf_atr": 0.3, "spike_atr": 2.0, "max_candle_atr": 2.5,
                                                "max_run_atr": 3.0, "run_bars": 12, "hours": ALL}),
    ("bb_reversion_am", "bb_reversion", "5m", {"hours": AM}),
    ("trend_break_am", "trend_break", "5m", {"hours": AM}),
    ("zone_retest_am", "zone_retest", "5m", {"hours": AM}),
    ("reversal_trail_5m_am", "reversal_trail", "5m", {"move_atr": 3.0, "rsi_level": 25, "target": "half_move",
                                                      "spike_atr": 2.5, "hours": AM}),
    ("break_retest_am", "session_sweep", "5m", {"mode": "retest", "bias": "4h", "wick_frac": 0.4,
                                                "ref_session": "18:00-24:00,00:00-08:00", "hours": AM}),
    ("sweep_reversal_eve", "session_sweep", "15m", {"mode": "reversal", "confirm": "choch", "bias": "4h",
                                                    "ref_session": "08:00-17:00", "hours": "20:00-24:00"}),
    ("london_fix_short", "evening", "5m", {"mode": "overnight", "hours": AM, "entry_time": "08:00",
                                           "direction": "short", "hold_bars": 24}),
    ("open_drive_0930_fade", "evening", "5m", {"mode": "open_drive", "hours": AM, "drive_start": "09:30",
                                               "drive_bars": 6, "variant": "fade"}),
    ("overnight_trend_eve", "evening", "5m", {"mode": "overnight", "hours": "20:00-24:00", "entry_time": "20:00",
                                              "bias": "4h", "hold_bars": 84}),
] + [(f"pattern_{p}_am", "chart_patterns", "5m", {"patterns": p, "hours": AM}) for p in ALL_PATTERNS
     if p not in ("double_top", "double_bottom")]
PERIODS = [("2010-2012", 2010, 2012), ("2013-2015", 2013, 2015), ("2016-2018", 2016, 2018),
           ("2019-2021", 2019, 2021), ("2022-2024", 2022, 2024), ("2025-2026", 2025, 2026)]
OUT = "research/library_16y_trades.csv"


def main():
    gc = load_csv("data/raw/GC_5m.csv")
    frames = {"5m": gc}
    done = set(pd.read_csv(OUT, usecols=["label"])["label"]) if os.path.exists(OUT) else set()
    for label, model, tf, params in CONFIGS:
        if label in done:
            continue
        if tf not in frames:
            frames[tf] = resample(gc, tf)
        cfg, inst = research_cfg()
        if model in ("orb", "trend_pullback", "vwap_reversion", "session_breakout", "liquidity_fvg", "silver_bullet"):
            cfg.session.overnight = False            # their own session logic, as in every earlier study
            cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(23, 59)
        try:
            t = run_backtest(frames[tf], build_strategy(model, cfg.session, params), inst, cfg).trades
        except Exception as e:
            print(f"{label}: FAILED {e!r}", flush=True)
            continue
        if len(t):
            risk = (t["entry"] - t["init_stop"]).abs()
            r = t["r"] - 2 * inst.commission_per_lot / (risk * inst.value_per_point)
            out = pd.DataFrame({"label": label, "entry_time": t["entry_time"], "r": r.round(4)})
        else:
            out = pd.DataFrame({"label": [label], "entry_time": [pd.NaT], "r": [np.nan]})
        out.to_csv(OUT, mode="a", header=not os.path.exists(OUT), index=False)
        print(f"done {label}: {len(t)} trades, total {float(t['r'].sum()) if len(t) else 0:.1f}R", flush=True)
        del t, out
    report()


def report():
    T = pd.read_csv(OUT).dropna(subset=["r"])
    et = pd.to_datetime(T["entry_time"], utc=True).dt.tz_convert("America/New_York")
    T["year"] = et.dt.year
    T["period"] = pd.cut(T.year, [2009, 2012, 2015, 2018, 2021, 2024, 2026], labels=[p[0] for p in PERIODS])
    T["gday"] = pd.to_datetime((et + pd.Timedelta(hours=6)).dt.date)
    f = daily_features("data/raw/GC_5m.csv")
    T = T.join(f[["volr"]], on="gday")
    rows = []
    for sw in ("none", "volr>=1.2"):
        X = T if sw == "none" else T[T.volr >= 1.2]
        for label, x in X.groupby("label"):
            per = x.groupby("period", observed=False).r.sum()
            old, new = x[x.year <= 2018].r, x[x.year >= 2019].r
            sd = x.r.std()
            row = {"switch": sw, "label": label, "n": len(x), "win%": round(100 * (x.r > 0).mean(), 1),
                   "expR": round(x.r.mean(), 3), "totR": round(x.r.sum(), 1),
                   "t": round(x.r.mean() / (sd / np.sqrt(len(x))), 2) if len(x) > 2 and sd > 0 else 0.0,
                   "older_R": round(old.sum(), 1), "newer_R": round(new.sum(), 1),
                   "pos_periods": int((per > 0).sum())}
            row.update({p: round(float(per.get(p, 0.0)), 1) for p, _, _ in PERIODS})
            row["PASS"] = (row["n"] >= 200 and row["totR"] > 0 and row["older_R"] > 0 and row["newer_R"] > 0
                           and row["pos_periods"] >= 4)
            row["STRONG"] = row["PASS"] and row["t"] >= 2.5
            rows.append(row)
    R = pd.DataFrame(rows).sort_values(["switch", "PASS", "totR"], ascending=[True, False, False])
    R.to_csv("research/library_16y_results.csv", index=False)
    pd.set_option("display.width", 250)
    print(R.drop(columns=[p for p, _, _ in PERIODS]).to_string(index=False))
    print("\nBy period (no switch):")
    print(R[R.switch == "none"].set_index("label")[[p for p, _, _ in PERIODS]].to_string())


if __name__ == "__main__":
    main()
