"""
Watch sent_orders.json and close orders with status PASSED.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.trade.bybit.exchange import get_exchange
from src.trade.bybit.order_manager import find_order_by_id


def _load_sent_orders(path: Path) -> Dict:
    if not path.exists():
        return {"sent_orders": [], "last_updated": None}

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_sent_orders(path: Path, data: Dict) -> None:
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _extract_close_amount(order: Dict) -> Optional[float]:
    filled = order.get("filled")
    amount = order.get("amount")
    if filled and float(filled) > 0:
        return float(filled)
    if amount and float(amount) > 0:
        return float(amount)
    return None


def _build_reduce_only_params(order: Dict) -> Dict:
    params = {"reduceOnly": True}
    position_idx = order.get("info", {}).get("positionIdx")
    if position_idx is not None:
        params["positionIdx"] = position_idx
    return params


def close_order_by_id(order_id: str) -> Dict:
    exchange = get_exchange()
    order, source = find_order_by_id(exchange, order_id)

    if not order:
        return {
            "success": False,
            "error": "Order not found",
            "order_id": order_id,
        }

    symbol = order.get("symbol")
    side = (order.get("side") or "").lower()
    amount = _extract_close_amount(order)

    if not symbol or side not in ("buy", "sell") or not amount:
        return {
            "success": False,
            "error": "Missing symbol/side/amount to close order",
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "source": source,
        }

    close_side = "sell" if side == "buy" else "buy"
    params = _build_reduce_only_params(order)

    try:
        result = exchange.create_market_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            params=params,
        )
        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "result": result,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "order_id": order_id,
            "symbol": symbol,
            "side": close_side,
            "amount": amount,
        }


def process_once(file_path: Path) -> Tuple[int, int]:
    data = _load_sent_orders(file_path)
    orders = data.get("sent_orders", [])
    updated = 0
    errors = 0

    for order in orders:
        status = str(order.get("status", "")).upper()
        if status != "PASSED":
            continue

        order_id = str(order.get("order_id"))
        result = close_order_by_id(order_id)
        if result.get("success"):
            order["status"] = "CLOSED"
            order["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated += 1
        else:
            order["last_error"] = result.get("error")
            order["last_error_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            errors += 1

    if updated or errors:
        _save_sent_orders(file_path, data)

    return updated, errors


def run_watcher(file_path: Path, interval: float) -> None:
    while True:
        updated, errors = process_once(file_path)
        if updated or errors:
            print(f"updated={updated} errors={errors}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch sent_orders.json and close PASSED orders")
    parser.add_argument(
        "--file",
        default="res/logs/sent_orders.json",
        help="Path to sent_orders.json",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds",
    )
    args = parser.parse_args()
    run_watcher(Path(args.file), args.interval)


if __name__ == "__main__":
    main()
