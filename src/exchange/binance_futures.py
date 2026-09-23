"""Binance USDT-M Futures exchange wrapper (demo trading only).

Binance deprecated the classic Futures testnet for private API calls.
Paper trading now uses Demo Trading (demo-fapi.binance.com) via ccxt
`enable_demo_trading(True)`.

No withdraw / transfer methods are exposed — intentionally.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

# Allowed paper-trading hosts (demo trading). Classic testnet is deprecated for private calls.
PAPER_HOST_MARKERS = ("demo-fapi.binance.com", "demo-dapi.binance.com")
FORBIDDEN_LIVE_MARKERS = ("fapi.binance.com", "dapi.binance.com")


class TestnetGuardError(RuntimeError):
    """Raised when the client is not pointed at Binance demo / paper trading."""


class BinanceFuturesClient:
    """Thin ccxt wrapper around Binance USD-M Futures demo trading."""

    def __init__(self, api_key: str, api_secret: str, *, require_testnet: bool = True):
        self._require_testnet = require_testnet
        self.exchange = ccxt.binanceusdm(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
                    "adjustForTimeDifference": True,
                },
            }
        )
        # Classic set_sandbox_mode(True) is deprecated for Futures private endpoints.
        # Use Binance Demo Trading instead (https://demo.binance.com).
        self.exchange.enable_demo_trading(True)
        self._assert_paper_trading()
        self.exchange.load_markets()
        logger.info(
            "Connected to Binance Futures DEMO trading (%s)", self._fapi_base_url()
        )

    def _fapi_base_url(self) -> str:
        urls = self.exchange.urls.get("api", {})
        if isinstance(urls, dict):
            return str(
                urls.get("fapiPublic")
                or urls.get("fapiPrivate")
                or urls.get("public")
                or urls
            )
        return str(urls)

    def _assert_paper_trading(self) -> None:
        if not self._require_testnet:
            return
        base = self._fapi_base_url().lower()
        looks_paper = any(m in base for m in PAPER_HOST_MARKERS)
        looks_live = any(m in base for m in FORBIDDEN_LIVE_MARKERS) and "demo-" not in base
        if looks_live or not looks_paper:
            raise TestnetGuardError(
                f"Refusing to run: exchange URL is not Binance demo trading ({base}). "
                "Create API keys at https://demo.binance.com/en/my/settings/api-management"
            )
        logger.info("Demo-trading guard OK: %s", base)

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> pd.DataFrame:
        """Return OHLCV as a DataFrame with columns open/high/low/close/volume."""
        raw = self._call_with_retry(
            self.exchange.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit
        )
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    def fetch_ticker_price(self, symbol: str) -> float:
        ticker = self._call_with_retry(self.exchange.fetch_ticker, symbol)
        return float(ticker["last"])

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def fetch_equity(self) -> float:
        """USDT wallet equity (balance + unrealized PnL when available)."""
        balance = self._call_with_retry(self.exchange.fetch_balance)
        usdt = balance.get("USDT") or {}
        total = usdt.get("total")
        if total is not None:
            return float(total)
        info = balance.get("info") or {}
        if isinstance(info, dict) and "totalWalletBalance" in info:
            return float(info["totalWalletBalance"])
        # Fallback: sum free+used
        free = float(usdt.get("free") or 0)
        used = float(usdt.get("used") or 0)
        return free + used

    def fetch_position(self, symbol: str) -> dict[str, Any] | None:
        """Return open position for symbol, or None if flat."""
        positions = self._call_with_retry(self.exchange.fetch_positions, [symbol])
        for pos in positions:
            contracts = abs(float(pos.get("contracts") or 0))
            if contracts > 0:
                return pos
        return None

    def has_open_position(self, symbol: str) -> bool:
        return self.fetch_position(symbol) is not None

    # ------------------------------------------------------------------
    # Orders (trading only — no withdraw)
    # ------------------------------------------------------------------

    def create_market_order(
        self, symbol: str, side: str, amount: float, *, reduce_only: bool = False
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        return self._call_with_retry(
            self.exchange.create_order,
            symbol,
            "market",
            side,
            amount,
            None,
            params,
        )

    def create_stop_market(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        *,
        reduce_only: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "stopPrice": self.exchange.price_to_precision(symbol, stop_price),
            "reduceOnly": reduce_only,
            "workingType": "MARK_PRICE",
        }
        return self._call_with_retry(
            self.exchange.create_order,
            symbol,
            "STOP_MARKET",
            side,
            amount,
            None,
            params,
        )

    def create_take_profit_market(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        *,
        reduce_only: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "stopPrice": self.exchange.price_to_precision(symbol, stop_price),
            "reduceOnly": reduce_only,
            "workingType": "MARK_PRICE",
        }
        return self._call_with_retry(
            self.exchange.create_order,
            symbol,
            "TAKE_PROFIT_MARKET",
            side,
            amount,
            None,
            params,
        )

    def cancel_all_orders(self, symbol: str) -> None:
        try:
            self._call_with_retry(self.exchange.cancel_all_orders, symbol)
        except ccxt.OrderNotFound:
            logger.debug("No open orders to cancel for %s", symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel_all_orders(%s) failed: %s", symbol, exc)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(symbol, price))

    def min_amount(self, symbol: str) -> float:
        market = self.exchange.market(symbol)
        limits = market.get("limits") or {}
        amount = limits.get("amount") or {}
        return float(amount.get("min") or 0)

    # ------------------------------------------------------------------
    # Resilience
    # ------------------------------------------------------------------

    def _call_with_retry(self, fn, *args, retries: int = 5, **kwargs) -> Any:
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.RateLimitExceeded, ccxt.DDoSProtection) as exc:
                last_exc = exc
                logger.warning(
                    "Rate limit on %s (attempt %s/%s): %s — sleeping %.1fs",
                    getattr(fn, "__name__", str(fn)),
                    attempt,
                    retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable) as exc:
                last_exc = exc
                logger.warning(
                    "Network error on %s (attempt %s/%s): %s — sleeping %.1fs",
                    getattr(fn, "__name__", str(fn)),
                    attempt,
                    retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except ccxt.ExchangeError:
                raise
        assert last_exc is not None
        raise last_exc
