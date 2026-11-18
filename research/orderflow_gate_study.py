"""Order-flow gate study (pre-registered 2026-11-16, Ricardo).

Question: does REAL COMEX gold volume (Databento GC) improve the live models' entries on XAUUSD?
GatesFX data has no real volume, so this is the one part of the sweep / ICT idea never tested.

Gates (atlas_trader/orderflow.py, fixed BEFORE running): V1 V2 V3 V4 (volume spike), D1 D2 (proxy
CVD divergence), A1 A2 (absorption), plus "ungated" (today's bot) as the baseline.
Models = the live lineup with live settings (config/default.yaml at c2b011c), live exit each:
  smc_ob 5m 08-11 (base) | momentum_rsi 5m 08-11 (base) |
  session_break_retest 5m 08-11 (base) | session_sweep_evening 15m 20-24 (half1_be)
reversal_trail (1m) excluded: no 1m GC join in v1. The evening sweep made only 15 trades in 3.7
years, so it cannot reach the 100-trade bar; it is run for the record.
Data: GatesFX XAUUSD.R 5m (prices, real spread, slippage, $7/lot per side) + GC 5m volume,
halves A/B by time. Holdout untouched.
PASS (model x gate): expectancy > 0 in BOTH halves, n >= 100, beats ungated expectancy in BOTH
halves, max drawdown (R) no more than 20% worse than ungated. Pick per model = best total R on
half A only; it must then pass on half B. STRONG = also t >= 2.5. 32 gated tests: expect 1-2
passes by luck alone.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/orderflow_gate_study.py data/raw/XAUUSD.R_5m.csv data/raw/GC_5m.csv
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader import orderflow
from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from atlas_trader.strategies.base import Strategy
from exit_options_study import replay, research_cfg

MODELS = [
    ("smc_ob", "smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0, "hours": "08:00-11:00"}, "base"),
    ("momentum_rsi", "momentum_rsi", "5m",
     {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0, "trail_buf_atr": 0.3, "spike_atr": 2.0,
      "hours": "08:00-11:00", "max_candle_atr": 2.5, "max_run_atr": 3.0, "run_bars": 12}, "base"),
    ("session_break_retest", "session_sweep", "5m",
     {"mode": "retest", "bias": "4h", "ref_session": "18:00-24:00,00:00-08:00", "hours": "08:00-11:00",
      "wick_frac": 0.4}, "base"),
    ("session_sweep_evening", "session_sweep", "15m",
     {"ref_session": "08:00-17:00", "bias": "4h", "confirm": "choch", "hours": "20:00-24:00"}, "half1_be"),
]
GATES = [None, *orderflow.GATES]
DATA: dict = {}


class OFGated(Strategy):
    """Runs `inner` and keeps only the signals that pass order-flow gate `name`."""
    name = "of_gated"
    uses_session_window = False

    def __init__(self, inner, feats, gate_name):
        self.inner, self.feats, self.gate_name = inner, feats, gate_name
        self.session = inner.session
        self.p = {}

    def generate(self, df):
        out = self.inner.generate(df).copy()
        f = self.feats.reindex(df.index)
        out["signal"] = orderflow.gate(out["signal"].to_numpy(int), f, self.gate_name)
        return Strategy._finalize(out, df["close"])


def run(job):
    label, model, tf, params, exit_mode, gate, part = job
    cfg, inst = research_cfg()
    df, feats = DATA[(tf, part)]
    strat = build_strategy(model, cfg.session, params)
    if gate is not None:
        strat = OFGated(strat, feats, gate)
    t = run_backtest(df, strat, inst, cfg).trades
    r = np.array([replay(df, tr, inst, exit_mode) for tr in t.itertuples()]) if len(t) else np.array([])
    print(f"done {label} {gate} {part}: {len(t)} trades", flush=True)
    return (label, gate), part, r


def max_dd(r: np.ndarray) -> float:
    if not len(r):
        return 0.0
    eq = np.cumsum(r)
    return float(np.max(np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq))


def tstat(r: np.ndarray) -> float:
    return float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 and r.std(ddof=1) > 0 else 0.0


def main(path_xau, path_gc):
    base = load_csv(path_xau)
    gc5 = load_csv(path_gc)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    cover = gc5.index.min(), gc5.index.max()
    print(f"XAUUSD {base.index[0]} -> {base.index[-1]}, GC {cover[0]} -> {cover[1]}, split {mid}", flush=True)
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        d15 = resample(d, "15m")
        DATA[("5m", part)] = (d, orderflow.features(d, gc5))
        DATA[("15m", part)] = (d15, orderflow.features(d15, resample(gc5, "15m")))
        miss = DATA[("5m", part)][1]["vol_ratio"].isna().mean()
        print(f"half {part}: {100 * miss:.1f}% of 5m bars have no GC volume feature", flush=True)
    jobs = [(*m, g, part) for m in MODELS for g in GATES for part in ("A", "B")]
    with Pool() as pool:
        res = pool.map(run, jobs, chunksize=1)
    tab: dict = {}
    for key, part, r in res:
        tab.setdefault(key, {})[part] = r
    rows = []
    for (label, g), parts in tab.items():
        a, b = parts["A"], parts["B"]
        r = np.concatenate([a, b])
        rows.append({"model": label, "gate": g or "ungated", "n": len(r), "nA": len(a), "nB": len(b),
                     "win%": round(100 * (r > 0).mean(), 1) if len(r) else 0.0,
                     "expR": round(r.mean(), 3) if len(r) else 0.0, "totR": round(r.sum(), 1),
                     "A_exp": round(a.mean(), 3) if len(a) else 0.0, "B_exp": round(b.mean(), 3) if len(b) else 0.0,
                     "A_tot": round(a.sum(), 1), "maxDD_R": round(max_dd(r), 1), "t": round(tstat(r), 2)})
    t = pd.DataFrame(rows)
    ung = t[t.gate == "ungated"].set_index("model")
    t["PASS"] = [r.gate != "ungated" and r.A_exp > 0 and r.B_exp > 0 and r.n >= 100
                 and r.A_exp > ung.loc[r.model, "A_exp"] and r.B_exp > ung.loc[r.model, "B_exp"]
                 and r.maxDD_R <= 1.2 * ung.loc[r.model, "maxDD_R"] for r in t.itertuples()]
    t["STRONG"] = t.PASS & (t.t >= 2.5)
    gated = t[(t.gate != "ungated") & (t.n > 0)]
    picks = gated.loc[gated.groupby("model")["A_tot"].idxmax()]
    t["PICK"] = t.index.isin(picks.index)
    t = t.sort_values(["model", "expR"], ascending=[True, False])
    t.to_csv("research/orderflow_gate_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))
    print("\nPicks (best half-A total R per model) and whether they pass:")
    print(t[t.PICK][["model", "gate", "n", "A_exp", "B_exp", "maxDD_R", "t", "PASS", "STRONG"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/GC_5m.csv")
