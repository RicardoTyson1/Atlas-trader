"""READ-ONLY: Ricardo's own (manual) trade history on GatesFX TradeLocker demo #2595168.

Goal (2026-11-18): build the bot from how HE trades, minus the spiral that blows accounts. This pulls
his fills, rebuilds round-trip trades, and prints what wins (hours, side, hold time, size) and what the
spiral looks like (trades after losses, size after losses, trades per day on losing days).
Only GET calls (orders history / executions). Never places, modifies or closes anything.
Logs at WARNING so no request headers or tokens are printed.

Run on the server from /opt/atlas-trader (writes CSVs to /opt/atlas-research/data/manual/):
  sudo -u atlas bash -c 'set -a; . ./.env; set +a; nice -n 19 .venv/bin/python research/manual_history.py fetch'
  sudo -u atlas bash -c 'cd /opt/atlas-research && nice -n 19 /opt/atlas-trader/.venv/bin/python research/manual_history.py report'
"""
from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pandas as pd

ACCOUNT_ID = 2595168
OUT = "/opt/atlas-research/data/manual"
NY = "America/New_York"
OZ_PER_LOT = 100.0


def fetch():
    logging.basicConfig(level=logging.WARNING)
    for name in ("tradelocker", "urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)
    from tradelocker import TLAPI
    api = TLAPI(environment="https://demo.tradelocker.com", username=os.environ["TL_EMAIL"],
                password=os.environ["TL_PASSWORD"], server=os.environ["TL_SERVER"],
                account_id=ACCOUNT_ID, log_level="warning")
    if int(api.account_id) != ACCOUNT_ID:
        raise SystemExit(f"connected to {api.account_id}, expected {ACCOUNT_ID}: stopping")
    os.makedirs(OUT, exist_ok=True)
    orders = api.get_all_orders(lookback_period="365D", history=True)
    execs = api.get_all_executions()
    names = {}
    for df in (orders, execs):
        if df is not None and len(df) and "tradableInstrumentId" in df:
            for iid in df["tradableInstrumentId"].dropna().unique():
                try:
                    names[int(iid)] = api.get_symbol_name_from_instrument_id(int(iid))
                except Exception:  # noqa: BLE001 - unknown instrument: keep the id
                    names[int(iid)] = str(iid)
            df["symbol"] = df["tradableInstrumentId"].map(lambda x: names.get(int(x), str(x)))
    orders.to_csv(f"{OUT}/orders_history.csv", index=False)
    execs.to_csv(f"{OUT}/executions.csv", index=False)
    print(f"orders history: {len(orders)} rows, columns {list(orders.columns)}")
    print(f"executions: {len(execs)} rows, columns {list(execs.columns)}")
    print("symbols:", names)


def _ts(x):
    """TradeLocker times are epoch ms (sometimes strings)."""
    v = pd.to_numeric(x, errors="coerce")
    return pd.to_datetime(v, unit="ms", utc=True)


def trades_from_orders(orders: pd.DataFrame) -> pd.DataFrame:
    """Round trips from FILLED orders: per positionId, opening fills vs closing fills (qty-weighted)."""
    o = orders.copy()
    o = o[o["status"].astype(str).str.lower() == "filled"]
    o["t"] = _ts(o["lastModified"] if "lastModified" in o else o["createdDate"])
    o["px"] = pd.to_numeric(o.get("avgPrice", o.get("price")), errors="coerce")
    o["q"] = pd.to_numeric(o.get("filledQty", o.get("qty")), errors="coerce")
    o["sgn"] = np.where(o["side"].astype(str).str.lower() == "buy", 1, -1)
    rows = []
    for pid, g in o.sort_values("t").groupby("positionId"):
        if pd.isna(pid) or not len(g):
            continue
        side = int(g["sgn"].iloc[0])
        op, cl = g[g["sgn"] == side], g[g["sgn"] != side]
        if not len(cl):
            continue                                     # still open
        qo, qc = op["q"].sum(), cl["q"].sum()
        entry = (op["px"] * op["q"]).sum() / qo
        exit_ = (cl["px"] * cl["q"]).sum() / qc
        q = min(qo, qc)
        rows.append(dict(position=pid, symbol=g["symbol"].iloc[0] if "symbol" in g else "", side=side,
                         open_t=op["t"].min(), close_t=cl["t"].max(), lots=qo, adds=len(op) - 1,
                         entry=entry, exit=exit_, move=side * (exit_ - entry),
                         usd=side * (exit_ - entry) * q * OZ_PER_LOT,
                         stop_set=bool(pd.to_numeric(g.get("stopLoss"), errors="coerce").notna().any())
                         if "stopLoss" in g else None))
    return pd.DataFrame(rows).sort_values("open_t").reset_index(drop=True)


def report():
    orders = pd.read_csv(f"{OUT}/orders_history.csv")
    T = trades_from_orders(orders)
    if not len(T):
        print("no closed trades found")
        return
    T.to_csv(f"{OUT}/trades.csv", index=False)
    lt = T["open_t"].dt.tz_convert(NY)
    T["hour"] = lt.dt.hour
    T["dow"] = lt.dt.day_name().str[:3]
    T["day"] = (lt + pd.Timedelta(hours=6)).dt.date
    T["hold_min"] = (T.close_t - T.open_t).dt.total_seconds() / 60
    T["win"] = T.usd > 0
    T["prev_loss"] = T.usd.shift() < 0
    T["mins_since_prev"] = (T.open_t - T.close_t.shift()).dt.total_seconds() / 60
    T["lots_vs_prev"] = T.lots / T.lots.shift()
    T["n_in_day"] = T.groupby("day").cumcount() + 1
    T["day_pnl_before"] = T.groupby("day").usd.cumsum() - T.usd
    pd.set_option("display.width", 220)

    def s(x):
        return pd.Series({"n": len(x), "win%": round(100 * x.win.mean(), 1), "usd": round(x.usd.sum(), 0),
                          "avg_usd": round(x.usd.mean(), 1), "avg_lots": round(x.lots.mean(), 2)})

    print(f"closed trades {len(T)}  {T.open_t.min()} -> {T.close_t.max()}  symbols {T.symbol.value_counts().to_dict()}")
    print("\nOVERALL\n", s(T).to_string())
    print("\nBY SIDE\n", T.groupby("side").apply(s, include_groups=False).to_string())
    print("\nBY NY HOUR\n", T.groupby("hour").apply(s, include_groups=False).to_string())
    print("\nBY WEEKDAY\n", T.groupby("dow").apply(s, include_groups=False).to_string())
    T["hold"] = pd.cut(T.hold_min, [-1, 2, 5, 15, 60, 240, 1e9], labels=["<2m", "2-5m", "5-15m", "15-60m", "1-4h", ">4h"])
    print("\nBY HOLD TIME\n", T.groupby("hold", observed=True).apply(s, include_groups=False).to_string())
    print("\nWITH ADD-ONS vs NONE\n", T.groupby(T.adds > 0).apply(s, include_groups=False).to_string())
    print("\n--- THE SPIRAL ---")
    print("\nAFTER A LOSS vs AFTER A WIN\n", T.iloc[1:].groupby("prev_loss").apply(s, include_groups=False).to_string())
    quick = T.prev_loss & (T.mins_since_prev <= 10)
    print("\nRE-ENTRY WITHIN 10 MIN OF A LOSS\n", T[quick].pipe(s).to_string())
    print("\nSIZE UP AFTER A LOSS (lots > previous)\n",
          T[T.prev_loss & (T.lots_vs_prev > 1.01)].pipe(s).to_string())
    T["nth"] = pd.cut(T.n_in_day, [0, 1, 2, 3, 5, 1e9], labels=["1st", "2nd", "3rd", "4-5th", "6th+"])
    print("\nNth TRADE OF THE DAY\n", T.groupby("nth", observed=True).apply(s, include_groups=False).to_string())
    T["day_state"] = np.select([T.day_pnl_before < -200, T.day_pnl_before > 200], ["down >$200", "up >$200"], "flat")
    print("\nDAY P&L BEFORE THE TRADE\n", T.groupby("day_state").apply(s, include_groups=False).to_string())
    D = T.groupby("day").agg(n=("usd", "size"), usd=("usd", "sum"), max_lots=("lots", "max"))
    print("\nDAYS: count", len(D), " winning", int((D.usd > 0).sum()), " total $", round(D.usd.sum(), 0))
    print("worst 5 days\n", D.sort_values("usd").head(5).to_string())
    print("best 5 days\n", D.sort_values("usd").tail(5).to_string())
    print("\nIF HE HAD STOPPED after the 3rd trade OR -$500 on the day (same trades, rest skipped):")
    keep = (T.n_in_day <= 3) & (T.day_pnl_before > -500) & ~quick
    print(T[keep].pipe(s).to_string(), "\n skipped:", T[~keep].pipe(s).to_string())


if __name__ == "__main__":
    {"fetch": fetch, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
