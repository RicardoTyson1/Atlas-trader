"""Dollar index from CME currency futures, 10Y T-Note, gold correlation, and news blackout windows."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from atlas_trader.desk import analysis as A
from atlas_trader.desk.databento_live import DXY_WEIGHTS, DatabentoLive, dollar_index
from atlas_trader.desk.feed import DemoSource, DeskFeed

from test_desk_databento import NOW, fake_db

# spot-like rates, as CME quotes them: US dollars per unit of the foreign currency
SPOT = {"eurusd": 1.08, "usdjpy": 150.0, "gbpusd": 1.27, "usdcad": 1.36, "usdsek": 10.5, "usdchf": 0.88}
LEGS = {"eur": SPOT["eurusd"], "jpy": 1 / SPOT["usdjpy"], "gbp": SPOT["gbpusd"], "cad": 1 / SPOT["usdcad"],
        "sek": 1 / SPOT["usdsek"], "chf": 1 / SPOT["usdchf"]}


def _ice_dxy(s):
    return (50.14348112 * s["eurusd"] ** -0.576 * s["usdjpy"] ** 0.136 * s["gbpusd"] ** -0.119
            * s["usdcad"] ** 0.091 * s["usdsek"] ** 0.042 * s["usdchf"] ** 0.036)


def _flat(px, n=5, start="2026-11-18 14:00"):
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"open": px, "high": px * 1.0001, "low": px * 0.9999, "close": px, "volume": 1.0}, index=idx)


def test_dollar_index_matches_the_ice_formula():
    out = dollar_index({k: _flat(v) for k, v in LEGS.items()})
    assert len(out) == 5
    assert abs(out["close"].iloc[-1] - _ice_dxy(SPOT)) < 1e-6
    r = out.iloc[-1]
    assert r.low <= r.open <= r.high and r.low <= r.close <= r.high and r.high > r.low


def test_dollar_index_undoes_a_yen_display_factor():
    legs = {k: _flat(v) for k, v in LEGS.items()}
    legs["jpy"] = _flat(LEGS["jpy"] * 1e4)           # yen quoted as 66.67 instead of 0.006667
    assert abs(dollar_index(legs)["close"].iloc[-1] - _ice_dxy(SPOT)) < 1e-6


def test_dollar_index_moves_against_the_euro():
    legs = {k: _flat(v) for k, v in LEGS.items()}
    up = dollar_index(legs)["close"].iloc[-1]
    legs["eur"] = _flat(LEGS["eur"] * 1.01)
    assert dollar_index(legs)["close"].iloc[-1] < up


def test_missing_leg_needs_a_fallback_and_gaps_keep_the_last_close():
    legs = {k: _flat(v) for k, v in LEGS.items() if k != "sek"}
    assert dollar_index(legs).empty
    out = dollar_index(legs, {"sek": LEGS["sek"]})
    assert abs(out["close"].iloc[-1] - _ice_dxy(SPOT)) < 1e-6
    legs = {k: _flat(v) for k, v in LEGS.items()}
    legs["chf"] = legs["chf"].iloc[:2]               # thin leg stops printing: last close carries on
    assert len(dollar_index(legs)) == 5 and not dollar_index(legs).isna().any().any()


DX_SYMBOLS = {"eur": "6E.v.0", "jpy": "6J.v.0", "gbp": "6B.v.0", "cad": "6C.v.0", "sek": "SEK.v.0", "chf": "6S.v.0"}


def _live(**kw):
    mod, calls = fake_db(NOW, **kw)
    live = DatabentoLive("k", {"gold": "GC.v.0", "zn": "ZN.v.0"}, client_factory=mod, clock=lambda: NOW,
                         dollar_index=DX_SYMBOLS)
    return live, calls


def test_live_feed_subscribes_the_legs_and_builds_dxy():
    live, calls = _live()
    assert set(DX_SYMBOLS.values()) <= set(live.symbols.values())
    assert live.covers("dxy") and not live.has("dxy")
    live._load_history()
    assert ("ohlcv-1m", "6J.v.0") in calls
    assert live.has("dxy") and live.has("zn")
    b = live.bars("dxy", "15m", 400)
    assert len(b) > 100 and not b.isna().any().any() and 20 < b["close"].iloc[-1] < 400
    info = live.info("dxy")
    assert info["synthetic"] and info["approx_legs"] == []


def test_over_cap_history_drops_the_dollar_legs_not_gold():
    live, calls = _live(cost=0.0)
    # price: $1 per symbol per schema pair; cap 10 -> 8 symbols too dear, 2 core fine
    mod = live._factory
    orig = mod.Historical

    class H(orig):
        def __init__(self, key):
            super().__init__(key)
            self.metadata.get_cost = lambda **kw: 0.5 * len(kw["symbols"])
    mod.Historical = H
    live.max_usd = 3.0
    live._load_history()
    assert live.has("gold") and live.has("zn") and not live.has("dxy")
    # a leg that then streams live still comes in
    for leg in DX_SYMBOLS:
        live.on_second(f"dx_{leg}", NOW, 1.0, 1.0, 1.0, 1.0, 1)
    assert live.has("dxy")


def test_desk_shows_zn_and_dxy_from_live_without_broker_and_logs_no_reads(tmp_path):
    live, _ = _live()
    live._load_history()
    cfg = {"instruments": [{"key": "gold", "name": "Gold", "symbols": ["XAUUSD"]},
                           {"key": "dxy", "name": "Dollar index", "symbols": ["NODXY"], "reads": False},
                           {"key": "zn", "name": "10Y T-Note", "symbols": [], "reads": False}],
           "smt_pairs": [["gold", "dxy", True], ["gold", "zn", False]]}
    feed = DeskFeed(cfg, DemoSource(), state_dir=tmp_path, live=live, clock=lambda: NOW)
    assert feed.inst("zn")["available"] and feed.inst("zn")["symbol"] is None
    snap = feed.analyse("zn", "5m")
    assert snap["available"] and snap["price"] is not None and snap.get("broker_price") is None
    assert feed.background_once() == 1                     # gold only: context markets log no reads
    feed._track_alert({**snap, "checklist": {"direction": "long", "score": 6, "of": 6, "checks": []}}, NOW)
    assert not (tmp_path / "desk_alerts.json").exists() or all(
        r["key"] != "zn" for r in json.loads((tmp_path / "desk_alerts.json").read_text()))
    assert all(x["signal"] in ("none", "bullish", "bearish") and "not available" not in x["detail"]
               for x in feed.smt())
    m = feed.macro()
    assert [x["key"] for x in m["markets"]] == ["dxy", "zn"] and all(x["available"] for x in m["markets"])
    assert len(m["corr"]) == 4 and m["markets"][0]["feed"]["synthetic"]


def test_return_corr_signs():
    idx = pd.date_range("2026-11-18", periods=200, freq="15min", tz="UTC")
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.001, 200)
    a = pd.DataFrame({"close": 100 * np.exp(np.cumsum(r))}, index=idx)
    b = pd.DataFrame({"close": 50 * np.exp(np.cumsum(-r + rng.normal(0, 0.0003, 200)))}, index=idx)
    res = A.return_corr(a, b, 96)
    assert res["r"] < -0.8 and res["n"] == 96
    assert A.corr_note(res["r"]).startswith("strong inverse")
    assert A.return_corr(a, b.iloc[:5], 96) is None


def test_news_rows_carry_the_bots_windows(tmp_path):
    class NB:
        minutes_before, minutes_after, no_entry_minutes_before = 15, 15, 30

    class Cfg:
        news = NB()

        class risk:
            cooldown_after_loss_minutes = 30
    ev = pd.Timestamp("2026-11-18 13:30", tz="UTC")
    (tmp_path / "news_events.json").write_text(json.dumps([{"time": ev.isoformat(), "title": "CPI m/m"}]))
    feed = DeskFeed({"instruments": []}, DemoSource(), bot_cfg=Cfg(), state_dir=tmp_path,
                    clock=lambda: ev - pd.Timedelta(minutes=40))
    n = feed.news(feed.clock())
    row = n["upcoming"][0]
    assert pd.Timestamp(row["no_entry_from"]) == ev - pd.Timedelta(minutes=45)
    assert pd.Timestamp(row["flat_from"]) == ev - pd.Timedelta(minutes=15)
    assert pd.Timestamp(row["until"]) == ev + pd.Timedelta(minutes=15)
    assert row["active"] and n["blackout"] and n["no_entry_minutes_before"] == 30
