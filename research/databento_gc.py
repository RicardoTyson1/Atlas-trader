"""COMEX gold futures (GC, front month by volume) 1-minute OHLCV with REAL volume, from Databento.

Reads DATABENTO_API_KEY from the environment (/opt/atlas-trader/.env). Prints the exact cost first
(free call) and only downloads if it is under `max_usd`. Downloads ONE YEAR AT A TIME and appends to
the CSV, so memory stays small on the 1 GB server (the trading bot runs there too).

Then builds a roll-adjusted series: at each contract roll the price jump between the last bar of the
old contract and the first bar of the new one is removed from all EARLIER bars (back-adjusted), so
strategies don't see fake gaps. Output:
  data/raw/GC_1m_raw.csv  (as downloaded, with instrument_id; NOT roll-adjusted)
  data/raw/GC_5m.csv      (5m, back-adjusted)
Usage:  python research/databento_gc.py 2010-06-06 2026-11-14 data/raw 1
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import databento as db
import pandas as pd

DATASET, SCHEMA, SYMBOL = "GLBX.MDP3", "ohlcv-1m", "GC.v.0"
COLS = ["open", "high", "low", "close", "volume", "instrument_id"]


def download(start: str, end: str, raw: Path, max_usd: float) -> None:
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    kw = dict(dataset=DATASET, schema=SCHEMA, symbols=[SYMBOL], stype_in="continuous")
    cost = client.metadata.get_cost(start=start, end=end, **kw)
    print(f"cost ${cost:.2f} for {SYMBOL} {SCHEMA} {start} -> {end}", flush=True)
    if cost > max_usd:
        print(f"over the ${max_usd:.0f} cap: not downloading", flush=True)
        sys.exit(1)
    done = set()
    if raw.exists():
        done = set(pd.read_csv(raw, usecols=["time"])["time"].str[:4])
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        if str(y) in done:
            continue
        s = start if y == y0 else f"{y}-01-01"
        e = end if y == y1 else f"{y + 1}-01-01"
        df = client.timeseries.get_range(start=s, end=e, **kw).to_df()
        df = df[COLS]
        df.index.name = "time"
        df.to_csv(raw, mode="a", header=not raw.exists())
        print(f"  {y}: {len(df)} bars", flush=True)
        del df


def adjust(raw: Path, out_dir: Path) -> None:
    """Build 5m bars year-chunk by year-chunk (small memory), then back-adjust the rolls on 5m."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "instrument_id": "last"}
    parts = []
    for ch in pd.read_csv(raw, parse_dates=["time"], chunksize=500_000):
        ch = ch.drop_duplicates("time").set_index("time").sort_index()
        parts.append(ch.resample("5min", label="left", closed="left").agg(agg).dropna())
    d = pd.concat(parts)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    roll = d["instrument_id"].ne(d["instrument_id"].shift()) & d["instrument_id"].shift().notna()
    gap = (d["open"] - d["close"].shift()).where(roll, 0.0)
    # remove each roll gap from everything BEFORE it (back-adjusted: latest prices stay real)
    shift = gap[::-1].cumsum()[::-1].shift(-1).fillna(0.0)
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] + shift
    d.drop(columns="instrument_id").to_csv(out_dir / "GC_5m.csv")
    print(f"adjusted: {int(roll.sum())} rolls, {len(d)} 5m bars, {d.index[0]} -> {d.index[-1]}", flush=True)


if __name__ == "__main__":
    start, end, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    cap = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    out.mkdir(parents=True, exist_ok=True)
    download(start, end, out / "GC_1m_raw.csv", cap)
    adjust(out / "GC_1m_raw.csv", out)
