#!/usr/bin/env python3
"""Offline backtest for EMA trend + ATR strategy (public OHLCV, no keys required)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ccxt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import StrategyConfig  # noqa: E402
from src.strategy.ema_rsi import SignalSide  # noqa: E402
from src.strategy.ema_trend_atr import EmaTrendAtrStrategy  # noqa: E402


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
    exchange = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
    # Public market data — mainnet public endpoint is fine for historical candles
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def simulate(
    df: pd.DataFrame,
    strategy: EmaTrendAtrStrategy,
    *,
    risk_pct: float,
    rr: float,
    equity0: float,
) -> list[dict]:
    trades: list[dict] = []
    equity = equity0
    i = max(strategy.config.ema_trend + 5, 50)

    while i < len(df) - 1:
        window = df.iloc[: i + 1].copy()
        # Append a fake incomplete candle so evaluate() drops the last real bar as "closed"
        pad = window.iloc[[-1]].copy()
        window = pd.concat([window, pad], ignore_index=True)
        signal = strategy.evaluate(window)
        if signal.side == SignalSide.HOLD or signal.sl_distance is None:
            i += 1
            continue

        entry = float(df.iloc[i]["close"])
        sl_dist = float(signal.sl_distance)
        risk_amount = equity * (risk_pct / 100.0)
        qty = risk_amount / sl_dist
        if signal.side == SignalSide.LONG:
            sl = entry - sl_dist
            tp = entry + sl_dist * rr
        else:
            sl = entry + sl_dist
            tp = entry - sl_dist * rr

        exit_price = None
        status = "timeout"
        j = i + 1
        while j < len(df):
            bar = df.iloc[j]
            high = float(bar["high"])
            low = float(bar["low"])
            if signal.side == SignalSide.LONG:
                if low <= sl:
                    exit_price, status = sl, "stopped"
                    break
                if high >= tp:
                    exit_price, status = tp, "take_profit"
                    break
            else:
                if high >= sl:
                    exit_price, status = sl, "stopped"
                    break
                if low <= tp:
                    exit_price, status = tp, "take_profit"
                    break
            j += 1

        if exit_price is None:
            exit_price = float(df.iloc[-1]["close"])
            j = len(df) - 1

        if signal.side == SignalSide.LONG:
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        equity += pnl
        trades.append(
            {
                "opened": str(df.iloc[i]["timestamp"]),
                "closed": str(df.iloc[j]["timestamp"]),
                "side": signal.side.value,
                "entry": entry,
                "exit": exit_price,
                "pnl": pnl,
                "status": status,
                "equity": equity,
            }
        )
        i = j + 1

    return trades


def summarize(trades: list[dict], equity0: float) -> str:
    if not trades:
        return "No trades generated."
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = len(wins) / len(pnls) * 100
    total = sum(pnls)
    peak = equity0
    max_dd = 0.0
    eq = equity0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    lines = [
        f"Trades: {len(pnls)} (W={len(wins)} L={len(losses)})",
        f"Win rate: {win_rate:.1f}%",
        f"Profit factor: {pf if pf != float('inf') else 'inf'}",
        f"Cumulative PnL: {total:.2f} USDT ({total / equity0 * 100:.1f}%)",
        f"Final equity: {equity0 + total:.2f} (start {equity0:.2f})",
        f"Max drawdown: {max_dd:.2f} USDT",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest EMA trend + ATR")
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--equity", type=float, default=5000.0)
    parser.add_argument("--risk-pct", type=float, default=5.0)
    parser.add_argument("--rr", type=float, default=2.0)
    args = parser.parse_args()

    cfg = StrategyConfig(
        name="ema_trend_atr",
        ema_fast=9,
        ema_slow=21,
        ema_trend=200,
        atr_period=14,
        atr_stop_mult=1.5,
        rsi_period=14,
        rsi_long_max=70,
        rsi_short_min=30,
    )
    strategy = EmaTrendAtrStrategy(cfg)
    print(f"Fetching {args.symbol} {args.timeframe} limit={args.limit} ...")
    df = fetch_ohlcv(args.symbol, args.timeframe, args.limit)
    trades = simulate(
        df,
        strategy,
        risk_pct=args.risk_pct,
        rr=args.rr,
        equity0=args.equity,
    )
    print(summarize(trades, args.equity))
    if trades:
        print("\nLast 5 trades:")
        for t in trades[-5:]:
            print(
                f"  {t['opened'][:16]} {t['side']} pnl={t['pnl']:+.2f} [{t['status']}]"
            )


if __name__ == "__main__":
    main()
