"""Master desk: analysis reads, guardrails, controls and the runner hook."""
import json

import numpy as np
import pandas as pd
import pytest

from atlas_trader.brokers.sim import SimBroker
from atlas_trader.data import synthetic_intraday
from atlas_trader.desk import analysis as A
from atlas_trader.desk import controls as C
from atlas_trader.desk.feed import DemoSource, DeskFeed, ReadOnlyBroker
from atlas_trader.notify import MemoryNotifier
from atlas_trader.runner import Runner

UTC = "UTC"


def bars(prices, start="2026-11-02 00:00", freq="1h", spread=0.5):
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz=UTC)
    p = np.asarray(prices, float)
    o = np.r_[p[0], p[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, p) + spread, "low": np.minimum(o, p) - spread,
                         "close": p, "volume": 100.0}, index=idx)


# ------------------------------------------------------------------ RSI bands (his numbers)
@pytest.mark.parametrize("v,zone", [(18.0, "buy"), (16.5, "buy"), (20.0, "buy"), (15.0, "past_buy"),
                                    (82.0, "sell"), (86.0, "past_sell"), (50.0, "neutral"), (None, "none")])
def test_rsi_zone_uses_his_bands(v, zone):
    assert A.rsi_zone(v) == zone


def test_rsi_grid_flags_a_sell_extreme_after_a_rally():
    df = bars(list(np.linspace(100, 160, 60)) + [159.5, 160.5, 161])
    g = A.rsi_grid({"5m": df})[0]
    assert g["rsi"] > 80 and g["zone"] in ("sell", "past_sell")


# ------------------------------------------------------------------ structure, FVG, OB
def test_structure_reads_an_uptrend():
    up = [100, 102, 101, 104, 103, 106, 105, 108, 107, 110, 109, 112, 111, 114, 113, 116]
    s = A.structure(bars(up, spread=0.1), k=1)
    assert s["trend"] == "up" and s["last_break"]["dir"] == "up"


def test_fvg_found_and_dropped_once_filled():
    df = bars([100] * 20 + [100, 101, 106, 107, 108], spread=0.2)
    gaps = A.fvgs(df, min_atr=0.0)
    assert any(g["dir"] == "bull" for g in gaps)
    filled = bars([100] * 20 + [100, 101, 106, 107, 108, 95], spread=0.2)
    assert not [g for g in A.fvgs(filled, min_atr=0.0) if g["dir"] == "bull" and g["bottom"] > 100]


def test_order_block_is_last_opposite_candle_before_displacement():
    p = [100, 101, 100.5, 101.5, 101, 102, 101.5, 102.5, 102, 101.8] * 3 + [101.5, 108, 109, 110]
    df = bars(p, spread=0.2)
    obs = A.order_blocks(df, disp_atr=1.0)
    assert obs and obs[-1]["dir"] == "bull"


# ------------------------------------------------------------------ daily Fib + solid candle bias
def _daily_down_leg():
    # high on day 3, low on day 8 (swings need 2 days each side), then a bounce
    closes = [110, 115, 120, 116, 112, 108, 104, 100, 103, 106, 107]
    idx = pd.date_range("2026-10-20", periods=len(closes), freq="1D", tz=UTC)
    o = np.r_[closes[0], closes[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, closes) + 1, "low": np.minimum(o, closes) - 1,
                       "close": closes, "volume": 1.0}, index=idx)
    df.iloc[2, df.columns.get_loc("high")] = 125        # swing high
    df.iloc[7, df.columns.get_loc("low")] = 95          # swing low
    return df


def test_daily_fib_down_leg_gives_demand_and_golden_ratio():
    f = A.daily_fib(_daily_down_leg())
    assert f["leg"] == "down" and f["zone_kind"] == "demand"
    assert f["golden"] == pytest.approx(95 + 0.618 * 30)
    assert f["zone_low"] == 95


def test_solid_4h_candle_out_of_demand_sets_long_bias():
    f = A.daily_fib(_daily_down_leg())
    t0 = pd.Timestamp(f["low_time"]) + pd.Timedelta(hours=4)
    h4 = pd.DataFrame({"open": [f["zone_low"] + 1], "high": [f["zone_high"] + 3.2],
                       "low": [f["zone_low"] + 0.5], "close": [f["zone_high"] + 3.0], "volume": [1.0]},
                      index=[t0])
    s = A.solid_candle_bias(h4, f, price=f["zone_high"] + 1)
    assert s["dir"] == "long" and s["retesting"]


def test_checklist_needs_bias_before_anything_counts():
    ck = A.rick_checklist({"bias": "neutral", "why": ""}, {"dir": "none"}, [], [], 100.0, {"blackout": False})
    assert ck["direction"] is None and ck["score"] == 1          # only "no news blackout"


def test_smt_flags_unconfirmed_lower_low():
    a = bars([100] * 70 + [99, 98, 97, 96] + [97] * 22, freq="15min")
    b = bars([100] * 96, freq="15min")
    assert A.smt(a, b, lookback=48)["signal"] == "bullish"


# ------------------------------------------------------------------ controls and guardrails
def test_entry_block_reads_file(tmp_path):
    assert C.entry_block(tmp_path, "smc_ob", "2026-11-18") is None        # no file: no effect
    d = C.load(tmp_path)
    d["disabled"] = ["smc_ob"]
    C.save(tmp_path, d)
    assert "switched off" in C.entry_block(tmp_path, "smc_ob", "2026-11-18")
    assert C.entry_block(tmp_path, "donchian_trend_1h", "2026-11-18") is None
    d["lockout_day"], d["lockout_reason"] = "2026-11-18", "2 losses in a row"
    C.save(tmp_path, d)
    assert "lockout" in C.entry_block(tmp_path, "donchian_trend_1h", "2026-11-18")
    assert C.entry_block(tmp_path, "donchian_trend_1h", "2026-11-19") is None   # next day: clear


def test_corrupt_controls_file_never_blocks(tmp_path):
    (tmp_path / C.FILE).write_text("{not json")
    assert C.entry_block(tmp_path, "smc_ob", "2026-11-18") is None


def test_guardrails_match_his_rule_book():
    g = C.Guardrails({"daily_loss_lockout_pct": 2, "loss_streak_lockout": 2, "cooldown_after_loss_min": 30})
    assert g.trip(10000, 9790, 0)                     # -2.1%
    assert g.trip(10000, 9900, 2)                     # 2 losses in a row
    assert g.trip(10000, 9900, 1) is None
    assert "cool-down" in g.can_loosen({}, "2026-11-18", 10)
    assert g.can_loosen({}, "2026-11-18", 45) is None


class FakeCfg:
    """Just what the desk reads from the bot config."""

    def __init__(self):
        from atlas_trader.config import Instrument, StrategySpec
        self.strategies = [StrategySpec(name="smc_ob", instruments=["XAUUSD.R"]),
                           StrategySpec(name="donchian_trend", instruments=["XAUUSD.R"], label="donchian_trend_1h",
                                        risk_pct=0.5)]
        self.instruments = {"XAUUSD.R": Instrument(symbol="XAUUSD.R", value_per_point=100.0)}
        self.news = None

        class R:
            risk_per_trade_pct = 1.0
        self.risk = R()
        self.tradelocker = {"demo_account_ids": [2594911]}


def _feed(tmp_path, journal_rows=(), sod=10000.0):
    st = tmp_path / "state"
    st.mkdir()
    (st / "runner_state.json").write_text(json.dumps({"risk": {"sod_balance": sod, "consec_losses": 0}, "extra": {}}))
    if journal_rows:
        lines = ["close_time,strategy,symbol,side,entry_time,pnl,risk_usd,r"] + list(journal_rows)
        (st / "journal.csv").write_text("\n".join(lines) + "\n")
    cfg = {"instruments": [{"key": "gold", "name": "Gold", "symbols": ["XAUUSD.R"]},
                           {"key": "oil", "name": "Oil", "symbols": ["NOPE.R"]}],
           "guardrails": {"daily_loss_lockout_pct": 2, "loss_streak_lockout": 2, "cooldown_after_loss_min": 30}}
    return DeskFeed(cfg, DemoSource(), bot_cfg=FakeCfg(), state_dir=st), st


def test_desk_can_switch_off_any_time_but_on_only_outside_cooldown(tmp_path):
    now = pd.Timestamp.now(tz=UTC)
    loss = f"{(now - pd.Timedelta(minutes=5)).isoformat()},smc_ob,XAUUSD.R,long,{now.isoformat()},-50,95,-0.5"
    feed, st = _feed(tmp_path, [loss], sod=9600.0)        # demo equity 9548 vs 9600: -0.5%, no lockout
    ok, _ = feed.set_control("model_off", "test", "smc_ob")
    assert ok and "smc_ob" in C.load(st)["disabled"]
    ok, msg = feed.set_control("model_on", "test", "smc_ob")
    assert not ok and "cool-down" in msg
    assert "smc_ob" in C.load(st)["disabled"]
    assert feed.set_control("pause", "test")[0]
    assert not feed.set_control("resume", "test")[0]
    assert not feed.set_control("model_off", "test", "rm -rf")[0]


def test_daily_loss_trips_lockout(tmp_path):
    feed, st = _feed(tmp_path, sod=10000.0)                # demo equity 9548: -4.5%
    b = feed.bot()
    assert b["locked_today"]
    d = C.load(st)
    assert d["lockout_day"] == feed.ny_day() and "down" in d["lockout_reason"]
    assert not feed.set_control("resume", "test")[0]


def test_unavailable_market_is_reported_not_crashing(tmp_path):
    feed, _ = _feed(tmp_path, sod=9550.0)
    assert feed.inst("oil")["available"] is False
    assert feed.analyse("oil")["available"] is False
    snap = feed.analyse("gold", "15m")
    assert snap["available"] and snap["price"] and snap["checklist"]["of"] == 6
    assert len(snap["chart"]["bars"]) > 50


def test_read_only_broker_has_no_order_methods():
    ro = ReadOnlyBroker(object())
    for name in ("market_bracket", "close_all", "close_position", "modify_stop", "close_partial"):
        assert not hasattr(ro, name)


# ------------------------------------------------------------------ the runner honours the desk
def test_runner_skips_new_entries_when_paused_from_desk(cfg):
    base = synthetic_intraday(days=5, seed=11)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    d = C.load(runner.state_dir)
    d["paused"] = True
    C.save(runner.state_dir, d)
    t = base.index[0] + pd.Timedelta("5min")
    while t <= base.index[-1]:
        broker.set_time(t)
        runner.step(t)
        t += pd.Timedelta("5min")
    assert not [f for f in broker.fills if f["action"] == "open"]
