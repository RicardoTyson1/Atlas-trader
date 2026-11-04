"""Notifications: always logs; optionally Telegram and/or Discord (set env vars)."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("atlas")


class Notifier:
    def __init__(self, label: str = "Atlas", healthcheck_env: str = "HEALTHCHECK_URL") -> None:
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.discord = os.environ.get("DISCORD_WEBHOOK_URL")
        self.label = label
        self.healthcheck = os.environ.get(healthcheck_env)

    def heartbeat(self, ok: bool = True, detail: str = "") -> None:
        """Ping an external dead-man's switch (e.g. healthchecks.io). If pings stop,
        THAT service alerts you, which covers crashes and a dead server."""
        if not self.healthcheck:
            return
        try:
            url = self.healthcheck if ok else self.healthcheck.rstrip("/") + "/fail"
            requests.post(url, data=detail[:1000].encode(), timeout=10)
        except Exception as e:
            log.warning("heartbeat failed: %s", e)

    def send(self, msg: str, level: int = logging.INFO) -> None:
        log.log(level, msg)
        text = f"[{self.label}] {msg}"
        try:
            if self.tg_token and self.tg_chat:
                requests.post(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    json={"chat_id": self.tg_chat, "text": text}, timeout=10,
                )
            if self.discord:
                requests.post(self.discord, json={"content": text}, timeout=10)
        except Exception as e:  # never let a notification failure stop trading logic
            log.warning("notification failed: %s", e)


class MemoryNotifier(Notifier):
    """For tests."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.heartbeats: list[bool] = []
        self.label = "test"

    def send(self, msg: str, level: int = logging.INFO) -> None:
        self.messages.append(msg)

    def heartbeat(self, ok: bool = True, detail: str = "") -> None:
        self.heartbeats.append(ok)
