"""EMA trend-following with ATR-based stop distance (closed candles only)."""

from __future__ import annotations

import pandas as pd

from src.config import StrategyConfig
from src.strategy.ema_rsi import Signal, SignalSide, _ema, _rsi


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


class EmaTrendAtrStrategy:
    """
    Trend-following:
    - LONG: EMA fast crosses above slow AND close > EMA trend
    - SHORT: EMA fast crosses below slow AND close < EMA trend
    Stop distance suggested = atr_stop_mult * ATR(period)
    """

    def __init__(self, config: StrategyConfig):
        self.config = config

    def evaluate(self, ohlcv: pd.DataFrame) -> Signal:
        cfg = self.config
        min_bars = max(cfg.ema_trend, cfg.ema_slow, cfg.atr_period, cfg.rsi_period) + 5
        if len(ohlcv) < min_bars:
            return Signal(SignalSide.HOLD, "insufficient_bars")

        df = ohlcv.iloc[:-1].copy()
        if len(df) < min_bars:
            return Signal(SignalSide.HOLD, "insufficient_closed_bars")

        close = df["close"].astype(float)
        df["ema_fast"] = _ema(close, cfg.ema_fast)
        df["ema_slow"] = _ema(close, cfg.ema_slow)
        df["ema_trend"] = _ema(close, cfg.ema_trend)
        df["rsi"] = _rsi(close, cfg.rsi_period)
        df["atr"] = _atr(df, cfg.atr_period)

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        ema_fast_now = float(curr["ema_fast"])
        ema_slow_now = float(curr["ema_slow"])
        ema_trend_now = float(curr["ema_trend"])
        rsi_now = float(curr["rsi"])
        atr_now = float(curr["atr"])
        price = float(curr["close"])

        if atr_now <= 0 or pd.isna(atr_now):
            return Signal(SignalSide.HOLD, "invalid_atr", rsi=rsi_now)

        sl_distance = atr_now * cfg.atr_stop_mult
        crossed_up = float(prev["ema_fast"]) <= float(prev["ema_slow"]) and ema_fast_now > ema_slow_now
        crossed_down = float(prev["ema_fast"]) >= float(prev["ema_slow"]) and ema_fast_now < ema_slow_now
        uptrend = price > ema_trend_now
        downtrend = price < ema_trend_now

        if crossed_up and uptrend and rsi_now < cfg.rsi_long_max:
            return Signal(
                SignalSide.LONG,
                (
                    f"EMA{cfg.ema_fast}>{cfg.ema_slow} cross + above EMA{cfg.ema_trend} "
                    f"ATR={atr_now:.4f} RSI={rsi_now:.1f}"
                ),
                rsi=rsi_now,
                ema_fast=ema_fast_now,
                ema_slow=ema_slow_now,
                atr=atr_now,
                sl_distance=sl_distance,
            )

        if crossed_down and downtrend and rsi_now > cfg.rsi_short_min:
            return Signal(
                SignalSide.SHORT,
                (
                    f"EMA{cfg.ema_fast}<{cfg.ema_slow} cross + below EMA{cfg.ema_trend} "
                    f"ATR={atr_now:.4f} RSI={rsi_now:.1f}"
                ),
                rsi=rsi_now,
                ema_fast=ema_fast_now,
                ema_slow=ema_slow_now,
                atr=atr_now,
                sl_distance=sl_distance,
            )

        reasons: list[str] = []
        if not crossed_up and not crossed_down:
            reasons.append("no_ema_cross")
        elif crossed_up and not uptrend:
            reasons.append("long_blocked_below_ema_trend")
        elif crossed_down and not downtrend:
            reasons.append("short_blocked_above_ema_trend")
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
            atr=atr_now,
            sl_distance=sl_distance,
        )
