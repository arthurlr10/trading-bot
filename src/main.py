"""Main trading loop — Binance Futures Testnet only."""

from __future__ import annotations

import logging
import signal
import sys
import time
from typing import Any

from src.config import load_settings
from src.exchange.binance_futures import BinanceFuturesClient, TestnetGuardError
from src.execution.order_manager import OrderManager
from src.logging_setup import setup_logging
from src.notify.telegram import TelegramNotifier
from src.risk.manager import RiskManager
from src.safety.kill_switch import KillSwitch
from src.storage.database import Database
from src.strategy.ema_rsi import SignalSide
from src.strategy.ema_trend_atr import EmaTrendAtrStrategy

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown
    logger.warning("Signal %s received — shutting down after current cycle", signum)
    _shutdown = True


def run() -> None:
    settings = load_settings()
    setup_logging(settings.log_path, settings.bot.log_level)
    logger.info("Starting bot (demo-trading only)")

    db = Database(settings.db_path)
    notifier = TelegramNotifier(
        settings.secrets.telegram_bot_token,
        settings.secrets.telegram_chat_id,
    )
    kill = KillSwitch(settings.kill_flag_path)

    try:
        client = BinanceFuturesClient(
            settings.secrets.binance_api_key,
            settings.secrets.binance_api_secret,
            require_testnet=True,
        )
    except TestnetGuardError as exc:
        logger.error("%s", exc)
        notifier.send(f"FATAL testnet guard: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to connect to exchange: %s", exc)
        notifier.send(f"FATAL exchange connect: {exc}")
        sys.exit(1)

    strategy = EmaTrendAtrStrategy(settings.strategy)
    risk = RiskManager(settings.risk, db)
    orders = OrderManager(client, db, notifier)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    def on_kill() -> None:
        kill.activate("telegram_/kill")
        try:
            bal = f"{client.fetch_equity():.2f} USDT"
        except Exception:  # noqa: BLE001
            bal = "indisponible"
        notifier.send(
            f"Kill switch ON — no new entries. Existing positions left open.\n"
            f"solde: {bal}"
        )
        db.log_event("WARNING", "kill_switch_on", {"source": "telegram"})

    def on_resume() -> None:
        kill.deactivate("telegram_/resume")
        try:
            bal = f"{client.fetch_equity():.2f} USDT"
        except Exception:  # noqa: BLE001
            bal = "indisponible"
        notifier.send(f"Kill switch OFF — bot resumed.\nsolde: {bal}")
        db.log_event("INFO", "kill_switch_off", {"source": "telegram"})

    def on_solde() -> None:
        try:
            bal = f"{client.fetch_equity():.2f} USDT"
        except Exception as exc:  # noqa: BLE001
            bal = f"erreur: {exc}"
        notifier.send(f"Solde actuel: {bal}")

    try:
        start_equity = f"{client.fetch_equity():.2f} USDT"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch equity at startup: %s", exc)
        start_equity = "indisponible"

    notifier.send(
        "Bot started (Binance Futures DEMO)\n"
        f"strategy={settings.strategy.name} tf={settings.exchange.timeframe}\n"
        f"pairs={settings.exchange.pairs}\n"
        f"risk={settings.risk.risk_per_trade_pct}% R:R={settings.risk.reward_risk_ratio}\n"
        f"solde: {start_equity}\n"
        "Commands: /kill /resume /solde"
    )
    db.log_event("INFO", "bot_started", {"pairs": settings.exchange.pairs, "equity": start_equity})

    last_signal_bar: dict[str, Any] = {}
    last_pause_notice: str | None = None

    while not _shutdown:
        try:
            notifier.poll_commands(
                {
                    "/kill": on_kill,
                    "/resume": on_resume,
                    "/solde": on_solde,
                    "/balance": on_solde,
                }
            )

            # Sync closes first
            orders.sync_closed_positions()

            equity = client.fetch_equity()
            gate = risk.refresh_loss_limits(equity)
            if not gate.allowed:
                if ("weekly_loss" in gate.reason or "daily_loss" in gate.reason) and gate.reason != last_pause_notice:
                    notifier.send(
                        f"Trading paused: {gate.reason}\n"
                        f"solde: {equity:.2f} USDT"
                    )
                    db.log_event("WARNING", "loss_limit", {"reason": gate.reason})
                    last_pause_notice = gate.reason
                logger.info("Risk gate blocked: %s", gate.reason)
                time.sleep(settings.bot.poll_seconds)
                continue

            last_pause_notice = None

            if kill.is_active:
                logger.info("Kill switch active (%s) — skipping entries", kill.reason())
                time.sleep(settings.bot.poll_seconds)
                continue

            for symbol in settings.exchange.pairs:
                try:
                    _process_symbol(
                        symbol=symbol,
                        settings=settings,
                        client=client,
                        strategy=strategy,
                        risk=risk,
                        orders=orders,
                        db=db,
                        equity=equity,
                        last_signal_bar=last_signal_bar,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Error processing %s: %s", symbol, exc)
                    db.log_event("ERROR", "symbol_cycle_error", {"symbol": symbol, "error": str(exc)})
                    notifier.send(f"ERROR on {symbol}: {exc}")

        except Exception as exc:  # noqa: BLE001
            logger.exception("Main loop error: %s", exc)
            db.log_event("ERROR", "main_loop_error", {"error": str(exc)})
            notifier.send(f"SYSTEM ERROR: {exc}")
            time.sleep(min(settings.bot.poll_seconds, 15))
            continue

        time.sleep(settings.bot.poll_seconds)

    notifier.send("Bot stopped cleanly.")
    db.log_event("INFO", "bot_stopped", {})
    logger.info("Shutdown complete")


def _process_symbol(
    *,
    symbol: str,
    settings,
    client: BinanceFuturesClient,
    strategy: EmaTrendAtrStrategy,
    risk: RiskManager,
    orders: OrderManager,
    db: Database,
    equity: float,
    last_signal_bar: dict[str, Any],
) -> None:
    ohlcv = client.fetch_ohlcv(
        symbol,
        settings.exchange.timeframe,
        limit=settings.exchange.ohlcv_limit,
    )
    if ohlcv.empty:
        return

    # Use last closed candle timestamp as dedupe key
    closed = ohlcv.iloc[:-1]
    if closed.empty:
        return
    bar_ts = str(closed.iloc[-1]["timestamp"])

    signal = strategy.evaluate(ohlcv)
    logger.info(
        "%s signal=%s atr=%s reason=%s",
        symbol,
        signal.side.value,
        f"{signal.atr:.4f}" if signal.atr is not None else "n/a",
        signal.reason,
    )
    db.log_event(
        "DEBUG",
        "signal",
        {
            "symbol": symbol,
            "side": signal.side.value,
            "reason": signal.reason,
            "rsi": signal.rsi,
            "atr": signal.atr,
            "sl_distance": signal.sl_distance,
            "bar": bar_ts,
        },
    )

    if signal.side == SignalSide.HOLD:
        return

    # One evaluation per closed bar
    if last_signal_bar.get(symbol) == bar_ts:
        logger.debug("%s already acted on bar %s", symbol, bar_ts)
        return

    has_pos = client.has_open_position(symbol)
    can = risk.can_open(symbol, equity, has_pos)
    if not can.allowed:
        logger.info("%s skip entry: %s", symbol, can.reason)
        return

    entry = float(closed.iloc[-1]["close"])
    plan = risk.build_plan(
        signal.side,
        entry,
        equity,
        amount_precision_fn=lambda q: client.amount_to_precision(symbol, q),
        min_amount=client.min_amount(symbol),
        sl_distance=signal.sl_distance,
    )
    if plan is None:
        logger.warning("%s could not build trade plan", symbol)
        return

    trade_id = orders.open_position(symbol, plan, signal.reason)
    if trade_id is not None:
        last_signal_bar[symbol] = bar_ts


if __name__ == "__main__":
    run()
