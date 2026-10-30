"""
Read-only view of an exchange account: every order on it (placed by the bot, the
web chart or by hand on the exchange) and the open positions. Used by the web
chart's Orders tab (GET /api/exchange/orders).

Each exchange package that supports it has `account.py` with:

    LABEL: str                                   display name ("Bybit demo", ...)
    is_configured() -> bool                      API keys are set in .env
    fetch_account(symbol=None, since_ms=..., limit=..., symbols=()) -> AccountSnapshot

and is listed in ACCOUNT_VIEWS (src/exchanges/__init__.py). Nothing here places,
changes or cancels an order.

Symbols are bot symbols ("BTCUSDT"). Times are unix seconds.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class AccountOrder(TypedDict):
    id: str
    clientId: Optional[str]     # the client order id (Bybit orderLinkId, Lighter client order index)
    symbol: str                 # bot symbol, e.g. "BTCUSDT"
    side: str                   # buy | sell
    positionSide: Optional[str]  # LONG | SHORT: the position the order opens or closes (None when unknown)
    kind: str                   # entry | stop_loss | take_profit | close
    type: str                   # exchange order type, lower case (limit, market, stop_market, ...)
    status: str                 # open | filled | canceled | rejected | expired | triggered
    price: Optional[float]      # limit price (None for market orders)
    triggerPrice: Optional[float]
    average: Optional[float]    # average fill price
    amount: float
    filled: float
    reduceOnly: bool
    stopLoss: Optional[float]   # SL / TP attached to the order (Bybit)
    takeProfit: Optional[float]
    time: Optional[float]       # created
    updated: Optional[float]    # last change (fill / cancel)


class AccountPosition(TypedDict):
    symbol: str
    side: str                   # LONG | SHORT
    size: float
    entryPrice: Optional[float]
    markPrice: Optional[float]
    unrealizedPnl: Optional[float]
    liquidationPrice: Optional[float]
    leverage: Optional[float]
    stopLoss: Optional[float]
    takeProfit: Optional[float]
    time: Optional[float]


class AccountSnapshot(TypedDict):
    orders: list[AccountOrder]
    positions: list[AccountPosition]


ORDER_STATUSES = ("open", "filled", "canceled", "rejected", "expired", "triggered")
DAY_MS = 86_400_000


def num(value: Any) -> Optional[float]:
    """Float, or None for empty / zero / invalid values (exchanges send "" or "0" for 'not set')."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (0.0, float("inf"), float("-inf")) else None


def seconds(value: Any) -> Optional[float]:
    """Exchange time (ms or s, number or text) -> unix seconds."""
    number = num(value)
    if number is None:
        return None
    return number / 1000.0 if number > 1e11 else number


def order_kind(order_type: str, reduce_only: bool, stop_type: str = "") -> str:
    """entry | stop_loss | take_profit | close from the exchange's type texts."""
    text = f"{stop_type} {order_type}".lower().replace("-", "_").replace(" ", "_")
    if "take_profit" in text or "takeprofit" in text:
        return "take_profit"
    if "stop_loss" in text or "stoploss" in text:
        return "stop_loss"
    if "stop" in text or "trailing" in text:
        # A stop order that does not reduce a position is a stop entry
        return "stop_loss" if reduce_only else "entry"
    return "close" if reduce_only else "entry"


def position_side(side: str, kind: str) -> str:
    """The position an order belongs to: entries open their side, exits close the other one."""
    buy = side == "buy"
    if kind == "entry":
        return "LONG" if buy else "SHORT"
    return "SHORT" if buy else "LONG"


def make_order(**fields: Any) -> AccountOrder:
    side = str(fields.get("side") or "").lower()
    kind = fields.get("kind") or "entry"
    order: AccountOrder = {
        "id": str(fields.get("id") or ""),
        "clientId": str(fields["clientId"]) if fields.get("clientId") not in (None, "", 0, "0") else None,
        "symbol": str(fields.get("symbol") or ""),
        "side": "sell" if side.startswith("s") else "buy",
        "positionSide": fields.get("positionSide"),
        "kind": kind,
        "type": str(fields.get("type") or "").lower(),
        "status": fields.get("status") if fields.get("status") in ORDER_STATUSES else "open",
        "price": num(fields.get("price")),
        "triggerPrice": num(fields.get("triggerPrice")),
        "average": num(fields.get("average")),
        "amount": num(fields.get("amount")) or 0.0,
        "filled": num(fields.get("filled")) or 0.0,
        "reduceOnly": bool(fields.get("reduceOnly")),
        "stopLoss": num(fields.get("stopLoss")),
        "takeProfit": num(fields.get("takeProfit")),
        "time": seconds(fields.get("time")),
        "updated": seconds(fields.get("updated")) or seconds(fields.get("time")),
    }
    if not order["positionSide"]:
        order["positionSide"] = position_side(order["side"], kind)
    return order


def make_position(**fields: Any) -> AccountPosition:
    side = str(fields.get("side") or "").lower()
    return {
        "symbol": str(fields.get("symbol") or ""),
        "side": "SHORT" if side in ("short", "sell") else "LONG",
        "size": abs(num(fields.get("size")) or 0.0),
        "entryPrice": num(fields.get("entryPrice")),
        "markPrice": num(fields.get("markPrice")),
        "unrealizedPnl": num(fields.get("unrealizedPnl")),
        "liquidationPrice": num(fields.get("liquidationPrice")),
        "leverage": num(fields.get("leverage")),
        "stopLoss": num(fields.get("stopLoss")),
        "takeProfit": num(fields.get("takeProfit")),
        "time": seconds(fields.get("time")),
    }


def finish(orders: list[AccountOrder], positions: list[AccountPosition], limit: int) -> AccountSnapshot:
    """Drop duplicates (an order can be in both the open and the history list), newest first."""
    seen: dict[str, AccountOrder] = {}
    for order in orders:
        key = f"{order['symbol']}|{order['id']}"
        if key not in seen or order["status"] == "open":
            seen[key] = order
    ordered = sorted(seen.values(), key=lambda o: o["time"] or 0, reverse=True)
    # Open orders always stay, however many old ones there are
    open_orders = [o for o in ordered if o["status"] == "open"]
    others = [o for o in ordered if o["status"] != "open"][: max(0, limit - len(open_orders))]
    return {
        "orders": sorted(open_orders + others, key=lambda o: o["time"] or 0, reverse=True),
        "positions": [p for p in positions if p["size"] > 0],
    }
