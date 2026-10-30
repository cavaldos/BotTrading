"""
Read-only Binance USD-M futures account view: open orders (regular + algo / conditional),
order history and positions, in the format of src/exchanges/account.py.

Binance only returns order history per symbol, so history covers: the symbol asked
for, or else the symbols with an open order / position plus `symbols` (the bot's).
"""

from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from src.exchanges.account import (
    DAY_MS, AccountOrder, AccountSnapshot, finish, make_order, make_position, order_kind,
)
from src.exchanges.binance.exchange import format_symbol, get_exchange

LABEL = "Binance"

HISTORY_WINDOW_MS = 7 * DAY_MS  # allOrders: at most 7 days per request
MAX_HISTORY_SYMBOLS = 12  # all-symbols view: allOrders costs 5 weight per symbol and window

_STATUS = {
    "open": "open",
    "closed": "filled",
    "canceled": "canceled",
    "expired": "expired",
    "rejected": "rejected",
}
_ALGO_STATUS = {
    "NEW": "open",
    "TRIGGERING": "open",
    "TRIGGERED": "triggered",
    "FINISHED": "filled",
    "CANCELED": "canceled",
    "CANCELLED": "canceled",
    "EXPIRED": "expired",
    "REJECTED": "rejected",
}


def is_configured() -> bool:
    return bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET"))


def _bot_symbol(value: str) -> str:
    """'BTC/USDT:USDT' or 'BTCUSDT' -> 'BTCUSDT'."""
    return str(value or "").split(":", 1)[0].replace("/", "").upper()


def _parse(item: dict) -> AccountOrder:
    """ccxt unified order."""
    info = item.get("info") or {}
    raw_type = str(info.get("type") or info.get("origType") or item.get("type") or "")
    reduce_only = bool(item.get("reduceOnly")) or str(info.get("closePosition")).lower() == "true"
    kind = order_kind(raw_type, reduce_only)
    position = str(info.get("positionSide") or "").upper()
    return make_order(
        id=item.get("id"),
        clientId=item.get("clientOrderId"),
        symbol=_bot_symbol(item.get("symbol")),
        side=item.get("side"),
        positionSide=position if position in ("LONG", "SHORT") else None,
        kind=kind,
        type=raw_type,
        status=_STATUS.get(str(item.get("status")), "canceled"),
        price=item.get("price") if "MARKET" not in raw_type.upper() else None,
        triggerPrice=item.get("triggerPrice") or item.get("stopPrice"),
        average=item.get("average"),
        amount=item.get("amount"),
        filled=item.get("filled"),
        reduceOnly=reduce_only,
        time=item.get("timestamp"),
        updated=item.get("lastUpdateTimestamp") or info.get("updateTime"),
    )


def _parse_algo(item: dict) -> AccountOrder:
    """Conditional (algo) order, raw /fapi/v1/openAlgoOrders or allAlgoOrders row."""
    raw_type = str(item.get("orderType") or item.get("type") or "")
    reduce_only = str(item.get("reduceOnly")).lower() == "true" or str(item.get("closePosition")).lower() == "true"
    position = str(item.get("positionSide") or "").upper()
    return make_order(
        id=f"algo:{item.get('algoId')}",
        clientId=item.get("clientAlgoId"),
        symbol=_bot_symbol(item.get("symbol")),
        side=str(item.get("side") or "").lower(),
        positionSide=position if position in ("LONG", "SHORT") else None,
        kind=order_kind(raw_type, reduce_only),
        type=raw_type,
        status=_ALGO_STATUS.get(str(item.get("algoStatus") or "").upper(), "open"),
        price=item.get("price") if "MARKET" not in raw_type.upper() else None,
        triggerPrice=item.get("triggerPrice"),
        average=item.get("actualPrice") or item.get("avgPrice"),
        amount=item.get("quantity"),
        filled=item.get("executedQty"),
        reduceOnly=reduce_only,
        time=item.get("createTime"),
        updated=item.get("updateTime"),
    )


def _algo(call, params: dict) -> list[dict]:
    """Algo order rows; [] when the endpoint is not available (older API / no permission)."""
    try:
        result = call(params)
    except Exception:
        return []
    return _rows(result)


def _rows(result) -> list[dict]:
    if isinstance(result, dict):
        result = result.get("orders") or result.get("rows") or result.get("data") or []
    return [r for r in result or [] if isinstance(r, dict)]


def fetch_account(
    symbol: Optional[str] = None,
    since_ms: Optional[int] = None,
    limit: int = 200,
    symbols: Iterable[str] = (),
) -> AccountSnapshot:
    exchange = get_exchange()
    market = format_symbol(symbol) if symbol else None

    open_orders = [_parse(o) for o in exchange.fetch_open_orders(market)]
    algo_params = {"symbol": _bot_symbol(symbol)} if symbol else {}
    open_orders += [_parse_algo(o) for o in _algo(exchange.fapiPrivateGetOpenAlgoOrders, algo_params)]

    raw_positions = exchange.fetch_positions([market] if market else None)
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
            time=p.get("timestamp"),
        )
        for p in raw_positions
    ]

    if symbol:
        history_symbols = [_bot_symbol(symbol)]
    else:
        wanted = [o["symbol"] for o in open_orders] + [p["symbol"] for p in positions if p["size"]]
        wanted += [_bot_symbol(s) for s in symbols]
        history_symbols = list(dict.fromkeys(s for s in wanted if s))[:MAX_HISTORY_SYMBOLS]

    now = int(time.time() * 1000)
    since = since_ms if since_ms is not None else now - HISTORY_WINDOW_MS
    if not symbol:
        since = max(since, now - HISTORY_WINDOW_MS)  # one request per symbol
    history: list[AccountOrder] = []
    for name in history_symbols:
        end = now
        while end > since:
            start = max(since, end - HISTORY_WINDOW_MS + 1)
            rows = exchange.fetch_orders(format_symbol(name), start, 500, {"until": end})
            history.extend(_parse(o) for o in rows)
            algo = _algo(exchange.fapiPrivateGetAllAlgoOrders, {"symbol": name, "startTime": start, "endTime": end, "limit": 100})
            history.extend(_parse_algo(o) for o in algo)
            end = start - 1

    return finish(open_orders + history, positions, limit)
