"""Telegram Bot API notifications and command polling."""

from __future__ import annotations

import logging
from typing import Callable

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self._offset: int | None = None
        if not self.enabled:
            logger.warning("Telegram disabled (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")

    def send(self, text: str) -> None:
        if not self.enabled:
            logger.debug("Telegram skip: %s", text)
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text[:4000]},
                timeout=15,
            )
            if resp.status_code >= 400:
                logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            logger.warning("Telegram send error: %s", exc)

    def poll_commands(self, handlers: dict[str, Callable[[], None]]) -> None:
        """Long-poll getUpdates and dispatch /kill /resume style commands."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: dict = {"timeout": 0, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            resp = requests.get(url, params=params, timeout=20)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Telegram poll error: %s", exc)
            return

        for update in data.get("result") or []:
            self._offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("id")) != str(self.chat_id):
                continue
            text = (message.get("text") or "").strip().lower()
            cmd = text.split()[0] if text else ""
            # Strip @botname
            if "@" in cmd:
                cmd = cmd.split("@", 1)[0]
            handler = handlers.get(cmd)
            if handler:
                logger.info("Telegram command received: %s", cmd)
                handler()
