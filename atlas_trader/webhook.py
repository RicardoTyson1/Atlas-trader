"""TradingView alert receiver.

Point a TradingView alert's Webhook URL at http://<your-server>:8787/tv and use
a message like:

    {"secret": "YOUR_WEBHOOK_SECRET", "symbol": "NAS100", "action": "buy",
     "stop": {{plot("Stop")}}, "rr": 2}

action: buy | sell | close
stop:   required for buy/sell (absolute price)
target: optional absolute price; otherwise rr (default from config) x risk

Every alert goes through the same RiskManager as the bot's own strategies.
TradingView webhooks need a public HTTPS-reachable address (or a tunnel).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .runner import Runner

log = logging.getLogger("atlas")


def handle_alert(runner: Runner, payload: dict[str, Any], secret: str, default_rr: float = 2.0) -> tuple[int, str]:
    if not secret:
        return 500, "server has no WEBHOOK_SECRET configured"
    if not hmac.compare_digest(str(payload.get("secret", "")), secret):
        return 401, "bad secret"
    symbol = str(payload.get("symbol", "")).upper()
    action = str(payload.get("action", "")).lower()
    if symbol not in runner.cfg.instruments:
        return 400, f"unknown symbol {symbol}"
    if action == "close":
        runner.broker.close_all(symbol)
        runner.notify.send(f"TradingView close {symbol}")
        return 200, "closed"
    if action not in ("buy", "sell"):
        return 400, "action must be buy, sell or close"
    try:
        stop = float(payload["stop"])
    except (KeyError, TypeError, ValueError):
        return 400, "stop (absolute price) is required"
    target = payload.get("target")
    target = float(target) if target not in (None, "") else None
    rr = float(payload.get("rr") or default_rr)
    res = runner.submit_external(symbol, 1 if action == "buy" else -1, stop, target, rr)
    return 200, res


def serve(runner: Runner, host: str = "0.0.0.0", port: int = 8787) -> None:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    default_rr = float(runner.cfg.webhook.get("default_reward_risk", 2.0))

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/tv":
                self._reply(404, "not found")
                return
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 10_000)
                payload = json.loads(self.rfile.read(n) or b"{}")
                code, msg = handle_alert(runner, payload, secret, default_rr)
            except json.JSONDecodeError:
                code, msg = 400, "invalid json"
            except Exception as e:
                log.exception("webhook error")
                code, msg = 500, f"error: {e}"
            self._reply(code, msg)

        def do_GET(self):  # noqa: N802
            self._reply(200, json.dumps(runner.rm.summary()))

        def _reply(self, code: int, msg: str) -> None:
            body = msg.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.info("webhook %s", fmt % args)

    httpd = ThreadingHTTPServer((host, port), H)
    log.info("TradingView webhook listening on %s:%s/tv", host, port)
    httpd.serve_forever()
