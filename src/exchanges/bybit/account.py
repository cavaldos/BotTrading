"""
Read-only Bybit account view (USDT perpetuals): open orders, order history and
positions, in the format of src/exchanges/account.py. Uses the v5 endpoints
directly (same calls as tools/get_history.py).
"""

from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from src.exchanges.account import (
    DAY_MS, AccountOrder, AccountSnapshot, finish, make_order, make_position, order_kind,
)
from src.exchanges.bybit.exchange import format_symbol, get_exchange

LABEL = "Bybit demo"

HISTORY_WINDOW_MS = 7 * DAY_MS  # Bybit order history: at most 7 days per request
PAGE = 50

_STATUS = {
    "New": "open",
    "PartiallyFilled": "open",
    "Untriggered": "open",
    "Active": "open",
    "Filled": "filled",
    "PartiallyFilledCanceled": "canceled",
    "Cancelled": "canceled",
    "Deactivated": "canceled",
    "Rejected": "rejected",
    "Triggered": "triggered",
}
_POSITION_IDX = {"1": "LONG", "2": "SHORT"}


def is_configured() -> bool:
    return bool(os.getenv("BYBIT_DEMO_API_KEY") and os.getenv("BYBIT_DEMO_SECRET"))


def _api_symbol(symbol: str) -> str:
    return format_symbol(symbol).replace("/", "").replace(":USDT", "")


def _check(result: dict) -> dict:
    if str(result.get("retCode")) != "0":
        raise RuntimeError(f"Bybit: {result.get('retMsg') or result.get('retCode')}")
    return result.get("result") or {}


def _parse(item: dict) -> AccountOrder:
    reduce_only = bool(item.get("reduceOnly")) or bool(item.get("closeOnTrigger"))
    stop_type = str(item.get("stopOrderType") or "")
    kind = order_kind(str(item.get("orderType") or ""), reduce_only, stop_type)
    return make_order(
        id=item.get("orderId"),
        clientId=item.get("orderLinkId"),
        symbol=str(item.get("symbol") or ""),
        side=str(item.get("side") or ""),
        positionSide=_POSITION_IDX.get(str(item.get("positionIdx"))),
        kind=kind,
        type=f"{stop_type} {item.get('orderType')}".strip() if stop_type else item.get("orderType"),
        status=_STATUS.get(str(item.get("orderStatus")), "canceled"),
        # Market orders carry a protection price: only show it for limits
        price=item.get("price") if str(item.get("orderType")) == "Limit" else None,
        triggerPrice=item.get("triggerPrice"),
        average=item.get("avgPrice"),
        amount=item.get("qty"),
        filled=item.get("cumExecQty"),
        reduceOnly=reduce_only,
        stopLoss=item.get("stopLoss"),
        takeProfit=item.get("takeProfit"),
        time=item.get("createdTime"),
        updated=item.get("updatedTime"),
    )


def _pages(call, params: dict, max_items: int) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while len(items) < max_items:
        page = dict(params, limit=PAGE)
        if cursor:
            page["cursor"] = cursor
        result = _check(call(page))
        rows = result.get("list") or []
        items.extend(rows)
        cursor = result.get("nextPageCursor")
        if not cursor or not rows:
            break
    return items


def fetch_account(
    symbol: Optional[str] = None,
    since_ms: Optional[int] = None,
    limit: int = 200,
    symbols: Iterable[str] = (),
) -> AccountSnapshot:
    exchange = get_exchange()
    base = {"category": "linear"}
    scope = {"symbol": _api_symbol(symbol)} if symbol else {"settleCoin": "USDT"}

    open_rows = _pages(exchange.privateGetV5OrderRealtime, dict(base, **scope), 200)

    # History: newest 7-day window first, until `since_ms` or `limit` orders
    now = int(time.time() * 1000)
    since = since_ms if since_ms is not None else now - HISTORY_WINDOW_MS
    history_rows: list[dict] = []
    end = now
    while end > since and len(history_rows) < limit:
        start = max(since, end - HISTORY_WINDOW_MS)
        params = dict(base, startTime=start, endTime=end)
        if symbol:
            params["symbol"] = _api_symbol(symbol)
        history_rows.extend(_pages(exchange.privateGetV5OrderHistory, params, limit - len(history_rows)))
        end = start

    positions = [
        make_position(
            symbol=p.get("symbol"),
            side=_POSITION_IDX.get(str(p.get("positionIdx"))) or p.get("side"),
            size=p.get("size"),
            entryPrice=p.get("avgPrice"),
            markPrice=p.get("markPrice"),
            unrealizedPnl=p.get("unrealisedPnl"),
            liquidationPrice=p.get("liqPrice"),
            leverage=p.get("leverage"),
            stopLoss=p.get("stopLoss"),
            takeProfit=p.get("takeProfit"),
            time=p.get("updatedTime") or p.get("createdTime"),
        )
        for p in _check(exchange.privateGetV5PositionList(dict(base, **scope))).get("list") or []
    ]
    orders = [_parse(row) for row in open_rows + history_rows]
    return finish(orders, positions, limit)
