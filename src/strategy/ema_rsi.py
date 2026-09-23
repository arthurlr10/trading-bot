"""EMA cross + RSI filter strategy (closed candles only)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from src.config import StrategyConfig


class SignalSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    HOLD = "hold"


@dataclass(frozen=True)
class Signal:
    side: SignalSide
    reason: str
    rsi: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


class EmaRsiStrategy:
    """Interpretável: EMA9/EMA21 cross filtered by RSI(14)."""

    def __init__(self, config: StrategyConfig):
        self.config = config

    def evaluate(self, ohlcv: pd.DataFrame) -> Signal:
        """Evaluate signal on the last *closed* candle (drop incomplete bar)."""
        cfg = self.config
        min_bars = max(cfg.ema_slow, cfg.rsi_period) + 3
        if len(ohlcv) < min_bars:
            return Signal(SignalSide.HOLD, "insufficient_bars")

        # Drop the last (potentially still forming) candle
        df = ohlcv.iloc[:-1].copy()
        if len(df) < min_bars:
            return Signal(SignalSide.HOLD, "insufficient_closed_bars")

        close = df["close"].astype(float)
        df["ema_fast"] = _ema(close, cfg.ema_fast)
        df["ema_slow"] = _ema(close, cfg.ema_slow)
        df["rsi"] = _rsi(close, cfg.rsi_period)

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        ema_fast_now = float(curr["ema_fast"])
        ema_slow_now = float(curr["ema_slow"])
        rsi_now = float(curr["rsi"])

        crossed_up = float(prev["ema_fast"]) <= float(prev["ema_slow"]) and ema_fast_now > ema_slow_now
        crossed_down = float(prev["ema_fast"]) >= float(prev["ema_slow"]) and ema_fast_now < ema_slow_now

        if crossed_up and rsi_now < cfg.rsi_long_max:
            return Signal(
                SignalSide.LONG,
                f"EMA{cfg.ema_fast} crossed above EMA{cfg.ema_slow} and RSI={rsi_now:.1f}<{cfg.rsi_long_max}",
                rsi=rsi_now,
                ema_fast=ema_fast_now,
                ema_slow=ema_slow_now,
            )

        if crossed_down and rsi_now > cfg.rsi_short_min:
            return Signal(
                SignalSide.SHORT,
                f"EMA{cfg.ema_fast} crossed below EMA{cfg.ema_slow} and RSI={rsi_now:.1f}>{cfg.rsi_short_min}",
                rsi=rsi_now,
                ema_fast=ema_fast_now,
                ema_slow=ema_slow_now,
            )

        reasons: list[str] = []
        if not crossed_up and not crossed_down:
            reasons.append("no_ema_cross")
        elif crossed_up and rsi_now >= cfg.rsi_long_max:
            reasons.append(f"rsi_overbought_{rsi_now:.1f}")
        elif crossed_down and rsi_now <= cfg.rsi_short_min:
            reasons.append(f"rsi_oversold_{rsi_now:.1f}")

        return Signal(
            SignalSide.HOLD,
            "+".join(reasons) or "hold",
            rsi=rsi_now,
            ema_fast=ema_fast_now,
            ema_slow=ema_slow_now,
        )
