"""Supply / demand zone retest study (strategies/zone_retest.py), pre-registered 2026-11-05.

Ricardo's zone setup: price bases in a tight box, leaves it with a big displacement candle; the
first return into the box gets a rejection candle -> sell at supply (buy at demand).

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX XAUUSD.R 1m resampled to 5m, 2025-10-01 -> 2026-11-05, split in halves A/B.
    Real spread, slippage and $7/lot fees. All hours (the bot's daily break still applies live).
  * 8 configs: zone chart {5min, 15min} x displacement candle {>= 1.5, >= 2.5 ATR}
                x exit {trail (no target, trail from 1R), 2R target}.
  * DEMO rule (agreed with Ricardo): a config may go on the demo only if its expectancy is
    > 0 in BOTH halves after costs with >= 60 trades in total. If several pass, the one with
    the highest t goes. STRONG (the shoot-out bar) = both halves > 0, >= 100 trades, t >= 2.5.

Run:  python research/zone_study.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import json
import sys

import pandas as pd

import gold_research as g
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

CONFIGS = [dict(zone_tf=tf, disp_atr=d, target=tg)
           for tf in ("5min", "15min") for d in (1.5, 2.5) for tg in ("trail", "rr")]


def main(path: str) -> None:
    base = resample(load_csv(path), "5m")
    a, b = split_in_out(base, 0.5)
    print(f"DATA {base.index[0].date()} -> {base.index[-1].date()} | A ends {a.index[-1].date()}\n")
    cfg, inst = g.research_cfg(False)
    rows = []
    for p in CONFIGS:
        parts = {}
        reasons = {}
        for part, d in (("A", a), ("B", b)):
            t = run_backtest(d, build_strategy("zone_retest", cfg.session, p), inst, cfg).trades
            parts[part] = t["r"].astype(float) if len(t) else pd.Series(dtype=float)
            if len(t):
                for k, v in t["reason"].value_counts().items():
                    reasons[k] = reasons.get(k, 0) + int(v)
        allr = pd.concat([parts["A"], parts["B"]])
        s = g.r_stats(allr)
        rows.append({"chart": p["zone_tf"], "disp": p["disp_atr"], "exit": p["target"], "n": s["n"],
                     "win%": round(s["win%"], 1), "expR": round(s["expR"], 3), "t": round(s["t"], 2),
                     "totalR": round(float(allr.sum()), 1),
                     "A": round(g.r_stats(parts["A"])["expR"], 3), "B": round(g.r_stats(parts["B"])["expR"], 3),
                     "exits": ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:3])})
        print("done", p, flush=True)
    t = pd.DataFrame(rows)
    t["DEMO_OK"] = (t.A > 0) & (t.B > 0) & (t.n >= 60)
    t["STRONG"] = (t.A > 0) & (t.B > 0) & (t.n >= 100) & (t.t >= 2.5)
    pd.set_option("display.width", 250, "display.max_colwidth", 80)
    print()
    print(t.sort_values("t", ascending=False).to_string(index=False))
    ok = t[t.DEMO_OK].sort_values("t", ascending=False)
    print("\nVERDICT:", "no config passes the demo rule -> stays off the bot" if ok.empty else
          f"demo candidate: chart {ok.iloc[0].chart}, disp {ok.iloc[0].disp}, exit {ok.iloc[0].exit} "
          f"(n {ok.iloc[0].n}, {ok.iloc[0].expR:+.3f}R, t {ok.iloc[0].t})")
    print("JSON", json.dumps(rows))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
