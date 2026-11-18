"""Live CME futures bars for the desk, from Databento (GLBX.MDP3), kept up to date every second.

How it works (one background thread, small memory):
  1. History once at start: 1-hour bars (~240 days, for 1H/4H/1D) and 1-minute bars (~4 days, for
     1m/5m/15m) per market, continuous front month by volume (e.g. GC.v.0). The cost is asked first
     (a free call) and the download is skipped if it is over `max_history_usd`.
  2. Then a live session on 1-second bars (`ohlcv-1s`), replayed from where the history ends, folded
     into 1-minute bars as they arrive. The forming minute is updated every second.
  3. If the session drops, it reconnects and replays from the last minute it has (Databento keeps
     24 hours for replay), so no bars go missing.

The desk asks `bars(key, tf, count)`; 5m/15m come from the minutes, 1H/4H/1D from the hours plus the
minutes since the history ended. 4H and daily candles start at 18:00 New York (the CME day).
The API key is read from the environment and never logged.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import timedelta

import pandas as pd

log = logging.getLogger("atlas.desk.databento")

DATASET = "GLBX.MDP3"
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
COLS = ["open", "high", "low", "close", "volume"]
NY = "America/New_York"


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """1m/1h bars -> tf. 4H and 1D are anchored on the 18:00 New York CME day."""
    if df.empty:
        return df
    if tf in ("4H", "1D"):
        loc = df.index.tz_convert(NY)
        shifted = df.set_axis(loc + pd.Timedelta(hours=6))       # 18:00 NY -> 00:00
        rule = "4h" if tf == "4H" else "1D"
        out = shifted.resample(rule, label="left", closed="left").agg(AGG).dropna(subset=["close"])
        out.index = (out.index - pd.Timedelta(hours=6)).tz_convert("UTC")
        return out
    rule = {"5m": "5min", "15m": "15min", "1H": "1h"}[tf]
    return df.resample(rule, label="left", closed="left").agg(AGG).dropna(subset=["close"])


class DatabentoLive:
    label = "CME futures live (Databento)"
    demo = False

    def __init__(self, key: str, symbols: dict[str, str], minute_days: float = 4, hour_days: int = 240,
                 max_history_usd: float = 2.0, client_factory=None, clock=None):
        self.key = key
        self.symbols = dict(symbols)                     # desk key -> Databento symbol, e.g. gold -> GC.v.0
        self.by_symbol = {v: k for k, v in self.symbols.items()}
        self.minute_days = minute_days
        self.hour_days = hour_days
        self.max_usd = max_history_usd
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self._factory = client_factory                   # tests inject fakes; None = the real databento
        self._lock = threading.Lock()
        self._hours: dict[str, pd.DataFrame] = {}
        self._mins: dict[str, dict[int, list[float]]] = {k: {} for k in self.symbols}
        self._last_update: dict[str, float] = {}         # monotonic time of the last live record
        self._last_ts: dict[str, pd.Timestamp] = {}      # event time of the last live record
        self.loaded: set[str] = set()
        self.status = "starting"
        self.history_cost: float | None = None
        self._stop = threading.Event()
        self._live = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public
    def start(self) -> DatabentoLive:
        self._thread = threading.Thread(target=self._run, name="databento-live", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        live = self._live
        if live is not None:
            try:
                live.stop()
            except Exception:  # noqa: BLE001
                pass

    def has(self, key: str) -> bool:
        return key in self.loaded

    def age_seconds(self, key: str) -> float | None:
        t = self._last_update.get(key)
        return None if t is None else _time.monotonic() - t

    def last_time(self, key: str) -> pd.Timestamp | None:
        return self._last_ts.get(key)

    def info(self, key: str) -> dict:
        age = self.age_seconds(key)
        return {"feed": "databento", "symbol": self.symbols.get(key), "status": self.status,
                "live": age is not None and age < 120,
                "age_s": None if age is None else round(age, 1),
                "last_time": None if key not in self._last_ts else self._last_ts[key].isoformat()}

    def minutes(self, key: str) -> pd.DataFrame:
        with self._lock:
            rows = sorted(self._mins.get(key, {}).items())
        if not rows:
            return _empty()
        idx = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
        return pd.DataFrame([r[1] for r in rows], index=idx, columns=COLS)

    def bars(self, key: str, tf: str, count: int) -> pd.DataFrame:
        """Bars INCLUDING the one still forming (that is the point of a live feed)."""
        m = self.minutes(key)
        if tf == "1m":
            return m.tail(count)
        if tf in ("5m", "15m"):
            return resample(m, tf).tail(count)
        with self._lock:
            h = self._hours.get(key, _empty())
        if not m.empty:
            cut = m.index[0].floor("1h")
            h = pd.concat([h[h.index < cut], resample(m[m.index >= cut], "1H")])
        if tf == "1H":
            return h.tail(count)
        return resample(h, tf).tail(count)

    # ------------------------------------------------------------------ incoming data
    def on_second(self, key: str, ts: pd.Timestamp, o: float, hi: float, lo: float, c: float, v: float) -> None:
        """Fold one 1-second bar into its minute."""
        minute = int(ts.floor("1min").timestamp())
        with self._lock:
            book = self._mins.setdefault(key, {})
            bar = book.get(minute)
            if bar is None:
                book[minute] = [o, hi, lo, c, v]
            else:
                bar[1] = max(bar[1], hi)
                bar[2] = min(bar[2], lo)
                bar[3] = c
                bar[4] += v
            self._last_update[key] = _time.monotonic()
            self._last_ts[key] = ts

    def _trim(self) -> None:
        keep = int((self.clock() - pd.Timedelta(days=self.minute_days)).timestamp())
        with self._lock:
            for book in self._mins.values():
                for m in [m for m in book if m < keep]:
                    del book[m]

    # ------------------------------------------------------------------ the thread
    def _db(self):
        if self._factory is not None:
            return self._factory
        import databento as db
        return db

    def _history_safe(self, tries: int = 8, wait_s: float = 30) -> None:
        """Databento's history gateway sometimes times out (504): retry, waiting longer each time.
        Until it loads, the desk keeps showing broker prices for that market."""
        for i in range(1, tries + 1):
            try:
                self._load_history()
                return
            except Exception as e:  # noqa: BLE001
                log.warning("databento history failed (try %d/%d): %s: %s", i, tries, type(e).__name__,
                            str(e)[:120])
                self.status = "history failed"
            if self._stop.wait(wait_s * i):
                return

    def _run(self) -> None:
        # History in its own thread: if a download hangs, the live session still starts after 90 s.
        hist = threading.Thread(target=self._history_safe, name="databento-history", daemon=True)
        hist.start()
        hist.join(90)
        if hist.is_alive():
            log.warning("databento history still loading after 90 s; starting live anyway")
        backoff = 5
        while not self._stop.is_set():
            try:
                self._stream()
                backoff = 5
            except Exception as e:  # noqa: BLE001 - never kill the desk; reconnect
                log.warning("databento live dropped: %s: %s", type(e).__name__, str(e)[:200])
                self.status = "reconnecting"
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 120)

    def _load_history(self) -> None:
        db = self._db()
        h = db.Historical(self.key)
        rng = h.metadata.get_dataset_range(dataset=DATASET)
        sch = rng.get("schema", {}) if isinstance(rng, dict) else {}
        end_m = pd.Timestamp(sch.get("ohlcv-1m", {}).get("end") or rng["end"])
        end_h = pd.Timestamp(sch.get("ohlcv-1h", {}).get("end") or rng["end"])
        start_m = end_m - timedelta(days=self.minute_days)
        start_h = end_h - timedelta(days=self.hour_days)
        syms = list(self.symbols.values())
        kw = dict(dataset=DATASET, symbols=syms, stype_in="continuous")
        cost = (h.metadata.get_cost(schema="ohlcv-1m", start=start_m, end=end_m, **kw)
                + h.metadata.get_cost(schema="ohlcv-1h", start=start_h, end=end_h, **kw))
        self.history_cost = round(float(cost), 4)
        log.info("databento history cost $%.4f (cap $%.2f)", cost, self.max_usd)
        if cost > self.max_usd:
            self.status = f"history skipped (cost ${cost:.2f} over cap)"
            return
        # one request per schema for all markets (fast: ~5 s for 4 days of 1m on six markets)
        hh_all = h.timeseries.get_range(schema="ohlcv-1h", start=start_h, end=end_h, **kw).to_df()
        mm_all = h.timeseries.get_range(schema="ohlcv-1m", start=start_m, end=end_m, **kw).to_df()
        for sym in syms:
            key = self.by_symbol[sym]
            hh = self._clean(hh_all[hh_all["symbol"] == sym] if "symbol" in hh_all else hh_all)
            mm = self._clean(mm_all[mm_all["symbol"] == sym] if "symbol" in mm_all else mm_all)
            if hh.empty and mm.empty:
                log.warning("databento history %s (%s): no bars", key, sym)
                continue
            with self._lock:
                self._hours[key] = hh
                book = self._mins.setdefault(key, {})
                for t, r in zip(mm.index, mm.itertuples(index=False)):
                    # never overwrite minutes the live session already built
                    book.setdefault(int(t.timestamp()), [float(r.open), float(r.high), float(r.low),
                                                         float(r.close), float(r.volume)])
            self.loaded.add(key)
            log.info("databento history %s (%s): %d hours, %d minutes", key, sym, len(hh), len(mm))
        if self.status in ("starting", "history failed"):
            self.status = "history loaded"

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return _empty()
        df = df[COLS].astype(float)
        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _replay_start(self) -> pd.Timestamp:
        """Where the live replay starts: the last minute we hold (re-built from scratch), max 23 h back."""
        now = self.clock()
        floor = now - pd.Timedelta(hours=23)
        with self._lock:
            lasts = [max(b) for b in self._mins.values() if b]
        start = pd.Timestamp(min(lasts), unit="s", tz="UTC") if lasts else now - pd.Timedelta(minutes=30)
        start = max(start, floor)
        cut = int(start.timestamp())
        with self._lock:                                  # the replay rebuilds these minutes
            for book in self._mins.values():
                for m in [m for m in book if m >= cut]:
                    del book[m]
        return start

    def _stream(self) -> None:
        db = self._db()
        live = db.Live(key=self.key)
        self._live = live
        start = self._replay_start()
        live.subscribe(dataset=DATASET, schema="ohlcv-1s", symbols=list(self.symbols.values()),
                       stype_in="continuous", start=start.isoformat())
        self.status = "live"
        names: dict[int, str] = {}
        n = 0
        for rec in live:
            if self._stop.is_set():
                break
            if isinstance(rec, db.SymbolMappingMsg):
                key = self.by_symbol.get(rec.stype_in_symbol)
                if key:
                    names[rec.instrument_id] = key
                continue
            if isinstance(rec, db.OHLCVMsg):
                key = names.get(rec.instrument_id)
                if key is None:
                    continue
                px = lambda v: round(v / 1e9, 9)
                self.on_second(key, pd.Timestamp(rec.ts_event, unit="ns", tz="UTC"), px(rec.open),
                               px(rec.high), px(rec.low), px(rec.close), float(rec.volume))
                n += 1
                if n % 20000 == 0:
                    self._trim()
        self._live = None
        if not self._stop.is_set():
            raise ConnectionError("live session ended")
