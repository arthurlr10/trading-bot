#!/usr/bin/env python3
"""Weekly performance report from SQLite trade history."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage.database import Database  # noqa: E402


def max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    return max_dd


def build_report(db: Database, days: int = 7) -> str:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    trades = db.list_closed_trades(since=since.isoformat(), until=until.isoformat())

    if not trades:
        return (
            f"Weekly report ({since.date()} → {until.date()})\n"
            "No closed trades in the period."
        )

    pnls = [float(t["pnl"] or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flat = [p for p in pnls if p == 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0
    total_pnl = sum(pnls)
    dd = max_drawdown(pnls)

    lines = [
        f"Performance report ({since.date()} → {until.date()})",
        f"Trades: {len(pnls)} (W={len(wins)} L={len(losses)} F={len(flat)})",
        f"Win rate: {win_rate:.1f}%",
        f"Profit factor: {profit_factor if profit_factor != float('inf') else 'inf'}",
        f"Cumulative PnL: {total_pnl:.4f} USDT",
        f"Max drawdown (trade equity curve): {dd:.4f} USDT",
        f"Gross profit: {gross_profit:.4f} | Gross loss: {gross_loss:.4f}",
        "",
        "Per trade:",
    ]
    for t in trades:
        lines.append(
            f"  #{t['id']} {t['closed_at'][:19]} {t['side']} {t['symbol']} "
            f"pnl={float(t['pnl'] or 0):+.4f} [{t['status']}]"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading bot weekly analysis")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "trades.db",
        help="Path to SQLite database",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}. Run the bot first.", file=sys.stderr)
        sys.exit(1)

    db = Database(args.db)
    print(build_report(db, days=args.days))


if __name__ == "__main__":
    main()
