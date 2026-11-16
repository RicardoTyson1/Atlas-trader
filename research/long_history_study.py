"""16-year check of the live lineup on COMEX gold (Databento, 2010-06 -> 2026-11), plus a volume test.

1. DATA CHECK  : COMEX 5m (roll-adjusted) vs GatesFX XAUUSD.R 5m over the overlap (2023-03 ->):
                 correlation of 5m returns, ratio of average candle ranges, share of shared bars.
2. LONG RE-TEST: the live 5m/15m setups (live settings, live exits, costs in R as every study) on
                 COMEX 2010-2026, reported by period. The 1m reversal is not included (5m data only).
                 Bar to keep trusting a setup: profit in most periods and over the whole 16 years.
3. VOLUME TEST : for every trade, the signal candle's volume vs the average volume of the same
                 time-of-day over the previous 20 sessions ("relative volume"). Pre-registered rule:
                 a volume filter is worth adding only if trades with rel. volume >= 1.0 beat the
                 others in BOTH the older and the newer half of the 16 years.
Run in /opt/atlas-research (after research/databento_gc.py):
  PYTHONPATH=.:research nice -n 19 python research/long_history_study.py data/raw/GC_5m.csv data/raw/XAUUSD.R_5m.csv
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
LINEUP = {   # name: (model, timeframe, params, live exit)
    "smc_ob": ("smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": AM}, "base"),
    "momentum_rsi": ("momentum_rsi", "5m",
                     {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0, "trail_buf_atr": 0.3,
                      "spike_atr": 2.0, "max_candle_atr": 2.5, "max_run_atr": 3.0, "run_bars": 12, "hours": AM},
                     "base"),
    "break_retest": ("session_sweep", "5m",
                     {"mode": "retest", "bias": "4h", "ref_session": "18:00-24:00,00:00-08:00", "hours": AM,
                      "wick_frac": 0.4}, "base"),
    "evening_sweep": ("session_sweep", "15m",
                      {"mode": "reversal", "confirm": "choch", "bias": "4h", "ref_session": "08:00-17:00",
                       "hours": "20:00-24:00"}, "half1_be"),
}
PERIODS = [("2010-2012", "2010", "2013"), ("2013-2015", "2013", "2016"), ("2016-2018", "2016", "2019"),
           ("2019-2021", "2019", "2022"), ("2022-2024", "2022", "2025"), ("2025-2026", "2025", "2027")]
DATA: dict = {}


def data_check(gc: pd.DataFrame, gfx: pd.DataFrame) -> None:
    a = gc[gc.index >= gfx.index[0]]
    j = a[["close", "high", "low"]].join(gfx[["close", "high", "low"]], how="inner", rsuffix="_g")
    r1, r2 = j["close"].pct_change(), j["close_g"].pct_change()
    print("DATA CHECK (COMEX vs GatesFX, overlap):")
    print(f"  shared 5m bars: {len(j)} ({100 * len(j) / max(len(gfx), 1):.0f}% of GatesFX bars)")
    print(f"  correlation of 5m returns: {r1.corr(r2):.3f}")
    print(f"  avg candle range COMEX/GatesFX: {((j.high - j.low).mean() / (j.high_g - j.low_g).mean()):.2f}")
    print(f"  median |price difference|: {(j.close - j.close_g).abs().median():.2f} (rolls back-adjusted)", flush=True)


def rel_volume(df: pd.DataFrame) -> pd.Series:
    """Volume vs the mean volume of the same 5m slot over the previous 20 sessions."""
    v = df["volume"]
    slot = df.index.strftime("%H:%M")
    prev = v.groupby(slot).transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    return v / prev


def run(job):
    name, period = job
    model, tf, params, ex = LINEUP[name]
    cfg, inst = research_cfg()
    df = DATA[(tf, period)]
    if len(df) < 500:
        return name, period, pd.DataFrame(columns=["entry_time", "r", "relvol"])
    t = run_backtest(df, build_strategy(model, cfg.session, params), inst, cfg).trades
    if not len(t):
        return name, period, pd.DataFrame(columns=["entry_time", "r", "relvol"])
    r = [replay(df, tr, inst, ex) for tr in t.itertuples()]
    rv = DATA[("relvol", tf, period)]
    pos = df.index.get_indexer(t["entry_time"]) - 1                # the signal candle
    out = pd.DataFrame({"entry_time": t["entry_time"].values, "r": r, "relvol": rv.to_numpy()[pos]})
    print(f"done {name} {period}: {len(out)} trades", flush=True)
    return name, period, out


def main(path_gc, path_gfx):
    gc = load_csv(path_gc)
    try:
        data_check(gc, load_csv(path_gfx))
    except Exception as e:  # the check is informative only
        print(f"data check skipped: {e!r}")
    for label, a, b in PERIODS:
        # 30 extra days of warm-up before each period (indicators), trades counted from the period start
        d = gc[(gc.index >= pd.Timestamp(a, tz="UTC") - pd.Timedelta(days=30)) & (gc.index < pd.Timestamp(b, tz="UTC"))]
        for tf in ("5m", "15m"):
            x = d if tf == "5m" else resample(d, tf)
            DATA[(tf, label)] = x
            DATA[("relvol", tf, label)] = rel_volume(x)
    with Pool(2) as pool:                                         # 2 workers: keep memory low
        res = pool.map(run, [(n, p[0]) for n in LINEUP for p in PERIODS], chunksize=1)
    rows, allt = [], []
    for name, period, t in res:
        start = pd.Timestamp(dict((p[0], p[1]) for p in PERIODS)[period], tz="UTC")
        t = t[pd.to_datetime(t["entry_time"], utc=True) >= start]
        t = t.assign(strategy=name, period=period)
        allt.append(t)
        rows.append({"strategy": name, "period": period, "n": len(t),
                     "win%": round(100 * float((t.r > 0).mean()), 1) if len(t) else 0.0,
                     "expR": round(float(t.r.mean()), 3) if len(t) else 0.0,
                     "totR": round(float(t.r.sum()), 1)})
    tab = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("\nLONG RE-TEST (totR by period):")
    print(tab.pivot(index="strategy", columns="period", values="totR").to_string())
    tot = tab.groupby("strategy").agg(n=("n", "sum"), totR=("totR", "sum"),
                                      pos_periods=("totR", lambda x: int((x > 0).sum())))
    tot["expR"] = (tot.totR / tot.n).round(3)
    print(tot.to_string())
    T = pd.concat(allt)
    T.to_csv("research/long_history_trades.csv", index=False)
    tab.to_csv("research/long_history_results.csv", index=False)
    print("\nVOLUME TEST (expR, rel. volume >= 1.0 vs < 1.0; older half 2010-2018 / newer half 2019-2026):")
    T["half"] = np.where(T.period.isin(["2010-2012", "2013-2015", "2016-2018"]), "older", "newer")
    T["hi_vol"] = T.relvol >= 1.0
    v = T.dropna(subset=["relvol"]).groupby(["strategy", "half", "hi_vol"]).r.agg(["count", "mean"]).round(3)
    print(v.to_string())
    for s in LINEUP:
        x = T[(T.strategy == s)].dropna(subset=["relvol"])
        ok = all(x[(x.half == h) & x.hi_vol].r.mean() > x[(x.half == h) & ~x.hi_vol].r.mean() for h in ("older", "newer"))
        print(f"  {s}: volume filter {'HELPS in both halves' if ok else 'does not pass'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/GC_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_5m.csv")
