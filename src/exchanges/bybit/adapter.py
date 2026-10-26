"""Bybit (USDT perpetuals, via ccxt) behind the common ExchangeAdapter interface."""

from __future__ import annotations

import re
from typing import Any, Optional

from src.exchanges.base import ExchangeAdapter, OrderState
from src.exchanges.bybit import exchange as bybit
from src.exchanges.bybit import limit_order, market_order


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


class BybitAdapter(ExchangeAdapter):
    name = "bybit"
    label = "Bybit"
    quote_currency = "USDT"

    def client(self):
        return bybit.get_exchange()

    # ---------------- symbols ----------------

    def market_symbol(self, symbol: str) -> str:
        return bybit.format_symbol(self.bot_symbol(symbol))

    def has_market(self, symbol: str) -> bool:
        return bybit.validate_symbol(self.client(), self.market_symbol(symbol))

    # ---------------- market data / account ----------------

    def last_price(self, symbol: str) -> Optional[float]:
        return _num(self.client().fetch_ticker(self.market_symbol(symbol)).get("last"))

    def free_balance(self) -> float:
        balance = self.client().fetch_balance()
        return float((balance.get(self.quote_currency) or {}).get("free") or 0)

    def position_size(self, *, symbol, entry_price, stop_loss, leverage, risk_per_trade, side) -> dict:
        return bybit.calculate_position_size(
            entry_price=entry_price,
            stop_loss=stop_loss,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            side="buy" if str(side).lower() in ("buy", "long") else "sell",
            skip_validation=True,
            exchange=self.client(),
            symbol=self.market_symbol(symbol),
        )

    # ---------------- orders ----------------

    def place_market_order(self, *, symbol, side, entry_price, stop_loss, take_profit, leverage,
                           risk_per_trade, risk_reward_ratio, signal_timestamp=None) -> dict:
        return market_order.validate_and_place_order(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
        )

    def place_limit_order(self, *, symbol, side, entry_price, stop_loss, take_profit, leverage,
                          risk_per_trade, risk_reward_ratio, signal_timestamp=None, time_in_force="GTC") -> dict:
        return limit_order.validate_and_place_limit_order(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            risk_per_trade=risk_per_trade,
            risk_reward_ratio=risk_reward_ratio,
            signal_timestamp=signal_timestamp,
            time_in_force=time_in_force,
        )

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return limit_order.cancel_limit_order(symbol, order_id)

    def order_status(self, symbol: str, order_id: str) -> dict:
        return limit_order.check_limit_order_status(symbol, order_id)

    def order_state(self, symbol: str, order_id: str) -> Optional[OrderState]:
        client = self.client()
        market = self.market_symbol(symbol)
        params = {"orderId": order_id}
        found = self._match(client.fetch_open_orders(market, None, None, params), order_id)
        if found is None:
            # Order history: filled, cancelled and rejected orders (fetch_closed_orders only returns filled ones)
            history = getattr(client, "fetch_canceled_and_closed_orders", None)
            if history is not None:
                found = self._match(history(market, None, None, params), order_id)
        if found is None:
            return None
        info = found.get("info") or {}
        reject = str(info.get("rejectReason") or "")
        return {
            "status": str(found.get("status") or "").lower(),
            "filled": _num(found.get("filled")) or 0.0,
            "amount": _num(found.get("amount")) or 0.0,
            "average": _num(found.get("average")),
            "reject_reason": "" if reject in ("", "EC_NoError") else reject,
        }

    @staticmethod
    def _match(orders, order_id: str) -> Optional[dict]:
        for item in orders or []:
            if str(item.get("id")) == str(order_id):
                return item
        return None

    def set_position_sl_tp(self, symbol: str, side: str, stop_loss: float, take_profit: Optional[float]) -> dict:
        return bybit.set_position_sl_tp(
            exchange=self.client(),
            symbol=self.bot_symbol(symbol),
            side="buy" if str(side).lower() in ("buy", "long") else "sell",
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    # ---------------- messages ----------------

    def format_order_message(self, result: dict, order_type: str = "market") -> str:
        if str(order_type).lower() == "limit":
            return limit_order.format_limit_order_message(result or {})
        return market_order.format_market_order_message(result or {})

    def parse_error(self, text: str) -> Optional[str]:
        text = str(text or "")
        msg = re.search(r'"retMsg"\s*:\s*"([^"]+)"', text) or re.search(r"retMsg'\s*:\s*'([^']+)'", text)
        if not msg:
            return None
        code = re.search(r'"retCode"\s*:\s*(\d+)', text) or re.search(r"retCode'\s*:\s*(\d+)", text)
        return f"{msg.group(1)}{f' (Bybit {code.group(1)})' if code else ''}"[:300]
