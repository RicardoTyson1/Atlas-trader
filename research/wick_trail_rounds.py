"""momentum_rsi wick trail: bounded adjust-and-retest with a fresh holdout. Pre-registered 2026-11-07.

Ricardo: "for what did not pass, adjust and retest". Agreed plan: at most 3 rounds of adjustments
on the development data, then ONE test of the survivor on data no exit study has touched.

DEVELOPMENT data: the 13 months of 1m used by research/wick_trail_study.py (2025-10-01 ->
  2026-11-05, same halves A/B), momentum_rsi entries exactly as there. Trail on 5m candles only
  (the holdout has no 1m data). No break-even step (P2 lost in every 5m case in the first study).
  Baseline = the live "trail from 1R" candle trail. Same costs and fill model.

ROUNDS (fixed now; each round starts from the previous round's half-A pick):
  R1  start the wick trail later: activation 1.5R or 2.0R  x  buffer 0.10 / 0.25 / 0.50     (6)
  R2  with R1's activation: confirmation on a close above S's CLOSE and/or a looser strong candle
      (body >= 50% of range and >= 0.3 ATR)  x  buffer                                      (9)
  R3  with R2's settings: any pullback counts (no lower low needed)  x  buffer              (3)
  Each round uses the first study's bar: pick the best total R on half A; it PASSES the round only
  if on half B it beats the baseline's total R, has expectancy > 0 and max DD <= 1.2 x baseline's.
  The first round whose pick passes sends that pick to the holdout and the rounds stop.
  No pass after R3 -> stop; the live trail stays.

HOLDOUT (one shot): XAUUSD.R 5m, 2023-03-02 -> 2025-09-30. No exit rule has ever been tested on it
  (the no-chase study did use this period for momentum_rsi's entry hours). Baseline vs the
  candidate on the same entries. PASS = total R beats the baseline, expectancy > 0, max DD <= 1.2 x
  baseline's. STRONG = also n >= 100 and paired t >= 2.5. Even a STRONG result goes to Ricardo
  for approval; nothing is deployed by this script.

Run (never in /opt/atlas-trader):
  PYTHONPATH=. python research/wick_trail_rounds.py data/raw/XAUUSD.R_1m.csv data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import wick_trail_study as w
from atlas_trader.backtest import split_in_out
from atlas_trader.data import load_csv, resample

MODEL = "momentum_rsi"
HOLDOUT = ("2023-03-02", "2025-09-30")
BUFS = (0.10, 0.25, 0.50)
LOOSE = dict(body_frac=0.5, body_atr=0.3)


def round_variants(k: int, base: dict) -> list[tuple[str, dict]]:
    """Variants of round k (1-3). `base` = settings carried over from the previous round's pick."""
    out = []
    if k == 1:
        for act in (1.5, 2.0):
            for b in BUFS:
                out.append((f"act {act}R buf {b:.2f}", dict(buffer_atr=b, activate_r=act)))
    elif k == 2:
        for conf in ("high", "close"):
            for loose in (False, True):
                if conf == "high" and not loose:
                    continue                                   # = round 1's rule
                for b in BUFS:
                    kw = dict(base, buffer_atr=b, confirm_on=conf, **(LOOSE if loose else {}))
                    out.append((f"act {kw.get('activate_r', 1.0)}R confirm {conf}"
                                + (" loose" if loose else "") + f" buf {b:.2f}", kw))
    else:
        for b in BUFS:
            kw = dict(base, buffer_atr=b, require_dip=False)
            name = (f"act {kw.get('activate_r', 1.0)}R confirm {kw.get('confirm_on', 'high')}"
                    + (" loose" if kw.get("body_frac") == 0.5 else "") + f" no-dip buf {b:.2f}")
            out.append((name, kw))
    return out


def dev_data(path1: str) -> None:
    base = load_csv(path1)
    base = base[(base.index >= pd.Timestamp(w.START, tz="UTC")) &
                (base.index < pd.Timestamp(w.END, tz="UTC") + pd.Timedelta(days=1))]
    a, b = split_in_out(base, 0.5)
    w.DATA.clear()
    for part, d in (("A", a), ("B", b)):
        w.DATA[("1m", part)] = d
        w.DATA[("5m", part)] = resample(d, "5m")
    print(f"DEV DATA {base.index[0]} -> {base.index[-1]} | A ends {a.index[-1].date()} | B starts {b.index[0].date()}",
          flush=True)


def holdout(path5: str, name: str, kw: dict) -> None:
    base = load_csv(path5)
    h = base[(base.index >= pd.Timestamp(HOLDOUT[0], tz="UTC")) &
             (base.index < pd.Timestamp(HOLDOUT[1], tz="UTC") + pd.Timedelta(days=1))]
    w.DATA.clear()
    w.DATA[("5m", "H")] = h
    w.VARIANTS = [(name, kw)]
    print(f"\nHOLDOUT {h.index[0]} -> {h.index[-1]} ({len(h):,} 5m bars), candidate '{name}'", flush=True)
    res = w.run_job((MODEL, "H"))
    print(f"baseline replay max |dR| {res.get('replay_max_err')} | entries {res['n']}")
    base_r = np.array(res["per_trade"]["baseline"])
    cand_r = np.array(res["per_trade"][f"wick 5m {name}"])
    sb, sc = w.stats(base_r, base_r), w.stats(cand_r, base_r)
    rows = pd.DataFrame([dict(variant="baseline", **sb), dict(variant=name, **sc)])
    rows.to_csv("research/wick_rounds_holdout.csv", index=False)
    print(rows.round(3).to_string(index=False))
    beats = sc["totalR"] > sb["totalR"]
    pos = sc["expR"] > 0
    dd_ok = sc["maxDD"] <= w.DD_TOLERANCE * sb["maxDD"] + 1e-12
    passed = beats and pos and dd_ok
    strong = passed and sc["n"] >= w.STRONG_N and sc["t"] >= w.STRONG_T
    print(f"\nHOLDOUT VERDICT: {sc['totalR']:+.1f}R vs baseline {sb['totalR']:+.1f}R [{'ok' if beats else 'FAIL'}], "
          f"exp {sc['expR']:+.3f}R [{'ok' if pos else 'FAIL'}], maxDD {sc['maxDD']:.1f}R vs {sb['maxDD']:.1f}R x1.2 "
          f"[{'ok' if dd_ok else 'FAIL'}], paired diff {sc['diff']:+.3f}R/trade t={sc['t']:.2f} n={sc['n']} -> "
          + ("STRONG" if strong else "PASS" if passed else "NOT PASSED (live trail stays)"))


def main(path1: str, path5: str) -> None:
    w.MODELS = {MODEL: dict(w.MODELS[MODEL], trail_tfs=("5m",))}
    dev_data(path1)
    carry: dict = {}
    for k in (1, 2, 3):
        variants = round_variants(k, carry)
        w.VARIANTS = variants
        print(f"\n{'=' * 25} ROUND {k}: {len(variants)} variants {'=' * 25}", flush=True)
        dec = w.run_study(f"wick_rounds_r{k}")[MODEL]
        pick = dec["variant"].removeprefix("wick 5m ")
        carry = {kk: v for kk, v in dict(variants)[pick].items() if kk != "buffer_atr"}
        if dec["passed"]:
            print(f"\nROUND {k} PICK PASSES ON DEV -> holdout test of '{pick}'", flush=True)
            dev_data_kw = dict(variants)[pick]
            holdout(path5, pick, dev_data_kw)
            return
        print(f"ROUND {k}: pick '{pick}' did not pass -> next round", flush=True)
    print("\nNo round passed on the development data. Stop: the live trail stays (no holdout test).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv",
         sys.argv[2] if len(sys.argv) > 2 else "data/raw/XAUUSD.R_5m.csv")
