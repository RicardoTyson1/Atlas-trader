"""P4 Bitcoin weekends, step 1: data. Read-only against the broker (no orders, no account changes).

1. Downloads GatesFX BTCUSD 1-minute bars for the longest period available (walks back from now
   until the broker returns no data) -> data/raw/BTCUSD_1m.csv in the CURRENT directory.
2. Reports the date range, gaps, and weekend coverage (Fri 17:00 -> Sun 18:00 New York).
3. TradeLocker's history endpoint has no bid/ask or price-type option (only routeId, from, to,
   resolution, instrument), so historical spread cannot be downloaded. Instead this samples the
   LIVE bid/ask now (run it on a weekend) and reports median / 90th percentile / max spread, and
   checks whether the history bars are built from the bid, the ask or the mid, by comparing each
   just-closed bar's close with the quote taken in that bar's last seconds.

Credentials are read from an existing .env (read only, never printed):
  python research/btc_weekend_data.py --env /opt/atlas-trader/.env [--samples 60] [--every 5]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from atlas_trader.cli import _load_env
from atlas_trader.config import load_config
from atlas_trader.weekend import in_weekend_window

BTC = "BTCUSD"
CHUNK_ROWS = 20000                   # the API rejects requests for more than 20,000 bars
PAUSE = 10.0                         # be gentle: the live bots share these credentials


def with_backoff(fn, *a, tries: int = 6, **kw):
    """Retry on HTTP 429 (Too Many Requests): wait 20 s, 40 s, 80 s ... then give up."""
    wait = 20.0
    for k in range(tries):
        try:
            return fn(*a, **kw)
        except Exception as e:                          # the SDK raises requests' HTTPError
            if "429" not in str(e) or k == tries - 1:
                raise
            print(f"  rate-limited, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
            wait *= 2


def broker_from_config():
    from atlas_trader.brokers.tradelocker import TradeLockerBroker
    cfg = load_config("config/default.yaml")
    ids = [int(a) for a in (cfg.tradelocker.get("demo_account_ids") or [])]
    return TradeLockerBroker(cfg.tradelocker.get("demo_url", "https://demo.tradelocker.com"),
                             account_id=ids[0] if ids else None, allowed_account_ids=ids or None)


def download_all(broker, max_days: int = 2000) -> pd.DataFrame:
    """Walk back in 20,000-minute chunks until two chunks in a row come back empty."""
    end = pd.Timestamp.now(tz="UTC").floor("min")
    frames, empty_run = [], 0
    t1 = end
    while (end - t1).days < max_days and empty_run < 2:
        t0 = t1 - pd.Timedelta(minutes=CHUNK_ROWS - 1)
        df = with_backoff(broker.history, BTC, "1m", t0, t1, chunk_rows=CHUNK_ROWS, pause=0.0)
        if df.empty:
            empty_run += 1
        else:
            empty_run = 0
            frames.append(df)
            print(f"  {df.index[0]} -> {df.index[-1]}  {len(df):,} bars", flush=True)
        t1 = t0 - pd.Timedelta(minutes=1)
        time.sleep(PAUSE)
    if not frames:
        raise SystemExit("no BTCUSD history returned")
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def gap_report(df: pd.DataFrame) -> dict:
    idx = df.index
    d = pd.Series(idx[1:] - idx[:-1], index=idx[:-1])
    gaps = d[d > pd.Timedelta(minutes=1)]
    big = gaps[gaps > pd.Timedelta(minutes=30)].sort_values(ascending=False)
    local = idx.tz_convert("America/New_York")
    wk = np.array([in_weekend_window(t) for t in local.to_pydatetime()])
    weekends = pd.Index(local[wk].normalize()).unique()
    return {
        "first": str(idx[0]), "last": str(idx[-1]), "bars": int(len(idx)),
        "gaps_over_1min": int(len(gaps)), "gaps_over_30min": int(len(big)),
        "largest_gaps": [f"{s} -> {s + g} ({g})" for s, g in big.head(10).items()],
        "weekend_bars": int(wk.sum()), "weekend_days_with_data": int(len(weekends)),
    }


def sample_spread(broker, samples: int, every: float) -> dict:
    sp = []
    for _ in range(samples):
        try:
            bid, ask = with_backoff(broker.quote, BTC)
            sp.append(ask - bid)
        except Exception as e:                      # a rate-limit reply must not stop the sample
            print(f"  quote failed: {type(e).__name__}", flush=True)
        time.sleep(every)
    a = np.array(sp)
    return {"n": int(len(a)), "median": float(np.median(a)), "p90": float(np.percentile(a, 90)),
            "max": float(a.max()), "min": float(a.min()), "taken_utc": str(pd.Timestamp.now(tz="UTC"))}


def price_basis(broker, tries: int = 6) -> dict:
    """Quote in a bar's last seconds vs that bar's close once it has closed."""
    rows = []
    for _ in range(tries):
        now = pd.Timestamp.now(tz="UTC")
        time.sleep(max(0.0, 57.5 - now.second - now.microsecond / 1e6))
        minute = pd.Timestamp.now(tz="UTC").floor("min")
        bid, ask = with_backoff(broker.quote, BTC)
        time.sleep(6)
        bars = with_backoff(broker.history, BTC, "1m", minute - pd.Timedelta(minutes=3),
                            minute + pd.Timedelta(minutes=1), chunk_rows=CHUNK_ROWS, pause=0.0)
        if minute in bars.index:
            c = float(bars.loc[minute, "close"])
            rows.append({"bid": bid, "ask": ask, "close": c, "close-bid": c - bid, "ask-close": ask - c})
    if not rows:
        return {"verdict": "unknown (no matching bars)"}
    t = pd.DataFrame(rows)
    rel = ((t["close"] - t["bid"]) / (t["ask"] - t["bid"])).median()   # 0 = bid, 0.5 = mid, 1 = ask
    verdict = "bid" if rel < 0.25 else "ask" if rel > 0.75 else "mid"
    return {"verdict": verdict, "median_position_in_spread": float(rel), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help="existing .env with TradeLocker credentials (read only)")
    ap.add_argument("--samples", type=int, default=60)
    ap.add_argument("--every", type=float, default=5.0)
    args = ap.parse_args()
    if not Path(args.env).exists():
        raise SystemExit(f"{args.env} not found")
    _load_env(args.env)
    broker = broker_from_config()
    print(f"spec {broker.spec(BTC)}", flush=True)
    print("downloading BTCUSD 1m (newest first)...", flush=True)
    df = download_all(broker)
    Path("data/raw").mkdir(parents=True, exist_ok=True)
    df.rename_axis("time").to_csv("data/raw/BTCUSD_1m.csv")
    rep = gap_report(df)
    print(json.dumps(rep, indent=1), flush=True)
    print(f"sampling live spread: {args.samples} quotes, every {args.every:g}s ...", flush=True)
    s = sample_spread(broker, args.samples, args.every)
    print("LIVE SPREAD " + json.dumps(s), flush=True)
    print("checking which price the history bars use ...", flush=True)
    pb = price_basis(broker)
    print("BAR PRICE BASIS " + json.dumps({k: v for k, v in pb.items() if k != "rows"}), flush=True)
    Path("research/btc_weekend_data.json").write_text(json.dumps({"data": rep, "live_spread": s,
                                                                  "bar_price_basis": pb}, indent=1, default=str))


if __name__ == "__main__":
    main()
