"""SQLite persistence for trades and events."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_reason TEXT,
                    qty REAL NOT NULL,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL,
                    status TEXT NOT NULL DEFAULT 'open',
                    entry_order_id TEXT,
                    sl_order_id TEXT,
                    tp_order_id TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
                CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
                """
            )
        logger.info("SQLite ready at %s", self.db_path)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def log_event(
        self, level: str, message: str, context: dict[str, Any] | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (timestamp, level, message, context) VALUES (?, ?, ?, ?)",
                (
                    _utc_now(),
                    level.upper(),
                    message,
                    json.dumps(context) if context else None,
                ),
            )

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(
        self,
        *,
        symbol: str,
        side: str,
        signal_reason: str,
        qty: float,
        entry: float,
        stop_loss: float,
        take_profit: float,
        entry_order_id: str | None = None,
        sl_order_id: str | None = None,
        tp_order_id: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    opened_at, symbol, side, signal_reason, qty, entry,
                    stop_loss, take_profit, status,
                    entry_order_id, sl_order_id, tp_order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    _utc_now(),
                    symbol,
                    side,
                    signal_reason,
                    qty,
                    entry,
                    stop_loss,
                    take_profit,
                    entry_order_id,
                    sl_order_id,
                    tp_order_id,
                ),
            )
            return int(cur.lastrowid)

    def close_trade(
        self,
        trade_id: int,
        *,
        exit_price: float,
        pnl: float,
        status: str = "closed",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET closed_at = ?, exit_price = ?, pnl = ?, status = ?
                WHERE id = ?
                """,
                (_utc_now(), exit_price, pnl, status, trade_id),
            )

    def get_open_trade(self, symbol: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            return dict(row) if row else None

    def list_open_trades(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def list_closed_trades(
        self, since: str | None = None, until: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM trades WHERE status IN ('closed', 'stopped', 'take_profit')"
        params: list[Any] = []
        if since:
            query += " AND closed_at >= ?"
            params.append(since)
        if until:
            query += " AND closed_at < ?"
            params.append(until)
        query += " ORDER BY closed_at"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def realized_pnl_since(self, since_iso: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl), 0) AS total
                FROM trades
                WHERE status IN ('closed', 'stopped', 'take_profit')
                  AND closed_at >= ?
                """,
                (since_iso,),
            ).fetchone()
            return float(row["total"])
