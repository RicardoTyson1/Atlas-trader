"""Desk data: prices (read-only), the bot's own state files, news, desk alerts and guardrails.

Light by design: the server has 1 CPU and ~500 MB free next to the live bot. Bars are fetched only for
what is on screen, cached per timeframe, and every broker call goes through one slow throttle so the
desk can never eat the bot's TradeLocker rate limits.
"""
from __future__ import annotations

import csv
import json
import logging
import threading
import time as _time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..indicators import atr as _atr
from . import analysis as A
from . import controls as C

log = logging.getLogger("atlas.desk")

TFS = ["1m", "5m", "15m", "1H", "4H", "1D"]
TTL = {"1m": 20, "5m": 45, "15m": 90, "1H": 300, "4H": 600, "1D": 1800}
COUNT = {"1m": 400, "5m": 400, "15m": 400, "1H": 400, "4H": 300, "1D": 160}
STEP = {"1m": "1min", "5m": "5min", "15m": "15min", "1H": "1h", "4H": "4h", "1D": "1D"}


# =========================================================================== price sources
class ReadOnlyBroker:
    """Only the read calls of a broker. The desk can never place, change or close an order."""

    def __init__(self, broker, min_interval: float = 1.0):
        self._b = broker
        self._lock = threading.Lock()
        self._last = 0.0
        self.min_interval = min_interval

    def _slow(self):
        with self._lock:
            wait = self._last + self.min_interval - _time.monotonic()
            if wait > 0:
                _time.sleep(wait)
            self._last = _time.monotonic()

    def resolve(self, symbol: str) -> bool:
        try:
            self._slow()
            self._b._iid(symbol)
            return True
        except Exception:  # noqa: BLE001 - unknown symbol on this broker
            return False

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        self._slow()
        return self._b.bars(symbol, tf, count)

    def account(self):
        self._slow()
        return self._b.account()

    def positions(self):
        self._slow()
        return self._b.positions()

    label = "TradeLocker (GatesFX demo feed)"
    demo = False


class DemoSource:
    """Made-up prices for trying the page without a broker. Clearly labelled DEMO everywhere."""

    BASE = {"XAUUSD": 4050.0, "XAGUSD": 51.0, "USOIL": 61.0, "SPX500": 6650.0, "US30": 46500.0,
            "NAS100": 24600.0, "DXY": 99.5, "EURUSD": 1.16}
    label = "DEMO DATA (made up, not real prices)"
    demo = True

    def __init__(self, seed: int = 7):
        self.seed = seed
        self._cache: dict[str, pd.DataFrame] = {}

    def _base(self, symbol: str) -> float | None:
        for k, v in self.BASE.items():
            if symbol.upper().startswith(k):
                return v
        return None

    def resolve(self, symbol: str) -> bool:
        return self._base(symbol) is not None

    def _minutes(self, symbol: str) -> pd.DataFrame:
        if symbol in self._cache:
            return self._cache[symbol]
        base = self._base(symbol)
        rng = np.random.default_rng(abs(hash((symbol[:3], self.seed))) % 2**32)
        common = np.random.default_rng(self.seed)
        n = 60 * 24 * 75
        end = pd.Timestamp.now(tz="UTC").floor("1min")
        idx = pd.date_range(end=end, periods=n, freq="1min")
        shock = common.standard_t(4, n) * 0.6 + rng.standard_t(4, n) * 0.4
        drift = np.sin(np.arange(n) / 9000.0) * 0.000004
        r = shock * 0.00012 + drift
        close = base * np.exp(np.cumsum(r))
        open_ = np.r_[close[0], close[:-1]]
        wig = np.abs(rng.normal(0, base * 0.00025, n))
        df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wig,
                           "low": np.minimum(open_, close) - wig, "close": close,
                           "volume": rng.integers(50, 600, n).astype(float)}, index=idx)
        loc = idx.tz_convert("America/New_York")
        brk = (loc.hour == 17) | (loc.weekday == 5) | ((loc.weekday == 4) & (loc.hour >= 17)) | \
              ((loc.weekday == 6) & (loc.hour < 18))
        df = df[~brk]
        self._cache[symbol] = df
        return df

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        m = self._minutes(symbol)
        if tf == "1m":
            out = m
        else:
            out = m.resample(STEP[tf], label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        return out.iloc[:-1].tail(count)

    def account(self):
        from ..brokers.base import Account
        return Account(balance=9512.40, equity=9548.10)

    def positions(self):
        from ..brokers.base import Position
        p = float(self._minutes("XAUUSD")["close"].iloc[-1])
        return [Position(id="demo1", symbol="XAUUSD.R", side=1, qty=0.05, entry=round(p - 3.2, 2),
                         opened=pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=37), tag="atlas")]


# =========================================================================== the feed
class DeskFeed:
    def __init__(self, desk_cfg: dict, source, bot_cfg=None, state_dir: str | Path = "state",
                 clock=None, live=None):
        self.cfg = desk_cfg
        self.src = source
        self.live = live          # DatabentoLive (CME futures, every second) or None = broker prices only
        self.bot_cfg = bot_cfg
        self.state_dir = Path(state_dir)
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.tz = "America/New_York"
        g = dict(desk_cfg.get("guardrails") or {})
        if "cooldown_after_loss_min" not in g and bot_cfg is not None:
            # one number for bot and desk: the bot's own pause after a loss (config/default.yaml)
            g["cooldown_after_loss_min"] = getattr(bot_cfg.risk, "cooldown_after_loss_minutes", 30)
        self.guard = C.Guardrails(g)
        self._bars: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._lock = threading.RLock()
        self._acct_cache: tuple[float, dict] | None = None
        self.instruments = []
        for ins in desk_cfg.get("instruments", []):
            sym = next((s for s in ins.get("symbols", []) if source.resolve(s)), None)
            self.instruments.append({**ins, "symbol": sym, "available": sym is not None})
            if sym is None:
                log.warning("desk: no symbol found on the broker for %s (tried %s)", ins["key"], ins.get("symbols"))
        self.alerts_file = self.state_dir / "desk_alerts.json"

    # ------------------------------------------------------------------ bars
    def inst(self, key: str) -> dict | None:
        return next((i for i in self.instruments if i["key"] == key), None)

    @property
    def label(self) -> str:
        if self.live is not None:
            return f"{self.live.label}; broker prices where not covered"
        return self.src.label

    def uses_live(self, key: str) -> bool:
        return self.live is not None and self.live.has(key)

    def feed_info(self, key: str) -> dict:
        if self.uses_live(key):
            return self.live.info(key)
        return {"feed": "broker", "symbol": (self.inst(key) or {}).get("symbol"), "live": False}

    def bars(self, key: str, tf: str) -> pd.DataFrame:
        if self.uses_live(key):
            try:
                return self.live.bars(key, tf, COUNT[tf])
            except Exception as e:  # noqa: BLE001 - fall back to the broker
                log.warning("desk: live bars %s %s failed: %r", key, tf, e)
        return self.broker_bars(key, tf)

    def broker_bars(self, key: str, tf: str, ttl: float | None = None) -> pd.DataFrame:
        ins = self.inst(key)
        if not ins or not ins["available"]:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        ck = (key, tf)
        with self._lock:
            hit = self._bars.get(ck)
            if hit and _time.monotonic() - hit[0] < (ttl or TTL[tf]):
                return hit[1]
        try:
            df = self.src.bars(ins["symbol"], tf, COUNT[tf])
        except Exception as e:  # noqa: BLE001 - show stale data rather than nothing
            log.warning("desk: bars %s %s failed: %r", key, tf, e)
            old = hit[1] if hit else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            with self._lock:      # back off for a full TTL: retrying at once is what feeds rate limits
                self._bars[ck] = (_time.monotonic(), old)
            return old
        with self._lock:
            self._bars[ck] = (_time.monotonic(), df)
        return df

    # ------------------------------------------------------------------ background desk reads
    def background_once(self) -> int:
        """Check every market for a desk read (and score open ones) whether or not the page is open.
        Uses no broker calls for markets on the live feed. Returns how many markets were checked."""
        n = 0
        for ins in self.instruments:
            if not ins["available"]:
                continue
            if not self.uses_live(ins["key"]) and not getattr(self.src, "demo", False):
                continue          # broker-priced markets: only when someone has the page open
            try:
                self.analyse(ins["key"], with_chart=False, with_broker=False)
                n += 1
            except Exception as e:  # noqa: BLE001 - one market must not stop the others
                log.warning("desk: background read %s failed: %r", ins["key"], e)
        try:
            self.alerts()
        except Exception as e:  # noqa: BLE001
            log.warning("desk: scoring desk reads failed: %r", e)
        return n

    def start_background(self, every_s: float = 60) -> threading.Thread:
        def loop():
            stop = threading.Event()
            while not stop.wait(every_s):
                self.background_once()
        t = threading.Thread(target=loop, name="desk-reads", daemon=True)
        t.start()
        return t

    # ------------------------------------------------------------------ news
    def news(self, now: pd.Timestamp) -> dict:
        nb = (self.bot_cfg.news if self.bot_cfg else None)
        before = getattr(nb, "minutes_before", 15)
        after = getattr(nb, "minutes_after", 15)
        no_entry = int(getattr(nb, "no_entry_minutes_before", 0) or 0)
        feed = self.state_dir / "news_events.json"
        try:
            events = json.loads(feed.read_text())
        except (OSError, ValueError):
            events = []
        evs = []
        for e in events:
            try:
                evs.append((pd.Timestamp(e["time"]).tz_convert("UTC"), e.get("title", "")))
            except Exception:  # noqa: BLE001
                continue
        evs.sort()
        upcoming = [(t, ti) for t, ti in evs if t + pd.Timedelta(minutes=after) >= now]
        out = {"blackout": False, "next_title": None, "next_time": None, "minutes_to": None,
               "minutes_before": before, "minutes_after": after, "upcoming": [],
               "loaded": bool(events)}
        for t, ti in upcoming[:8]:
            out["upcoming"].append({"time": t.isoformat(), "title": ti})
        if upcoming:
            t, ti = upcoming[0]
            mins = (t - now).total_seconds() / 60
            out.update(next_title=ti, next_time=t.isoformat(), minutes_to=round(mins, 1))
            out["blackout"] = -after <= mins <= before + no_entry
        return out

    # ------------------------------------------------------------------ one instrument
    def analyse(self, key: str, chart_tf: str = "5m", with_chart: bool = True,
                with_broker: bool = True) -> dict:
        now = self.clock()
        ins = self.inst(key)
        if not ins:
            return {"error": f"unknown instrument {key}"}
        if not ins["available"]:
            return {"key": key, "name": ins["name"], "available": False,
                    "error": f"no matching symbol on the broker (tried {', '.join(ins.get('symbols', []))})"}
        b = {tf: self.bars(key, tf) for tf in TFS}
        price = float(b["1m"]["close"].iloc[-1]) if len(b["1m"]) else (
            float(b["5m"]["close"].iloc[-1]) if len(b["5m"]) else None)
        daily = b["1D"]
        fib = A.daily_fib(daily)
        solid = A.solid_candle_bias(b["4H"], fib, price)
        sd, s4, s1 = A.structure(daily, 2), A.structure(b["4H"], 2), A.structure(b["1H"], 2)
        s15, s5 = A.structure(b["15m"], 2), A.structure(b["5m"], 2)
        bias = A.htf_bias(sd, s4, s1, solid, fib)
        rsi = A.rsi_grid({tf: b[tf] for tf in ["1m", "5m", "15m", "1H", "4H"]})
        zones = []
        for tf in ("5m", "15m"):
            zones += [{**z, "kind": "FVG", "tf": tf} for z in A.fvgs(b[tf])]
            zones += [{**z, "kind": "OB", "tf": tf} for z in A.order_blocks(b[tf])]
        if price is not None:
            zones.sort(key=lambda z: abs((z["top"] + z["bottom"]) / 2 - price))
        levels = A.session_levels(b["5m"], now) if len(b["5m"]) else []
        if fib:
            levels += [{"name": "Golden ratio 0.618", "price": fib["golden"]},
                       {"name": f"Daily {fib['zone_kind']} top", "price": fib["zone_high"]},
                       {"name": f"Daily {fib['zone_kind']} bottom", "price": fib["zone_low"]},
                       {"name": "Daily 50%", "price": fib["equilibrium"]}]
        above, below = A.nearest(levels, price) if price is not None else (None, None)
        news = self.news(now)
        check = A.rick_checklist(bias, solid, rsi, zones, price, news)
        atr15 = float(_atr(b["15m"], 14).iloc[-1]) if len(b["15m"]) > 20 else None
        out = {
            "key": key, "name": ins["name"], "symbol": ins["symbol"], "available": True,
            "time": now.isoformat(), "price": price,
            "change_today": self._change_today(b["5m"], now, price),
            "bias": bias, "fib": fib, "solid": solid,
            "structure": {"1D": sd, "4H": s4, "1H": s1, "15m": s15, "5m": s5},
            "rsi": rsi, "zones": zones[:10], "levels": levels,
            "nearest_above": above, "nearest_below": below,
            "checklist": check, "atr15": None if atr15 is None else round(atr15, 4),
            "brief": A.brief(ins["name"], price, bias, fib, solid, check, above, below, news),
            "session": A.session_now(now),
            "feed": self.feed_info(key),
        }
        if with_broker and self.uses_live(key) and price is not None:
            # the bot trades the broker's CFD; futures sit a little apart (the basis). Trade lines on the
            # chart are shifted by it so they land where the trade really is. Once a minute is plenty:
            # the desk must never eat the bot's TradeLocker rate limit.
            bb = self.broker_bars(key, "1m", ttl=60)
            bp = float(bb["close"].iloc[-1]) if len(bb) else None
            out["broker_price"] = bp
            out["basis"] = None if bp is None else round(price - bp, 5)
        if with_chart:
            cb = b.get(chart_tf) if chart_tf in b else self.bars(key, chart_tf)
            cb = cb.tail(300)
            out["chart"] = {"tf": chart_tf, "bars": [
                [int(t.timestamp()), round(float(r.open), 5), round(float(r.high), 5), round(float(r.low), 5),
                 round(float(r.close), 5), float(r.volume)] for t, r in cb.iterrows()]}
        self._track_alert(out, now)
        return out

    def _change_today(self, df5: pd.DataFrame, now: pd.Timestamp, price: float | None):
        if price is None or df5.empty:
            return None
        loc = df5.index.tz_convert(self.tz)
        start = (now.tz_convert(self.tz) + pd.Timedelta(hours=6)).normalize() - pd.Timedelta(hours=6)
        seg = df5[loc >= start]
        if seg.empty:
            return None
        o = float(seg["open"].iloc[0])
        return {"points": round(price - o, 4), "pct": round((price - o) / o * 100, 3)}

    def overview(self) -> list[dict]:
        """Small strip for every instrument: price, bias, checklist score (cheap: no 1m/5m bars)."""
        now = self.clock()
        out = []
        for ins in self.instruments:
            if not ins["available"]:
                out.append({"key": ins["key"], "name": ins["name"], "available": False})
                continue
            b15, b4, bd = self.bars(ins["key"], "15m"), self.bars(ins["key"], "4H"), self.bars(ins["key"], "1D")
            price = float(b15["close"].iloc[-1]) if len(b15) else None
            fib = A.daily_fib(bd)
            solid = A.solid_candle_bias(b4, fib, price)
            bias = A.htf_bias(A.structure(bd), A.structure(b4), A.structure(self.bars(ins["key"], "1H")), solid, fib)
            r15 = A.rsi_grid({"15m": b15, "4H": b4})
            out.append({"key": ins["key"], "name": ins["name"], "available": True, "price": price,
                        "bias": bias["bias"], "rsi": r15,
                        "change_today": self._change_today(b15, now, price)})
        return out

    def smt(self) -> list[dict]:
        out = []
        for pair in self.cfg.get("smt_pairs", []):
            a, b = pair[0], pair[1]
            inv = bool(pair[2]) if len(pair) > 2 else False
            ia, ib = self.inst(a), self.inst(b)
            if not ia or not ib or not ia["available"] or not ib["available"]:
                out.append({"a": a, "b": b, "signal": "none", "detail": "one market not available on the broker"})
                continue
            res = A.smt(self.bars(a, "15m"), self.bars(b, "15m"), lookback=32, inverse=inv)
            out.append({"a": ia["name"], "b": ib["name"], "inverse": inv, **res})
        return out

    # ------------------------------------------------------------------ desk alerts (honest scoring)
    def _load_alerts(self) -> list[dict]:
        try:
            return list(json.loads(self.alerts_file.read_text()))
        except (OSError, ValueError):
            return []

    def _save_alerts(self, rows: list[dict]) -> None:
        self.alerts_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.alerts_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-300:], indent=0))
        tmp.replace(self.alerts_file)

    def _track_alert(self, snap: dict, now: pd.Timestamp) -> None:
        """Log a read when all but one of his checks line up (and never in a news blackout), then
        score it later: did price go 2 x ATR(15m) his way before 1 x ATR against? Rough, but honest."""
        ck = snap["checklist"]
        if ck["direction"] is None or snap["price"] is None or not snap.get("atr15"):
            return
        need = int(self.cfg.get("alert_min_checks", ck["of"] - 1))
        news_ok = next((c["ok"] for c in ck["checks"] if c["key"] == "news"), True)
        if ck["score"] < need or not news_ok:
            return
        with self._lock:
            rows = self._load_alerts()
            recent = [r for r in rows if r["key"] == snap["key"] and r["dir"] == ck["direction"]
                      and now - pd.Timestamp(r["time"]) < pd.Timedelta(minutes=60)]
            if recent:
                return
            rows.append({"time": now.isoformat(), "key": snap["key"], "name": snap["name"],
                         "dir": ck["direction"], "price": snap["price"], "score": ck["score"], "of": ck["of"],
                         "unit": snap["atr15"], "missing": [c["label"] for c in ck["checks"] if not c["ok"]],
                         "outcome": "open"})
            self._save_alerts(rows)

    def alerts(self, limit: int = 30) -> dict:
        now = self.clock()
        with self._lock:
            rows = self._load_alerts()
            changed = False
            for r in rows:
                if r.get("outcome") != "open":
                    continue
                df = self.bars(r["key"], "5m") if self.inst(r["key"]) else pd.DataFrame()
                t0 = pd.Timestamp(r["time"])
                seg = df[df.index > t0] if len(df) else df
                if len(seg):
                    s = 1 if r["dir"] == "long" else -1
                    tgt, stop = r["price"] + s * 2 * r["unit"], r["price"] - s * r["unit"]
                    for _, b in seg.iterrows():
                        hit_stop = b.low <= stop if s > 0 else b.high >= stop
                        hit_tgt = b.high >= tgt if s > 0 else b.low <= tgt
                        if hit_stop:
                            r["outcome"], changed = "stopped", True
                            break
                        if hit_tgt:
                            r["outcome"], changed = "reached 2R", True
                            break
                if r["outcome"] == "open" and now - t0 > pd.Timedelta(hours=24):
                    r["outcome"], changed = "expired", True
            if changed:
                self._save_alerts(rows)
        done = [r for r in rows if r["outcome"] in ("stopped", "reached 2R")]
        wins = sum(r["outcome"] == "reached 2R" for r in done)
        return {"rows": list(reversed(rows[-limit:])),
                "score": {"n": len(done), "wins": wins, "stopped": len(done) - wins,
                          "open": sum(r["outcome"] == "open" for r in rows),
                          "net_r": 2 * wins - (len(done) - wins)}}

    # ------------------------------------------------------------------ the bot
    def _journal(self) -> list[dict]:
        p = self.state_dir / "journal.csv"
        if not p.exists():
            return []
        with p.open(newline="") as f:
            return list(csv.DictReader(f))

    def _runner_state(self) -> tuple[dict, float | None]:
        p = self.state_dir / "runner_state.json"
        try:
            return json.loads(p.read_text()), _time.time() - p.stat().st_mtime
        except (OSError, ValueError):
            return {}, None

    def _account(self) -> dict:
        if self._acct_cache and _time.monotonic() - self._acct_cache[0] < 30:
            return self._acct_cache[1]
        try:
            a = self.src.account()
            pos = self.src.positions()
            d = {"ok": True, "balance": a.balance, "equity": a.equity,
                 "positions": [{"id": p.id, "symbol": p.symbol, "side": "long" if p.side > 0 else "short",
                                "qty": p.qty, "entry": p.entry,
                                "opened": p.opened.isoformat() if p.opened is not None else None,
                                "bot": p.tag == "atlas"} for p in pos]}
        except Exception as e:  # noqa: BLE001
            d = {"ok": False, "error": repr(e)[:200], "positions": []}
        self._acct_cache = (_time.monotonic(), d)
        return d

    def ny_day(self, now: pd.Timestamp | None = None) -> str:
        n = (now or self.clock()).tz_convert(self.tz)
        return str((n + pd.Timedelta(hours=6)).date())      # NY trading day starts 18:00

    def bot(self) -> dict:
        now = self.clock()
        st, age = self._runner_state()
        risk = st.get("risk", {})
        extra = st.get("extra", {})
        acct = self._account()
        ctrl = C.load(self.state_dir)
        tracked = extra.get("tracked", {})
        value = {}
        if self.bot_cfg is not None:
            for name, ins in self.bot_cfg.instruments.items():
                value[name] = ins.value_per_point
        sym_to_key = {i["symbol"]: i["key"] for i in self.instruments if i["available"]}
        for p in acct.get("positions", []):
            rec = tracked.get(p["id"], {})
            p["strategy"] = rec.get("strategy") or ("manual" if not p["bot"] else "bot")
            k = sym_to_key.get(p["symbol"])
            px = None
            if k:
                df = self.bars(k, "1m")
                px = float(df["close"].iloc[-1]) if len(df) else None
            p["price"] = px
            vpp = value.get(p["symbol"])
            if px is not None and vpp:
                p["pnl"] = round((px - p["entry"]) * (1 if p["side"] == "long" else -1) * p["qty"] * vpp, 2)
        rows = self._journal()
        day = self.ny_day(now)
        today = []
        for r in rows:
            try:
                ct = pd.Timestamp(r["close_time"])
                if ct.tzinfo is None:
                    ct = ct.tz_localize("UTC")
                r["_close"] = ct
                if self.ny_day(ct.tz_convert("UTC")) == day:
                    today.append(r)
            except Exception:  # noqa: BLE001
                continue
        losses = [r for r in rows if "_close" in r and float(r.get("pnl") or 0) < 0]
        last_loss = max((r["_close"] for r in losses), default=None)
        mins_since_loss = None if last_loss is None else (now - last_loss.tz_convert("UTC")).total_seconds() / 60
        streak = 0
        for r in reversed(today):
            if float(r.get("pnl") or 0) < 0:
                streak += 1
            else:
                break
        models = []
        if self.bot_cfg is not None:
            for spec in self.bot_cfg.strategies:
                mine = [r for r in rows if r.get("strategy") == spec.id]
                rs = [float(r["r"]) for r in mine if r.get("r") not in ("", None)]
                models.append({
                    "id": spec.id, "name": spec.name, "tf": spec.timeframe,
                    "instruments": spec.instruments, "hours": spec.params.get("hours", "config"),
                    "risk_pct": spec.risk_pct or self.bot_cfg.risk.risk_per_trade_pct,
                    "on": spec.id not in ctrl["disabled"],
                    "trades": len(mine), "wins": sum(float(r.get("pnl") or 0) > 0 for r in mine),
                    "net": round(sum(float(r.get("pnl") or 0) for r in mine), 2),
                    "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
                    "today": sum(1 for r in today if r.get("strategy") == spec.id),
                })
        sod = float(risk.get("sod_balance") or 0)
        eq = acct.get("equity")
        day_pnl = None if (eq is None or not sod) else round(eq - sod, 2)
        killed = (self.state_dir / "KILL").exists()
        out = {
            "alive": age is not None and age < 180, "state_age_s": None if age is None else round(age),
            "killed": killed, "account": acct, "sod_balance": sod or None, "day_pnl": day_pnl,
            "day_pnl_pct": None if day_pnl is None or not sod else round(day_pnl / sod * 100, 2),
            "trades_today": len(today), "losses_in_row": max(streak, int(risk.get("consec_losses") or 0)),
            "minutes_since_loss": None if mins_since_loss is None else round(mins_since_loss),
            "day_halt": risk.get("day_halt"), "hard_halt": risk.get("hard_halt"),
            "cooldown_until": risk.get("cooldown_until"),
            "cooldown_left_min": self._cooldown_left(risk.get("cooldown_until"), now),
            "models": models, "controls": {k: ctrl[k] for k in ("disabled", "paused", "lockout_day", "lockout_reason")},
            "control_log": list(reversed(ctrl.get("log", [])[-15:])),
            "today": [{k: v for k, v in r.items() if not k.startswith("_")} for r in today][-20:],
            "guardrails": {"daily_loss_lockout_pct": self.guard.daily_loss_pct,
                           "loss_streak_lockout": self.guard.loss_streak,
                           "cooldown_after_loss_min": self.guard.cooldown_min},
            "risk_per_trade_pct": self.bot_cfg.risk.risk_per_trade_pct if self.bot_cfg else None,
            "account_id": (self.bot_cfg.tradelocker.get("demo_account_ids") or [None])[0] if self.bot_cfg else None,
        }
        out["locked_today"] = ctrl.get("lockout_day") == day
        self.guard_tick(out, now)
        return out

    @staticmethod
    def _cooldown_left(until, now: pd.Timestamp) -> float | None:
        """Minutes left in the BOT's own pause after a loss (risk state cooldown_until), or None."""
        if not until:
            return None
        try:
            t = pd.Timestamp(until)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        except Exception:  # noqa: BLE001
            return None
        left = (t - now).total_seconds() / 60
        return round(left, 1) if left > 0 else None

    # ------------------------------------------------------------------ guardrails
    def guard_tick(self, bot: dict, now: pd.Timestamp) -> None:
        """Trip the daily lockout when a limit is hit. Only ever makes the bot safer."""
        if bot.get("locked_today"):
            return
        sod = bot.get("sod_balance") or 0
        eq = bot["account"].get("equity")
        if eq is None:
            return
        reason = self.guard.trip(sod, eq, bot.get("losses_in_row") or 0)
        if not reason:
            return
        with C._lock:
            d = C.load(self.state_dir)
            d["lockout_day"], d["lockout_reason"] = self.ny_day(now), reason
            C.record(d, "guardrail", f"LOCKOUT for the rest of the day: {reason}", now.to_pydatetime())
            C.save(self.state_dir, d)
        bot["locked_today"] = True
        bot["controls"]["lockout_day"], bot["controls"]["lockout_reason"] = self.ny_day(now), reason
        log.warning("desk lockout: %s", reason)

    def set_control(self, action: str, who: str, model: str | None = None) -> tuple[bool, str]:
        """Apply a control from the page. Returns (ok, message). Guardrails decide what may loosen."""
        now = self.clock()
        bot = self.bot()
        day = self.ny_day(now)
        with C._lock:
            d = C.load(self.state_dir)
            known = {m["id"] for m in bot["models"]}
            if action == "model_off":
                if model not in known:
                    return False, f"unknown model {model}"
                if model not in d["disabled"]:
                    d["disabled"].append(model)
                C.record(d, who, f"switched OFF {model}", now.to_pydatetime())
                msg = f"{model} is off. Open trades are still managed."
            elif action == "pause":
                d["paused"] = True
                C.record(d, who, "PAUSED all new bot entries", now.to_pydatetime())
                msg = "All new bot entries are paused. Open trades are still managed."
            elif action in ("model_on", "resume"):
                why = self.guard.can_loosen(d, day, bot.get("minutes_since_loss"))
                if not why and bot.get("cooldown_left_min"):
                    why = f"the bot is cooling down after a loss: {bot['cooldown_left_min']:.0f} more minutes"
                if why:
                    C.record(d, who, f"BLOCKED {action} {model or ''}: {why}", now.to_pydatetime())
                    C.save(self.state_dir, d)
                    return False, why
                if action == "model_on":
                    if model not in known:
                        return False, f"unknown model {model}"
                    d["disabled"] = [m for m in d["disabled"] if m != model]
                    C.record(d, who, f"switched ON {model}", now.to_pydatetime())
                    msg = f"{model} is on."
                else:
                    d["paused"] = False
                    C.record(d, who, "RESUMED bot entries", now.to_pydatetime())
                    msg = "Bot entries resumed."
            elif action == "kill":
                (self.state_dir / "KILL").write_text(f"desk {now.isoformat()} {who}\n")
                C.record(d, who, "EMERGENCY STOP (kill switch): close everything, stop the bot",
                         now.to_pydatetime())
                msg = ("Kill switch set. The bot closes every position on its next cycle and stops. "
                       "Restarting it needs the server.")
            else:
                return False, f"unknown action {action}"
            C.save(self.state_dir, d)
        return True, msg
