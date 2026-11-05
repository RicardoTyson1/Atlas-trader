"""Strategy shoot-out: every one of Ricardo's models, same data, same costs, ranked.

Models (rules in each strategy's docstring):
  reversal_trail : fast move (+ extreme RSI) -> CHoCH -> entry; 50%-of-move target or trail.
  momentum_rsi   : RSI crosses 50 on an aggressive candle -> ride it with the trailing stop.
  break_retest   : consolidation -> solid breakout -> verified retest (tested before; included
                   so the weak can be ranked against the strong).
All share: stop beyond the setup's wick/extreme, candle trailing stop that never moves
backwards, aggressive candle in our favor pulls the stop to mid-candle (stay in the move).

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX XAUUSD.R 1m (resampled to 5m too), split in halves A/B. Costs as always.
  * 44 configs (listed below). A config is STRONG only if: expectancy > 0 in BOTH halves,
    >= 100 trades, t >= 2.5. Everything else is WEAK and gets eliminated.
  * Strong configs go to the demo account; weak ones are removed from the bot.
  * These calendar periods were used before by other studies (never with these models), so a
    STRONG result is a screen; the demo forward test decides.

Run:  python research/shootout.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool

import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.data import load_csv, resample

MIN_TRADES, MIN_T = 100, 2.5
HRS = "08:00-11:00"
CONFIGS = (
    [dict(strategy="reversal_trail", tf=tf, move_atr=m, rsi_level=rl, target=tg, hours=HRS)
     for tf in ("1m", "5m") for m in (3.0, 5.0) for rl in (0.0, 25.0) for tg in ("rr", "half_move")]
    + [dict(strategy="momentum_rsi", tf=tf, disp_atr=d, trend_filter=tfl, trail_start_r=ts, hours=HRS)
       for tf in ("1m", "5m") for d in (1.0, 1.5) for tfl in (True, False) for ts in (0.5, 1.0)]
    + [dict(strategy="break_retest", tf=tf, bias=b, require_choch=ch, hours="08:00-11:00")
       for tf in ("1m", "5m") for b in ("bos", "none") for ch in (False, True)]
    + [dict(strategy="reversal_trail", tf="5m", move_atr=3.0, rsi_level=25.0, target="half_move",
            spike_mode=1, hours=HRS),
       dict(strategy="momentum_rsi", tf="5m", disp_atr=1.0, trend_filter=True, spike_mode=1, hours=HRS)]
    + [dict(strategy="momentum_rsi", tf="5m", disp_atr=1.0, trend_filter=True, spike_atr=0.0, hours=HRS),
       dict(strategy="reversal_trail", tf="5m", move_atr=3.0, rsi_level=25.0, target="half_move",
            spike_atr=0.0, hours=HRS)]
)
DATA: dict = {}


def _run(job):
    params, part = job
    p = dict(params)
    strat, tf = p.pop("strategy"), p.pop("tf")
    from atlas_trader.strategies import build_strategy
    cfg, inst = g.research_cfg(False)
    t = run_backtest(DATA[(tf, part)], build_strategy(strat, cfg.session, p), inst, cfg).trades
    return json.dumps(params, sort_keys=True), part, (t["r"] if len(t) else pd.Series(dtype=float))


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
    tab: dict = {}
    for key, part, r in res:
        tab.setdefault(key, {})[part] = r
    rows = []
    for key, parts in tab.items():
        s = g.r_stats(pd.concat([parts["A"], parts["B"]]).astype(float))
        rows.append({"model": json.loads(key)["strategy"], "params": key, "n": s["n"], "win%": s["win%"],
                     "expR": s["expR"], "t": s["t"], "A": g.r_stats(parts["A"].astype(float))["expR"],
                     "B": g.r_stats(parts["B"].astype(float))["expR"]})
    t = pd.DataFrame(rows)
    t["STRONG"] = (t.A > 0) & (t.B > 0) & (t.n >= MIN_TRADES) & (t.t >= MIN_T)
    pd.set_option("display.width", 260, "display.max_colwidth", 150)
    print("By model:")
    print(t.groupby("model").agg(configs=("n", "size"), strong=("STRONG", "sum"), median_win=("win%", "median"),
                                 median_expR=("expR", "median"), best_t=("t", "max")).round(3).to_string(), "\n")
    print(t.sort_values("t", ascending=False).round(3).to_string(index=False))
    print(f"\nSTRONG: {int(t.STRONG.sum())} of {len(t)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
