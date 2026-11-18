"""Desk analysis: Ricardo's own way of reading a chart, as pure functions on OHLC bars.

Everything here is a READ of the chart, not a proven trade signal. His daily Fib + 1m extreme-RSI
model failed its 16-year test as coded (research/fib_bias_study.py: -98R net, +0.07R/trade before
costs), so the desk shows where his conditions line up and keeps score honestly.

Definitions follow /mnt/project-files/atlas/rick-rules.md and research/fib_bias_study.py:
  * Daily swings: high/low beyond the 2 days on each side. The latest leg runs between the latest
    confirmed swing high H and swing low L.
  * Down leg (H then L): DEMAND = range of the swing-low day, golden ratio G = L + 0.618 (H - L).
    Up leg (L then H): SUPPLY = range of the swing-high day, G = H - 0.618 (H - L). Zone 0.618-0.65.
  * Bias: a completed 4H candle with body >= 60% of its range that touches the zone and CLOSES out of
    it toward G. Ends when price reaches G or closes beyond the far side of the zone.
  * RSI(14) extremes on any timeframe: buy 16.5-20, sell 80-85.
  * Structure (BOS/CHoCH), FVGs and order blocks on 5m / 15m.

All functions use completed bars only (the caller passes completed bars).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from ..indicators import atr as _atr
from ..indicators import rsi as _rsi

FIB, FIB_HI = 0.618, 0.65
SOLID_BODY = 0.60
RSI_N = 14
RSI_BUY = (16.5, 20.0)
RSI_SELL = (80.0, 85.0)
NY = "America/New_York"


# ----------------------------------------------------------------------------------- helpers
def _f(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else round(v, 5)


def _ts(t) -> str | None:
    return None if t is None else pd.Timestamp(t).isoformat()


def ny_daily(df: pd.DataFrame, tz: str = NY) -> pd.DataFrame:
    """Daily candles on the NY trading day (18:00 to 17:00), from intraday bars."""
    if df.empty:
        return df
    local = df.index.tz_convert(tz)
    day = (local + pd.Timedelta(hours=6)).normalize().tz_localize(None)   # 18:00 starts tomorrow's day
    g = df.groupby(day.values)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()})
    out.index = pd.DatetimeIndex(out.index).tz_localize(tz)
    return out


# ----------------------------------------------------------------------------------- RSI grid
def rsi_zone(v: float | None) -> str:
    """buy = 16.5-20, sell = 80-85 (his bands); past_buy / past_sell = beyond them; else neutral."""
    if v is None:
        return "none"
    if RSI_BUY[0] <= v <= RSI_BUY[1]:
        return "buy"
    if RSI_SELL[0] <= v <= RSI_SELL[1]:
        return "sell"
    if v < RSI_BUY[0]:
        return "past_buy"
    if v > RSI_SELL[1]:
        return "past_sell"
    return "neutral"


def rsi_grid(bars: dict[str, pd.DataFrame]) -> list[dict]:
    out = []
    for tf, df in bars.items():
        if df is None or len(df) < RSI_N + 2:
            out.append({"tf": tf, "rsi": None, "zone": "none", "prev": None})
            continue
        r = _rsi(df["close"], RSI_N)
        v, p = _f(r.iloc[-1]), _f(r.iloc[-2])
        out.append({"tf": tf, "rsi": None if v is None else round(v, 1),
                    "prev": None if p is None else round(p, 1), "zone": rsi_zone(v)})
    return out


# ----------------------------------------------------------------------------------- swings
def swings(df: pd.DataFrame, k: int = 2) -> tuple[list[tuple], list[tuple]]:
    """Fractal swing highs / lows: beyond the k bars on each side. Returns [(time, price, i)]."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    hi, lo = [], []
    for i in range(k, n - k):
        # strictly beyond the bars on the left, at least equal on the right (bars that open at the
        # previous close often share a high/low with their neighbour)
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            hi.append((df.index[i], float(h[i]), i))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            lo.append((df.index[i], float(l[i]), i))
    return hi, lo


def structure(df: pd.DataFrame, k: int = 2) -> dict:
    """Market structure from fractal swings: the direction of the last break of structure (a CLOSE
    beyond the most recent swing high / low), and whether it was a CHoCH (against the prior trend)."""
    if df is None or len(df) < 2 * k + 5:
        return {"trend": "unknown", "last_break": None, "swing_high": None, "swing_low": None}
    hi, lo = swings(df, k)
    c = df["close"].to_numpy()
    events = []          # (bar index, direction, level)
    for lst, d in ((hi, 1), (lo, -1)):
        for t, p, i in lst:
            j = i + k + 1                                   # swing confirmed after k bars
            seg = c[j:]
            hit = np.flatnonzero(seg > p) if d > 0 else np.flatnonzero(seg < p)
            if len(hit):
                events.append((j + int(hit[0]), d, p))
    events.sort()
    trend, last, prev_dir = "range", None, 0
    for idx, d, p in events:
        kind = "BOS" if prev_dir in (0, d) else "CHoCH"
        last = {"dir": "up" if d > 0 else "down", "kind": kind, "level": _f(p), "time": _ts(df.index[idx])}
        prev_dir = d
    if last:
        trend = last["dir"]
    return {"trend": trend, "last_break": last,
            "swing_high": _f(hi[-1][1]) if hi else None, "swing_low": _f(lo[-1][1]) if lo else None}


# ----------------------------------------------------------------------------------- daily Fib
def daily_fib(daily: pd.DataFrame, k: int = 2) -> dict | None:
    """His daily Fibonacci: latest confirmed daily leg, golden ratio and the demand/supply zone."""
    if daily is None or len(daily) < 2 * k + 3:
        return None
    done = daily.iloc[:-1] if len(daily) > 1 else daily          # last row is today's, still forming
    hi, lo = swings(done, k)
    if not hi or not lo:
        return None
    th, H, ih = hi[-1]
    tl, L, il = lo[-1]
    if H <= L:
        return None
    if th < tl:      # high came first: down leg, demand at the low, golden ratio is up
        leg = "down"
        zl, zh = float(done["low"].iloc[il]), float(done["high"].iloc[il])
        g, g_hi = L + FIB * (H - L), L + FIB_HI * (H - L)
        zone_kind = "demand"
    else:
        leg = "up"
        zl, zh = float(done["low"].iloc[ih]), float(done["high"].iloc[ih])
        g, g_hi = H - FIB * (H - L), H - FIB_HI * (H - L)
        zone_kind = "supply"
    eq = (H + L) / 2
    last = float(daily["close"].iloc[-1])
    return {"leg": leg, "high": _f(H), "low": _f(L), "high_time": _ts(th), "low_time": _ts(tl),
            "golden": _f(g), "golden_far": _f(g_hi), "equilibrium": _f(eq),
            "zone_kind": zone_kind, "zone_low": _f(zl), "zone_high": _f(zh),
            "premium": last > eq,
            "levels": {f"{r:g}": _f(L + r * (H - L)) for r in (0.0, 0.236, 0.382, 0.5, 0.618, 0.65, 0.786, 1.0)}}


def solid_candle_bias(h4: pd.DataFrame, fib: dict | None, price: float | None) -> dict:
    """A solid 4H candle (body >= 60% of range) that touched the zone and closed out of it toward the
    golden ratio. Returns long / short / none, with the candle and why it is (in)valid now."""
    none = {"dir": "none", "reason": "no daily leg yet", "candle": None}
    if fib is None or h4 is None or h4.empty:
        return none
    zl, zh, g = fib["zone_low"], fib["zone_high"], fib["golden"]
    start = pd.Timestamp(fib["low_time"] if fib["leg"] == "down" else fib["high_time"])
    seg = h4[h4.index >= start]
    found = None
    for t, r in seg.iterrows():
        rng = r["high"] - r["low"]
        if rng <= 0 or abs(r["close"] - r["open"]) < SOLID_BODY * rng:
            continue
        touches = r["low"] <= zh and r["high"] >= zl
        if not touches:
            continue
        if fib["zone_kind"] == "demand" and r["close"] > r["open"] and zh < r["close"] < g:
            found = ("long", t, r)
        elif fib["zone_kind"] == "supply" and r["close"] < r["open"] and g < r["close"] < zl:
            found = ("short", t, r)
    if not found:
        return {"dir": "none", "reason": f"waiting for a solid 4H candle out of {fib['zone_kind']}",
                "candle": None}
    d, t, r = found
    after = h4[h4.index > t]
    if d == "long":
        if len(after) and (after["high"].max() >= g):
            return {"dir": "none", "reason": "golden ratio already reached", "candle": _ts(t)}
        if len(after) and (after["close"].min() < zl):
            return {"dir": "none", "reason": "closed back below demand (invalid)", "candle": _ts(t)}
    else:
        if len(after) and (after["low"].min() <= g):
            return {"dir": "none", "reason": "golden ratio already reached", "candle": _ts(t)}
        if len(after) and (after["close"].max() > zh):
            return {"dir": "none", "reason": "closed back above supply (invalid)", "candle": _ts(t)}
    close = float(r["close"])
    half = (close + (zh if d == "long" else zl)) / 2
    retest = price is not None and ((price <= half) if d == "long" else (price >= half))
    return {"dir": d, "reason": "solid 4H candle closed out of " + fib["zone_kind"], "candle": _ts(t),
            "candle_close": _f(close), "retest_level": _f(half), "retesting": bool(retest)}


def htf_bias(daily_struct: dict, h4_struct: dict, h1_struct: dict, solid: dict, fib: dict | None) -> dict:
    """One word bias. His rule: 4H + daily for direction, 1H to 4H for timing. Solid-candle bias wins
    when present; otherwise daily and 4H structure must agree."""
    d, h4, h1 = daily_struct.get("trend"), h4_struct.get("trend"), h1_struct.get("trend")
    if solid.get("dir") == "long":
        word, why = "bullish", "solid 4H candle out of daily demand, heading to the golden ratio"
    elif solid.get("dir") == "short":
        word, why = "bearish", "solid 4H candle out of daily supply, heading to the golden ratio"
    elif d == h4 == "up":
        word, why = "bullish", "daily and 4H structure both up"
    elif d == h4 == "down":
        word, why = "bearish", "daily and 4H structure both down"
    else:
        word, why = "neutral", f"daily {d}, 4H {h4}: no agreement"
    pd_ = None
    if fib:
        pd_ = "premium" if fib["premium"] else "discount"
    return {"bias": word, "why": why, "daily": d, "h4": h4, "h1": h1, "array": pd_}


# ----------------------------------------------------------------------------------- PD arrays
def fvgs(df: pd.DataFrame, min_atr: float = 0.2, keep: int = 6) -> list[dict]:
    """Unfilled fair value gaps (3-candle gaps). Bullish: low[i] > high[i-2]."""
    if df is None or len(df) < 20:
        return []
    a = _atr(df, 14).to_numpy()
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    out = []
    n = len(df)
    for i in range(2, n):
        if l[i] > h[i - 2] and l[i] - h[i - 2] >= min_atr * a[i]:
            top, bot, d = l[i], h[i - 2], "bull"
        elif h[i] < l[i - 2] and l[i - 2] - h[i] >= min_atr * a[i]:
            top, bot, d = l[i - 2], h[i], "bear"
        else:
            continue
        later_l, later_h = l[i + 1:], h[i + 1:]
        if d == "bull":
            fill = (top - later_l.min()) / (top - bot) if len(later_l) else 0.0
        else:
            fill = (later_h.max() - bot) / (top - bot) if len(later_h) else 0.0
        fill = max(0.0, float(fill))
        if fill >= 1.0:
            continue                                         # fully filled: gone
        out.append({"dir": d, "top": _f(top), "bottom": _f(bot), "mid": _f((top + bot) / 2),
                    "time": _ts(df.index[i - 1]), "filled": round(min(fill, 1.0), 2)})
    return out[-keep:]


def order_blocks(df: pd.DataFrame, disp_atr: float = 1.2, keep: int = 4) -> list[dict]:
    """Order blocks: the last opposite-colour candle before a displacement candle (body >= 1.2 ATR,
    body >= 60% of range) that breaks the most recent swing. Dropped once a close goes through it."""
    if df is None or len(df) < 30:
        return []
    a = _atr(df, 14).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    hi, lo = swings(df, 2)
    out = []
    for i in range(15, len(df)):
        body, rng = abs(c[i] - o[i]), h[i] - l[i]
        if rng <= 0 or body < disp_atr * a[i] or body < 0.6 * rng:
            continue
        d = 1 if c[i] > o[i] else -1
        prior = [p for t, p, j in (hi if d > 0 else lo) if j + 2 < i]
        if not prior or (c[i] <= prior[-1] if d > 0 else c[i] >= prior[-1]):
            continue
        j = i - 1
        while j > i - 6 and (c[j] > o[j]) == (d > 0):
            j -= 1
        if (c[j] > o[j]) == (d > 0):
            continue
        top, bot = h[j], l[j]
        later = c[i + 1:]
        if len(later) and ((later < bot).any() if d > 0 else (later > top).any()):
            continue                                         # mitigated through: gone
        out.append({"dir": "bull" if d > 0 else "bear", "top": _f(top), "bottom": _f(bot),
                    "time": _ts(df.index[j])})
    seen, uniq = set(), []
    for ob in reversed(out):
        key = (ob["top"], ob["bottom"])
        if key not in seen:
            seen.add(key)
            uniq.append(ob)
    return list(reversed(uniq[:keep]))


# ----------------------------------------------------------------------------------- sessions
SESSIONS = {"Asia": (time(18, 0), time(2, 0)), "London": (time(2, 0), time(8, 0)),
            "NY": (time(8, 0), time(17, 0))}


def session_now(now: pd.Timestamp, tz: str = NY) -> dict:
    loc = now.tz_convert(tz)
    m = loc.hour * 60 + loc.minute
    if loc.weekday() == 5 or (loc.weekday() == 4 and m >= 17 * 60) or (loc.weekday() == 6 and m < 18 * 60):
        return {"name": "Weekend", "ends_in_min": None}
    if 17 * 60 <= m < 18 * 60:
        return {"name": "Daily break", "ends_in_min": 18 * 60 - m}
    for name, (a, b) in SESSIONS.items():
        am, bm = a.hour * 60 + a.minute, b.hour * 60 + b.minute
        inside = am <= m < bm if am < bm else (m >= am or m < bm)
        if inside:
            return {"name": name, "ends_in_min": (bm - m) % 1440}
    return {"name": "Off", "ends_in_min": None}


def session_levels(df: pd.DataFrame, now: pd.Timestamp, tz: str = NY) -> list[dict]:
    """PDH/PDL, NY midnight open, and the latest Asia / London / NY highs and lows."""
    if df is None or df.empty:
        return []
    loc = df.index.tz_convert(tz)
    out = []
    daily = ny_daily(df, tz)
    if len(daily) >= 2:
        pdh, pdl = daily.iloc[-2]["high"], daily.iloc[-2]["low"]
        out += [{"name": "PDH", "price": _f(pdh)}, {"name": "PDL", "price": _f(pdl)}]
    nowl = now.tz_convert(tz)
    mid = nowl.normalize()
    m = df[(loc >= mid)]
    if len(m):
        out.append({"name": "Midnight open", "price": _f(m["open"].iloc[0])})
    trading_day = (loc + pd.Timedelta(hours=6)).normalize()
    today = (nowl + pd.Timedelta(hours=6)).normalize()
    mins = loc.hour * 60 + loc.minute
    for name, (a, b) in SESSIONS.items():
        am, bm = a.hour * 60 + a.minute, b.hour * 60 + b.minute
        inside = (mins >= am) & (mins < bm) if am < bm else ((mins >= am) | (mins < bm))
        for day, tag in ((today, ""), (today - pd.Timedelta(days=1), " (prev)")):
            seg = df[inside & (trading_day == day)]
            if len(seg):
                out += [{"name": f"{name} high{tag}", "price": _f(seg["high"].max())},
                        {"name": f"{name} low{tag}", "price": _f(seg["low"].min())}]
                break
    return out


def nearest(levels: list[dict], price: float) -> tuple[dict | None, dict | None]:
    above = [x for x in levels if x.get("price") is not None and x["price"] > price]
    below = [x for x in levels if x.get("price") is not None and x["price"] < price]
    a = min(above, key=lambda x: x["price"] - price) if above else None
    b = max(below, key=lambda x: x["price"]) if below else None
    return a, b


# ----------------------------------------------------------------------------------- SMT
def smt(a: pd.DataFrame, b: pd.DataFrame, lookback: int = 48, inverse: bool = False) -> dict:
    """Smart-money divergence between two correlated markets over the last `lookback` bars vs the
    `lookback` bars before: one makes a new low (high) and the other does not. `inverse` for a
    negatively correlated pair (gold vs the dollar)."""
    if a is None or b is None or len(a) < 2 * lookback or len(b) < 2 * lookback:
        return {"signal": "none", "detail": "not enough data"}
    a, b = a.align(b, join="inner")[0], a.align(b, join="inner")[1]
    if len(a) < 2 * lookback:
        return {"signal": "none", "detail": "not enough overlapping data"}
    ra, pa = a.iloc[-lookback:], a.iloc[-2 * lookback:-lookback]
    rb, pb = b.iloc[-lookback:], b.iloc[-2 * lookback:-lookback]
    a_ll, a_hh = ra["low"].min() < pa["low"].min(), ra["high"].max() > pa["high"].max()
    b_ll, b_hh = rb["low"].min() < pb["low"].min(), rb["high"].max() > pb["high"].max()
    if inverse:
        b_ll, b_hh = b_hh, b_ll
    if a_ll and not b_ll:
        return {"signal": "bullish", "detail": "made a lower low the other market did not confirm"}
    if b_ll and not a_ll:
        return {"signal": "bullish", "detail": "held its low while the other market made a lower low"}
    if a_hh and not b_hh:
        return {"signal": "bearish", "detail": "made a higher high the other market did not confirm"}
    if b_hh and not a_hh:
        return {"signal": "bearish", "detail": "failed to make the higher high the other market made"}
    return {"signal": "none", "detail": "both markets moving together"}


# ----------------------------------------------------------------------------------- checklist
@dataclass
class Check:
    key: str
    label: str
    ok: bool
    detail: str = ""


def rick_checklist(bias: dict, solid: dict, rsi: list[dict], pd_arrays: list[dict], price: float | None,
                   news: dict | None) -> dict:
    """His entry checklist, in his order. Direction comes from the bias; everything else must agree."""
    d = {"bullish": "long", "bearish": "short"}.get(bias.get("bias"), None)
    checks = [Check("bias", "HTF bias (4H + daily)", d is not None, bias.get("why", ""))]
    checks.append(Check("solid", "Solid candle out of demand / supply", solid.get("dir") in ("long", "short")
                        and (d is None or solid["dir"] == d), solid.get("reason", "")))
    checks.append(Check("retest", "Confirmation retest", bool(solid.get("retesting")),
                        "pulled back halfway to the zone" if solid.get("retesting") else "no retest yet"))
    want = ("buy", "past_buy") if d == "long" else ("sell", "past_sell") if d == "short" else ()
    hits = [r["tf"] for r in rsi if r["zone"] in want]
    checks.append(Check("rsi", "Extreme RSI with the bias", bool(hits),
                        ("RSI extreme on " + ", ".join(hits)) if hits else "no extreme in the bias direction"))
    inside = []
    if price is not None and d:
        want_dir = "bull" if d == "long" else "bear"
        for z in pd_arrays:
            if z["dir"] == want_dir and z["bottom"] <= price <= z["top"]:
                inside.append(z["kind"] + " " + z["tf"])
    checks.append(Check("pd", "Price in an FVG / order block", bool(inside),
                        ", ".join(inside) if inside else "not inside a zone"))
    blackout = bool(news and news.get("blackout"))
    checks.append(Check("news", "No news blackout", not blackout,
                        news.get("next_title", "") if news else ""))
    score = sum(c.ok for c in checks)
    return {"direction": d, "score": score, "of": len(checks),
            "checks": [c.__dict__ for c in checks]}


def brief(sym_name: str, price: float | None, bias: dict, fib: dict | None, solid: dict,
          check: dict, above: dict | None, below: dict | None, news: dict | None) -> str:
    """One-paragraph desk read in plain words, built from the numbers above (no AI, no guessing)."""
    if price is None:
        return f"{sym_name}: no price data."
    parts = [f"{sym_name} {price:,.2f}. Bias {bias['bias']}: {bias['why']}."]
    if fib:
        parts.append(f"Daily leg {fib['leg']} {fib['low']:,.2f} to {fib['high']:,.2f}; golden ratio "
                     f"{fib['golden']:,.2f}; {fib['zone_kind']} {fib['zone_low']:,.2f}-{fib['zone_high']:,.2f}; "
                     f"price in {bias.get('array')}.")
    if solid.get("dir") in ("long", "short"):
        parts.append(f"Solid 4H candle says {solid['dir']}; retest level {solid.get('retest_level'):,.2f}"
                     + (" (retesting now)." if solid.get("retesting") else "."))
    if above:
        parts.append(f"Nearest above: {above['name']} {above['price']:,.2f}.")
    if below:
        parts.append(f"Nearest below: {below['name']} {below['price']:,.2f}.")
    if news and news.get("blackout"):
        parts.append(f"NEWS BLACKOUT: {news.get('next_title')}. Stand aside.")
    s, n = check["score"], check["of"]
    if check["direction"] is None:
        parts.append("No direction: stand aside.")
    elif s == n:
        parts.append(f"All {n} of your checks line up {check['direction']}. Still a read, not a proven edge.")
    else:
        missing = [c["label"] for c in check["checks"] if not c["ok"]]
        parts.append(f"{s} of {n} checks for {check['direction']}. Missing: " + "; ".join(missing) + ".")
    return " ".join(parts)
