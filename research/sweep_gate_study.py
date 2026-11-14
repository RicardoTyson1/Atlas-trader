"""Sweep gate on the 4 morning strategies (pre-registered 2026-11-14, Ricardo).

Ricardo: smc_ob, st_confluence, momentum_rsi and the symmetrical triangle should "follow the session
sweep setup". Implemented as a gate on each strategy's own signals (its candle = the 5m/15m confirm):
  * a SHORT is kept only if the reference session's HIGH has been swept (traded above) since that
    session ended, and the 1H / 4H trend is down; a LONG needs the LOW swept and the trend up;
  * optionally ("inside") the signal candle must also have closed back inside the swept range;
  * stop / trailing = each strategy's own (the stop is already beyond its structure).
Entries 08:00-11:00 NY, live settings. Exits on the same entries: base (live trailing), half1_be,
half0.5_be (research/exit_options_study.py replay, costs in R).

Grid (fixed BEFORE running) per strategy: reference session {overnight 18:00-08:00, london 02:00-08:00}
x bias {1h, 4h, both} x inside {no, yes} = 12 gates, plus "ungated" (today's bot) as the baseline.
Data: GatesFX XAUUSD.R 5m 2023-03 -> 2026-11, halves A/B. Real spread, slippage, $7/lot per side.
PASS (gate + exit): expectancy > 0 in BOTH halves and >= 100 trades. A gate is only worth deploying
if it also beats the ungated strategy's expectancy in both halves.

Also tested (Ricardo's follow-up): the break-and-RETEST of the session level in the breakout
direction, as a standalone setup (session_sweep mode "retest"), see RETEST below.

Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/sweep_gate_study.py data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy
from atlas_trader.strategies.base import Strategy
from atlas_trader.strategies.session_sweep import sweep_context, sweep_gate
from exit_options_study import replay, research_cfg

MODELS = [
    ("smc_ob", "smc_ob", "5m", {"sweep_bars": 24, "reward_risk": 0.0}),
    ("st_confluence", "st_confluence", "15m", {"st_mult": 3.0, "vol_mult": 0.0}),
    ("momentum_rsi", "momentum_rsi", "5m",
     {"disp_atr": 1.0, "trend_filter": True, "trail_start_r": 1.0, "trail_buf_atr": 0.3, "spike_atr": 2.0,
      "max_candle_atr": 2.5, "max_run_atr": 3.0, "run_bars": 12}),
    ("sym_triangle", "chart_patterns", "5m", {"patterns": "symmetrical_triangle"}),
]
REFS = {"overnight": "18:00-24:00,00:00-08:00", "london": "02:00-08:00"}
# Ricardo, same day: when the session high/low is broken and HOLDS, trade the retest of the broken
# level (new support / resistance) in the breakout direction, with the 1H/4H trend. Standalone,
# ungated, 12 configs: window {morning 08-11 / overnight levels, evening 20-24 / NY-day levels}
# x timeframe {5m, 15m} x bias {1h, 4h, both}.
RETEST = [(f"retest_{w}_{tf}_{b}", "session_sweep", tf,
           {"mode": "retest", "bias": b, "ref_session": ref, "hours": hrs})
          for w, hrs, ref in (("morning", "08:00-11:00", "18:00-24:00,00:00-08:00"),
                              ("evening", "20:00-24:00", "08:00-17:00"))
          for tf in ("5m", "15m") for b in ("1h", "4h", "both")]
GATES = [None] + [(ref, bias, inside) for ref in REFS for bias in ("1h", "4h", "both") for inside in (False, True)]
EXITS = ["base", "half1_be", "half0.5_be"]
DATA: dict = {}


class Gated(Strategy):
    """Runs `inner` and keeps only the signals that pass the sweep gate."""
    name = "gated"
    uses_session_window = False

    def __init__(self, inner, ref, bias, inside):
        self.inner, self.ref, self.bias, self.inside = inner, ref, bias, inside
        self.session = inner.session
        self.p = {}

    def generate(self, df):
        out = self.inner.generate(df).copy()
        ctx = sweep_context(df, self.session.timezone, REFS[self.ref], self.bias)
        out["signal"] = sweep_gate(df, out["signal"].to_numpy(int), ctx, self.inside)
        return Strategy._finalize(out, df["close"])


def run(job):
    label, model, tf, params, gate, part = job
    cfg, inst = research_cfg()
    df = DATA[(tf, part)]
    strat = build_strategy(model, cfg.session, {"hours": "08:00-11:00", **params})
    if gate is not None:
        strat = Gated(strat, *gate)
    t = run_backtest(df, strat, inst, cfg).trades
    out = {e: np.array([replay(df, tr, inst, e) for tr in t.itertuples()]) if len(t) else np.array([])
           for e in EXITS}
    print(f"done {label} {gate} {part}: {len(t)} trades", flush=True)
    return (label, gate), part, out


def main(path5):
    base = load_csv(path5)
    mid = base.index[0] + (base.index[-1] - base.index[0]) / 2
    for part, d in (("A", base[base.index < mid]), ("B", base[base.index >= mid])):
        DATA[("5m", part)] = d
        DATA[("15m", part)] = resample(d, "15m")
    jobs = [(*m, g, part) for m in MODELS for g in GATES for part in ("A", "B")]
    jobs += [(*m, None, part) for m in RETEST for part in ("A", "B")]
    with Pool() as pool:
        res = pool.map(run, jobs, chunksize=1)
    tab: dict = {}
    for key, part, out in res:
        tab.setdefault(key, {})[part] = out
    rows = []
    for (label, gate), parts in tab.items():
        for e in EXITS:
            a, b = parts["A"][e], parts["B"][e]
            r = np.concatenate([a, b])
            rows.append({"model": label,
                         "gate": "ungated" if gate is None else f"{gate[0]}/{gate[1]}/{'inside' if gate[2] else 'any'}",
                         "exit": e, "n": len(r),
                         "win%": round(100 * (r > 0).mean(), 1) if len(r) else 0.0,
                         "expR": round(r.mean(), 3) if len(r) else 0.0, "totR": round(r.sum(), 1),
                         "A_exp": round(a.mean(), 3) if len(a) else 0.0,
                         "B_exp": round(b.mean(), 3) if len(b) else 0.0})
    t = pd.DataFrame(rows)
    ung = t[t.gate == "ungated"].set_index(["model", "exit"])
    t["PASS"] = (t.A_exp > 0) & (t.B_exp > 0) & (t.n >= 100)
    t["beats_ungated"] = [r.gate != "ungated" and r.n > 0 and r.A_exp > ung.loc[(r.model, r.exit), "A_exp"]
                          and r.B_exp > ung.loc[(r.model, r.exit), "B_exp"] for r in t.itertuples()]
    t = t.sort_values(["model", "exit", "expR"], ascending=[True, True, False])
    t.to_csv("research/sweep_gate_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
