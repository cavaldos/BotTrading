"""Lighter (perp DEX, own signer bindings) behind the common ExchangeAdapter interface."""

from __future__ import annotations

from typing import Optional

from src.exchanges.base import ExchangeAdapter, OrderState
from src.exchanges.lighter import exchange as lighter
from src.exchanges.lighter import limit_order, market_order


class LighterAdapter(ExchangeAdapter):
    name = "lighter"
    label = "Lighter"
    quote_currency = "USDC"

    def client(self):
        return lighter.get_exchange()

    # ---------------- symbols ----------------

    def market_symbol(self, symbol: str) -> str:
        return lighter.format_symbol(self.bot_symbol(symbol))

    def has_market(self, symbol: str) -> bool:
        return lighter.validate_symbol(self.client(), self.market_symbol(symbol))

    # ---------------- market data / account ----------------

    def last_price(self, symbol: str) -> Optional[float]:
        price = self.client().fetch_ticker(self.market_symbol(symbol)).get("last")
        return float(price) if price is not None else None

    def free_balance(self) -> float:
        balance = self.client().fetch_balance()
        return float((balance.get(self.quote_currency) or {}).get("free") or 0)

    def position_size(self, *, symbol, entry_price, stop_loss, leverage, risk_per_trade, side) -> dict:
        return lighter.calculate_position_size(
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
        order, _ = self.client().find_order(order_id, self.market_symbol(symbol))
        if order is None:
            return None
        status = str(order.get("status") or "").lower()
        return {
            "status": status,
            "filled": float(order.get("filled") or 0.0),
            "amount": float(order.get("amount") or 0.0),
            "average": order.get("average"),
            "reject_reason": str(order.get("raw_status") or "") if status == "rejected" else "",
        }

    def set_position_sl_tp(self, symbol: str, side: str, stop_loss: float, take_profit: Optional[float]) -> dict:
        return lighter.set_position_sl_tp(
            exchange=self.client(),
            symbol=self.market_symbol(symbol),
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    # ---------------- messages ----------------

    def format_order_message(self, result: dict, order_type: str = "market") -> str:
        if str(order_type).lower() == "limit":
            return limit_order.format_limit_order_message(result or {})
        return market_order.format_market_order_message(result or {})
