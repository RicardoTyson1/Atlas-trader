"""The desk web server: one private page plus a small JSON API (Python standard library only).

Private by default:
  * binds to 127.0.0.1 unless the config says otherwise (reach it through a tunnel / Tailscale);
  * every page and API call needs a login (DESK_PASSWORD in .env); sessions live in memory for 12 h;
  * control calls also need the X-Desk header (a cross-site form cannot send it) and a same-origin
    request, so another web page cannot press the buttons for you.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time as _time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("atlas.desk")

HERE = Path(__file__).parent
SESSION_HOURS = 12


class Desk:
    def __init__(self, feed, password: str, title: str = "Atlas Desk"):
        self.feed = feed
        self.password = password
        self.title = title
        self.sessions: dict[str, float] = {}
        self.compute = threading.Lock()
        self._slow: dict[str, tuple[float, object]] = {}

    def cached(self, name: str, ttl: float, fn):
        hit = self._slow.get(name)
        if hit and _time.monotonic() - hit[0] < ttl:
            return hit[1]
        v = fn()
        self._slow[name] = (_time.monotonic(), v)
        return v

    def payload(self, key: str, tf: str) -> dict:
        with self.compute:
            snap = self.feed.analyse(key, tf)
            bot = self.feed.bot()
            return {
                "title": self.title, "source": self.feed.src.label, "demo": self.feed.src.demo,
                "instruments": [{"key": i["key"], "name": i["name"], "available": i["available"],
                                 "symbol": i["symbol"]} for i in self.feed.instruments],
                "snap": snap, "bot": bot, "record": self.feed.cfg.get("record", {}),
                "news": self.feed.news(self.feed.clock()),
                "alerts": self.feed.alerts(),
                "smt": self.cached("smt", 120, self.feed.smt),
                "overview": self.cached("overview", 120, self.feed.overview),
            }


def make_handler(desk: Desk):
    page = (HERE / "static" / "index.html").read_text()
    login = (HERE / "static" / "login.html").read_text()
    chartlib = (HERE / "static" / "lightweight-charts.js").read_bytes()   # Apache-2.0, TradingView

    class H(BaseHTTPRequestHandler):
        server_version = "AtlasDesk"

        def log_message(self, fmt, *args):  # quiet access log
            log.debug("%s " + fmt, self.client_address[0], *args)

        # -------------------------------------------------------------- helpers
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _session(self) -> bool:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            tok = c["desk"].value if "desk" in c else None
            exp = desk.sessions.get(tok or "")
            return bool(exp and exp > _time.time())

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host")

        def _body(self) -> bytes:
            n = min(int(self.headers.get("Content-Length") or 0), 10_000)
            return self.rfile.read(n) if n else b""

        # -------------------------------------------------------------- routes
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/login":
                return self._send(200, login.replace("{{TITLE}}", desk.title).encode(), "text/html; charset=utf-8")
            if not self._session():
                if u.path.startswith("/api/"):
                    return self._json(401, {"error": "login needed"})
                return self._send(302, b"", "text/plain", {"Location": "/login"})
            if u.path == "/":
                return self._send(200, page.replace("{{TITLE}}", desk.title).encode(), "text/html; charset=utf-8")
            if u.path == "/static/lightweight-charts.js":
                return self._send(200, chartlib, "application/javascript", {"Cache-Control": "max-age=86400"})
            if u.path == "/api/desk":
                q = parse_qs(u.query)
                key = (q.get("key") or ["gold"])[0]
                tf = (q.get("tf") or ["5m"])[0]
                if tf not in ("1m", "5m", "15m", "1H", "4H", "1D"):
                    tf = "5m"
                try:
                    return self._json(200, desk.payload(key, tf))
                except Exception as e:  # noqa: BLE001
                    log.exception("desk payload failed")
                    return self._json(500, {"error": repr(e)[:300]})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/login":
                form = parse_qs(self._body().decode(errors="ignore"))
                pw = (form.get("password") or [""])[0]
                if desk.password and hmac.compare_digest(pw.encode(), desk.password.encode()):
                    tok = secrets.token_urlsafe(32)
                    desk.sessions[tok] = _time.time() + SESSION_HOURS * 3600
                    return self._send(302, b"", "text/plain", {
                        "Location": "/",
                        "Set-Cookie": f"desk={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}"})
                _time.sleep(1.5)                      # slow down password guessing
                return self._send(302, b"", "text/plain", {"Location": "/login?bad=1"})
            if u.path == "/logout":
                return self._send(302, b"", "text/plain", {"Location": "/login",
                                                           "Set-Cookie": "desk=; Path=/; Max-Age=0"})
            if not self._session():
                return self._json(401, {"error": "login needed"})
            if u.path == "/api/control":
                if self.headers.get("X-Desk") != "1" or not self._same_origin():
                    return self._json(403, {"error": "blocked: not from the desk page"})
                try:
                    req = json.loads(self._body() or b"{}")
                except ValueError:
                    return self._json(400, {"error": "bad request"})
                if req.get("action") == "kill" and req.get("confirm") != "STOP":
                    return self._json(400, {"ok": False, "message": "type STOP to confirm the emergency stop"})
                ok, msg = desk.feed.set_control(str(req.get("action")), "desk page", req.get("model"))
                return self._json(200 if ok else 409, {"ok": ok, "message": msg})
            return self._send(404, b"not found", "text/plain")

    return H


def serve(desk: Desk, host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(desk))
    httpd.daemon_threads = True
    log.info("desk on http://%s:%d (%s)", host, port, desk.feed.src.label)
    print(f"Atlas desk on http://{host}:{port}  ({desk.feed.src.label})", flush=True)
    httpd.serve_forever()


def password_from_env() -> str:
    return os.environ.get("DESK_PASSWORD", "")
