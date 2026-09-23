"""Order execution: market entry + frozen SL/TP reduce-only orders."""

from __future__ import annotations

import logging
from typing import Any

from src.exchange.binance_futures import BinanceFuturesClient
from src.notify.telegram import TelegramNotifier
from src.risk.manager import TradePlan
from src.storage.database import Database
from src.strategy.ema_rsi import SignalSide

logger = logging.getLogger(__name__)


def _fmt_equity(client: BinanceFuturesClient) -> str:
    try:
        return f"{client.fetch_equity():.2f} USDT"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch equity for notification: %s", exc)
        return "indisponible"


class OrderManager:
    def __init__(
        self,
        client: BinanceFuturesClient,
        db: Database,
        notifier: TelegramNotifier,
    ):
        self.client = client
        self.db = db
        self.notifier = notifier

    def open_position(self, symbol: str, plan: TradePlan, signal_reason: str) -> int | None:
        side = "buy" if plan.side == SignalSide.LONG else "sell"
        close_side = "sell" if plan.side == SignalSide.LONG else "buy"

        sl = self.client.price_to_precision(symbol, plan.stop_loss)
        tp = self.client.price_to_precision(symbol, plan.take_profit)
        qty = self.client.amount_to_precision(symbol, plan.qty)

        logger.info(
            "Opening %s %s qty=%s entry~%s SL=%s TP=%s reason=%s",
            plan.side.value,
            symbol,
            qty,
            plan.entry,
            sl,
            tp,
            signal_reason,
        )

        entry_order = self.client.create_market_order(symbol, side, qty)
        entry_price = float(
            entry_order.get("average")
            or entry_order.get("price")
            or plan.entry
        )
        if entry_price <= 0:
            entry_price = plan.entry

        # Recalculate SL/TP from *actual* fill using same distances (frozen afterward)
        sl_distance = abs(plan.entry - plan.stop_loss)
        rr_distance = abs(plan.take_profit - plan.entry)
        if plan.side == SignalSide.LONG:
            sl = self.client.price_to_precision(symbol, entry_price - sl_distance)
            tp = self.client.price_to_precision(symbol, entry_price + rr_distance)
        else:
            sl = self.client.price_to_precision(symbol, entry_price + sl_distance)
            tp = self.client.price_to_precision(symbol, entry_price - rr_distance)

        sl_order: dict[str, Any] | None = None
        tp_order: dict[str, Any] | None = None
        try:
            sl_order = self.client.create_stop_market(
                symbol, close_side, qty, sl, reduce_only=True
            )
            tp_order = self.client.create_take_profit_market(
                symbol, close_side, qty, tp, reduce_only=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to place SL/TP after entry — flattening: %s", exc)
            try:
                self.client.create_market_order(symbol, close_side, qty, reduce_only=True)
            except Exception as flatten_exc:  # noqa: BLE001
                logger.exception("Emergency flatten failed: %s", flatten_exc)
                self.notifier.send(
                    f"CRITICAL: entry opened but SL/TP failed and flatten failed on {symbol}: {flatten_exc}"
                )
            self.notifier.send(
                f"ERROR: SL/TP placement failed on {symbol}, position flattened. {exc}"
            )
            return None

        trade_id = self.db.insert_trade(
            symbol=symbol,
            side=plan.side.value,
            signal_reason=signal_reason,
            qty=qty,
            entry=entry_price,
            stop_loss=sl,
            take_profit=tp,
            entry_order_id=str(entry_order.get("id") or ""),
            sl_order_id=str((sl_order or {}).get("id") or ""),
            tp_order_id=str((tp_order or {}).get("id") or ""),
        )
        self.db.log_event(
            "INFO",
            "position_opened",
            {
                "trade_id": trade_id,
                "symbol": symbol,
                "side": plan.side.value,
                "qty": qty,
                "entry": entry_price,
                "sl": sl,
                "tp": tp,
                "reason": signal_reason,
            },
        )
        self.notifier.send(
            f"OPEN {plan.side.value.upper()} {symbol}\n"
            f"qty={qty} entry={entry_price}\n"
            f"SL={sl} TP={tp}\n"
            f"reason: {signal_reason}\n"
            f"solde: {_fmt_equity(self.client)}"
        )
        return trade_id

    def sync_closed_positions(self) -> None:
        """Detect exchange-flat trades still marked open in DB and close them."""
        for trade in self.db.list_open_trades():
            symbol = trade["symbol"]
            position = self.client.fetch_position(symbol)
            if position is not None:
                continue

            # Position closed on exchange (SL/TP hit or manual)
            exit_price = self.client.fetch_ticker_price(symbol)
            side = trade["side"]
            entry = float(trade["entry"])
            qty = float(trade["qty"])
            if side == "long":
                pnl = (exit_price - entry) * qty
            else:
                pnl = (entry - exit_price) * qty

            # Classify roughly vs SL/TP
            sl = float(trade["stop_loss"])
            tp = float(trade["take_profit"])
            status = "closed"
            if side == "long":
                if exit_price <= sl * 1.001:
                    status = "stopped"
                elif exit_price >= tp * 0.999:
                    status = "take_profit"
            else:
                if exit_price >= sl * 0.999:
                    status = "stopped"
                elif exit_price <= tp * 1.001:
                    status = "take_profit"

            self.client.cancel_all_orders(symbol)
            self.db.close_trade(trade["id"], exit_price=exit_price, pnl=pnl, status=status)
            self.db.log_event(
                "INFO",
                "position_closed",
                {
                    "trade_id": trade["id"],
                    "symbol": symbol,
                    "exit": exit_price,
                    "pnl": pnl,
                    "status": status,
                },
            )
            emoji = "PROFIT" if pnl >= 0 else "LOSS"
            self.notifier.send(
                f"CLOSE {side.upper()} {symbol} [{status}]\n"
                f"exit≈{exit_price} PnL={pnl:.4f} USDT ({emoji})\n"
                f"solde: {_fmt_equity(self.client)}"
            )
            logger.info("Closed trade #%s %s pnl=%.4f status=%s", trade["id"], symbol, pnl, status)
