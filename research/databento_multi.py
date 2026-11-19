"""Multi-market CME futures 5m/1h bars from Databento 1m OHLCV (GC, SI, CL, ES, YM, NQ), cost-gated.

Prints the exact cost first (free metadata call) and downloads only if the TOTAL is under `max_usd`.
Built for the 1 GB / 1 CPU bot server: downloads ONE YEAR of 1m at a time, resamples to 5m right away
(keeps instrument_id), appends to a CSV, and frees memory. Then back-adjusts the rolls (gap removed
from all EARLIER bars; latest prices stay real) and writes 5m + 1h files.

Output (per root):  <out>/<ROOT>_5m_raw.csv, <ROOT>_5m.csv, <ROOT>_1h.csv   (UTC timestamps)
Usage:
  python research/databento_multi.py cost 2010-06-06 2026-11-18
  python research/databento_multi.py get  2010-06-06 2026-11-18 data/multi 0
"""
from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import databento as db
import pandas as pd

DATASET, SCHEMA = "GLBX.MDP3", "ohlcv-1m"
ROOTS = ["GC", "SI", "CL", "ES", "YM", "NQ"]
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
       "instrument_id": "last"}


def client() -> db.Historical:
    return db.Historical(os.environ["DATABENTO_API_KEY"])


def kw(root: str) -> dict:
    return dict(dataset=DATASET, schema=SCHEMA, symbols=[f"{root}.v.0"], stype_in="continuous")


def costs(start: str, end: str) -> dict[str, float]:
    c = client()
    return {r: float(c.metadata.get_cost(start=start, end=end, **kw(r))) for r in ROOTS}


def back_adjust(d: pd.DataFrame) -> pd.DataFrame:
    roll = d["instrument_id"].ne(d["instrument_id"].shift()) & d["instrument_id"].shift().notna()
    gap = (d["open"] - d["close"].shift()).where(roll, 0.0)
    shift = gap[::-1].cumsum()[::-1].shift(-1).fillna(0.0)
    d = d.copy()
    for col in ("open", "high", "low", "close"):
        d[col] = d[col] + shift
    return d.drop(columns="instrument_id")


def download_root(c: db.Historical, root: str, start: str, end: str, out: Path) -> None:
    raw = out / f"{root}_5m_raw.csv"
    done = set()
    if raw.exists():
        done = set(pd.read_csv(raw, usecols=["time"])["time"].str[:4])
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        if str(y) in done:
            continue
        s = start if y == y0 else f"{y}-01-01"
        e = end if y == y1 else f"{y + 1}-01-01"
        df = c.timeseries.get_range(start=s, end=e, **kw(root)).to_df()
        df = df[["open", "high", "low", "close", "volume", "instrument_id"]]
        b = df.resample("5min", label="left", closed="left").agg(AGG).dropna()
        b.index.name = "time"
        b.to_csv(raw, mode="a", header=not raw.exists())
        print(f"  {root} {y}: {len(df)} 1m -> {len(b)} 5m", flush=True)
        del df, b
        gc.collect()


def finish_root(root: str, out: Path) -> None:
    d = pd.read_csv(out / f"{root}_5m_raw.csv", parse_dates=["time"], index_col="time")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    a = back_adjust(d)
    a.to_csv(out / f"{root}_5m.csv")
    h = d.resample("1h", label="left", closed="left").agg(AGG).dropna()
    back_adjust(h).to_csv(out / f"{root}_1h.csv")
    print(f"{root}: {len(a)} 5m bars, {a.index[0]} -> {a.index[-1]}", flush=True)
    del d, a, h
    gc.collect()


def get(start: str, end: str, out: Path, max_usd: float) -> None:
    cs = costs(start, end)
    total = sum(cs.values())
    for r, v in cs.items():
        print(f"cost {r}: ${v:.2f}", flush=True)
    print(f"TOTAL ${total:.2f}", flush=True)
    if total > max_usd:
        print(f"over the ${max_usd:.2f} cap: not downloading", flush=True)
        sys.exit(1)
    out.mkdir(parents=True, exist_ok=True)
    c = client()
    for r in ROOTS:
        download_root(c, r, start, end, out)
        finish_root(r, out)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    mode, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "cost":
        cs = costs(start, end)
        print({k: round(v, 2) for k, v in cs.items()}, "TOTAL", round(sum(cs.values()), 2))
    else:
        get(start, end, Path(sys.argv[4]), float(sys.argv[5]) if len(sys.argv) > 5 else 0.0)
