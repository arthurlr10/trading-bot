"""Kill switch via flag file and Telegram commands."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KillSwitch:
    """When active, the bot must not open new positions."""

    def __init__(self, flag_path: Path):
        self.flag_path = flag_path
        self._telegram_killed = False

    @property
    def is_active(self) -> bool:
        return self._telegram_killed or self.flag_path.exists()

    def activate(self, source: str = "manual") -> None:
        self._telegram_killed = True
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.write_text(f"killed via {source}\n", encoding="utf-8")
        logger.warning("KILL SWITCH ACTIVATED (%s)", source)

    def deactivate(self, source: str = "manual") -> None:
        self._telegram_killed = False
        if self.flag_path.exists():
            self.flag_path.unlink()
        logger.info("Kill switch cleared (%s)", source)

    def reason(self) -> str:
        if self.flag_path.exists():
            return "flag_file"
        if self._telegram_killed:
            return "telegram"
        return "inactive"
