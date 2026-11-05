"""Pre-registered test of Ricardo's own model (`break_retest`) on 1-minute gold.

Model (rules in atlas_trader/strategies/break_retest.py): 5m break-of-structure direction,
1m consolidation -> solid breakout candle (BOS / optional CHoCH) -> verified retest entry,
stop beyond the retest wick, candle-by-candle trailing stop. One position, no adding.

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX XAUUSD.R 1m, everything downloaded. NOTE: these calendar periods were
    used before at 5m by other studies (never with this model or at 1m), so treat a pass
    as a screen, not proof. The real test is demo forward trading.
  * Split into halves A (older) and B (newer). Costs as in gold_research.py.
  * 32 configs. A config PASSES only if expectancy > 0 in BOTH halves, >= 100 trades,
    and t >= 2.5. Win rate is reported for every config (target: 68%+), but a high win
    rate with negative expectancy is a FAIL.

Run:  python research/ricardo_research.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool

import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.data import load_csv
from atlas_trader.strategies import build_strategy

MIN_TRADES, MIN_T = 100, 2.5
CONFIGS = [dict(bias=b, require_choch=ch, trail_start_r=ts, trail_buf_atr=tb, reward_risk=rr)
           for b in ("bos", "none") for ch in (False, True) for ts in (0.5, 1.0)
           for tb in (0.3, 0.6) for rr in (0.0, 2.0)]
DATA: dict[str, pd.DataFrame] = {}


def _run(job):
    params, part = job
    cfg, inst = g.research_cfg(False)
    t = run_backtest(DATA[part], build_strategy("break_retest", cfg.session, params), inst, cfg).trades
    return json.dumps(params, sort_keys=True), part, (t[["r", "stop_moves"]] if len(t) else pd.DataFrame(columns=["r", "stop_moves"]))


def main(path: str) -> None:
    base = load_csv(path)
    a, b = split_in_out(base, 0.5)
    DATA["A"], DATA["B"] = a, b
    print(f"DATA {base.index[0].date()} -> {base.index[-1].date()} | A ends {a.index[-1].date()}, B starts {b.index[0].date()}")
    print(f"{len(CONFIGS)} pre-registered configs\n")
    with Pool() as pool:
        res = pool.map(_run, [(p, part) for p in CONFIGS for part in ("A", "B")])
    tab: dict[str, dict] = {}
    for key, part, t in res:
        tab.setdefault(key, {})[part] = t
    rows = []
    for key, parts in tab.items():
        both = pd.concat([parts["A"], parts["B"]])
        s = g.r_stats(both["r"].astype(float))
        rows.append({"params": key, "n": s["n"], "win%": s["win%"], "expR": s["expR"], "t": s["t"],
                     "A_expR": g.r_stats(parts["A"]["r"].astype(float))["expR"],
                     "B_expR": g.r_stats(parts["B"]["r"].astype(float))["expR"],
                     "avg_stop_moves": float(both["stop_moves"].mean()) if len(both) else 0.0})
    t = pd.DataFrame(rows).sort_values("win%", ascending=False)
    pd.set_option("display.width", 250, "display.max_colwidth", 120)
    print(f"Median win% {t['win%'].median():.0f} | positive configs {(t.expR > 0).mean() * 100:.0f}% | median expR {t.expR.median():+.3f}\n")
    print(t.round(3).to_string(index=False))
    ok = t[(t.A_expR > 0) & (t.B_expR > 0) & (t.n >= MIN_TRADES) & (t.t >= MIN_T)]
    print(f"\nVERDICT: {len(ok)} of {len(t)} pass (both halves > 0, n >= {MIN_TRADES}, t >= {MIN_T}).")
    hi = ok[ok["win%"] >= 68]
    print(f"Of those, {len(hi)} also win 68%+ of trades.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
