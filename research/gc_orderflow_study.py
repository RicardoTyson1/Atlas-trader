"""Gold true order-flow study (1 year of Databento TBBO). PRE-REGISTERED: docs/PREREG_GC_ORDERFLOW_2026-11-22.md.

5m bars built from 1m order-flow bars (roll-adjusted). Costs: GC $1.26 round trip per trade. Every stop
is at least $5 away (1m-size stops get eaten by costs). Entry at the NEXT bar's open. One trade at a time.
Tests (both directions each):
  O1 delta_divergence  new 20-bar high (low) while the last 3 bars' true delta is negative (positive) -> fade
  O2 delta_thrust      5m delta z > 2.5 (vs prior 100 bars) on a bar closing in its direction -> follow
  O3 absorption        volume z > 2.5 at a new 20-bar low (high), delta against the bar, close in the
                       upper (lower) half -> reversal
  O4 block_print       largest single trade in the bar >= block size (99.5th pct of month 1) -> follow its side
  O5 book_imbalance    mean top-of-book imbalance over the bar > +0.30 (< -0.30) -> follow
Exits: target 2R, stop as listed, time exit after 12 bars (O4: 6, O5: 3).
Pass: n >= 200, net R > 0 in both halves (split 2026-05-01) and in >= 4 of 6 two-month periods. STRONG: t >= 2.5.

Usage: nice -n 19 python research/gc_orderflow_study.py data/of/GC_of_1m.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import multi_market_study as m  # noqa: E402  (shared simulate / trade_row / score)

m.SPLIT = pd.Timestamp("2026-05-01", tz=m.NY)
m.HOLDOUT = pd.Timestamp("2026-08-01", tz=m.NY)   # "last 3 months" view only
MIN_STOP = 5.0
ROOT = "GC"


def load(path: str) -> pd.DataFrame:
    d = pd.read_csv(path, parse_dates=["time"], index_col="time")
    d.index = pd.to_datetime(d.index, utc=True)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    roll = d["instrument_id"].ne(d["instrument_id"].shift()) & d["instrument_id"].shift().notna()
    gap = (d["open"] - d["close"].shift()).where(roll, 0.0)
    shift = gap[::-1].cumsum()[::-1].shift(-1).fillna(0.0)
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] + shift
    d["abs_smax"] = d["smax"].abs()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "buy_vol": "sum", "sell_vol": "sum", "imb_sum": "sum", "imb_n": "sum", "abs_smax": "max"}
    b = d.resample("5min", label="left", closed="left").agg(agg).dropna(subset=["open"])
    # side of the biggest trade in each 5m bar
    q = pd.DataFrame({"k": d.index.floor("5min"), "smax": d["smax"].values, "a": d["abs_smax"].values})
    b["smax"] = q.sort_values(["k", "a"], kind="stable").groupby("k")["smax"].last().reindex(b.index).values
    b["delta"] = b["buy_vol"] - b["sell_vol"]
    b["imb"] = b["imb_sum"] / b["imb_n"].replace(0, np.nan)
    b.index = b.index.tz_convert(m.NY)
    return b


def zscore(s: pd.Series, n: int = 100) -> pd.Series:
    return (s - s.rolling(n).mean().shift()) / s.rolling(n).std().shift()


def run(d: pd.DataFrame, sig: np.ndarray, stop_px: np.ndarray, hold: int) -> pd.DataFrame:
    rows, i, n = [], 0, len(d)
    o = d["open"].values
    while i < n - 2:
        s = sig[i]
        if s == 0 or not np.isfinite(stop_px[i]):
            i += 1
            continue
        entry = o[i + 1]
        stop = stop_px[i]
        if s > 0:
            stop = min(stop, entry - MIN_STOP)
        else:
            stop = max(stop, entry + MIN_STOP)
        risk = abs(entry - stop)
        res = m.trade_row(d, ROOT, i + 1, int(s), stop, entry + s * 2 * risk, i + hold)
        if res is None:
            i += 1
            continue
        rows.append(res[0])
        i = res[1] + 1
    return pd.DataFrame(rows)


def tests(d: pd.DataFrame, block: float) -> dict[str, pd.DataFrame]:
    h, l, c, o = d["high"], d["low"], d["close"], d["open"]
    a = m.atr(d, 14)
    rng = (h - l).replace(0, np.nan)
    hi20, lo20 = h.shift().rolling(20).max(), l.shift().rolling(20).min()
    d3 = d["delta"].rolling(3).sum()
    out = {}
    # O1
    sig = np.where((h > hi20) & (d3 < 0), -1, np.where((l < lo20) & (d3 > 0), 1, 0))
    stp = np.where(sig < 0, h.rolling(3).max() + 0.1, l.rolling(3).min() - 0.1)
    out["O1 delta_divergence"] = run(d, sig, stp, 12)
    # O2
    dz = zscore(d["delta"])
    sig = np.where((dz > 2.5) & (c > o), 1, np.where((dz < -2.5) & (c < o), -1, 0))
    stp = np.where(sig > 0, c - a, c + a)
    out["O2 delta_thrust"] = run(d, sig, stp, 12)
    # O3
    vz = zscore(d["volume"])
    pos = (c - l) / rng
    sig = np.where((vz > 2.5) & (l < lo20) & (d["delta"] < 0) & (pos >= 0.5), 1,
                   np.where((vz > 2.5) & (h > hi20) & (d["delta"] > 0) & (pos <= 0.5), -1, 0))
    stp = np.where(sig > 0, l - 0.1, h + 0.1)
    out["O3 absorption"] = run(d, sig, stp, 12)
    # O4 (month 1 only sets the block size; trades from month 2)
    live = d.index >= d.index[0] + pd.Timedelta(days=31)
    sig = np.where(live & (d["abs_smax"] >= block), np.sign(d["smax"]), 0)
    stp = np.where(sig > 0, c - a, c + a)
    out["O4 block_print"] = run(d, sig, stp, 6)
    # O5
    sig = np.where(d["imb"] > 0.30, 1, np.where(d["imb"] < -0.30, -1, 0))
    stp = np.where(sig > 0, c - a, c + a)
    out["O5 book_imbalance"] = run(d, sig, stp, 3)
    return out


def main(path: str) -> None:
    d = load(path)
    first = d.index < d.index[0] + pd.Timedelta(days=31)
    block = float(np.nanpercentile(d.loc[first, "abs_smax"], 99.5))
    print(f"5m bars: {len(d)}  {d.index[0]} -> {d.index[-1]}  block size (99.5th pct, month 1): {block:.0f} lots")
    print(f"mean |imbalance| {d['imb'].abs().mean():.3f}; bars with |imb|>0.30: {(d['imb'].abs() > 0.3).mean():.1%}")
    res = []
    for name, t in tests(d, block).items():
        s = m.score(name, t)
        s["2026-08+_R"] = s.pop("2023+_R", None)
        print(s, flush=True)
        res.append(s)
    r = pd.DataFrame(res)
    print("\n=== SUMMARY (pass bar adjusted to 1 year: halves split 2026-05-01, 6 two-month periods) ===")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(r.to_string(index=False))
    print("\n5 tests: a single non-STRONG pass is a lead, not proof. Any pass needs the prior year as out-of-sample.")


if __name__ == "__main__":
    main(sys.argv[1])
