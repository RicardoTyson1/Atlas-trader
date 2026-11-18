"""Ricardo's model, all pieces STACKED, with anti-spiral guardrails. 16 yrs COMEX 1m. Pre-registered 2026-11-18.

Rule book: /mnt/project-files/atlas/rick-rules.md. Ricardo: "do everything the way I trade, but not the way I
spiral to a blown account". Each piece alone failed before; this tests them together, as he trades them.

  1 BIAS (daily + 4H): the daily-Fib bias windows of research/fib_bias_study.py (latest daily swing,
    golden ratio 0.618; a solid 4H candle out of demand toward G = longs only; out of supply = shorts only).
  2 STRUCTURE (5m, or 15m as a variant): inside the bias, a SOLID candle (body >= 60% of range) closes
    beyond the last confirmed swing (BOS / CHoCH) in the bias direction. It leaves a fair value gap
    (candle before / after do not overlap) -> zone = the FVG; if there is no FVG, the order block
    (the last opposite-colour candle in the 5 before) -> zone = that candle's range.
  3 RETEST + TRIGGER on the 1m, within 2 hours of the break (setup cancelled on a 1m close through the zone):
       rejection  : price touched the zone, then a 1m candle closes back out of it in the trade direction
       rsi_extreme: touched, RSI(14) on 1m / 5m / 15m in 16.5-20 (buy) / 80-85 (sell), then the first 1m close
                    in the trade direction within 10 minutes
       rsi50      : touched, then an aggressive 1m candle (body >= 1 ATR) that crosses RSI 50 in the trade direction
       any        : whichever comes first
  4 STOP beyond the zone / retest wick (+0.2); MINIMUM $5 (1m stops of ~$1.70 lost ~0.7R/trade to costs).
    TARGET = the golden ratio; skipped if it pays < 1R (live bot's min_target_r). After +1R the stop trails
    behind 5m pullback wicks; an aggressive 1m candle in favour (body >= 1.5 ATR) pulls it to mid-candle.
    Stop only moves forward. Flat at 16:50 NY (daily break), no entries 16:30-18:05. 24/5 otherwise.
  5 GUARDRAILS (from his own account: after a loss 31% wins vs 77% after a win; sizing up after a loss lost
    $5,152 in 2 trades): fixed risk (results in R), 30 min cooldown after a loss, stop for the day after 2
    losses in a row or -2R on the day. One position at a time, one trade per setup.
Costs: GatesFX XAUUSD.R (spread 0.69 + slippage 0.25 each side, $7/lot round trip), in R.
PRIMARY (decides): structure 5m, trigger any, guardrails ON. Others are context only.
PASS: >= 200 trades, total R > 0, both halves (2010-2018 / 2019-2026) > 0, >= 4 of 6 periods > 0. STRONG: t >= 2.5.

Run in /opt/atlas-research (needs data/raw/fib_cache from fib_bias_study.py):
  PYTHONPATH=.:research nice -n 19 python research/rick_model_study.py
  PYTHONPATH=.:research nice -n 19 python research/rick_model_study.py --report
"""
from __future__ import annotations

import os
import sys
import time as _time

import numpy as np
import pandas as pd

import fib_bias_study as fb

OUT = "research/rick_model_trades.csv"
RES = "research/rick_model_results.csv"
MIN_STOP = 5.0
BUF = 0.2
RETEST_MIN = 120
COOLDOWN_MIN = 30
DAY_MAX_LOSS_STREAK, DAY_MAX_LOSS_R = 2, -2.0
MIN_TARGET_R = 1.0
FLAT_MOD, LAST_ENTRY_MOD, REOPEN_MOD = 16 * 60 + 50, 16 * 60 + 30, 18 * 60 + 5
TRIGGERS = ("rejection", "rsi_extreme", "rsi50", "any")
STRUCT_TFS = (5, 15)
BUY_BAND, SELL_BAND = fb.BANDS["rick"]


def atr(h, l, c, n=14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))).astype(np.float32)
    return fb._wilder(tr, n)


def frame(D, minutes):
    """OHLC of `minutes` bars from the 1m arrays; index of each bar's last 1m bar."""
    _, s, e = fb.buckets(D["t"] // (minutes * fb.MIN))
    O, H, L, C = fb.ohlc(D["o"], D["h"], D["l"], D["c"], s, e)
    sh, sl = fb.swing_flags(H, L)
    ch, cl = fb.latest_confirmed(sh, H), fb.latest_confirmed(sl, L)
    return dict(O=O, H=H, L=L, C=C, last=e - 1, csh=ch, csl=cl)


def setups(D, F, B):
    """BOS + FVG/OB setups inside bias windows. Each: side, ready (1m index), zone lo/hi, G, bias id."""
    O, H, L, C, last, csh, csl = F["O"], F["H"], F["L"], F["C"], F["last"], F["csh"], F["csl"]
    out = []
    for bi, bz in enumerate(B):
        side = bz["side"]
        k0 = int(np.searchsorted(last, bz["start"]))
        k1 = int(np.searchsorted(last, bz["end"]))
        for k in range(max(k0, 6), min(k1, len(C) - 1)):
            rng = H[k] - L[k]
            if rng <= 0 or abs(C[k] - O[k]) < fb.BODY * rng or (C[k] - O[k]) * side <= 0:
                continue
            ref = csh[k] if side == 1 else csl[k]
            if ref < 0:
                continue
            level = H[ref] if side == 1 else L[ref]
            if (C[k] - level) * side <= 0 or (O[k] - level) * side > 0:     # this candle makes the break
                continue
            # FVG between k-1 and k+1 (known when k+1 closes), else the order block
            if side == 1 and L[k + 1] > H[k - 1]:
                lo, hi, kind = H[k - 1], L[k + 1], "fvg"
            elif side == -1 and H[k + 1] < L[k - 1]:
                lo, hi, kind = H[k + 1], L[k - 1], "fvg"
            else:
                ob = [j for j in range(k - 1, k - 6, -1) if (C[j] - O[j]) * side < 0]
                if not ob:
                    continue
                lo, hi, kind = L[ob[0]], H[ob[0]], "ob"
            if (bz["G"] - (hi if side == 1 else lo)) * side <= 0:
                continue
            out.append(dict(side=side, ready=int(last[k + 1]) + 1, lo=float(lo), hi=float(hi), G=bz["G"],
                            kind=kind, bias=bi))
    out.sort(key=lambda x: x["ready"])
    return out


def simulate(D, S, trigger, guard, after_t=None, core=None):
    t, o, h, l, c, mod, dbid = D["t"], D["o"], D["h"], D["l"], D["c"], D["mod"], D["dbid"]
    r1, r5, r15, a1 = D["r1"], D["r5"], D["r15"], D["atr1"]
    n = len(c)
    lo_i, hi_i = (0, n) if core is None else (int(np.searchsorted(t, core[0])), int(np.searchsorted(t, core[1])))
    free_at = max(lo_i, 0 if after_t is None else int(np.searchsorted(t, after_t, side="right")))
    trades = []
    day_streak, day_r, day_id = 0, 0.0, -1
    for su in S:
        side, s0 = su["side"], su["ready"]
        if s0 >= hi_i or s0 + RETEST_MIN <= free_at:
            continue
        zt, zb = (su["hi"], su["lo"]) if side == 1 else (su["lo"], su["hi"])   # near edge, far edge
        touched, ext, arm = -1, (np.inf if side == 1 else -np.inf), -1
        ent = -1
        for j in range(s0, min(s0 + RETEST_MIN, n)):
            if (c[j] - zb) * side < 0:                       # closed through the zone: setup dead
                break
            if touched < 0 and (l[j] <= zt if side == 1 else h[j] >= zt):
                touched = j
            if touched < 0:
                continue
            ext = min(ext, l[j]) if side == 1 else max(ext, h[j])
            if j < free_at:
                continue
            body = (c[j] - o[j]) * side
            fire = False
            if trigger in ("rejection", "any") and body > 0 and (c[j] - zt) * side > 0:
                fire = True
            if not fire and trigger in ("rsi_extreme", "any"):
                lo_b, hi_b = BUY_BAND if side == 1 else SELL_BAND
                if any(lo_b <= r[j] <= hi_b for r in (r1, r5, r15)):
                    arm = j
                elif arm >= 0 and j - arm <= fb.ARM_WINDOW and body > 0:
                    fire = True
            if not fire and trigger in ("rsi50", "any") and j > 0 and body >= a1[j]:
                if (r1[j - 1] - 50) * side < 0 <= (r1[j] - 50) * side:
                    fire = True
            if fire:
                ent = j
                break
        if ent < 0 or ent < lo_i or ent >= hi_i:
            continue
        m = int(mod[ent])
        if LAST_ENTRY_MOD <= m < REOPEN_MOD:
            continue
        if guard:
            if dbid[ent] != day_id:
                day_id, day_streak, day_r = dbid[ent], 0, 0.0
            if day_streak >= DAY_MAX_LOSS_STREAK or day_r <= DAY_MAX_LOSS_R:
                continue
        fill = c[ent] + side * fb.HALF_COST
        stop = min(zb, ext) - BUF if side == 1 else max(zb, ext) + BUF
        if (fill - stop) * side < MIN_STOP:
            stop = fill - side * MIN_STOP
        risk = (fill - stop) * side
        if (su["G"] - fill) * side < MIN_TARGET_R * risk:
            continue
        tr = run_trade(D, side, ent, fill, stop, risk, su["G"])
        tr.update(kind=su["kind"], trigger=trigger)
        trades.append(tr)
        free_at = tr["exit_i"] + 1
        if guard:
            if tr["r"] < 0:
                free_at = max(free_at, int(np.searchsorted(t, t[tr["exit_i"]] + COOLDOWN_MIN * fb.MIN)))
            if dbid[tr["exit_i"]] == day_id:
                day_r += tr["r"]
                day_streak = day_streak + 1 if tr["r"] < 0 else 0
    return trades


def run_trade(D, side, ent, fill, stop, risk, G):
    t, o, h, l, c, mod, a1 = D["t"], D["o"], D["h"], D["l"], D["c"], D["mod"], D["atr1"]
    sw_lo, sw_hi = D["sw5_low"], D["sw5_high"]
    n = len(c)
    trail, best, j = False, fill, ent + 1
    px, why = None, None
    while j < n:
        if side == 1 and l[j] <= stop:
            px, why = min(o[j], stop), "stop"
            break
        if side == -1 and h[j] >= stop:
            px, why = max(o[j], stop), "stop"
            break
        if (side == 1 and h[j] >= G) or (side == -1 and l[j] <= G):
            px, why = (max(o[j], G) if side == 1 else min(o[j], G)), "target"
            break
        if mod[j] == FLAT_MOD:
            px, why = c[j], "daily_flat"
            break
        best = max(best, h[j]) if side == 1 else min(best, l[j])
        if not trail and (best - fill) * side >= risk:
            trail = True
        if trail:
            sw = sw_lo[j] if side == 1 else sw_hi[j]
            if not np.isnan(sw):
                new = sw - side * BUF
                if (new - stop) * side > 0 and (c[j] - new) * side > 0:
                    stop = new
            if (c[j] - o[j]) * side >= 1.5 * a1[j]:
                mid = (o[j] + c[j]) / 2
                if (mid - stop) * side > 0:
                    stop = mid
        j += 1
    if px is None:
        j, px, why = n - 1, c[n - 1], "end_of_data"
    out = px - side * fb.HALF_COST
    r = (out - fill) * side / risk - fb.COMM / risk
    return dict(side=side, entry_i=ent, exit_i=j, entry_time=pd.Timestamp(t[ent], tz="UTC"),
                exit_time=pd.Timestamp(t[j], tz="UTC"), entry=round(float(fill), 2), risk=round(float(risk), 2),
                target=round(float(G), 2), exit=round(float(out), 2), r=round(float(r), 4), why=why)


def run(out: str = fb.CACHE):
    if not os.path.exists(f"{out}/rolls.npz"):
        fb.make_cache(fb.RAW, out)
    years = fb.cache_years(out)
    labels = [(tf, trg, g) for tf in STRUCT_TFS for trg in TRIGGERS for g in (True, False)]
    last_exit, rows = {}, []
    for y in years:
        t0 = _time.time()
        win = [x for x in (y - 1, y, y + 1) if x in years]
        D = fb.build(*fb.load_1m(win, out))
        D["atr1"] = atr(D["h"], D["l"], D["c"])
        B = fb.biases(D)
        core = (pd.Timestamp(f"{y}-01-01", tz="UTC").value, pd.Timestamp(f"{y + 1}-01-01", tz="UTC").value)
        counts = []
        for tf in STRUCT_TFS:
            S = setups(D, frame(D, tf), B)
            for trg in TRIGGERS:
                for g in (True, False):
                    label = f"s{tf}m|{trg}|{'guard' if g else 'noguard'}"
                    tr = simulate(D, S, trg, g, after_t=last_exit.get(label), core=core)
                    for x in tr:
                        x["label"] = label
                        x.pop("entry_i"), x.pop("exit_i")
                    if tr:
                        last_exit[label] = tr[-1]["exit_time"].value
                    rows += tr
                    counts.append(len(tr))
        print(f"{y}: trades per variant {counts} ({_time.time() - t0:.0f}s)", flush=True)
        del D, B
        fb._trim()
    T = pd.DataFrame(rows)
    T.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(T)} trades); labels {[f's{a}m|{b}|{c}' for a, b, c in labels][:2]}...", flush=True)


def report():
    fb.OUT, fb.RES = OUT, RES
    fb.report()
    T = pd.read_csv(OUT)
    T["cost_r"] = (2 * fb.HALF_COST + fb.COMM) / T.risk
    g = T.groupby("label").agg(n=("r", "size"), netR=("r", "sum"), grossR=("cost_r", "sum"),
                               med_risk=("risk", "median"))
    g["grossR"] = g["netR"] + g["grossR"]
    print("\nBefore costs (gross) vs after:\n", g.round(1).to_string())
    print("\nBy zone kind (primary):\n",
          T[T.label == "s5m|any|guard"].groupby("kind").r.agg(["size", "sum", "mean"]).round(3).to_string())


if __name__ == "__main__":
    report() if "--report" in sys.argv else run()
