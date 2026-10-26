"""
Order manager for Lighter: list open orders, find, check status and cancel orders by ID
(order index or client order index). Same functions as src/exchanges/bybit/tools/order_manager.py.

    python -m src.exchanges.lighter.tools.order_manager --action list
    python -m src.exchanges.lighter.tools.order_manager <order_id> --action status|info|cancel
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Dict, Optional, Tuple

from src.exchanges.lighter.exchange import LighterAPIError, LighterSignerError, get_exchange


def find_order_by_id(exchange, order_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Find an order in open, then recent closed orders, using only its ID.
    Returns (order, source) with source 'open' or 'closed'.
    """
    try:
        return exchange.find_order(order_id, closed_limit=200)
    except (LighterAPIError, LighterSignerError):
        return None, None


def get_order_status_by_id(order_id: str) -> Dict:
    exchange = get_exchange()
    order, source = find_order_by_id(exchange, order_id)
    if not order:
        return {"success": False, "error": "Không tìm thấy order với ID đã cung cấp", "order_id": order_id}
    return {
        "success": True,
        "order_id": order["id"],
        "status": order["status"],
        "raw_status": order["raw_status"],
        "symbol": order["symbol"],
        "source": source,
        "order": order,
    }


def cancel_order_by_id(order_id: str) -> Dict:
    """Cancel an open order using only its ID."""
    exchange = get_exchange()
    order, source = find_order_by_id(exchange, order_id)
    if not order:
        return {"success": False, "error": "Không tìm thấy order với ID đã cung cấp", "order_id": order_id}
    if source != "open":
        return {
            "success": False,
            "error": "Order không còn mở để hủy",
            "order_id": order["id"],
            "status": order["raw_status"],
            "symbol": order["symbol"],
        }
    try:
        result = exchange.cancel_order(order["id"], order["symbol"])
        return {"success": True, "order_id": order["id"], "symbol": order["symbol"], "result": result}
    except (LighterAPIError, LighterSignerError) as e:
        return {"success": False, "error": str(e), "order_id": order["id"], "symbol": order["symbol"]}


def format_order_summary(order: Dict, source: str) -> str:
    created = datetime.fromtimestamp(order["timestamp"] / 1000).strftime("%Y-%m-%d %H:%M:%S") if order.get("timestamp") else "N/A"
    lines = [
        "ORDER INFO",
        f"- ID: {order.get('id', 'N/A')} (client {order.get('clientOrderId') or '-'})",
        f"- Symbol: {order.get('symbol', 'N/A')}",
        f"- Side: {(order.get('side') or 'N/A').upper()}",
        f"- Type: {(order.get('type') or 'N/A').upper()}{' (reduce only)' if order.get('reduceOnly') else ''}",
        f"- Status: {(order.get('raw_status') or source or 'N/A').upper()}",
        f"- Amount: {order.get('amount')}",
        f"- Price: {order.get('price')}",
    ]
    if order.get("triggerPrice"):
        lines.append(f"- Trigger: {order['triggerPrice']} ({order.get('triggerStatus') or '-'})")
    lines += [
        f"- Filled: {order.get('filled')} (avg {order.get('average') or '-'})",
        f"- Created: {created}",
    ]
    return "\n".join(lines)


def print_open_orders() -> None:
    exchange = get_exchange()
    orders = exchange.fetch_open_orders()
    if not orders:
        print("Không có lệnh đang mở.")
        return
    print(f"{'Order ID':<18} {'Client ID':<15} {'Symbol':<9} {'Side':<5} {'Type':<18} {'Amount':>12} {'Price':>12} {'Trigger':>12} {'Status':<10}")
    print("-" * 120)
    for order in orders:
        print(
            f"{order['id']:<18} {order['clientOrderId'] or '-':<15} {order['symbol']:<9} {order['side'].upper():<5} "
            f"{order['type']:<18} {order['remaining']:>12g} {order['price'] or 0:>12g} {order['triggerPrice'] or 0:>12g} {order['raw_status']:<10}"
        )
    print(f"\nTổng: {len(orders)} lệnh")


def main() -> None:
    try:
        _run_cli()
    except (LighterAPIError, LighterSignerError) as e:
        print(f"❌ {e}")


def _run_cli() -> None:
    parser = argparse.ArgumentParser(description="Lighter order manager")
    parser.add_argument("order_id", nargs="?", help="Order ID (order index hoặc client order index)")
    parser.add_argument("--action", choices=["list", "status", "cancel", "info"], default="status")
    args = parser.parse_args()

    if args.action == "list":
        print_open_orders()
        return
    if not args.order_id:
        parser.error("cần order_id cho status / info / cancel")

    if args.action == "cancel":
        print(cancel_order_by_id(args.order_id))
        return

    exchange = get_exchange()
    order, source = find_order_by_id(exchange, args.order_id)
    if not order:
        print({"success": False, "error": "Không tìm thấy order với ID đã cung cấp", "order_id": args.order_id})
        return
    if args.action == "info":
        print(format_order_summary(order, source or "unknown"))
        return
    print({
        "success": True,
        "order_id": order["id"],
        "status": order["raw_status"],
        "symbol": order["symbol"],
        "source": source,
    })


if __name__ == "__main__":
    main()
