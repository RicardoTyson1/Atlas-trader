"""TradingView alert receiver.

TradingView only delivers webhooks to ports 80 and 443, so the bot listens on
port 80 (config `webhook.port`). Point the alert's Webhook URL at

    http://<your-server-ip>/tv

The Atlas Silver Bullet indicator (pine/atlas_silver_bullet.pine) builds the
message itself. A hand-written alert message looks like:

    {"secret": "YOUR_WEBHOOK_SECRET", "symbol": "XAUUSD", "action": "buy",
     "stop": 4100.5, "rr": 2}

Fields
  secret   required, must equal WEBHOOK_SECRET in the server's .env
  symbol   bot name (XAUUSD.R) or TradingView name (XAUUSD, OANDA:XAUUSD)
  action   buy | sell | close
  stop     required for buy/sell (absolute price)
  target   optional absolute price; otherwise rr (default from config) x risk
  rr       optional reward:risk used when no target is given
  entry    optional: the price the setup wanted. If the market is already more
           than `webhook.max_entry_slip_r` x risk past it, the trade is skipped.
  id       optional unique alert id; a repeated id is ignored (no double entries)
  time     optional send time in ms ({{timenow}}); alerts older than
           `webhook.max_alert_age_seconds` are ignored

Every alert goes through the same RiskManager as the bot's own strategies
(sizing, cooldown, daily stop, trading hours, one position, no adding).

TradingView gives up on a webhook after ~3 seconds, so the server checks the
secret and the message, answers 202 at once, and places the trade in the
background. The result (entered / skipped and why) goes to Telegram.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .runner import Runner

log = logging.getLogger("atlas")

# TradingView's published webhook source addresses (optional allowlist, see config).
TRADINGVIEW_IPS = ("52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7")


class AlertGate:
    """Remembers recent alert ids so a repeated alert never opens a second trade."""

    def __init__(self, size: int = 500) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def first_time(self, alert_id: str) -> bool:
        with self._lock:
            if alert_id in self._seen:
                return False
            self._seen[alert_id] = time.time()
            while len(self._seen) > self._size:
                self._seen.popitem(last=False)
            return True


def resolve_symbol(runner: Runner, raw: Any) -> str | None:
    """Map a TradingView ticker (OANDA:XAUUSD, XAUUSD) to the bot's instrument (XAUUSD.R)."""
    s = str(raw or "").strip().upper()
    if not s:
        return None
    s = s.split(":")[-1]
    inst = runner.cfg.instruments
    if s in inst:
        return s
    aliases = {str(k).upper(): v for k, v in (runner.cfg.webhook.get("symbol_aliases") or {}).items()}
    if s in aliases and aliases[s] in inst:
        return aliases[s]
    matches = [k for k in inst if k.upper().split(".")[0] == s]
    return matches[0] if len(matches) == 1 else None


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    return float(v)


def validate_alert(runner: Runner, payload: dict[str, Any], secret: str, default_rr: float = 2.0,
                   gate: AlertGate | None = None, now_ms: float | None = None
                   ) -> tuple[int, str, dict[str, Any] | None]:
    """Fast checks only (no broker calls). Returns (http code, message, order or None)."""
    if not secret:
        return 500, "server has no WEBHOOK_SECRET configured", None
    if not isinstance(payload, dict):
        return 400, "message must be a JSON object", None
    if not hmac.compare_digest(str(payload.get("secret", "")), secret):
        return 401, "bad secret", None
    symbol = resolve_symbol(runner, payload.get("symbol"))
    if symbol is None:
        return 400, f"unknown symbol {payload.get('symbol')!r}", None
    action = str(payload.get("action", "")).lower()
    if action not in ("buy", "sell", "close"):
        return 400, "action must be buy, sell or close", None

    max_age = float(runner.cfg.webhook.get("max_alert_age_seconds", 120))
    try:
        sent = _num(payload.get("time"))
    except (TypeError, ValueError):
        return 400, "time must be a number (ms)", None
    if sent is not None and max_age > 0:
        sent_ms = sent * 1000 if sent < 1e11 else sent        # accept seconds or ms
        now = now_ms if now_ms is not None else time.time() * 1000
        if now - sent_ms > max_age * 1000:
            return 200, f"ignored: alert is {(now - sent_ms) / 1000:.0f}s old (max {max_age:.0f}s)", None

    order: dict[str, Any] = {"symbol": symbol, "action": action,
                             "source": str(payload.get("source") or "tradingview")[:40]}
    if action != "close":
        try:
            order["stop"] = float(payload["stop"])
        except (KeyError, TypeError, ValueError):
            return 400, "stop (absolute price) is required", None
        try:
            order["target"] = _num(payload.get("target"))
            order["rr"] = float(payload.get("rr") or default_rr)
            order["entry"] = _num(payload.get("entry"))
        except (TypeError, ValueError):
            return 400, "target, rr and entry must be numbers", None
        if order["rr"] <= 0:
            return 400, "rr must be positive", None

    alert_id = str(payload.get("id") or "").strip()
    if alert_id and gate is not None and not gate.first_time(alert_id):
        return 200, f"ignored: duplicate alert {alert_id}", None
    return 202, "accepted", order


def execute_alert(runner: Runner, order: dict[str, Any]) -> str:
    """Place (or close) the trade. Broker calls happen here."""
    sym = order["symbol"]
    if order["action"] == "close":
        runner.broker.close_all(sym)
        runner.notify.send(f"TradingView close {sym}")
        return "closed"
    side = 1 if order["action"] == "buy" else -1
    worst = None
    entry = order.get("entry")
    if entry is not None:
        slip_r = float(runner.cfg.webhook.get("max_entry_slip_r", 0.25))
        worst = entry + side * slip_r * abs(entry - order["stop"])
    return runner.submit_external(sym, side, order["stop"], order.get("target"), order.get("rr"),
                                  source=order.get("source", "tradingview"), worst_price=worst)


def handle_alert(runner: Runner, payload: dict[str, Any], secret: str, default_rr: float = 2.0,
                 gate: AlertGate | None = None, now_ms: float | None = None) -> tuple[int, str]:
    """Validate and execute in one call (used by tests and scripts)."""
    code, msg, order = validate_alert(runner, payload, secret, default_rr, gate, now_ms)
    if order is None:
        return code, msg
    return 200, execute_alert(runner, order)


def _run_in_background(runner: Runner, order: dict[str, Any]) -> None:
    try:
        res = execute_alert(runner, order)
        log.info("TradingView %s %s -> %s", order["action"], order["symbol"], res)
        if res.startswith("skipped"):
            runner.notify.send(f"TradingView {order['action'].upper()} {order['symbol']} {res.replace(':', ': ', 1)}")
    except Exception as e:  # never let a bad alert kill the server
        log.exception("TradingView alert failed")
        runner.notify.send(f"TradingView {order['action']} {order['symbol']} FAILED: {e!r}", logging.ERROR)


def serve(runner: Runner, host: str = "0.0.0.0", port: int = 80) -> None:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    wh = runner.cfg.webhook
    default_rr = float(wh.get("default_reward_risk", 2.0))
    allowed_ips = set(wh.get("allowed_ips") or [])
    gate = AlertGate()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/tv":
                self._reply(404, "not found")
                return
            if allowed_ips and self.client_address[0] not in allowed_ips:
                log.warning("webhook from %s rejected (not in allowed_ips)", self.client_address[0])
                self._reply(403, "forbidden")
                return
            order = None
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 10_000)
                payload = json.loads(self.rfile.read(n) or b"{}")
                code, msg, order = validate_alert(runner, payload, secret, default_rr, gate)
            except (json.JSONDecodeError, UnicodeDecodeError):
                code, msg = 400, "invalid json"
            except Exception as e:
                log.exception("webhook error")
                code, msg = 500, f"error: {e}"
            if code >= 400:
                log.warning("webhook rejected (%s): %s", code, msg)
            self._reply(code, msg)
            if order is not None:
                threading.Thread(target=_run_in_background, args=(runner, order), daemon=True).start()

        def do_GET(self):  # noqa: N802
            # health check: no account details to the public internet
            self._reply(200 if self.path.rstrip("/") in ("", "/tv") else 404, "atlas webhook ok")

        def _reply(self, code: int, msg: str) -> None:
            body = msg.encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt, *args):
            log.info("webhook %s", fmt % args)

    try:
        httpd = ThreadingHTTPServer((host, port), H)
    except OSError as e:
        msg = f"Webhook could NOT start on port {port}: {e}. TradingView alerts will not reach the bot."
        log.error(msg)
        runner.notify.send(msg, logging.CRITICAL)
        return
    log.info("TradingView webhook listening on %s:%s/tv", host, port)
    runner.notify.send(f"TradingView webhook listening on port {port}")
    httpd.serve_forever()
