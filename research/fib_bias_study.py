"""Ricardo's daily-Fib bias + RSI-extreme entries, 16 years of COMEX gold 1m. Pre-registered 2026-11-18.

His method (thread 2026-11-18): direction from the Daily (Fibonacci) and 4H; a SOLID candle out of DEMAND
heading up toward the golden ratio = longs only, out of SUPPLY heading down = shorts only; wait for a
confirmation retest; enter on the 1m; extreme RSI (buy 16.5-20, sell 80-85, length 14, any timeframe);
5m / 15m guide continuation or exit. Rules as agreed with him before running (no tuning afterwards):

  1 Daily swings: high/low beyond the 2 days on each side (confirmed 2 days later). NY trading day
    starts 18:00. The latest leg is between the latest confirmed swing high H and swing low L.
  2 Down leg (H then L): DEMAND = range of the swing-low day; golden ratio G = L + 0.618 (H - L).
    Up leg (L then H): SUPPLY = range of the swing-high day; G = H - 0.618 (H - L).
  3 Bias: a completed 4H candle with body >= 60% of its range, that touches the zone and CLOSES out of
    it toward G (long: close > demand top and < G; short: close < supply bottom and > G).
    Bias ends when price reaches G, a 1m close is beyond the far side of the zone, or the daily leg changes.
  4 Retest: price pulls back at least halfway from the solid candle's close toward the zone.
  5 Entry: RSI(14) in the band on the chosen timeframe (last COMPLETED bar) while pulled back, then the
    first 1m candle that closes in the trade direction within 10 minutes, and outside the zone (the
    retest held); enter at that close.
  6 Stop: beyond the 1m pullback extreme (last 15 bars) + 0.2. 7 Target: G.
  8 Early exit: a 15m close back inside the zone. After +1R the stop trails beyond 5m swing points.
  One position at a time per variant. Stop before target when both are hit in the same minute.
Costs: GatesFX XAUUSD.R costs (spread 0.69 + slippage 0.25 each side, $7/lot round trip), in R.
Variants: RSI timeframe 1m / 5m / 15m / any of them x entry hours 08-11 NY / all hours x RSI band
Ricardo (16.5-20 / 80-85) / side check (20-30 / 70-80, NOT his rule, reported for context only).
PASS (same bar as research/library_16y_study.py): >= 200 trades, total R > 0, profit in both halves
(2010-2018 / 2019-2026) and in >= 4 of 6 periods. STRONG: also t >= 2.5.

Data: data/raw/GC_1m_raw.csv (Databento, per-contract), back-adjusted here at each roll.
Memory: one year at a time (+ a year of warm-up each side), float32, for the 1 GB server.
Run in /opt/atlas-research:
  PYTHONPATH=.:research nice -n 19 python research/fib_bias_study.py            # -> research/fib_bias_trades.csv
  (first run splits the raw file into data/raw/fib_cache/<year>.npz; then works one year at a time)
  PYTHONPATH=.:research nice -n 19 python research/fib_bias_study.py --report
"""
from __future__ import annotations

import sys
import time as _time

import numpy as np
import pandas as pd

RAW = "data/raw/GC_1m_raw.csv"
OUT = "research/fib_bias_trades.csv"
RES = "research/fib_bias_results.csv"
NY = "America/New_York"
MIN = 60_000_000_000                          # one minute in ns

FIB, FIB_HI = 0.618, 0.65
BODY = 0.60
RSI_N = 14
ARM_WINDOW = 10                               # minutes to get a confirming 1m candle after the RSI hit
STOP_LOOKBACK, STOP_BUF = 15, 0.2
HALF_COST = 0.69 / 2 + 0.25                   # spread/2 + slippage, each side (price units)
COMM = 7.0 / 100.0                            # $7 per lot round trip, 100 oz per lot -> price units
BANDS = {"rick": ((16.5, 20.0), (80.0, 85.0)), "side_20_30": ((20.0, 30.0), (70.0, 80.0))}
HOURS = {"am": (8 * 60, 11 * 60), "all": (0, 24 * 60)}
RSI_TFS = ("1m", "5m", "15m", "any")
PERIODS = [("2010-2012", 2012), ("2013-2015", 2015), ("2016-2018", 2018),
           ("2019-2021", 2021), ("2022-2024", 2024), ("2025-2026", 2026)]


# ----------------------------------------------------------------------------------------- data
def _trim():
    """Give freed heap memory back to the OS (glibc), so the 1 GB server is not starved."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - not glibc: nothing to do
        pass


CACHE = "data/raw/fib_cache"


def make_cache(path: str = RAW, out: str = CACHE):
    """One streaming pass: raw 1m -> one .npz per year (raw prices) + the list of roll gaps.
    Keeps memory small: the full 16 years are never in memory at once."""
    import os
    os.makedirs(out, exist_ok=True)
    f32 = {"open": "float32", "high": "float32", "low": "float32", "close": "float32", "instrument_id": "int32"}
    buf = {}
    roll_t, roll_gap = [], []
    last_t, last_c, last_iid = None, None, None

    def flush(year):
        d = buf.pop(year)
        arr = {k: np.concatenate(v) for k, v in d.items()}
        np.savez(f"{out}/{year}.npz", **arr)

    for ch in pd.read_csv(path, chunksize=200_000, usecols=["time", *f32], dtype=f32):
        t = pd.to_datetime(ch["time"], utc=True, format="ISO8601").dt.as_unit("ns").astype("int64").to_numpy()
        o, h, l, c = (ch[k].to_numpy() for k in ("open", "high", "low", "close"))
        iid = ch["instrument_id"].to_numpy()
        del ch
        if not np.all(t[1:] > t[:-1]) or (last_t is not None and t[0] <= last_t):
            keep = np.ones(len(t), bool)
            keep[1:] = t[1:] > t[:-1]
            if last_t is not None:
                keep &= t > last_t
            if not np.all(np.maximum.accumulate(t[keep])[:-1] < t[keep][1:]):
                raise SystemExit("raw file is not time-ordered: sort it first")
            t, o, h, l, c, iid = (x[keep] for x in (t, o, h, l, c, iid))
        if not len(t):
            continue
        pi = np.concatenate(([last_iid if last_iid is not None else iid[0]], iid[:-1]))
        pc = np.concatenate(([last_c if last_c is not None else c[0]], c[:-1]))
        r = iid != pi
        roll_t += list(t[r])
        roll_gap += list((o[r].astype(np.float64) - pc[r]))
        last_t, last_c, last_iid = t[-1], c[-1], iid[-1]
        years = pd.DatetimeIndex(t.astype("datetime64[ns]")).year.to_numpy()
        for y in np.unique(years):
            m = years == y
            d = buf.setdefault(int(y), {k: [] for k in ("t", "o", "h", "l", "c")})
            for k, v in (("t", t), ("o", o), ("h", h), ("l", l), ("c", c)):
                d[k].append(v[m])
        for y in [y for y in buf if y < years[-1]]:
            flush(y)
    for y in list(buf):
        flush(y)
    np.savez(f"{out}/rolls.npz", t=np.array(roll_t, np.int64), gap=np.array(roll_gap, np.float64))
    print(f"cache: {len(roll_t)} rolls -> {out}", flush=True)


def cache_years(out: str = CACHE):
    import glob
    import os
    return sorted(int(os.path.basename(p)[:4]) for p in glob.glob(f"{out}/[12][0-9][0-9][0-9].npz"))


def load_1m(years, out: str = CACHE):
    """Load cached years, back-adjusted for every roll AFTER each bar (latest prices stay real)."""
    R = np.load(f"{out}/rolls.npz")
    rt, rg = R["t"], R["gap"]
    after = np.concatenate((np.cumsum(rg[::-1])[::-1], [0.0]))      # after[i] = sum of gaps of rolls i..end
    parts = [np.load(f"{out}/{y}.npz") for y in years]
    t = np.concatenate([p["t"] for p in parts])
    shift = after[np.searchsorted(rt, t, side="right")].astype(np.float32)
    o, h, l, c = (np.concatenate([p[k] for p in parts]) + shift for k in ("o", "h", "l", "c"))
    del parts, shift
    _trim()
    return t, o, h, l, c


def local_minutes(t):
    """NY local wall-clock ns (naive) for each UTC ns."""
    out = np.empty_like(t)
    step = 1_000_000
    for i in range(0, len(t), step):
        idx = pd.DatetimeIndex(t[i:i + step].astype("datetime64[ns]")).tz_localize("UTC").tz_convert(NY)
        out[i:i + step] = idx.tz_localize(None).as_unit("ns").asi8
    return out


def buckets(key):
    """Group consecutive equal keys (key sorted ascending). Returns bucket id per row, starts, ends (excl)."""
    new = np.ones(len(key), bool)
    new[1:] = key[1:] != key[:-1]
    starts = np.flatnonzero(new)
    ends = np.append(starts[1:], len(key))
    bid = np.cumsum(new, dtype=np.int32) - 1
    return bid, starts, ends


def ohlc(o, h, l, c, starts, ends):
    return (o[starts], np.maximum.reduceat(h, starts), np.minimum.reduceat(l, starts), c[ends - 1])


def _wilder(x, n, step=400_000):
    """Wilder smoothing (EMA, alpha 1/n, adjust=False) in chunks, carrying the state (small memory)."""
    out = np.empty(len(x), np.float32)
    prev = None
    for i in range(0, len(x), step):
        seg = x[i:i + step].astype(np.float64)
        if prev is not None:
            seg = np.concatenate(([prev], seg))
        y = pd.Series(seg).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
        if prev is not None:
            y = y[1:]
        out[i:i + len(y)] = y
        prev = y[-1]
    return out


def rsi(close, n=RSI_N):
    d = np.diff(close, prepend=close[:1]).astype(np.float32)
    up = _wilder(np.clip(d, 0, None), n)
    dn = _wilder(np.clip(-d, 0, None), n)
    del d
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (100 - 100 / (1 + up / dn)).astype(np.float32)
    r[dn == 0] = 100.0
    r[:n] = np.nan
    _trim()
    return r


def completed_value(per_bucket, bid, ends):
    """Per 1m bar: the value of the last COMPLETED bucket (this one if the bar closes it, else previous)."""
    last = np.zeros(len(bid), bool)
    last[ends - 1] = True
    prev = np.where(bid > 0, per_bucket[np.maximum(bid - 1, 0)], np.nan)
    return np.where(last, per_bucket[bid], prev).astype(np.float32)


def swing_flags(hi, lo, k=2):
    n = len(hi)
    sh = np.zeros(n, bool)
    sl = np.zeros(n, bool)
    for i in range(k, n - k):
        if hi[i] > hi[i - k:i].max() and hi[i] >= hi[i + 1:i + k + 1].max():
            sh[i] = True
        if lo[i] < lo[i - k:i].min() and lo[i] <= lo[i + 1:i + k + 1].min():
            sl[i] = True
    return sh, sl


def latest_confirmed(flags, values, k=2):
    """Per bar j: index of the latest flagged bar i with i + k < j (confirmed before bar j starts)."""
    n = len(flags)
    out = np.full(n, -1)
    last = -1
    for j in range(n):
        i = j - k - 1
        if i >= 0 and flags[i]:
            last = i
        out[j] = last
    return out


# ----------------------------------------------------------------------------------------- setup
def build(t, o, h, l, c):
    t0 = _time.time()
    gshift = local_minutes(t) + 6 * 60 * MIN                        # trading day starts 18:00 NY
    mod = (((gshift // MIN) - 360) % 1440).astype(np.int16)         # NY minute of day
    dbid, ds, de = buckets(gshift // (1440 * MIN))
    dbid = dbid.astype(np.int32)
    DO, DH, DL, DC = ohlc(o, h, l, c, ds, de)
    sh, sl = swing_flags(DH, DL)                                    # daily swings, latest leg per day
    lsh, lsl = latest_confirmed(sh, DH), latest_confirmed(sl, DL)
    _, h4s, h4e = buckets(gshift // (240 * MIN))                    # 4H aligned to 18:00 NY
    del gshift, _
    H4O, H4H, H4L, H4C = ohlc(o, h, l, c, h4s, h4e)
    r1 = rsi(c)
    m15bid, m15s, m15e = buckets(t // (15 * MIN))
    r15 = completed_value(rsi(c[m15e - 1]), m15bid, m15e)
    m15_last = np.zeros(len(t), bool)
    m15_last[m15e - 1] = True
    del m15bid, m15s, m15e
    m5bid, m5s, m5e = buckets(t // (5 * MIN))
    _, M5H, M5L, M5C = ohlc(o, h, l, c, m5s, m5e)
    r5 = completed_value(rsi(M5C), m5bid, m5e)
    # 5m swings (2 bars each side): per 1m bar, the latest swing CONFIRMED before its 5m bar started
    s5h, s5l = swing_flags(M5H, M5L)
    c5h, c5l = latest_confirmed(s5h, M5H), latest_confirmed(s5l, M5L)
    v_lo = np.where(c5l >= 0, M5L[np.maximum(c5l, 0)], np.nan).astype(np.float32)
    v_hi = np.where(c5h >= 0, M5H[np.maximum(c5h, 0)], np.nan).astype(np.float32)
    sw5_low, sw5_high = v_lo[m5bid], v_hi[m5bid]
    del m5bid, m5s, m5e, _
    print(f"built in {_time.time() - t0:.0f}s: {len(DO)} days, {len(H4O)} 4H, {len(M5C)} 5m bars", flush=True)
    return dict(t=t, o=o, h=h, l=l, c=c, mod=mod, dbid=dbid, DH=DH, DL=DL, lsh=lsh, lsl=lsl,
                h4s=h4s, h4e=h4e, H4O=H4O, H4H=H4H, H4L=H4L, H4C=H4C,
                r1=r1, r5=r5, r15=r15, sw5_low=sw5_low, sw5_high=sw5_high, m15_last=m15_last)


def biases(D):
    """Bias windows from the daily leg + solid 4H candles. Returns list of dicts."""
    DH, DL, lsh, lsl = D["DH"], D["DL"], D["lsh"], D["lsl"]
    h4s, h4e, dbid = D["h4s"], D["h4e"], D["dbid"]
    h, l, c = D["h"], D["l"], D["c"]
    out = []
    busy_until = -1
    for b in range(len(h4s)):
        s, e = h4s[b], h4e[b]
        if e <= busy_until:
            continue
        day = dbid[e - 1]
        ih, il = lsh[day], lsl[day]
        if ih < 0 or il < 0:
            continue
        H, L = DH[ih], DL[il]
        if H <= L:
            continue
        O4, H4, L4, C4 = D["H4O"][b], D["H4H"][b], D["H4L"][b], D["H4C"][b]
        rng = H4 - L4
        if rng <= 0 or abs(C4 - O4) < BODY * rng:
            continue
        if ih < il:                                   # down leg -> demand at the low, longs toward G
            side, z_lo, z_hi = 1, DL[il], DH[il]
            G = L + FIB * (H - L)
            ok = C4 > O4 and L4 <= z_hi and z_hi < C4 < G
        else:                                         # up leg -> supply at the high, shorts toward G
            side, z_lo, z_hi = -1, DL[ih], DH[ih]
            G = H - FIB * (H - L)
            ok = C4 < O4 and H4 >= z_lo and G < C4 < z_lo
        if not ok:
            continue
        # bias window: from the next 1m bar until G is reached, a close beyond the far side of the zone,
        # or the daily leg changes
        j = e
        n = len(c)
        leg = (ih, il)
        while j < n:
            dj = dbid[j]
            if (lsh[dj], lsl[dj]) != leg:
                break
            if side == 1 and (h[j] >= G or c[j] < z_lo):
                break
            if side == -1 and (l[j] <= G or c[j] > z_hi):
                break
            j += 1
        if j > e:
            edge = z_hi if side == 1 else z_lo
            out.append(dict(side=side, start=e, end=j, G=G, z_lo=z_lo, z_hi=z_hi,
                            pull=(C4 + edge) / 2.0, solid_close=C4, h4_time=D["t"][s]))
        busy_until = j
    return out


# ----------------------------------------------------------------------------------------- trades
def simulate(D, B, band, hours, rsi_tf, after_t=None, core=None):
    """core = (t_from, t_to): only entries inside it are taken (the rest of the data is warm-up)."""
    t, o, h, l, c, mod = D["t"], D["o"], D["h"], D["l"], D["c"], D["mod"]
    (lo_b, hi_b), (slo_b, shi_b) = BANDS[band]
    h0, h1 = HOURS[hours]
    if rsi_tf == "any":
        R = (D["r1"], D["r5"], D["r15"])
    else:
        R = (D[{"1m": "r1", "5m": "r5", "15m": "r15"}[rsi_tf]],)
    n = len(c)
    trades = []
    free_at = 0 if after_t is None else int(np.searchsorted(t, after_t, side="right"))
    lo_i, hi_i = (0, n) if core is None else (int(np.searchsorted(t, core[0])), int(np.searchsorted(t, core[1])))
    free_at = max(free_at, lo_i)
    for bz in B:
        side, s, e, G = bz["side"], bz["start"], bz["end"], bz["G"]
        if max(s, free_at) >= e:
            continue
        seg = slice(s, e)
        # pulled-back latch (from the start of the bias window)
        if side == 1:
            pulled = np.minimum.accumulate(l[seg]) <= bz["pull"]
        else:
            pulled = np.maximum.accumulate(h[seg]) >= bz["pull"]
        hit = np.zeros(e - s, bool)
        for r in R:
            rv = r[seg]
            hit |= (rv >= lo_b) & (rv <= hi_b) if side == 1 else (rv >= slo_b) & (rv <= shi_b)
        arm = np.flatnonzero(hit & pulled) + s
        k = 0
        while k < len(arm):
            a = arm[k]
            if a < free_at:
                k += 1
                continue
            # confirming 1m candle within ARM_WINDOW minutes (and inside the bias window)
            ent = -1
            for j in range(a + 1, min(a + 1 + ARM_WINDOW, e, n)):
                if (c[j] - o[j]) * side > 0:
                    ent = j
                    break
            if ent < 0 or ent >= hi_i:
                k += 1
                continue
            if not (h0 <= mod[ent] < h1):
                k += 1
                continue
            edge = bz["z_hi"] if side == 1 else bz["z_lo"]
            if (c[ent] - edge) * side <= 0:          # the retest must HOLD: entry candle closes outside the zone
                k += 1
                continue
            lb = max(0, ent - STOP_LOOKBACK + 1)
            stop = l[lb:ent + 1].min() - STOP_BUF if side == 1 else h[lb:ent + 1].max() + STOP_BUF
            fill = c[ent] + side * HALF_COST
            risk = (fill - stop) * side
            if risk <= 0 or (G - fill) * side <= 0:
                k += 1
                continue
            tr = run_trade(D, bz, side, ent, fill, stop, risk, G)
            trades.append(tr)
            free_at = tr["exit_i"] + 1
            k += 1
    return trades


def run_trade(D, bz, side, ent, fill, stop, risk, G):
    t, o, h, l, c = D["t"], D["o"], D["h"], D["l"], D["c"]
    m15_last, sw_lo, sw_hi = D["m15_last"], D["sw5_low"], D["sw5_high"]
    n = len(c)
    z_in = bz["z_hi"] if side == 1 else bz["z_lo"]          # 15m close back past this = back inside
    trail_on = False
    best = fill
    j = ent + 1
    exit_px, why = None, None
    while j < n:
        # 1) stop (gap through -> open)
        if side == 1 and l[j] <= stop:
            exit_px, why = (min(o[j], stop), "stop")
            break
        if side == -1 and h[j] >= stop:
            exit_px, why = (max(o[j], stop), "stop")
            break
        # 2) target
        if (side == 1 and h[j] >= G) or (side == -1 and l[j] <= G):
            exit_px, why = (max(o[j], G) if side == 1 else min(o[j], G), "target")
            break
        # 3) 15m close back inside the zone
        if m15_last[j] and ((side == 1 and c[j] < z_in) or (side == -1 and c[j] > z_in)):
            exit_px, why = c[j], "15m_back_in_zone"
            break
        # 4) trail after +1R on 5m swings
        best = max(best, h[j]) if side == 1 else min(best, l[j])
        if not trail_on and (best - fill) * side >= risk:
            trail_on = True
        if trail_on:
            if side == 1 and not np.isnan(sw_lo[j]):
                stop = max(stop, sw_lo[j] - STOP_BUF)
            elif side == -1 and not np.isnan(sw_hi[j]):
                stop = min(stop, sw_hi[j] + STOP_BUF)
        j += 1
    if exit_px is None:
        j = n - 1
        exit_px, why = c[j], "end_of_data"
    out_fill = exit_px - side * HALF_COST
    r = (out_fill - fill) * side / risk - COMM / risk
    return dict(side=side, entry_i=ent, exit_i=j, entry_time=pd.Timestamp(t[ent], tz="UTC"),
                exit_time=pd.Timestamp(t[j], tz="UTC"), entry=round(fill, 2), stop0=round(fill - side * risk, 2),
                target=round(G, 2), exit=round(out_fill, 2), risk=round(risk, 2), r=round(r, 4), why=why)


def run(path: str = RAW, out: str = CACHE):
    """Year by year: each year's entries use the year before as warm-up and the year after for exits."""
    import os
    if not os.path.exists(f"{out}/rolls.npz"):
        make_cache(path, out)
    years = cache_years(out)
    labels = [(b, hh, tf) for b in BANDS for hh in HOURS for tf in RSI_TFS]
    last_exit = {}
    rows = []
    for y in years:
        t0 = _time.time()
        win = [x for x in (y - 1, y, y + 1) if x in years]
        D = build(*load_1m(win, out))
        B = biases(D)
        core = (pd.Timestamp(f"{y}-01-01", tz="UTC").value, pd.Timestamp(f"{y + 1}-01-01", tz="UTC").value)
        summary = []
        for band, hours, tf in labels:
            label = f"{band}|{hours}|rsi_{tf}"
            tr = simulate(D, B, band, hours, tf, after_t=last_exit.get(label), core=core)
            for x in tr:
                x["label"] = label
                x.pop("entry_i"), x.pop("exit_i")
            if tr:
                last_exit[label] = tr[-1]["exit_time"].value
            rows += tr
            summary.append(len(tr))
        print(f"{y}: biases {sum(b['start'] >= np.searchsorted(D['t'], core[0]) and b['start'] < np.searchsorted(D['t'], core[1]) for b in B)}"
              f"  trades per variant {summary}  ({_time.time() - t0:.0f}s)", flush=True)
        del D, B
        _trim()
    T = pd.DataFrame(rows)
    T.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(T)} trades)", flush=True)
    if len(T):
        for label, x in T.groupby("label", sort=False):
            print(f"  {label:28s} n={len(x):5d}  totR={x.r.sum():8.1f}", flush=True)


# ----------------------------------------------------------------------------------------- report
def report(volr: bool = True):
    T = pd.read_csv(OUT)
    et = pd.to_datetime(T["entry_time"], utc=True).dt.tz_convert(NY)
    T["year"] = et.dt.year
    T["period"] = pd.cut(T.year, [2009] + [p[1] for p in PERIODS], labels=[p[0] for p in PERIODS])
    switches = ["none"]
    if volr:
        try:
            from regime_study import daily_features
            T["gday"] = pd.to_datetime((et + pd.Timedelta(hours=6)).dt.date)
            f = daily_features("data/raw/GC_5m.csv")
            T = T.join(f[["volr"]], on="gday")
            switches.append("volr>=1.2")
        except Exception as ex:  # noqa: BLE001 - report without the switch rather than fail
            print("volatility switch not available:", ex)
    rows = []
    labels = sorted(T.label.unique())
    for sw in switches:
        X = T if sw == "none" else T[T.volr >= 1.2]
        for label in labels:
            x = X[X.label == label]
            if not len(x):
                continue
            per = x.groupby("period", observed=False).r.sum()
            old, new = x[x.year <= 2018].r, x[x.year >= 2019].r
            sd = x.r.std()
            row = {"switch": sw, "label": label, "n": len(x), "win%": round(100 * (x.r > 0).mean(), 1),
                   "expR": round(x.r.mean(), 3), "totR": round(x.r.sum(), 1),
                   "t": round(x.r.mean() / (sd / np.sqrt(len(x))), 2) if len(x) > 2 and sd > 0 else 0.0,
                   "older_R": round(old.sum(), 1), "newer_R": round(new.sum(), 1),
                   "pos_periods": int((per > 0).sum()), "med_risk": round(x.risk.median(), 2),
                   "tgt%": round(100 * (x.why == "target").mean(), 1)}
            row.update({p: round(float(per.get(p, 0.0)), 1) for p, _ in PERIODS})
            row["PASS"] = (row["n"] >= 200 and row["totR"] > 0 and row["older_R"] > 0 and row["newer_R"] > 0
                           and row["pos_periods"] >= 4)
            row["STRONG"] = row["PASS"] and row["t"] >= 2.5
            rows.append(row)
    R = pd.DataFrame(rows)
    R.to_csv(RES, index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(R.drop(columns=[p for p, _ in PERIODS]).to_string(index=False))
    print("\nBy period (no switch):")
    print(R[R.switch == "none"].set_index("label")[[p for p, _ in PERIODS]].to_string())
    print("\nExit reasons (no switch):")
    print(T.groupby(["label", "why"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        run(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else RAW)
