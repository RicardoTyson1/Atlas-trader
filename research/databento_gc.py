"""COMEX gold futures (GC, front month, continuous) 1-minute OHLCV with REAL volume from Databento.

Reads DATABENTO_API_KEY from the environment (/opt/atlas-trader/.env). First prints the exact cost of
the request (free call), and only downloads if it is under `max_usd` (default $40 of the free credit).
  python research/databento_gc.py 2023-03-01 2026-11-14 data/raw/GC_1m.csv 40
"""
from __future__ import annotations

import os
import sys

import databento as db

DATASET, SCHEMA, SYMBOL = "GLBX.MDP3", "ohlcv-1m", "GC.v.0"     # front month by volume


def main(start: str, end: str, out: str, max_usd: float) -> None:
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    kw = dict(dataset=DATASET, schema=SCHEMA, symbols=[SYMBOL], stype_in="continuous", start=start, end=end)
    cost = client.metadata.get_cost(**kw)
    size = client.metadata.get_billable_size(**kw)
    print(f"cost ${cost:.2f} for {size / 1e6:.1f} MB ({SYMBOL} {SCHEMA} {start} -> {end})", flush=True)
    if cost > max_usd:
        print(f"over the ${max_usd:.0f} cap: not downloading", flush=True)
        return
    df = client.timeseries.get_range(**kw).to_df()
    df = df[["open", "high", "low", "close", "volume"]]
    df.index.name = "time"
    df.to_csv(out)
    print(f"saved {len(df)} bars {df.index[0]} -> {df.index[-1]} to {out}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 40.0)
