"""Gold (GC) true order flow from Databento TBBO (every trade + aggressor side + top of book), cost-gated.

Prints the exact cost first (free call) and stops if it is over `max_usd`. Downloads in half-month
pieces to a temp .dbn file, streams it in chunks (small memory: 1 GB server), and keeps only 1-minute
order-flow bars. Raw ticks are deleted after each piece.

Per 1m bar: open high low close, volume, buy_vol (buyer-aggressor), sell_vol, n_trades,
smax (signed size of the largest single trade: + buyer, - seller), imb_sum / imb_n (top-of-book
imbalance (bid_sz - ask_sz) / (bid_sz + ask_sz) seen at each trade), instrument_id.
Output: <out>/GC_of_1m.csv (UTC).

Usage: python research/gc_orderflow_build.py 2025-11-01 2026-11-01 data/of 5
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

KW = dict(dataset="GLBX.MDP3", schema="tbbo", symbols=["GC.v.0"], stype_in="continuous")


def partial(df: pd.DataFrame) -> pd.DataFrame:
    t = pd.to_datetime(df["ts_event"], utc=True).dt.floor("1min")
    side = np.where(df["side"].values == "B", 1, np.where(df["side"].values == "A", -1, 0))
    size = df["size"].values.astype("int64")
    bs, as_ = df["bid_sz_00"].values.astype(float), df["ask_sz_00"].values.astype(float)
    den = bs + as_
    imb = np.where(den > 0, (bs - as_) / np.where(den > 0, den, 1), np.nan)
    x = pd.DataFrame({"t": t.values, "seq": np.arange(len(df)), "price": df["price"].values.astype(float),
                      "size": size, "buy": size * (side > 0), "sell": size * (side < 0),
                      "ssize": size * side, "imb": imb, "iid": df["instrument_id"].values})
    g = x.groupby("t", sort=True)
    out = pd.DataFrame({"open": g["price"].first(), "high": g["price"].max(), "low": g["price"].min(),
                        "close": g["price"].last(), "volume": g["size"].sum(), "buy_vol": g["buy"].sum(),
                        "sell_vol": g["sell"].sum(), "n_trades": g["size"].count(),
                        "imb_sum": g["imb"].sum(), "imb_n": g["imb"].count(), "instrument_id": g["iid"].last()})
    k = x.loc[x["ssize"].abs().groupby(x["t"]).idxmax().values]
    out["smax"] = k.set_index("t")["ssize"]
    return out


def combine(parts: list[pd.DataFrame]) -> pd.DataFrame:
    p = pd.concat(parts)
    g = p.groupby(level=0, sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "buy_vol": g["buy_vol"].sum(),
                        "sell_vol": g["sell_vol"].sum(), "n_trades": g["n_trades"].sum(),
                        "imb_sum": g["imb_sum"].sum(), "imb_n": g["imb_n"].sum(),
                        "instrument_id": g["instrument_id"].last()})
    q = pd.DataFrame({"t": p.index, "smax": p["smax"].values, "a": p["smax"].abs().values})
    out["smax"] = q.sort_values(["t", "a"], kind="stable").groupby("t")["smax"].last()
    return out


def main(start: str, end: str, out: Path, max_usd: float) -> None:
    c = db.Historical(os.environ["DATABENTO_API_KEY"])
    cost = float(c.metadata.get_cost(start=start, end=end, **KW))
    print(f"cost GC tbbo {start} -> {end}: ${cost:.2f} (cap ${max_usd:.2f})", flush=True)
    if cost > max_usd:
        print("over the cap: not downloading", flush=True)
        sys.exit(1)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / "GC_of_1m.csv"
    marks = out / "pieces_done.txt"
    done = set(marks.read_text().split()) if marks.exists() else set()
    edges = list(pd.date_range(start, end, freq="SMS").strftime("%Y-%m-%d"))
    edges = sorted(set([start] + edges + [end]))
    for s, e in zip(edges[:-1], edges[1:]):
        if s in done:
            continue
        tmp = out / "piece.dbn.zst"
        store = c.timeseries.get_range(start=s, end=e, path=tmp, **KW)
        parts = [partial(ch) for ch in store.to_df(count=250_000) if len(ch)]
        if parts:
            bars = combine(parts)
            bars.index.name = "time"
            bars.to_csv(dst, mode="a", header=not dst.exists())
            print(f"  {s} -> {e}: {sum(len(p) for p in parts)} partial rows, {len(bars)} 1m bars", flush=True)
        with marks.open("a") as f:
            f.write(s + "\n")
        del store, parts
        tmp.unlink(missing_ok=True)
        gc.collect()
    print("BUILD DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]), float(sys.argv[4]) if len(sys.argv) > 4 else 5.0)
