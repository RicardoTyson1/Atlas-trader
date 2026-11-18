"""Check what the Databento key can do, without printing it. Free: historical metadata only, plus a
~15 s live session (covered by a live subscription) that replays the last 3 minutes of 1-second bars.
Usage:  python research/databento_live_check.py
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

import databento as db

KEY = os.environ.get("DATABENTO_API_KEY", "")
SYMS = ["GC.v.0", "SI.v.0", "CL.v.0", "ES.v.0", "YM.v.0", "NQ.v.0"]
print("databento", db.__version__, "| key set:", bool(KEY))

try:
    h = db.Historical(KEY)
    r = h.metadata.get_dataset_range(dataset="GLBX.MDP3")
    print("historical OK, GLBX.MDP3 range:", r)
except Exception as e:  # noqa: BLE001
    print("historical FAILED:", type(e).__name__, str(e)[:200])

live = db.Live(key=KEY)
got: dict[str, float] = {}
try:
    start = datetime.now(timezone.utc) - timedelta(minutes=3)
    live.subscribe(dataset="GLBX.MDP3", schema="ohlcv-1s", symbols=SYMS, stype_in="continuous",
                   start=start.isoformat())
    threading.Timer(15, live.stop).start()
    n = 0
    names: dict[int, str] = {}
    for rec in live:
        if isinstance(rec, db.SymbolMappingMsg):
            names[rec.instrument_id] = rec.stype_in_symbol
            continue
        if isinstance(rec, db.OHLCVMsg):
            n += 1
            got[names.get(rec.instrument_id, str(rec.instrument_id))] = rec.close / 1e9
            last_ts = rec.ts_event
    print(f"LIVE OK: {n} one-second bars in 15 s")
    for k, v in got.items():
        print(f"  {k}: {v}")
    if n:
        print("  latest bar time:", datetime.fromtimestamp(last_ts / 1e9, timezone.utc))
except Exception as e:  # noqa: BLE001
    print("LIVE FAILED:", type(e).__name__, str(e)[:300])
finally:
    try:
        live.stop()
    except Exception:  # noqa: BLE001
        pass
