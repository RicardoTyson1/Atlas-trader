"""Download free Dukascopy XAUUSD 1-minute BID candles and save 1m + 5m CSVs (read-only, no account).

Dukascopy publishes one LZMA-compressed file of 1-minute candles per day:
  https://datafeed.dukascopy.com/datafeed/XAUUSD/{year}/{month-1:02d}/{day:02d}/BID_candles_min_1.bi5
Each record is 24 bytes, big-endian: seconds from midnight UTC, open, close, low, high (integers,
price x 1000 for XAUUSD) and volume (float).

Usage:
  python research/dukascopy_download.py 2008-01-01 2026-11-13 data/raw/duka
  -> data/raw/duka/XAUUSD_duka_1m.csv and XAUUSD_duka_5m.csv (time, open, high, low, close, volume)
Days already downloaded are cached in data/raw/duka/days/ so the script can be re-run.
"""
from __future__ import annotations

import lzma
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
SCALE = 1000.0
REC = struct.Struct(">IIIIIf")


def fetch_day(day: date, cache: Path) -> pd.DataFrame | None:
    f = cache / f"{day:%Y%m%d}.csv"
    if f.exists():
        return pd.read_csv(f, parse_dates=["time"]) if f.stat().st_size > 40 else None
    url = URL.format(y=day.year, m=day.month - 1, d=day.day)
    for attempt in range(8):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 429:                       # rate limited: back off and retry
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 404:
                f.write_text("time,open,high,low,close,volume\n")
                return None
            r.raise_for_status()
            break
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    else:
        print(f"  gave up on {day}", flush=True)
        return None
    raw = lzma.decompress(r.content) if r.content else b""
    rows = []
    t0 = pd.Timestamp(day, tz="UTC")
    for i in range(0, len(raw) - REC.size + 1, REC.size):
        s, o, c, lo, hi, v = REC.unpack_from(raw, i)
        if v <= 0:            # Dukascopy fills closed minutes with flat zero-volume candles
            continue
        rows.append((t0 + pd.Timedelta(seconds=s), o / SCALE, hi / SCALE, lo / SCALE, c / SCALE, v))
    d = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    d.to_csv(f, index=False)
    return d if len(d) else None


def main(start: str, end: str, out: str) -> None:
    out_dir = Path(out)
    cache = out_dir / "days"
    cache.mkdir(parents=True, exist_ok=True)
    days = [date.fromisoformat(start) + timedelta(n)
            for n in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
    days = [d for d in days if d.weekday() != 5]          # gold is shut on Saturdays
    frames = []
    with ThreadPoolExecutor(2) as pool:                   # gentle: 2 requests at a time
        for k, d in enumerate(pool.map(lambda x: fetch_day(x, cache), days)):
            if d is not None:
                frames.append(d)
            if k % 250 == 0:
                print(f"  {days[k]}  ({k}/{len(days)} days)", flush=True)
    m1 = pd.concat(frames).drop_duplicates("time").sort_values("time").set_index("time")
    m1.to_csv(out_dir / "XAUUSD_duka_1m.csv")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    m5 = m1.resample("5min", label="left", closed="left").agg(agg).dropna()
    m5.to_csv(out_dir / "XAUUSD_duka_5m.csv")
    print(f"done: {len(m1)} 1m bars, {len(m5)} 5m bars, {m1.index[0]} -> {m1.index[-1]}", flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:4] if len(sys.argv) > 3 else ("2008-01-01", "2026-11-13", "data/raw/duka")))
