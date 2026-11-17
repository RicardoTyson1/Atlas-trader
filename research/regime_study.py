"""Market-condition switch (pre-registered 2026-11-16, Ricardo): can a daily regime filter keep the
lineup out of the 16-year losing periods?

Input: research/long_history_trades.csv (the lineup's trades on COMEX 2010-2026, live exits, costs in R,
from research/long_history_study.py) + data/raw/GC_5m.csv for the daily features.
Daily features use COMPLETED gold days only (the day rolls at 18:00 NY); a trade uses the values of
the last day that ended before its entry:
  volr   : 20-day average daily range / 250-day median of that average   (is gold moving more than usual?)
  trend  : |close - 50-day average| / 20-day average daily range           (how far from "fair", in ranges)
  bull   : close above the 200-day average (1) or not (0)
Candidate switches (fixed BEFORE running):
  V  volr >= {1.0, 1.2, 1.5}
  T  trend >= {1.0, 2.0, 3.0}
  B  bull == 1
  VT volr >= 1.2 and trend >= 2.0
  VB volr >= 1.2 and bull
Selection on the OLDER half only (2010-2018): best total R across the lineup, keeping >= 20% of trades.
It is accepted only if, on the NEWER half (2019-2026), it ALSO beats trading without a switch and is
positive, and if it is positive in at least 4 of the 6 periods.
Run in /opt/atlas-research (single process, low memory):
  PYTHONPATH=.:research nice -n 19 python research/regime_study.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from atlas_trader.data import load_csv

TZ = "America/New_York"


def daily_features(path: str) -> pd.DataFrame:
    m5 = load_csv(path)[["high", "low", "close"]]
    day = (m5.index.tz_convert(TZ) + pd.Timedelta(hours=6)).date          # gold day rolls at 18:00 NY
    d = m5.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    d = d[(d.high - d.low) > 0]
    rng = d.high - d.low
    avg20 = rng.rolling(20).mean()
    f = pd.DataFrame(index=pd.to_datetime(d.index))
    f["volr"] = (avg20 / avg20.rolling(250, min_periods=120).median()).to_numpy()
    f["trend"] = ((d.close - d.close.rolling(50).mean()).abs() / avg20).to_numpy()
    f["bull"] = (d.close > d.close.rolling(200).mean()).astype(float).to_numpy()
    return f.shift(1)            # value known at the START of each gold day = end of the previous one


SWITCHES = {"none": lambda x: np.ones(len(x), bool)}
for v in (1.0, 1.2, 1.5):
    SWITCHES[f"volr>={v}"] = (lambda v: lambda x: x.volr >= v)(v)
for t in (1.0, 2.0, 3.0):
    SWITCHES[f"trend>={t}"] = (lambda t: lambda x: x.trend >= t)(t)
SWITCHES["bull"] = lambda x: x.bull == 1
SWITCHES["volr>=1.2 & trend>=2"] = lambda x: (x.volr >= 1.2) & (x.trend >= 2.0)
SWITCHES["volr>=1.2 & bull"] = lambda x: (x.volr >= 1.2) & (x.bull == 1)


def main():
    f = daily_features("data/raw/GC_5m.csv")
    T = pd.read_csv("research/long_history_trades.csv")
    et = pd.to_datetime(T["entry_time"], utc=True).dt.tz_convert(TZ)
    T["gday"] = pd.to_datetime((et + pd.Timedelta(hours=6)).dt.date)
    T = T.join(f, on="gday")
    T["half"] = np.where(T.period.isin(["2010-2012", "2013-2015", "2016-2018"]), "older", "newer")
    rows = []
    for name, fn in SWITCHES.items():
        keep = np.asarray(fn(T), bool) & T.volr.notna().to_numpy()
        if name == "none":
            keep = np.ones(len(T), bool)
        k = T[keep]
        row = {"switch": name, "kept%": round(100 * keep.mean(), 1)}
        for h in ("older", "newer"):
            x = k[k.half == h]
            row[f"{h}_n"], row[f"{h}_R"] = len(x), round(float(x.r.sum()), 1)
            row[f"{h}_exp"] = round(float(x.r.mean()), 3) if len(x) else 0.0
            row[f"{h}_win%"] = round(100 * float((x.r > 0).mean()), 1) if len(x) else 0.0
        per = k.groupby("period").r.sum()
        row["pos_periods"] = int((per > 0).sum())
        row.update({f"{s}_R": round(float(k[k.strategy == s].r.sum()), 1) for s in sorted(T.strategy.unique())})
        rows.append(row)
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(t.to_string(index=False))
    base = t[t.switch == "none"].iloc[0]
    cand = t[(t.switch != "none") & (t["kept%"] >= 20)].sort_values("older_R", ascending=False)
    if cand.empty:
        print("no switch keeps >= 20% of trades")
        return
    pick = cand.iloc[0]
    ok = pick.newer_R > base.newer_R and pick.newer_R > 0 and pick.pos_periods >= 4
    print(f"\nPICK (older half only): {pick.switch} -> older {pick.older_R}R, newer {pick.newer_R}R "
          f"(no switch: older {base.older_R}R, newer {base.newer_R}R), positive periods {pick.pos_periods}/6, "
          f"kept {pick['kept%']}% -> {'ACCEPT' if ok else 'REJECT'}")
    k = T[np.asarray(SWITCHES[pick.switch](T), bool)]
    print("\nPICK by period and strategy (totR):")
    print(k.pivot_table(index="strategy", columns="period", values="r", aggfunc="sum").round(1).to_string())
    t.to_csv("research/regime_results.csv", index=False)


if __name__ == "__main__":
    main()
