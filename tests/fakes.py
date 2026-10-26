"""Test doubles: an in-memory exchange that records what the bot asks it to do."""

from __future__ import annotations

from typing import Optional

from src.exchanges.base import ExchangeAdapter, OrderState


class FakeExchange(ExchangeAdapter):
    name = "fake"
    label = "Fake"
    quote_currency = "USDT"

    def __init__(self, price: float = 100.0, balance: float = 1000.0):
        self.price = price
        self.balance = balance
        self.markets = {"BTCUSDT", "ETHUSDT"}
        self.calls: list[tuple[str, dict]] = []
        self.states: dict[str, OrderState] = {}
        self._next_id = 1

    def client(self):
        return self

    def market_symbol(self, symbol: str) -> str:
        return self.bot_symbol(symbol)

    def has_market(self, symbol: str) -> bool:
        return self.market_symbol(symbol) in self.markets

    def last_price(self, symbol: str) -> Optional[float]:
        return self.price

    def free_balance(self) -> float:
        return self.balance

    def position_size(self, *, symbol, entry_price, stop_loss, leverage, risk_per_trade, side) -> dict:
        quantity = round(risk_per_trade / abs(entry_price - stop_loss), 6)
        notional = quantity * entry_price
        return {
            "quantity": quantity,
            "actual_risk": round(quantity * abs(entry_price - stop_loss), 2),
            "notional_value": notional,
            "margin_required": notional / leverage,
        }

    def _place(self, kind: str, **kwargs) -> dict:
        self.calls.append((kind, kwargs))
        order_id = f"ord-{self._next_id}"
        self._next_id += 1
        self.states[order_id] = {"status": "open", "filled": 0.0, "amount": 1.0, "average": None, "reject_reason": ""}
        side = "buy" if str(kwargs["side"]).lower() in ("buy", "long") else "sell"
        return {
            "success": True,
            "order_id": order_id,
            "symbol": self.bot_symbol(kwargs["symbol"]),
            "entry_price": kwargs["entry_price"],
            "stop_loss": kwargs["stop_loss"],
            "take_profit": kwargs["take_profit"],
            "contracts": 1.0,
            "final_quantity": 1.0,
            "position_info": {"side": side, "leverage": kwargs["leverage"], "quantity": 1.0},
        }

    def place_market_order(self, **kwargs) -> dict:
        return self._place("market", **kwargs)

    def place_limit_order(self, **kwargs) -> dict:
        return self._place("limit", **kwargs)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        self.calls.append(("cancel", {"symbol": symbol, "order_id": order_id}))
        if order_id in self.states:
            self.states[order_id] = dict(self.states[order_id], status="canceled")
        return {"success": True, "order_id": order_id}

    def order_status(self, symbol: str, order_id: str) -> dict:
        state = self.states.get(order_id)
        return {"success": state is not None, **(state or {})}

    def order_state(self, symbol: str, order_id: str) -> Optional[OrderState]:
        return self.states.get(order_id)

    def set_position_sl_tp(self, symbol, side, stop_loss, take_profit) -> dict:
        self.calls.append(("sl_tp", {"symbol": symbol, "stop_loss": stop_loss, "take_profit": take_profit}))
        return {"success": True}

    def format_order_message(self, result: dict, order_type: str = "market") -> str:
        if not result.get("success"):
            return f"❌ THẤT BẠI\n└─ Lỗi: {result.get('error')}"
        return f"FAKE {order_type} {result.get('symbol')} {result.get('order_id')}"
