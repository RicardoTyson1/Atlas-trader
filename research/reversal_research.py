"""Pre-registered test of Ricardo's video model (`reversal_trail`) on 1m and 5m gold.

Model (rules in atlas_trader/strategies/reversal_trail.py): fast move (optionally with an
extreme RSI reading) -> change of character with a solid candle -> entry, stop beyond the
move's extreme, take profit at 50% of the move or trail only, take profit on an aggressive
candle in our favor (optional), candle trailing stop.

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX XAUUSD.R 1m, everything downloaded. NOTE: these calendar periods were
    used before by other studies (never with this model), so treat a pass
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
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

MIN_TRADES, MIN_T = 100, 2.5
CONFIGS = [dict(tf=tf, move_atr=m, rsi_level=rl, target=tg, spike_atr=sp, trail_start_r=1.0,
                hours="08:00-11:00,19:00-23:00")
           for tf in ("1m", "5m") for m in (3.0, 5.0)
           for rl in (0.0, 25.0) for tg in ("rr", "half_move") for sp in (0.0, 2.5)]
DATA: dict[str, pd.DataFrame] = {}


def _run(job):
    params, part = job
    p = dict(params)
    tf = p.pop("tf")
    cfg, inst = g.research_cfg(False)
    t = run_backtest(DATA[(tf, part)], build_strategy("reversal_trail", cfg.session, p), inst, cfg).trades
    return json.dumps(params, sort_keys=True), part, (t[["r", "stop_moves"]] if len(t) else pd.DataFrame(columns=["r", "stop_moves"]))


def main(path: str) -> None:
    base = load_csv(path)
    a, b = split_in_out(base, 0.5)
    for part, d in (("A", a), ("B", b)):
        DATA[("1m", part)] = d
        DATA[("5m", part)] = resample(d, "5m")
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
