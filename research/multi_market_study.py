"""Multi-market edge hunt on CME futures (GC, SI, CL, ES, YM, NQ), 2010-2026. PRE-REGISTERED.

Everything is fixed BEFORE running (docs/PREREG_MULTI_MARKET_2026-11-19.md). One run, no re-tuning.
Families:
  D  donchian_1h   55/20 Donchian trend, 2 ATR stop, both sides, per market + 6-market portfolio
  S  smt_5m        SMT sweep-reversal on pairs (ES/NQ, ES/YM, NQ/YM, GC/SI), NY and Asia killzones
  L  leadlag_5m    leader 5m shock (|z|>3) while follower lags (|z|<1) -> follower, 30 min hold
  C  calendar      overnight hold (16:00 -> 09:30 NY) per market; turn-of-month per market
Costs (round trip, price units): GC 1.26 (bot's real XAUUSD cost); others 2 ticks/side + $5.
Pass: n>=200, net R>0 in BOTH halves (split 2018-06-01) and in >=4 of 6 equal periods.
STRONG = pass and t>=2.5. Holdout view 2023-01-01+ reported, not used to pick.

Usage: nice -n 19 python research/multi_market_study.py data/multi  > research/multi_market_report.txt
"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
COST = {"GC": 1.26, "SI": 4 * 0.005 + 5 / 5000, "CL": 4 * 0.01 + 5 / 1000,
        "ES": 4 * 0.25 + 5 / 50, "YM": 4 * 1.0 + 5 / 5, "NQ": 4 * 0.25 + 5 / 20}
TICK = {"GC": 0.1, "SI": 0.005, "CL": 0.01, "ES": 0.25, "YM": 1.0, "NQ": 0.25}
SPLIT = pd.Timestamp("2018-06-01", tz=NY)
HOLDOUT = pd.Timestamp("2023-01-01", tz=NY)
DATA: Path = Path("data/multi")


def load(root: str, tf: str) -> pd.DataFrame:
    d = pd.read_csv(DATA / f"{root}_{tf}.csv", parse_dates=["time"], index_col="time")
    d.index = pd.to_datetime(d.index, utc=True).tz_convert(NY)
    return d[["open", "high", "low", "close", "volume"]].astype("float64")


def atr(d: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = d["close"].shift()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def daily(d: pd.DataFrame) -> pd.DataFrame:
    # CME trading day: starts 18:00 NY -> shift by 6h so one session = one calendar date
    k = (d.index + pd.Timedelta(hours=6)).date
    g = d.groupby(k)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last()})
    out.index = pd.to_datetime(out.index)
    return out


# ---------------------------------------------------------------- trade simulation
def simulate(d: pd.DataFrame, i_entry: int, side: int, stop: float, target: float | None,
             i_last: int, exit_mask: np.ndarray | None = None) -> tuple[int, float]:
    """Enter at OPEN of bar i_entry. Stop checked before target in the same bar (conservative).
    Exit at close of bar i_last at the latest, or at close of a bar where exit_mask is set."""
    o, h, l, c = (d[k].values for k in ("open", "high", "low", "close"))
    entry = o[i_entry]
    i_last = min(i_last, len(o) - 1)
    for i in range(i_entry, i_last + 1):
        if side > 0:
            if l[i] <= stop:
                return i, min(stop, o[i]) if i > i_entry else stop
            if target is not None and h[i] >= target:
                return i, max(target, o[i]) if i > i_entry else target
        else:
            if h[i] >= stop:
                return i, max(stop, o[i]) if i > i_entry else stop
            if target is not None and l[i] <= target:
                return i, min(target, o[i]) if i > i_entry else target
        if exit_mask is not None and exit_mask[i]:
            return i, c[i]
    return i_last, c[i_last]


def trade_row(d, root, i_entry, side, stop, target, i_last, exit_mask=None):
    entry = d["open"].values[i_entry]
    risk = abs(entry - stop)
    if risk <= 0 or not np.isfinite(risk):
        return None
    i_x, px = simulate(d, i_entry, side, stop, target, i_last, exit_mask)
    gross = side * (px - entry) / risk
    net = gross - COST[root] / risk
    return {"time": d.index[i_entry], "root": root, "side": side, "risk": risk,
            "gross_r": gross, "r": net, "bars": i_x - i_entry}, i_x


# ---------------------------------------------------------------- D: donchian 1h
def donchian(root: str) -> pd.DataFrame:
    d = load(root, "1h")
    a = atr(d, 14).values
    hi55 = d["high"].shift(1).rolling(55).max().values
    lo55 = d["low"].shift(1).rolling(55).min().values
    hi20 = d["high"].shift(1).rolling(20).max().values
    lo20 = d["low"].shift(1).rolling(20).min().values
    c = d["close"].values
    rows, i, n = [], 60, len(d)
    while i < n - 1:
        side = 1 if c[i] > hi55[i] else (-1 if c[i] < lo55[i] else 0)
        if side == 0 or not np.isfinite(a[i]):
            i += 1
            continue
        stop = c[i] - side * 2 * a[i]
        ex = (c < lo20) if side > 0 else (c > hi20)
        res = trade_row(d, root, i + 1, side, stop, None, n - 1, ex)
        if res is None:
            i += 1
            continue
        row, i_x = res
        rows.append(row)
        i = i_x + 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- S: SMT 5m
WINDOWS = {"NY": ((0, 0), (8, 0), (8, 0), (11, 0)),      # ref range 00:00-08:00, trade 08:00-11:00
           "ASIA": ((8, 0), (17, 0), (19, 0), (22, 0))}  # ref range 08:00-17:00, trade 19:00-22:00


def smt(a_root: str, b_root: str, win: str) -> pd.DataFrame:
    A, B = load(a_root, "5m"), load(b_root, "5m")
    idx = A.index.intersection(B.index)
    A, B = A.loc[idx], B.loc[idx]
    (rh0, rm0), (rh1, rm1), (th0, tm0), (th1, tm1) = WINDOWS[win]
    mins = idx.hour * 60 + idx.minute
    ref = (mins >= rh0 * 60 + rm0) & (mins < rh1 * 60 + rm1)
    trd = (mins >= th0 * 60 + tm0) & (mins < th1 * 60 + tm1)
    day = idx.normalize()
    rows = []
    frames = {a_root: A, b_root: B}
    allpos = np.arange(len(idx))
    rdays = pd.Series(allpos[ref]).groupby(day[ref]).agg(list)
    tdays = pd.Series(allpos[trd]).groupby(day[trd]).agg(list)
    for dday, tl in tdays.items():
        rl = rdays.get(dday)
        if rl is None or len(rl) < 12 or len(tl) < 12:
            continue
        rp = np.array(rl)
        lv = {r: (f["high"].values[rp].max(), f["low"].values[rp].min()) for r, f in frames.items()}
        tpos = np.array(tl)
        done = False
        for k, p in enumerate(tpos):
            if done:
                break
            for sweeper, other in ((a_root, b_root), (b_root, a_root)):
                S, O = frames[sweeper], frames[other]
                s_hi = S["high"].values[tpos[0]:p + 1].max()
                s_lo = S["low"].values[tpos[0]:p + 1].min()
                o_hi = O["high"].values[tpos[0]:p + 1].max()
                o_lo = O["low"].values[tpos[0]:p + 1].min()
                c = S["close"].values[p]
                side = 0
                if s_hi > lv[sweeper][0] and o_hi <= lv[other][0] and c < lv[sweeper][0] \
                        and S["high"].values[max(tpos[0], p - 6):p + 1].max() > lv[sweeper][0]:
                    side, stop = -1, s_hi + TICK[sweeper]
                elif s_lo < lv[sweeper][1] and o_lo >= lv[other][1] and c > lv[sweeper][1] \
                        and S["low"].values[max(tpos[0], p - 6):p + 1].min() < lv[sweeper][1]:
                    side, stop = 1, s_lo - TICK[sweeper]
                if side == 0 or p + 1 > tpos[-1]:
                    continue
                entry = S["open"].values[p + 1]
                risk = abs(entry - stop)
                if risk < 3 * COST[sweeper] or (side > 0 and stop >= entry) or (side < 0 and stop <= entry):
                    continue
                res = trade_row(S, sweeper, p + 1, side, stop, entry + side * 2 * risk, tpos[-1])
                if res:
                    rows.append(res[0])
                done = True
                break
    del A, B
    gc.collect()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- L: lead-lag 5m
def leadlag(lead: str, fol: str) -> pd.DataFrame:
    L, F = load(lead, "5m"), load(fol, "5m")
    idx = L.index.intersection(F.index)
    L, F = L.loc[idx], F.loc[idx]
    rl, rf = np.log(L["close"]).diff(), np.log(F["close"]).diff()
    zl = (rl / rl.rolling(1000).std().shift()).values
    zf = (rf / rf.rolling(1000).std().shift()).values
    # only use bars contiguous in time (no gaps) and outside 16:00-18:00 NY
    contig = (idx.to_series().diff() == pd.Timedelta(minutes=5)).values
    hr = idx.hour
    ok = contig & ~((hr >= 16) & (hr < 18))
    a = atr(F, 14).values
    rows, i, n = [], 1001, len(idx)
    while i < n - 8:
        if ok[i] and abs(zl[i]) > 3 and abs(zf[i]) < 1 and np.isfinite(a[i]):
            side = int(np.sign(zl[i]))
            entry = F["open"].values[i + 1]
            stop = entry - side * max(a[i], 3 * COST[fol])
            res = trade_row(F, fol, i + 1, side, stop, None, i + 6)
            if res:
                rows.append(res[0])
                i = res[1] + 1
                continue
        i += 1
    del L, F
    gc.collect()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- C: calendar
def overnight(root: str) -> pd.DataFrame:
    d = load(root, "5m")
    da = atr(daily(d), 14)
    rows = []
    t = d.index
    is_close = (t.hour == 15) & (t.minute == 55)
    is_open = (t.hour == 9) & (t.minute == 30)
    close_pos = np.flatnonzero(is_close)
    open_pos = np.flatnonzero(is_open)
    j = 0
    for p in close_pos:
        while j < len(open_pos) and open_pos[j] <= p:
            j += 1
        if j >= len(open_pos):
            break
        q = open_pos[j]
        if (t[q] - t[p]) > pd.Timedelta(days=4):
            continue
        dd = pd.Timestamp(t[p].date())
        prev = da.index[da.index < dd]
        if len(prev) == 0 or not np.isfinite(da.loc[prev[-1]]):
            continue
        # enter at open of the bar after 15:55 (=16:00), exit at OPEN of 09:30 bar (close of bar q-1)
        res = trade_row(d, root, p + 1, 1, d["open"].values[p + 1] - da.loc[prev[-1]], None, q - 1)
        if res:
            rows.append(res[0])
    return pd.DataFrame(rows)


def turn_of_month(root: str) -> pd.DataFrame:
    d = load(root, "5m")
    dl = daily(d)
    dl = dl[dl.index.dayofweek < 5]
    da = atr(dl, 14)
    ym = dl.index.to_period("M")
    rows = []
    months = ym.unique()
    for m0, m1 in zip(months[:-1], months[1:]):
        a_days = dl.index[ym == m0]
        b_days = dl.index[ym == m1]
        if len(a_days) < 3 or len(b_days) < 4:
            continue
        d_in, d_out = a_days[-2], b_days[2]          # buy close of 2nd-last day, sell close of 3rd day
        if not np.isfinite(da.loc[d_in]):
            continue
        # session for date X ends 17:00 NY on X; enter at first bar >= 16:00 of d_in, exit 15:55 bar of d_out
        t_in = pd.Timestamp(d_in.date(), tz=NY) + pd.Timedelta(hours=16)
        t_out = pd.Timestamp(d_out.date(), tz=NY) + pd.Timedelta(hours=15, minutes=55)
        i0 = d.index.searchsorted(t_in)
        i1 = d.index.searchsorted(t_out, side="right") - 1
        if i0 >= len(d) - 1 or i1 <= i0:
            continue
        res = trade_row(d, root, i0, 1, d["open"].values[i0] - 2 * da.loc[d_in], None, i1)
        if res:
            rows.append(res[0])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- scoring
def score(name: str, t: pd.DataFrame) -> dict:
    if t is None or t.empty:
        return {"test": name, "n": 0, "verdict": "FAIL (no trades)"}
    t = t.sort_values("time")
    r = t["r"].values
    n = len(r)
    tt = t["time"]
    edges = pd.date_range(tt.iloc[0], tt.iloc[-1], periods=7)
    per = [t.loc[(tt >= edges[k]) & (tt <= edges[k + 1]) if k == 5 else (tt >= edges[k]) & (tt < edges[k + 1]), "r"].sum()
           for k in range(6)]
    a, b = t.loc[tt < SPLIT, "r"], t.loc[tt >= SPLIT, "r"]
    ho = t.loc[tt >= HOLDOUT, "r"]
    tstat = r.mean() / (r.std(ddof=1) / np.sqrt(n)) if n > 2 and r.std() > 0 else 0.0
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if n else 0.0
    passed = n >= 200 and a.sum() > 0 and b.sum() > 0 and sum(p > 0 for p in per) >= 4
    return {"test": name, "n": n, "win%": round(100 * (r > 0).mean(), 1), "exp_R": round(r.mean(), 3),
            "gross_R/tr": round(t["gross_r"].mean(), 3), "total_R": round(r.sum(), 1),
            "half_A": round(a.sum(), 1), "half_B": round(b.sum(), 1), "pos_periods": sum(p > 0 for p in per),
            "t": round(tstat, 2), "maxDD_R": round(dd, 1), "2023+_R": round(ho.sum(), 1),
            "verdict": ("STRONG PASS" if passed and tstat >= 2.5 else "PASS" if passed else "FAIL")}


def main() -> None:
    global DATA
    DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    roots = ["GC", "SI", "CL", "ES", "YM", "NQ"]
    tests: list[tuple[str, callable]] = []
    tests += [(f"D donchian_1h {r}", lambda r=r: donchian(r)) for r in roots]
    tests += [(f"S smt {a}/{b} {w}", lambda a=a, b=b, w=w: smt(a, b, w))
              for a, b in (("ES", "NQ"), ("ES", "YM"), ("NQ", "YM"), ("GC", "SI")) for w in ("NY", "ASIA")]
    tests += [(f"L leadlag {a}->{b}", lambda a=a, b=b: leadlag(a, b))
              for a, b in (("ES", "GC"), ("ES", "CL"), ("ES", "SI"), ("GC", "SI"), ("SI", "GC"),
                           ("ES", "NQ"), ("NQ", "ES"), ("ES", "YM"))]
    tests += [(f"C overnight {r}", lambda r=r: overnight(r)) for r in roots]
    tests += [(f"C turn_of_month {r}", lambda r=r: turn_of_month(r)) for r in roots]
    results, port = [], []
    out_dir = DATA / "trades"
    out_dir.mkdir(exist_ok=True)
    for name, fn in tests:
        if only and not name.startswith(only):
            continue
        t = fn()
        if not t.empty:
            t.to_csv(out_dir / (name.replace(" ", "_").replace("/", "-").replace(">", "") + ".csv"), index=False)
        s = score(name, t)
        print(s, flush=True)
        results.append(s)
        if name.startswith("D ") and not t.empty:
            port.append(t)
        gc.collect()
    if port:
        s = score("D donchian_1h PORTFOLIO(6)", pd.concat(port))
        print(s, flush=True)
        results.append(s)
    res = pd.DataFrame(results)
    print("\n=== SUMMARY ===")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(res.to_string(index=False))
    print(f"\n{len(res)} tests. With ~{len(res)} tests, 1-2 marginal passes are expected by luck alone.")


if __name__ == "__main__":
    main()
