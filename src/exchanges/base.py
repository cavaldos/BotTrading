"""
The contract every trading exchange implements.

The live bot (signal sender, order tracker, web order ticket) only talks to an
`ExchangeAdapter`, never to an exchange package directly. To trade on another
exchange, write `src/exchanges/<name>/adapter.py` with a subclass of
`ExchangeAdapter`, add it to `TRADING_ADAPTERS` in `src/exchanges/__init__.py`
and set `exchange: <name>` in live.yaml. Nothing else changes.

Symbols: callers pass bot symbols ("BTCUSDT" or "BTCUSDT_1m"); the adapter
maps them to the exchange market itself (see `market_symbol`).

Sides: callers pass "buy"/"sell" or "LONG"/"SHORT"; adapters accept both.

Order results (place_market_order / place_limit_order) are dicts with at least:
    success: bool
    error: str                      (when success is False)
    order_id: str
    symbol: str
    entry_price: float              (fill price, or the limit price)
    stop_loss, take_profit: float   (levels set on the exchange)
    contracts / final_quantity      (order size)
    position_info: dict             (side, leverage, quantity, expected_risk)
    sl_tp_warning: str              (market orders: SL/TP could not be set)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, TypedDict

from src.core.symbols import strip_timeframe


class OrderState(TypedDict):
    """An order as the exchange reports it (used by the order tracker)."""

    status: str           # open | closed (filled) | canceled | rejected | expired
    filled: float         # filled size
    amount: float         # order size (0 when unknown)
    average: Optional[float]  # average fill price
    reject_reason: str    # "" when none


class ExchangeAdapter(ABC):
    name: str = ""            # registry key, as written in live.yaml (e.g. "bybit")
    label: str = ""           # display name (e.g. "Bybit")
    quote_currency: str = "USDT"

    # ---------------- connection ----------------

    @abstractmethod
    def client(self) -> Any:
        """The native client (ccxt instance, LighterExchange, ...), created once."""

    # ---------------- symbols ----------------

    @staticmethod
    def bot_symbol(symbol: str) -> str:
        """'BTCUSDT_1m' -> 'BTCUSDT': the symbol the bot uses everywhere (files, web, tracker)."""
        return strip_timeframe(symbol).upper()

    @abstractmethod
    def market_symbol(self, symbol: str) -> str:
        """Bot symbol -> the exchange market id ('BTC/USDT:USDT' on Bybit, 'BTC' on Lighter)."""

    @abstractmethod
    def has_market(self, symbol: str) -> bool:
        """True when the symbol can be traded on this exchange."""

    # ---------------- market data / account ----------------

    @abstractmethod
    def last_price(self, symbol: str) -> Optional[float]:
        """Last traded price. Raises on API errors."""

    @abstractmethod
    def free_balance(self) -> float:
        """Free margin in the quote currency. Raises on API errors."""

    @abstractmethod
    def position_size(
        self,
        *,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        leverage: int,
        risk_per_trade: float,
        side: str,
    ) -> dict:
        """Size for a trade risking `risk_per_trade` in quote currency.

        Returns at least: quantity, actual_risk, margin_required, notional_value.
        """

    # ---------------- orders ----------------

    @abstractmethod
    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float],
        leverage: int,
        risk_per_trade: float,
        risk_reward_ratio: float,
        signal_timestamp: Optional[float] = None,
    ) -> dict:
        """Market entry with SL/TP (TP from the RR ratio when None). Never raises."""

    @abstractmethod
    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float],
        leverage: int,
        risk_per_trade: float,
        risk_reward_ratio: float,
        signal_timestamp: Optional[float] = None,
        time_in_force: str = "GTC",
    ) -> dict:
        """Limit entry with SL/TP attached. Never raises."""

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """{'success': bool, 'error': str}. Never raises."""

    @abstractmethod
    def order_status(self, symbol: str, order_id: str) -> dict:
        """{'success', 'status', 'filled', 'remaining', 'average'}. Never raises."""

    @abstractmethod
    def order_state(self, symbol: str, order_id: str) -> Optional[OrderState]:
        """The order as the exchange sees it, or None when it does not know the id (yet).

        Raises on API errors (the order tracker retries)."""

    @abstractmethod
    def set_position_sl_tp(self, symbol: str, side: str, stop_loss: float, take_profit: Optional[float]) -> dict:
        """Replace the SL/TP of an open position. Never raises."""

    # ---------------- messages ----------------

    @abstractmethod
    def format_order_message(self, result: dict, order_type: str = "market") -> str:
        """Human readable text of an order result (Telegram / Discord / web)."""

    def parse_error(self, text: str) -> Optional[str]:
        """The exchange's own error message inside `text` (e.g. Bybit retMsg), or None when there is none."""
        return None

    def short_error(self, text: str) -> str:
        """One line from an API error text."""
        parsed = self.parse_error(text)
        if parsed:
            return parsed
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        return lines[0][:200] if lines else ""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
