"""reversal_trail target study, pre-registered 2026-11-07 (Ricardo).

Question: is 50% of the fast move the right take-profit for reversal_trail? Same entries as the
current model; ONLY the exit changes. The stop rules stay as they are (initial stop beyond the
move's extreme, candle trail from +1R with 0.3 ATR room).

Model and entries: reversal_trail exactly as in research/wick_trail_study.py (research copy
research/reversal_trail_model.py, 1m, move 5 ATR, RSI 25/75, entries 08:00-11:00 NY), from the
normal backtester (one position at a time). Each entry is then re-run with every exit variant.

Variants (fixed before running):
  1 baseline   target at 50% of the move (current)
  2 fib 38.2   target at 38.2%
  3 fib 61.8   target at 61.8%
  4 fib 78.6   target at 78.6%
  5 fib 100    target at 100% (back to the start of the move)
  6 split      close half at 50%, trail the rest with the current "trail from 1R" (no target).
               Lot split = atlas_trader.reversal_exits.split_qty on the trade's size (0.01 steps);
               a size too small to split goes out whole at 50%.
  7 switch     no fixed target: the current trail runs, and once a closed candle reaches the 50%
               level it trails from 0R instead of waiting for +1R (SwitchTrail).
  If a fib level is already behind the entry fill, that trade takes its target at once (a resting
  limit would fill immediately), i.e. it loses about the costs. Counted and reported.

Data and costs: GatesFX XAUUSD.R 1m, 2025-10-01 -> 2026-11-05, halves A/B split by whole days, as the
shoot-out and wick-trail studies. Spread 0.14 + slippage 0.10, $3.50/lot/side, stop first when a bar
touches both, gaps fill at the open. R is net of commission.

Pass bar (fixed before running):
  1. Pick the variant (2-7) with the best total R on half A only.
  2. PASS only if on half B it beats the baseline's total R, its expectancy is > 0 and its max
     drawdown (R, trades in entry order) is no more than 20% worse than the baseline's.
  3. Paired per-trade R difference vs baseline with t-stat (half B). STRONG = PASS, n >= 100, t >= 2.5.
  4. Only if it passes: ONE run on the untouched 2023-03 -> 2025-09 holdout. reversal_trail runs on
     1m and the 1m history on disk starts 2025-10-01, so the holdout needs 1m data for that period
     (pass it with --holdout PATH). Without it the pass stays UNCONFIRMED; it is never retested on
     other data. If it fails on the holdout, it fails.
  5. Nothing passes -> say so; the 50% target stays.

Checks built in: the simulator must reproduce the backtester's R on every baseline trade, and the
recorded move must reproduce every live 50% target exactly (else the script stops).

Run (never in /opt/atlas-trader):
  PYTHONPATH=. python research/reversal_target_study.py data/raw/XAUUSD.R_1m.csv [--holdout PATH_1m]
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import wick_trail_study as w
from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.data import load_csv
from atlas_trader.reversal_exits import SwitchTrail, fib_target, split_qty
from reversal_trail_model import ReversalTrail

PARAMS = dict(move_atr=5.0, rsi_level=25, target="half_move", spike_atr=0.0,
              trail_start_r=1.0, trail_buf_atr=0.3, hours="08:00-11:00")
FIBS = {"fib 38.2%": 0.382, "fib 61.8%": 0.618, "fib 78.6%": 0.786, "fib 100%": 1.0}
VARIANTS = ["baseline 50%", *FIBS, "split: half at 50%, rest trails", "switch: trail after 50%"]
HOLDOUT = ("2023-03-02", "2025-09-30")
LOT_STEP = MIN_LOT = 0.01


def run_part(df: pd.DataFrame, label: str) -> dict:
    cfg, inst = w.research_cfg()
    half_cost = inst.spread_points / 2 + inst.slippage_points
    model = ReversalTrail(cfg.session, **PARAMS)
    trades = run_backtest(df, model, inst, cfg).trades
    out = {"n": len(trades), "r": {v: [] for v in VARIANTS}, "entry_time": [], "immediate": {k: 0 for k in FIBS},
           "unsplit": 0}
    if trades.empty:
        return out
    sigs = ReversalTrail(cfg.session, **PARAMS).generate(df)
    B = w.Bars(df, cfg, inst)
    i0s = B.idx.get_indexer(trades.entry_time)
    assert (i0s >= 1).all()
    inf = float("inf")
    for t, i0 in zip(trades.itertuples(), i0s):
        s = int(i0) - 1                                           # signal bar = the bar before the fill
        ext, start = float(sigs["move_ext"].iloc[s]), float(sigs["move_start"].iloc[s])
        assert int(sigs["signal"].iloc[s]) == t.side
        t50 = fib_target(t.side, ext, start, 0.5)
        if abs(t50 - t.target) > 1e-6:
            raise SystemExit(f"{label}: recorded move does not reproduce the live target ({t50} vs {t.target})")
        risk = abs(t.entry - t.init_stop)

        def gross(fill):
            return t.side * (fill - t.entry) / risk

        comm_r = 2 * inst.commission_per_lot / (inst.value_per_point * risk)
        # 1) baseline, and the check against the backtester
        f_base, _, _ = w.simulate(B, int(i0), t.side, t.entry, t.init_stop, t.target, w.baseline_manager(B, t), half_cost)
        if abs(gross(f_base) - t.r) > 1e-6:
            raise SystemExit(f"{label}: simulator does not reproduce the backtester ({gross(f_base)} vs {t.r})")
        out["r"]["baseline 50%"].append(gross(f_base) - comm_r)
        # 2-5) other fib targets, same stop rules
        for name, frac in FIBS.items():
            tgt = fib_target(t.side, ext, start, frac)
            if t.side * (tgt - t.entry) <= 0:
                out["immediate"][name] += 1
            f, _, _ = w.simulate(B, int(i0), t.side, t.entry, t.init_stop, tgt, w.baseline_manager(B, t), half_cost)
            out["r"][name].append(gross(f) - comm_r)
        # 6) split: half at 50% (= the baseline leg), the rest trails with no target
        f_rest, _, _ = w.simulate(B, int(i0), t.side, t.entry, t.init_stop, t.side * inf,
                                  w.baseline_manager(B, t), half_cost)
        q1, q2 = split_qty(max(t.lots, LOT_STEP), LOT_STEP, MIN_LOT)
        if q2 == 0:
            out["unsplit"] += 1
        r_split = (q1 * gross(f_base) + q2 * gross(f_rest)) / (q1 + q2)
        out["r"]["split: half at 50%, rest trails"].append(r_split - comm_r)
        # 7) switch: no target; trail from 1R as now, from 0R once a closed candle reached 50%
        sw = SwitchTrail(t.side, t.entry, t.init_stop, t50, t.trail_start_r, t.trail_buf)

        def manage(i, stop, sw=sw):
            if i >= 2:
                new = sw.update(B.o[i - 2:i + 1], B.h[i - 2:i + 1], B.l[i - 2:i + 1], B.c[i - 2:i + 1], stop)
                if new is not None:
                    stop = new
            return stop
        f_sw, _, _ = w.simulate(B, int(i0), t.side, t.entry, t.init_stop, t.side * inf, manage, half_cost)
        out["r"]["switch: trail after 50%"].append(gross(f_sw) - comm_r)
        out["entry_time"].append(t.entry_time)
    return out


def load_1m(path: str, start: str, end: str) -> pd.DataFrame:
    base = load_csv(path)
    return base[(base.index >= pd.Timestamp(start, tz="UTC")) &
                (base.index < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default="data/raw/XAUUSD.R_1m.csv")
    ap.add_argument("--holdout", default=None, help="1m file covering 2023-03-02 -> 2025-09-30")
    args = ap.parse_args()

    base = load_1m(args.data, w.START, w.END)
    a, b = split_in_out(base, 0.5)
    print(f"DATA {base.index[0]} -> {base.index[-1]} ({len(base):,} 1m bars) | A {a.index[0].date()} -> "
          f"{a.index[-1].date()} | B {b.index[0].date()} -> {b.index[-1].date()}", flush=True)
    A = run_part(a, "A")
    print(f"done half A: {A['n']} trades", flush=True)
    Bh = run_part(b, "B")
    print(f"done half B: {Bh['n']} trades | baseline replay exact, live targets reproduced", flush=True)
    print(f"targets already behind the fill (exit at once): A {A['immediate']} | B {Bh['immediate']}")
    print(f"trades too small to split: A {A['unsplit']} | B {Bh['unsplit']}")

    bA, bB = np.array(A["r"]["baseline 50%"]), np.array(Bh["r"]["baseline 50%"])
    rows = []
    for v in VARIANTS:
        rA, rB = np.array(A["r"][v]), np.array(Bh["r"][v])
        sA, sB = w.stats(rA, bA), w.stats(rB, bB)
        sAll = w.stats(np.concatenate([rA, rB]), np.concatenate([bA, bB]))
        rows.append({"variant": v, "A_n": sA["n"], "A_totalR": sA["totalR"], "B_n": sB["n"], "B_totalR": sB["totalR"],
                     "B_win%": sB["win%"], "B_expR": sB["expR"], "B_maxDD": sB["maxDD"], "B_avg_win_R": sB["avg_win_R"],
                     "B_diff": sB["diff"], "B_t": sB["t"], "A_win%": sA["win%"], "A_maxDD": sA["maxDD"],
                     "all_totalR": sAll["totalR"], "all_diff": sAll["diff"], "all_t": sAll["t"]})
    t = pd.DataFrame(rows)
    t.to_csv("research/reversal_target_results.csv", index=False)
    per = []
    for part, R in (("A", A), ("B", Bh)):
        for k, et in enumerate(R["entry_time"]):
            per.append({"part": part, "entry_time": et, **{v: R["r"][v][k] for v in VARIANTS}})
    pd.DataFrame(per).to_csv("research/reversal_target_trades.csv", index=False)
    pd.set_option("display.width", 250)
    print()
    print(t.round(3).to_string(index=False))

    base_row = t.iloc[0]
    cand = t.iloc[1:].sort_values("A_totalR", ascending=False).iloc[0]
    beats = cand.B_totalR > base_row.B_totalR
    pos = cand.B_expR > 0
    dd_ok = cand.B_maxDD <= w.DD_TOLERANCE * base_row.B_maxDD + 1e-12
    passed = beats and pos and dd_ok
    strong = passed and cand.B_n >= w.STRONG_N and cand.B_t >= w.STRONG_T
    print(f"\nVERDICT: picked on A = '{cand.variant}' (A {cand.A_totalR:+.1f}R vs baseline {base_row.A_totalR:+.1f}R). "
          f"Half B: {cand.B_totalR:+.1f}R vs baseline {base_row.B_totalR:+.1f}R [{'ok' if beats else 'FAIL'}], "
          f"exp {cand.B_expR:+.3f}R [{'ok' if pos else 'FAIL'}], maxDD {cand.B_maxDD:.1f}R vs {base_row.B_maxDD:.1f}R "
          f"x1.2={w.DD_TOLERANCE * base_row.B_maxDD:.1f} [{'ok' if dd_ok else 'FAIL'}], paired diff "
          f"{cand.B_diff:+.3f}R/trade t={cand.B_t:.2f} n={int(cand.B_n)} -> "
          + ("STRONG" if strong else "PASS" if passed else "NOT PASSED (the 50% target stays)"))
    if not passed:
        return
    if args.holdout is None:
        print("HOLDOUT: needed, but no 1m data for 2023-03 -> 2025-09 was given. Result stays UNCONFIRMED.")
        return
    h = load_1m(args.holdout, *HOLDOUT)
    print(f"\nHOLDOUT {h.index[0]} -> {h.index[-1]} ({len(h):,} 1m bars), one run", flush=True)
    H = run_part(h, "H")
    hb, hc = np.array(H["r"]["baseline 50%"]), np.array(H["r"][cand.variant])
    sb, sc = w.stats(hb, hb), w.stats(hc, hb)
    ok = sc["totalR"] > sb["totalR"] and sc["expR"] > 0 and sc["maxDD"] <= w.DD_TOLERANCE * sb["maxDD"] + 1e-12
    print(f"HOLDOUT VERDICT: {sc['totalR']:+.1f}R vs baseline {sb['totalR']:+.1f}R, exp {sc['expR']:+.3f}R, "
          f"maxDD {sc['maxDD']:.1f}R vs {sb['maxDD']:.1f}R, paired t={sc['t']:.2f} n={sc['n']} -> "
          + ("PASS" if ok else "FAIL (it fails; no retesting)"))


if __name__ == "__main__":
    main()
