"""TradingView webhook: validation, symbol mapping, de-duplication, staleness, slippage guard."""
import json
import threading
import time
import urllib.error
import urllib.request

import pandas as pd

from atlas_trader.brokers.sim import SimBroker
from atlas_trader.data import synthetic_intraday
from atlas_trader.notify import MemoryNotifier
from atlas_trader.runner import Runner
from atlas_trader.webhook import AlertGate, handle_alert, resolve_symbol, serve, validate_alert

S = "s3cret"


def make(cfg):
    cfg.strategies = []
    cfg.webhook = {"symbol_aliases": {"USTEC": "NAS100"}, "max_entry_slip_r": 0.25}
    base = synthetic_intraday(days=1, seed=11)
    broker = SimBroker({"NAS100": base}, cfg.starting_balance, {"NAS100": 1.0})
    runner = Runner(cfg, broker, MemoryNotifier())
    runner.clock = lambda: broker.now
    t = base.index[0] + pd.Timedelta(minutes=60)       # 10:30 NY, inside the entry window
    broker.set_time(t)
    return broker, runner


def opens(broker):
    return [f for f in broker.fills if f["action"] == "open"]


def test_symbol_mapping(cfg):
    _, runner = make(cfg)
    assert resolve_symbol(runner, "NAS100") == "NAS100"
    assert resolve_symbol(runner, "pepperstone:nas100") == "NAS100"
    assert resolve_symbol(runner, "USTEC") == "NAS100"
    assert resolve_symbol(runner, "XAUUSD") is None


def test_rejects_bad_messages(cfg):
    _, runner = make(cfg)
    assert validate_alert(runner, {"secret": S}, "")[0] == 500
    assert validate_alert(runner, {"secret": "bad"}, S)[0] == 401
    assert validate_alert(runner, {"secret": S, "symbol": "XYZ", "action": "buy"}, S)[0] == 400
    assert validate_alert(runner, {"secret": S, "symbol": "NAS100", "action": "hold"}, S)[0] == 400
    assert validate_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy"}, S)[0] == 400  # no stop
    assert validate_alert(runner, [1, 2], S)[0] == 400


def test_buy_enters_with_stop_and_target(cfg):
    broker, runner = make(cfg)
    bid, ask = broker.quote("NAS100")
    code, msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy",
                                      "stop": ask - 20, "rr": 2}, S)
    assert (code, msg) == (200, "entered")
    pos = broker.positions()
    assert len(pos) == 1 and pos[0].has_stop
    # a second alert while a trade is open is not added on top
    code, msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy",
                                      "stop": ask - 20}, S)
    assert msg.startswith("skipped")
    assert len(opens(broker)) == 1


def test_duplicate_id_is_ignored(cfg):
    broker, runner = make(cfg)
    gate = AlertGate()
    _, ask = broker.quote("NAS100")
    p = {"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20, "id": "SB-1"}
    assert validate_alert(runner, p, S, gate=gate)[0] == 202
    code, msg, order = validate_alert(runner, p, S, gate=gate)
    assert order is None and "duplicate" in msg


def test_stale_alert_is_ignored(cfg):
    broker, runner = make(cfg)
    _, ask = broker.quote("NAS100")
    now = time.time() * 1000
    p = {"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20}
    assert validate_alert(runner, {**p, "time": now - 10_000}, S, now_ms=now)[0] == 202
    code, msg, order = validate_alert(runner, {**p, "time": now - 600_000}, S, now_ms=now)
    assert order is None and "old" in msg
    assert validate_alert(runner, {**p, "time": (now - 10_000) / 1000}, S, now_ms=now)[0] == 202  # seconds


def test_entry_slippage_guard(cfg):
    broker, runner = make(cfg)
    _, ask = broker.quote("NAS100")
    # setup wanted 10 points lower with a 20-point risk: price is 0.5R past it -> skip
    msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy",
                                "stop": ask - 30, "entry": ask - 10}, S)[1]
    assert "too far" in msg and not opens(broker)
    # within 0.25R -> enter
    msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy",
                                "stop": ask - 20, "entry": ask - 2}, S)[1]
    assert msg == "entered"


def test_risk_rules_still_apply(cfg):
    broker, runner = make(cfg)
    _, ask = broker.quote("NAS100")
    # outside trading hours: 05:00 NY is before the session's trade_start
    t = pd.Timestamp(broker.now).tz_convert("America/New_York").normalize() + pd.Timedelta(hours=5)
    broker.set_time(t.tz_convert("UTC"))
    msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20}, S)[1]
    assert msg.startswith("skipped") and not opens(broker)


def test_http_server_answers_fast_and_trades(cfg, monkeypatch):
    broker, runner = make(cfg)
    monkeypatch.setenv("WEBHOOK_SECRET", S)
    port = 18787
    threading.Thread(target=serve, args=(runner, "127.0.0.1", port), daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/tv", timeout=1).read()
            break
        except OSError:
            time.sleep(0.05)
    _, ask = broker.quote("NAS100")
    body = json.dumps({"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20, "id": "x1"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/tv", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        assert r.status == 202
    for _ in range(100):
        if broker.positions():
            break
        time.sleep(0.05)
    assert len(broker.positions()) == 1
    # GET must not leak account details
    assert urllib.request.urlopen(f"http://127.0.0.1:{port}/tv", timeout=1).read() == b"atlas webhook ok"


def test_close_alert_never_touches_manual_trades(cfg):
    broker, runner = make(cfg)
    broker.add_manual("NAS100", 1, 1.0, stop=broker.last_price("NAS100") - 30)
    code, msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "close"}, S)
    assert code == 200 and msg.startswith("skipped:no bot position")
    assert len(broker.positions()) == 1                       # the manual trade is still open


def test_close_alert_closes_bot_trade(cfg):
    broker, runner = make(cfg)
    _, ask = broker.quote("NAS100")
    assert handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20}, S)[1] == "entered"
    code, msg = handle_alert(runner, {"secret": S, "symbol": "NAS100", "action": "close"}, S)
    assert (code, msg) == (200, "closed 1 bot position(s)")
    assert not broker.positions()
    assert any("closed 1 bot position" in m for m in runner.notify.messages)


def test_allowlist_rejects_other_ips(cfg, monkeypatch):
    broker, runner = make(cfg)
    runner.cfg.webhook["allowed_ips"] = ["52.89.214.238"]
    monkeypatch.setenv("WEBHOOK_SECRET", S)
    port = 18788
    threading.Thread(target=serve, args=(runner, "127.0.0.1", port), daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/tv", timeout=1).read()
            break
        except OSError:
            time.sleep(0.05)
    _, ask = broker.quote("NAS100")
    body = json.dumps({"secret": S, "symbol": "NAS100", "action": "buy", "stop": ask - 20}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/tv", data=body, method="POST")
    try:
        urllib.request.urlopen(req, timeout=3)
        assert False, "expected 403"
    except urllib.error.HTTPError as e:
        assert e.code == 403
    time.sleep(0.2)
    assert not broker.positions()
