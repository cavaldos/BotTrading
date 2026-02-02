"""
Order manager for Bybit: find, check status, and cancel orders by order ID.
"""

from __future__ import annotations

import argparse
from typing import Dict, Optional, Tuple

from src.trade.bybit.exchange import get_exchange


def _extract_order_id(order: Dict) -> Optional[str]:
    return order.get("id") or order.get("info", {}).get("orderId")


def _find_order_in_list(orders: list, order_id: str) -> Optional[Dict]:
    for order in orders:
        if _extract_order_id(order) == order_id:
            return order
    return None


def find_order_by_id(exchange, order_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Find an order across open/closed/canceled lists using only order_id.
    Returns (order, source).
    """
    try:
        open_orders = exchange.fetch_open_orders()
    except Exception:
        open_orders = []

    order = _find_order_in_list(open_orders, order_id)
    if order:
        return order, "open"

    try:
        closed_orders = exchange.fetch_closed_orders()
    except Exception:
        closed_orders = []

    order = _find_order_in_list(closed_orders, order_id)
    if order:
        return order, "closed"

    try:
        canceled_orders = exchange.fetch_canceled_orders()
    except Exception:
        canceled_orders = []

    order = _find_order_in_list(canceled_orders, order_id)
    if order:
        return order, "canceled"

    return None, None


def get_order_status_by_id(order_id: str) -> Dict:
    """
    Get order status using only order_id.
    """
    exchange = get_exchange()
    order, source = find_order_by_id(exchange, order_id)

    if not order:
        return {
            "success": False,
            "error": "Không tìm thấy order với ID đã cung cấp",
            "order_id": order_id,
        }

    return {
        "success": True,
        "order_id": order_id,
        "status": order.get("status", source),
        "symbol": order.get("symbol"),
        "source": source,
        "order": order,
    }


def cancel_order_by_id(order_id: str) -> Dict:
    """
    Cancel an open order using only order_id.
    """
    exchange = get_exchange()
    order, source = find_order_by_id(exchange, order_id)

    if not order:
        return {
            "success": False,
            "error": "Không tìm thấy order với ID đã cung cấp",
            "order_id": order_id,
        }

    status = order.get("status")
    if source != "open" and status not in ("open", "new"):
        return {
            "success": False,
            "error": "Order không còn mở để hủy",
            "order_id": order_id,
            "status": status or source,
            "symbol": order.get("symbol"),
        }

    symbol = order.get("symbol")
    if not symbol:
        return {
            "success": False,
            "error": "Không tìm thấy symbol của order",
            "order_id": order_id,
        }

    try:
        result = exchange.cancel_order(order_id, symbol)
        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "result": result,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "order_id": order_id,
            "symbol": symbol,
        }


def format_order_summary(order: Dict, source: str) -> str:
    symbol = order.get("symbol", "N/A")
    side = (order.get("side") or "N/A").upper()
    order_type = (order.get("type") or "N/A").upper()
    status = (order.get("status") or source or "N/A").upper()
    amount = order.get("amount") or 0
    price = order.get("price") or 0
    filled = order.get("filled") or 0

    return (
        "ORDER INFO\n"
        f"- ID: {order.get('id', 'N/A')}\n"
        f"- Symbol: {symbol}\n"
        f"- Side: {side}\n"
        f"- Type: {order_type}\n"
        f"- Status: {status}\n"
        f"- Amount: {amount}\n"
        f"- Price: {price}\n"
        f"- Filled: {filled}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bybit order manager")
    parser.add_argument("order_id", help="Order ID")
    parser.add_argument(
        "--action",
        choices=["status", "cancel", "info"],
        default="status",
        help="Action to perform",
    )
    args = parser.parse_args()

    if args.action == "cancel":
        result = cancel_order_by_id(args.order_id)
        print(result)
        return

    exchange = get_exchange()
    order, source = find_order_by_id(exchange, args.order_id)

    if not order:
        print({
            "success": False,
            "error": "Không tìm thấy order với ID đã cung cấp",
            "order_id": args.order_id,
        })
        return

    if args.action == "info":
        print(format_order_summary(order, source or "unknown"))
        return

    status_result = {
        "success": True,
        "order_id": args.order_id,
        "status": order.get("status", source),
        "symbol": order.get("symbol"),
        "source": source,
    }
    print(status_result)


if __name__ == "__main__":
    main()
