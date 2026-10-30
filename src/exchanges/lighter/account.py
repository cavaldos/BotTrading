"""
Read-only Lighter account view: open orders, closed orders and positions, in the
format of src/exchanges/account.py. Needs the API key (Lighter only shows orders
with an auth token). About 3 requests per call (Lighter allows 60 per minute).
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from src.exchanges.account import AccountOrder, AccountSnapshot, finish, make_order, make_position, order_kind
from src.exchanges.lighter.exchange import TESTNET_URL, get_base_url, get_exchange

LABEL = "Lighter"

_STATUS = {
    "open": "open",
    "closed": "filled",
    "canceled": "canceled",
    "expired": "expired",
    "rejected": "rejected",
}


def label() -> str:
    return f"{LABEL} testnet" if get_base_url() == TESTNET_URL else LABEL


def is_configured() -> bool:
    return bool(os.getenv("LIGHTER_API_PRIVATE_KEY") and os.getenv("LIGHTER_ACCOUNT_INDEX"))


def _bot_symbol(market: str) -> str:
    """Lighter market 'BTC' -> bot symbol 'BTCUSDT'."""
    return f"{str(market or '').upper()}USDT"


def _parse(item: dict) -> AccountOrder:
    info = item.get("info") or {}
    return make_order(
        id=item.get("id"),
        clientId=item.get("clientOrderId"),
        symbol=_bot_symbol(item.get("symbol")),
        side=item.get("side"),
        kind=order_kind(str(item.get("type") or ""), bool(item.get("reduceOnly"))),
        type=item.get("type"),
        status=_STATUS.get(str(item.get("status")), "canceled"),
        price=item.get("price") if "market" not in str(item.get("type") or "") else None,
        triggerPrice=item.get("triggerPrice"),
        average=item.get("average"),
        amount=item.get("amount"),
        filled=item.get("filled"),
        reduceOnly=item.get("reduceOnly"),
        # Lighter's `timestamp` is the last change: created_at is when the order was placed
        time=info.get("created_at") or item.get("timestamp"),
        updated=info.get("updated_at") or item.get("updated") or item.get("timestamp"),
    )


def fetch_account(
    symbol: Optional[str] = None,
    since_ms: Optional[int] = None,
    limit: int = 200,
    symbols: Iterable[str] = (),
) -> AccountSnapshot:
    exchange = get_exchange()
    open_orders = exchange.fetch_open_orders(symbol)
    closed = exchange.fetch_closed_orders(symbol=symbol, limit=limit, since=since_ms)
    positions = [
        make_position(
            symbol=_bot_symbol(p.get("symbol")),
            side=p.get("side"),
            size=p.get("contracts"),
            entryPrice=p.get("entryPrice"),
            markPrice=p.get("markPrice"),
            unrealizedPnl=p.get("unrealizedPnl"),
            liquidationPrice=p.get("liquidationPrice"),
            leverage=p.get("leverage"),
        )
        for p in exchange.fetch_positions([symbol] if symbol else None)
    ]
    return finish([_parse(o) for o in open_orders + closed], positions, limit)
