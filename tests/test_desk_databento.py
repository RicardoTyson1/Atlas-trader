"""Desk live feed from Databento: folding 1-second bars, history + live merge, 18:00 NY candles, cost cap,
and the desk falling back to broker prices. Uses a fake databento module (no network, no key)."""
from __future__ import annotations

import types

import numpy as np
import pandas as pd

from atlas_trader.desk.databento_live import DatabentoLive, resample


def _bars(start, periods, freq, base=100.0):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    c = base + np.arange(periods) * 0.1
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c,
                         "volume": np.ones(periods)}, index=idx)


class _Res:
    def __init__(self, df):
        self.df = df

    def to_df(self):
        return self.df


def fake_db(now, cost=0.05, live_records=None):
    end = now.floor("1min")
    calls = []

    class Meta:
        def get_dataset_range(self, dataset):
            return {"start": "2010-06-06", "end": end.isoformat(),
                    "schema": {"ohlcv-1m": {"end": end.isoformat()}, "ohlcv-1h": {"end": end.floor("1h").isoformat()}}}

        def get_cost(self, **kw):
            return cost / 2

    class TS:
        def get_range(self, schema, start, end, **kw):
            calls.append((schema, kw["symbols"][0]))
            if schema == "ohlcv-1h":
                n = int((pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta("1h"))
                return _Res(_bars(pd.Timestamp(start), n, "1h"))
            n = int((pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta("1min"))
            return _Res(_bars(pd.Timestamp(start), n, "1min", base=200.0))

    class Historical:
        def __init__(self, key):
            self.metadata, self.timeseries = Meta(), TS()

    class SymbolMappingMsg:
        def __init__(self, iid, sym):
            self.instrument_id, self.stype_in_symbol = iid, sym

    class OHLCVMsg:
        def __init__(self, iid, ts, px, vol=1):
            self.instrument_id, self.ts_event = iid, int(ts.value)
            self.open = self.high = self.low = self.close = int(px * 1e9)
            self.volume = vol

    class Live:
        def __init__(self, key):
            self.sub = None

        def subscribe(self, **kw):
            self.sub = kw

        def __iter__(self):
            return iter(live_records(SymbolMappingMsg, OHLCVMsg) if live_records else [])

        def stop(self):
            pass

    mod = types.SimpleNamespace(Historical=Historical, Live=Live, SymbolMappingMsg=SymbolMappingMsg,
                                OHLCVMsg=OHLCVMsg)
    return mod, calls


NOW = pd.Timestamp("2026-11-18 06:20:30", tz="UTC")


def test_seconds_fold_into_minutes():
    f = DatabentoLive("k", {"gold": "GC.v.0"}, client_factory=object(), clock=lambda: NOW)
    t = pd.Timestamp("2026-11-18 06:20:00", tz="UTC")
    f.on_second("gold", t, 10, 11, 9.5, 10.5, 2)
    f.on_second("gold", t + pd.Timedelta(seconds=5), 10.5, 12, 10, 11.5, 3)
    f.on_second("gold", t + pd.Timedelta(seconds=61), 11.5, 11.6, 11.4, 11.6, 1)
    m = f.minutes("gold")
    assert len(m) == 2
    r = m.iloc[0]
    assert (r.open, r.high, r.low, r.close, r.volume) == (10, 12, 9.5, 11.5, 5)
    assert f.age_seconds("gold") is not None and f.last_time("gold") == t + pd.Timedelta(seconds=61)


def test_4h_and_daily_start_at_18_ny():
    h = _bars("2026-11-10 00:00", 24 * 5, "1h")
    d = resample(h, "1D")
    hours_ny = {t.tz_convert("America/New_York").hour for t in d.index}
    assert hours_ny == {18}
    four = resample(h, "4H")
    assert {t.tz_convert("America/New_York").hour for t in four.index} <= {18, 22, 2, 6, 10, 14}


def test_history_then_live_replay_from_last_minute():
    def recs(Map, Bar):
        t = NOW.floor("1min")
        return [Map(7, "GC.v.0"), Bar(7, t, 250.0, 4), Bar(7, t + pd.Timedelta(seconds=1), 251.0, 1),
                Bar(99, t, 1.0)]  # unknown instrument ignored
    mod, calls = fake_db(NOW, live_records=recs)
    f = DatabentoLive("k", {"gold": "GC.v.0", "oil": "CL.v.0"}, client_factory=mod, clock=lambda: NOW)
    f._load_history()
    assert f.has("gold") and f.has("oil") and f.history_cost == 0.05
    assert ("ohlcv-1h", "GC.v.0") in calls and ("ohlcv-1m", "CL.v.0") in calls
    before = len(f.minutes("gold"))
    try:
        f._stream()
    except ConnectionError:
        pass                                            # session ended -> the thread would reconnect
    m = f.minutes("gold")
    assert m["close"].iloc[-1] == 251.0 and m["volume"].iloc[-1] == 5
    assert len(m) <= before + 1                         # replay rebuilt the last minute, no duplicates
    h1 = f.bars("gold", "1H", 400)
    assert h1.index.is_monotonic_increasing and not h1.index.duplicated().any()
    assert h1["close"].iloc[-1] == 251.0                # the forming hour includes the live second
    assert len(f.bars("gold", "1D", 160)) > 100 and len(f.bars("gold", "5m", 400)) == 400


def test_history_over_cost_cap_is_skipped():
    mod, calls = fake_db(NOW, cost=50.0)
    f = DatabentoLive("k", {"gold": "GC.v.0"}, client_factory=mod, clock=lambda: NOW, max_history_usd=2.0)
    f._load_history()
    assert not f.has("gold") and calls == [] and "over cap" in f.status


def test_desk_uses_live_feed_and_falls_back_to_broker(tmp_path):
    from atlas_trader.desk.feed import DemoSource, DeskFeed
    mod, _ = fake_db(NOW)
    live = DatabentoLive("k", {"gold": "GC.v.0"}, client_factory=mod, clock=lambda: NOW)
    live._load_history()
    cfg = {"instruments": [{"key": "gold", "name": "Gold", "symbols": ["XAUUSD"]},
                           {"key": "silver", "name": "Silver", "symbols": ["XAGUSD"]}]}
    feed = DeskFeed(cfg, DemoSource(), state_dir=tmp_path, live=live)
    assert feed.uses_live("gold") and not feed.uses_live("silver")
    snap = feed.analyse("gold", "5m")
    assert snap["feed"]["feed"] == "databento" and snap["feed"]["symbol"] == "GC.v.0"
    assert snap["broker_price"] is not None and abs(snap["basis"] - (snap["price"] - snap["broker_price"])) < 1e-4
    assert feed.analyse("silver", "5m")["feed"]["feed"] == "broker"
    assert "Databento" in feed.label
