"""Hard-coded risk management — non-bypassable rules."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.config import RiskConfig
from src.storage.database import Database
from src.strategy.ema_rsi import SignalSide

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradePlan:
    side: SignalSide
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    reward_amount: float


@dataclass(frozen=True)
class RiskGateResult:
    allowed: bool
    reason: str


class RiskManager:
    """
    Enforces:
    - risk per trade from config (capped at 10%)
    - SL fixed at open (ATR distance or % fallback), R:R >= 1:1.5
    - no martingale (size from risk %, never raised after loss)
    - daily / weekly loss pauses
    - one position per pair
    """

    def __init__(self, config: RiskConfig, db: Database):
        self.config = config
        self.db = db
        self._daily_pause_until: datetime | None = None
        self._weekly_pause_until: datetime | None = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _start_of_day(self, when: datetime | None = None) -> datetime:
        now = when or self._now()
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _start_of_week(self, when: datetime | None = None) -> datetime:
        """Monday 00:00 UTC."""
        now = when or self._now()
        monday = now - timedelta(days=now.weekday())
        return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)

    def refresh_loss_limits(self, equity: float) -> RiskGateResult:
        """Update pause flags from realized PnL vs equity."""
        now = self._now()

        if self._weekly_pause_until and now < self._weekly_pause_until:
            return RiskGateResult(False, f"weekly_loss_pause_until_{self._weekly_pause_until.isoformat()}")
        if self._daily_pause_until and now < self._daily_pause_until:
            return RiskGateResult(False, f"daily_loss_pause_until_{self._daily_pause_until.isoformat()}")

        # Clear expired pauses
        if self._weekly_pause_until and now >= self._weekly_pause_until:
            logger.info("Weekly loss pause expired")
            self._weekly_pause_until = None
        if self._daily_pause_until and now >= self._daily_pause_until:
            logger.info("Daily loss pause expired")
            self._daily_pause_until = None

        if equity <= 0:
            return RiskGateResult(False, "equity_non_positive")

        day_start = self._start_of_day().isoformat()
        week_start = self._start_of_week().isoformat()
        daily_pnl = self.db.realized_pnl_since(day_start)
        weekly_pnl = self.db.realized_pnl_since(week_start)

        daily_loss_limit = -(self.config.max_daily_loss_pct / 100.0) * equity
        weekly_loss_limit = -(self.config.max_weekly_loss_pct / 100.0) * equity

        if weekly_pnl <= weekly_loss_limit:
            # Pause until next Monday 00:00 UTC
            next_monday = self._start_of_week() + timedelta(days=7)
            self._weekly_pause_until = next_monday
            reason = (
                f"weekly_loss_limit_hit pnl={weekly_pnl:.4f} "
                f"limit={weekly_loss_limit:.4f} pause_until={next_monday.isoformat()}"
            )
            logger.warning(reason)
            return RiskGateResult(False, reason)

        if daily_pnl <= daily_loss_limit:
            tomorrow = self._start_of_day() + timedelta(days=1)
            self._daily_pause_until = tomorrow
            reason = (
                f"daily_loss_limit_hit pnl={daily_pnl:.4f} "
                f"limit={daily_loss_limit:.4f} pause_until={tomorrow.isoformat()}"
            )
            logger.warning(reason)
            return RiskGateResult(False, reason)

        return RiskGateResult(True, "ok")

    def can_open(self, symbol: str, equity: float, has_exchange_position: bool) -> RiskGateResult:
        gate = self.refresh_loss_limits(equity)
        if not gate.allowed:
            return gate

        if has_exchange_position:
            return RiskGateResult(False, "exchange_position_already_open")

        open_trade = self.db.get_open_trade(symbol)
        if open_trade is not None:
            return RiskGateResult(False, "db_position_already_open")

        if self.config.max_positions_per_pair != 1:
            logger.warning(
                "max_positions_per_pair forced to 1 (config was %s)",
                self.config.max_positions_per_pair,
            )

        return RiskGateResult(True, "ok")

    def build_plan(
        self,
        side: SignalSide,
        entry: float,
        equity: float,
        *,
        amount_precision_fn,
        min_amount: float,
        sl_distance: float | None = None,
    ) -> TradePlan | None:
        """Compute qty / SL / TP from risk rules. Never increases size after losses."""
        if side not in (SignalSide.LONG, SignalSide.SHORT):
            return None
        if entry <= 0 or equity <= 0:
            return None

        cfg = self.config
        risk_pct = min(max(cfg.risk_per_trade_pct, 0.1), 10.0)
        rr = max(cfg.reward_risk_ratio, 1.5)

        risk_amount = equity * (risk_pct / 100.0)
        if sl_distance is None or sl_distance <= 0:
            sl_distance = entry * (cfg.stop_loss_pct / 100.0)
        if sl_distance <= 0:
            return None

        if side == SignalSide.LONG:
            stop_loss = entry - sl_distance
            take_profit = entry + sl_distance * rr
        else:
            stop_loss = entry + sl_distance
            take_profit = entry - sl_distance * rr

        raw_qty = risk_amount / sl_distance
        qty = float(amount_precision_fn(raw_qty))
        if qty < min_amount or qty <= 0:
            logger.warning(
                "Qty %.8f below minimum %.8f (equity=%.2f risk=%.4f sl_dist=%.4f)",
                qty,
                min_amount,
                equity,
                risk_amount,
                sl_distance,
            )
            return None

        reward_amount = risk_amount * rr
        return TradePlan(
            side=side,
            qty=qty,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
        )
