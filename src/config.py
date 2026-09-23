"""Load and validate centralized configuration (YAML + environment)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class ExchangeConfig:
    testnet: bool
    pairs: list[str]
    timeframe: str
    ohlcv_limit: int


@dataclass(frozen=True)
class StrategyConfig:
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_long_max: float
    rsi_short_min: float


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float
    reward_risk_ratio: float
    stop_loss_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_positions_per_pair: int


@dataclass(frozen=True)
class BotConfig:
    poll_seconds: int
    close_on_kill: bool
    timezone: str
    log_level: str


@dataclass(frozen=True)
class Secrets:
    binance_api_key: str
    binance_api_secret: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None


@dataclass(frozen=True)
class Settings:
    exchange: ExchangeConfig
    strategy: StrategyConfig
    risk: RiskConfig
    bot: BotConfig
    secrets: Secrets
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    db_path: Path = field(default_factory=lambda: DATA_DIR / "trades.db")
    log_path: Path = field(default_factory=lambda: DATA_DIR / "bot.log")
    kill_flag_path: Path = field(default_factory=lambda: DATA_DIR / "KILL")


def _require(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise KeyError(f"Missing config key: {key}")
    return mapping[key]


def load_settings(config_path: Path | None = None) -> Settings:
    """Load YAML settings and secrets from environment. Enforces testnet-only."""
    load_dotenv(PROJECT_ROOT / ".env")

    path = config_path or DEFAULT_CONFIG_PATH
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    exchange_raw = _require(raw, "exchange")
    strategy_raw = _require(raw, "strategy")
    risk_raw = _require(raw, "risk")
    bot_raw = _require(raw, "bot")

    exchange = ExchangeConfig(
        testnet=bool(_require(exchange_raw, "testnet")),
        pairs=list(_require(exchange_raw, "pairs")),
        timeframe=str(_require(exchange_raw, "timeframe")),
        ohlcv_limit=int(exchange_raw.get("ohlcv_limit", 100)),
    )

    if not exchange.testnet:
        print(
            "FATAL: exchange.testnet must be true. "
            "This bot is testnet-only and refuses mainnet.",
            file=sys.stderr,
        )
        sys.exit(1)

    strategy = StrategyConfig(
        ema_fast=int(_require(strategy_raw, "ema_fast")),
        ema_slow=int(_require(strategy_raw, "ema_slow")),
        rsi_period=int(_require(strategy_raw, "rsi_period")),
        rsi_long_max=float(_require(strategy_raw, "rsi_long_max")),
        rsi_short_min=float(_require(strategy_raw, "rsi_short_min")),
    )

    risk = RiskConfig(
        risk_per_trade_pct=float(_require(risk_raw, "risk_per_trade_pct")),
        reward_risk_ratio=float(_require(risk_raw, "reward_risk_ratio")),
        stop_loss_pct=float(_require(risk_raw, "stop_loss_pct")),
        max_daily_loss_pct=float(_require(risk_raw, "max_daily_loss_pct")),
        max_weekly_loss_pct=float(_require(risk_raw, "max_weekly_loss_pct")),
        max_positions_per_pair=int(_require(risk_raw, "max_positions_per_pair")),
    )

    bot = BotConfig(
        poll_seconds=int(_require(bot_raw, "poll_seconds")),
        close_on_kill=bool(bot_raw.get("close_on_kill", False)),
        timezone=str(bot_raw.get("timezone", "UTC")),
        log_level=str(bot_raw.get("log_level", "INFO")),
    )

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        print(
            "FATAL: BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None

    secrets = Secrets(
        binance_api_key=api_key,
        binance_api_secret=api_secret,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    return Settings(
        exchange=exchange,
        strategy=strategy,
        risk=risk,
        bot=bot,
        secrets=secrets,
        data_dir=DATA_DIR,
        db_path=DATA_DIR / "trades.db",
        log_path=DATA_DIR / "bot.log",
        kill_flag_path=DATA_DIR / "KILL",
    )
