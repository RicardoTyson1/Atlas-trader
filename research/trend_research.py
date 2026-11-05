"""Pre-registered test of the best-documented gold edge: slow time-series momentum.

Why this study: across the published evidence, the only gold timing rules that
survive a data-snooping correction are slow trend / time-series momentum rules
(Bartsch, Baur, Dichtl & Drobetz 2018, 4,000+ rules, 1990-2015; best = 24-month
momentum). Intraday rules (ORB, sessions, SMC) were already tested in
docs/RESULTS_*.md and showed no edge after costs.

Protocol (fixed BEFORE looking at results):
  * Data: daily gold closes (GatesFX XAUUSD.R if long enough, else GC=F futures).
  * Rules are those from the literature, NOT tuned here:
      MOM{L}  : long gold if close > close L months ago, else cash. L in 1,3,6,12,24.
      MA      : long if 50d MA > 200d MA, else cash.
      Each also as long/SHORT (short instead of cash).
    Signals use month-end (MOM) or daily (MA) closes, traded at the NEXT close.
  * Sizing: volatility-targeted to TARGET_VOL annualised using 60d realised vol
    (capped at MAX_LEV), the standard way trend followers size.
  * Costs: COST_BPS per unit of turnover + FIN_BPS_YR financing on the notional
    held (CFD swap; longs and shorts both pay).
  * Out-of-sample = everything AFTER the paper's sample: 2016-01-01 -> today.
    In-sample shown for reference only.
  * A rule PASSES only if, out-of-sample: Sharpe >= buy&hold Sharpe (vol-targeted
    buy&hold, same costs), AND max drawdown smaller than buy&hold's, AND t >= 1.5
    on the excess return vs cash.

Run:  python research/trend_research.py data/raw/XAUUSD.R_1D.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

OOS_START = "2016-01-01"
TARGET_VOL = 0.10
MAX_LEV = 3.0
VOL_LB = 60
COST_BPS = 3.0        # spread + slippage per 1.0 of turnover (gold CFD ~0.24/3,000 = 0.8bp, padded)
FIN_BPS_YR = 400.0    # 4%/yr swap on notional held; check GatesFX swap rates and update
DAYS = 252


def load_daily(path: str) -> pd.Series:
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("time", "date", "datetime", "timestamp"))
    ccol = next(c for c in df.columns if c.lower() in ("close", "adj close"))
    s = pd.Series(pd.to_numeric(df[ccol], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df[tcol], utc=True, errors="coerce")).dropna()
    s = s[~s.index.duplicated()].sort_index()
    s.index = s.index.tz_convert(None).normalize()
    return s.groupby(level=0).last()


def positions(close: pd.Series) -> dict[str, pd.Series]:
    """Raw direction (+1/0/-1) decided on each day's close."""
    out: dict[str, pd.Series] = {}
    month_end = close.groupby(close.index.to_period("M")).tail(1)
    for L in (1, 3, 6, 12, 24):
        mom = np.sign(month_end / month_end.shift(L) - 1)
        d = mom.reindex(close.index, method="ffill")
        out[f"MOM{L}_longflat"] = d.clip(lower=0)
        out[f"MOM{L}_longshort"] = d
    ma = np.sign(close.rolling(50).mean() - close.rolling(200).mean())
    out["MA50_200_longflat"] = ma.clip(lower=0)
    out["MA50_200_longshort"] = ma
    out["BUY_HOLD"] = pd.Series(1.0, index=close.index)
    return out


def simulate(close: pd.Series, direction: pd.Series) -> pd.Series:
    ret = close.pct_change()
    vol = ret.rolling(VOL_LB).std() * np.sqrt(DAYS)
    lev = (TARGET_VOL / vol).clip(upper=MAX_LEV)
    pos = (direction * lev).shift(1)          # decided at close t, held over day t+1
    turnover = pos.diff().abs()
    pnl = pos * ret - turnover * COST_BPS / 1e4 - pos.abs() * FIN_BPS_YR / 1e4 / DAYS
    return pnl.dropna()


def stats(p: pd.Series) -> dict:
    if len(p) < 30:
        return {}
    eq = (1 + p).cumprod()
    yrs = len(p) / DAYS
    sharpe = p.mean() / p.std() * np.sqrt(DAYS) if p.std() > 0 else 0.0
    return {
        "CAGR%": 100 * (eq.iloc[-1] ** (1 / yrs) - 1),
        "vol%": 100 * p.std() * np.sqrt(DAYS),
        "Sharpe": sharpe,
        "t": sharpe * np.sqrt(yrs),
        "maxDD%": 100 * (eq / eq.cummax() - 1).min(),
        "worst_month%": 100 * ((1 + p).groupby(p.index.to_period("M")).prod() - 1).min(),
        "in_mkt%": 100 * (p != 0).mean(),
    }


def main(path: str) -> None:
    close = load_daily(path)
    print(f"Data {close.index[0].date()} -> {close.index[-1].date()} ({len(close)} days)")
    if close.index[0] > pd.Timestamp(OOS_START) - pd.DateOffset(years=5):
        print("WARNING: under 5 years before the out-of-sample start; in-sample stats are thin.")
    res = {k: simulate(close, v) for k, v in positions(close).items()}
    rows = []
    for k, p in res.items():
        for part, q in (("IS", p[p.index < OOS_START]), ("OOS", p[p.index >= OOS_START])):
            s = stats(q)
            if s:
                rows.append({"rule": k, "part": part, **s})
    t = pd.DataFrame(rows).round(2)
    pd.set_option("display.width", 200)
    for part in ("IS", "OOS"):
        print(f"\n{part} ({'reference only' if part == 'IS' else OOS_START + ' -> today, decides the verdict'}):")
        print(t[t.part == part].drop(columns="part").to_string(index=False))

    oos = t[t.part == "OOS"].set_index("rule")
    bh = oos.loc["BUY_HOLD"]
    print(f"\nVERDICT (bar: Sharpe >= {bh.Sharpe}, maxDD better than {bh['maxDD%']}%, t >= 1.5)")
    for rule, r in oos.drop(index="BUY_HOLD").iterrows():
        ok = r.Sharpe >= bh.Sharpe and r["maxDD%"] > bh["maxDD%"] and r.t >= 1.5
        print(f"  {'PASS' if ok else 'FAIL'}  {rule:<22} Sharpe {r.Sharpe:+.2f}  CAGR {r['CAGR%']:+.1f}%  maxDD {r['maxDD%']:.1f}%  t {r.t:+.2f}")
    print(f"\nCosts: {COST_BPS}bp/turnover, financing {FIN_BPS_YR/100:.1f}%/yr on notional, vol target {TARGET_VOL:.0%}.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_1D.csv")
