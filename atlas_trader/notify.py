"""Notifications: always logs; optionally Telegram and/or Discord (set env vars)."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("atlas")


class Notifier:
    def __init__(self) -> None:
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.discord = os.environ.get("DISCORD_WEBHOOK_URL")

    def send(self, msg: str, level: int = logging.INFO) -> None:
        log.log(level, msg)
        text = f"[Atlas] {msg}"
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

    def send(self, msg: str, level: int = logging.INFO) -> None:
        self.messages.append(msg)
