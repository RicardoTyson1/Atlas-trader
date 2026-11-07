"""P1/P2 wick-trail study, pre-registered 2026-11-07 (Ricardo).

Question: does Ricardo's confirmed wick trail (P1), with or without a break-even step at +1R
(P2), beat the current "trail from 1R" candle trail on the same entries?

Exit rules: atlas_trader/wick_trail.py (strong-body candle = body >= 60% of range and >= 0.5 x
ATR(14) on the trail timeframe; activation at +1R; pullback must undercut a previous candle's
low; confirmation = a closed strong candle beyond S's extreme; stop = pullback extreme -/+ buffer,
forward only, live from the next candle; P2 = stop to entry + round-trip costs at +1R).

Models and entries (fixed, identical for every exit variant):
  * momentum_rsi, 5m entries: shoot-out #1 / last live settings (disp 1.0 ATR, EMA-50 trend, trail
    from 1R with 0.3 ATR room, big-candle lock 2.0 ATR, entries 08:00-11:00 NY).
  * reversal_trail, 1m entries: shoot-out #4 / last live settings (move 5 ATR, RSI 25/75, target
    at 50% of the fast move, trail from 1R with 0.3 ATR room, entries 08:00-11:00 NY). The
    strategy file was deleted from the bot on 2026-11-06; research/reversal_trail_model.py is an
    unchanged copy.
  Entries come from the normal backtester (one position at a time, same as every study). Each
  entry is then re-run with every exit variant, so per-trade results can be compared in pairs.

What changes: ONLY the "trail from 1R" candle trail is replaced. Everything else stays: the
initial stop, reversal_trail's 50%-of-move target, momentum_rsi's big-candle lock (still judged
on 5m candles when the trail runs on 1m), the 16:50 NY / end-of-day flat, costs and fills.

Grid (fixed before running):
  buffer 0.10 / 0.25 / 0.50 x ATR(14) on the trail timeframe
  x trail timeframe (momentum_rsi: 5m and 1m; reversal_trail: 1m)
  x break-even step on / off.          Baseline = the current live exit.

Data and costs: GatesFX XAUUSD.R 1m, 2025-10-01 -> 2026-11-05 (13 months), halves A/B split by whole
days (as the shoot-out). Spread 0.14 + slippage 0.10 points, $3.50 per lot per side, stop/target
inside one bar = stop first, gaps fill at the open. R here is NET of commission too (earlier docs
excluded commission from R).

Pass bar (fixed before running), per model:
  1. Pick the variant with the best total R on half A only.
  2. PASS only if on half B it beats the baseline's total R AND its expectancy is > 0 AND its max
     drawdown (in R, trades in entry order) is no more than 20% worse than the baseline's.
  3. Paired per-trade R difference vs baseline, with t-stat, on half B (the untouched half).
     STRONG = PASS and n >= 100 and t >= 2.5 (all on half B).
  4. Nothing passes -> the baseline stays.

Check built in: before any variant runs, the exit simulator replays the baseline and must
reproduce the backtester's R on every trade (else the script stops).

Run (never in /opt/atlas-trader):
  PYTHONPATH=. python research/wick_trail_study.py data/raw/XAUUSD.R_1m.csv
"""
from __future__ import annotations

import copy
import sys
from datetime import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest, spike_lock, split_in_out, trail_candidate
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.indicators import atr as atr_ind
from atlas_trader.risk import RiskManager
from atlas_trader.strategies import build_strategy
from atlas_trader.wick_trail import WickTrail

SYM = "XAUUSD.R"
START, END = "2025-10-01", "2026-11-05"           # same 13 months as the shoot-out
BUFFERS = (0.10, 0.25, 0.50)
HOURS = "08:00-11:00"
MODELS = {
    "momentum_rsi": dict(sig_tf="5m", trail_tfs=("5m", "1m"),
                         params=dict(disp_atr=1.0, trend_filter=True, trail_start_r=1.0, trail_buf_atr=0.3,
                                     spike_atr=2.0, hours=HOURS)),
    "reversal_trail": dict(sig_tf="1m", trail_tfs=("1m",),
                           params=dict(move_atr=5.0, rsi_level=25, target="half_move", spike_atr=0.0,
                                       trail_start_r=1.0, trail_buf_atr=0.3, hours=HOURS)),
}
DD_TOLERANCE = 1.20
STRONG_N, STRONG_T = 100, 2.5
DATA: dict = {}


# ------------------------------------------------------------------ setup (as every study)
def research_cfg():
    cfg = load_config("config/default.yaml")                 # read only
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 50
    cfg.risk.max_lots = 1e12
    cfg.risk.max_room_fraction_per_trade = 1.0
    cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(23, 59)
    cfg.session.overnight = False
    inst = copy.deepcopy(cfg.instrument(SYM))
    inst.min_lot = inst.lot_step = 1e-9
    return cfg, inst


def build(model: str, session):
    p = dict(MODELS[model]["params"])
    if model == "reversal_trail":
        from reversal_trail_model import ReversalTrail
        return ReversalTrail(session, **p)
    return build_strategy(model, session, p)


class Bars:
    """One timeframe of one half, with everything the exit simulator needs, precomputed."""

    def __init__(self, df: pd.DataFrame, cfg, inst, spike_src: pd.DataFrame | None = None):
        self.idx = df.index
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = atr_ind(df, 14).to_numpy()
        self.n = len(df)
        tz = cfg.session.tz
        delta = pd.Series(self.idx).diff().mode().iloc[0]
        close_local = (self.idx + delta).tz_convert(tz)
        close_mins = (close_local.hour * 60 + close_local.minute).to_numpy()
        days = self.idx.tz_convert(tz).date
        rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, cfg.starting_balance)
        flat_m = cfg.session.flat_by.hour * 60 + cfg.session.flat_by.minute
        force = np.zeros(self.n, bool)
        for i in range(self.n):                                  # same order of checks as the backtester
            end = i + 1 >= self.n
            force[i] = (rm.must_flatten(close_local[i].to_pydatetime()) is not None
                        or close_mins[i] >= flat_m or end or days[i + 1] != days[i])
        self.force = force
        # big-candle lock candles: the bar itself, or (1m trail of a 5m model) the 5m candle closing here
        self.spk_o, self.spk_c = self.o.copy(), self.c.copy()
        self.spk_on = np.ones(self.n, bool)
        if spike_src is not None:
            key = self.idx.floor("5min")
            last = ~pd.Series(key).duplicated(keep="last").to_numpy()
            five = spike_src.reindex(key)
            self.spk_o, self.spk_c = five["open"].to_numpy(float), five["close"].to_numpy(float)
            self.spk_on = last & ~np.isnan(self.spk_o)


def simulate(B: Bars, i0: int, side: int, entry: float, stop0: float, target: float,
             manage, half_cost: float) -> tuple[float, str, int]:
    """Exit one trade bar by bar exactly like the backtester. `manage(i, stop)` is called at each
    bar close and returns the stop for the next bar. Returns (exit fill, reason, exit bar)."""
    stop = stop0
    o, h, l, c = B.o, B.h, B.l, B.c
    for i in range(i0, B.n):
        if side == 1:
            if o[i] <= stop:
                return o[i] - half_cost, "stop (gap)", i
            if l[i] <= stop:
                return stop - half_cost, "stop", i
            if o[i] >= target:
                return o[i] - half_cost, "target (gap)", i
            if h[i] >= target:
                return target - half_cost, "target", i
        else:
            if o[i] >= stop:
                return o[i] + half_cost, "stop (gap)", i
            if h[i] >= stop:
                return stop + half_cost, "stop", i
            if o[i] <= target:
                return o[i] + half_cost, "target (gap)", i
            if l[i] <= target:
                return target + half_cost, "target", i
        if B.force[i]:
            return c[i] - side * half_cost, "flat", i
        stop = manage(i, stop)
    raise RuntimeError("ran out of bars")                    # force[] is True on the last bar


def baseline_manager(B: Bars, t) -> callable:
    def manage(i, stop):
        if not np.isnan(t.trail_start_r) and i >= 2:
            new = trail_candidate(t.side, t.entry, t.init_stop, stop, t.trail_start_r, t.trail_buf,
                                  B.o[i - 2:i + 1], B.h[i - 2:i + 1], B.l[i - 2:i + 1], B.c[i - 2:i + 1])
            if new is not None:
                stop = new
        if t.spike_mode == 2:
            new = spike_lock(t.side, t.entry, t.init_stop, stop, t.spike_pts, t.spike_min_r, B.o[i], B.c[i])
            if new is not None:
                stop = new
        return stop
    return manage


def wick_manager(B: Bars, t, buf: float, be: bool, be_offset: float) -> callable:
    wt = WickTrail(t.side, t.entry, t.init_stop, buf, breakeven=be, be_offset=be_offset)

    def manage(i, stop):
        if not np.isnan(B.atr[i]):
            wt.update(B.o[i], B.h[i], B.l[i], B.c[i], B.atr[i])
        if t.side * (wt.stop - stop) > 0:
            stop = wt.stop
        if t.spike_mode == 2 and B.spk_on[i]:                  # unchanged big-candle lock
            new = spike_lock(t.side, t.entry, t.init_stop, stop, t.spike_pts, t.spike_min_r,
                             B.spk_o[i], B.spk_c[i])
            if new is not None:
                stop = new
        return stop
    return manage


def net_r(side, entry, init_stop, fill, inst) -> float:
    risk = abs(entry - init_stop)
    return side * (fill - entry) / risk - 2 * inst.commission_per_lot / (inst.value_per_point * risk)


# ------------------------------------------------------------------ one model x one half
def run_job(job):
    model, part = job
    spec = MODELS[model]
    cfg, inst = research_cfg()
    half_cost = inst.spread_points / 2 + inst.slippage_points
    be_offset = half_cost + 2 * inst.commission_per_lot / inst.value_per_point
    sig_df = DATA[(spec["sig_tf"], part)]
    trades = run_backtest(sig_df, build(model, cfg.session), inst, cfg).trades
    out = {"model": model, "part": part, "n": len(trades), "per_trade": {}}
    if trades.empty:
        return out
    # 1) the simulator must reproduce the backtester on the baseline
    B_sig = Bars(sig_df, cfg, inst)
    i0s = B_sig.idx.get_indexer(trades.entry_time)
    assert (i0s >= 0).all()
    replay = []
    for t, i0 in zip(trades.itertuples(), i0s):
        fill, _, _ = simulate(B_sig, int(i0), t.side, t.entry, t.init_stop, t.target, baseline_manager(B_sig, t), half_cost)
        replay.append(t.side * (fill - t.entry) / abs(t.entry - t.init_stop))
    err = float(np.max(np.abs(np.array(replay) - trades.r.to_numpy(float))))
    out["replay_max_err"] = err
    if err > 1e-6:
        raise SystemExit(f"{model} {part}: exit simulator does not reproduce the backtester (max |dR| {err})")
    comm_r = 2 * inst.commission_per_lot / (inst.value_per_point * (trades.entry - trades.init_stop).abs())
    out["per_trade"]["baseline"] = list(trades.r.astype(float) - comm_r)
    out["entry_time"] = list(trades.entry_time)
    # 2) every wick-trail variant on the same entries
    for tf in spec["trail_tfs"]:
        df = DATA[("1m", part)] if tf == "1m" else DATA[("5m", part)]
        spike_src = DATA[("5m", part)] if (tf == "1m" and spec["sig_tf"] == "5m") else None
        B = B_sig if (tf == spec["sig_tf"]) else Bars(df, cfg, inst, spike_src=spike_src)
        i0v = np.searchsorted(B.idx.values, trades.entry_time.values)
        out.setdefault("entry_bar_mismatch", {})[tf] = int((B.idx.values[np.minimum(i0v, B.n - 1)] != trades.entry_time.values).sum())
        for buf in BUFFERS:
            for be in (False, True):
                name = f"wick {tf} buf {buf:.2f}" + (" +BE" if be else "")
                rs = []
                for t, i0 in zip(trades.itertuples(), i0v):
                    fill, _, _ = simulate(B, int(i0), t.side, t.entry, t.init_stop, t.target,
                                          wick_manager(B, t, buf, be, be_offset), half_cost)
                    rs.append(net_r(t.side, t.entry, t.init_stop, fill, inst))
                out["per_trade"][name] = rs
    return out


# ------------------------------------------------------------------ stats
def max_dd(r: np.ndarray) -> float:
    cum = np.concatenate([[0.0], np.cumsum(r)])
    return float(np.max(np.maximum.accumulate(cum) - cum))


def paired_t(d: np.ndarray) -> float:
    if len(d) < 3 or d.std(ddof=1) == 0:
        return 0.0
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))


def stats(r: np.ndarray, base: np.ndarray) -> dict:
    wins = r[r > 0]
    return {"n": len(r), "totalR": float(r.sum()), "expR": float(r.mean()) if len(r) else 0.0,
            "win%": float(100 * (r > 0).mean()) if len(r) else 0.0, "maxDD": max_dd(r),
            "avg_win_R": float(wins.mean()) if len(wins) else 0.0,
            "diff": float((r - base).mean()) if len(r) else 0.0, "t": paired_t(r - base)}


def main(path: str) -> None:
    base = load_csv(path)
    base = base[(base.index >= pd.Timestamp(START, tz="UTC")) &
                (base.index < pd.Timestamp(END, tz="UTC") + pd.Timedelta(days=1))]
    a, b = split_in_out(base, 0.5)
    for part, d in (("A", a), ("B", b)):
        DATA[("1m", part)] = d
        DATA[("5m", part)] = resample(d, "5m")
    print(f"DATA {base.index[0]} -> {base.index[-1]} ({len(base):,} 1m bars) | "
          f"A {a.index[0].date()} -> {a.index[-1].date()} | B {b.index[0].date()} -> {b.index[-1].date()}", flush=True)
    jobs = [(m, p) for m in MODELS for p in ("A", "B")]
    with Pool(min(4, len(jobs))) as pool:
        res = pool.map(run_job, jobs)
    got = {(r["model"], r["part"]): r for r in res}

    rows, trades_out = [], []
    pd.set_option("display.width", 250)
    verdicts = []
    for model in MODELS:
        A, Bh = got[(model, "A")], got[(model, "B")]
        print(f"\n{model}: n A={A['n']} B={Bh['n']} | baseline replay max |dR| A={A.get('replay_max_err')} "
              f"B={Bh.get('replay_max_err')} | entries not on an exact trail bar: A {A.get('entry_bar_mismatch')} "
              f"B {Bh.get('entry_bar_mismatch')}")
        bA, bB = np.array(A["per_trade"]["baseline"]), np.array(Bh["per_trade"]["baseline"])
        for name in A["per_trade"]:
            rA, rB = np.array(A["per_trade"][name]), np.array(Bh["per_trade"][name])
            sA, sB = stats(rA, bA), stats(rB, bB)
            sAll = stats(np.concatenate([rA, rB]), np.concatenate([bA, bB]))
            rows.append({"model": model, "variant": name, "A_n": sA["n"], "A_totalR": sA["totalR"],
                         "B_n": sB["n"], "B_totalR": sB["totalR"], "B_expR": sB["expR"], "B_win%": sB["win%"],
                         "B_maxDD": sB["maxDD"], "B_avg_win_R": sB["avg_win_R"], "B_diff": sB["diff"],
                         "B_t": sB["t"], "A_expR": sA["expR"], "A_maxDD": sA["maxDD"],
                         "all_totalR": sAll["totalR"], "all_diff": sAll["diff"], "all_t": sAll["t"]})
        for part, R in (("A", A), ("B", Bh)):
            for k, et in enumerate(R.get("entry_time", [])):
                trades_out.append({"model": model, "part": part, "entry_time": et,
                                   **{name: R["per_trade"][name][k] for name in R["per_trade"]}})
    t = pd.DataFrame(rows)
    t.to_csv("research/wick_trail_results.csv", index=False)
    pd.DataFrame(trades_out).to_csv("research/wick_trail_trades.csv", index=False)

    for model, g in t.groupby("model", sort=False):
        print(f"\n=== {model} ===")
        print(g.drop(columns="model").round(3).to_string(index=False))
        base_row = g[g.variant == "baseline"].iloc[0]
        cand = g[g.variant != "baseline"].sort_values("A_totalR", ascending=False).iloc[0]
        beats = cand.B_totalR > base_row.B_totalR
        pos = cand.B_expR > 0
        dd_ok = cand.B_maxDD <= DD_TOLERANCE * base_row.B_maxDD + 1e-12
        passed = beats and pos and dd_ok
        strong = passed and cand.B_n >= STRONG_N and cand.B_t >= STRONG_T
        verdicts.append(
            f"{model}: picked on A = '{cand.variant}' (A {cand.A_totalR:+.1f}R vs baseline {base_row.A_totalR:+.1f}R). "
            f"Half B: {cand.B_totalR:+.1f}R vs baseline {base_row.B_totalR:+.1f}R [{'ok' if beats else 'FAIL'}], "
            f"exp {cand.B_expR:+.3f}R [{'ok' if pos else 'FAIL'}], maxDD {cand.B_maxDD:.1f}R vs "
            f"{base_row.B_maxDD:.1f}R x1.2={DD_TOLERANCE * base_row.B_maxDD:.1f} [{'ok' if dd_ok else 'FAIL'}], "
            f"paired diff {cand.B_diff:+.3f}R/trade t={cand.B_t:.2f} n={int(cand.B_n)} -> "
            + ("STRONG" if strong else "PASS" if passed else "NOT PASSED (baseline stays)"))
    print("\nVERDICT")
    for v in verdicts:
        print("  " + v)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1m.csv")
