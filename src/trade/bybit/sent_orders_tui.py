"""
Interactive TUI for sent_orders.json.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table

from src.trade.bybit.order_manager import cancel_order_by_id, get_order_status_by_id


def _load_sent_orders(path: Path) -> Dict:
    if not path.exists():
        return {"sent_orders": [], "last_updated": None}

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_sent_orders(path: Path, data: Dict) -> None:
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _build_table(sent_orders: List[Dict]) -> Table:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Key", style="cyan", width=30)
    table.add_column("Order ID", style="magenta", width=36)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("SL", justify="right", width=10)
    table.add_column("TP", justify="right", width=10)
    table.add_column("Sent At", width=20)
    table.add_column("Status", width=10)

    for idx, order in enumerate(sent_orders, start=1):
        table.add_row(
            str(idx),
            str(order.get("key", ""))[:30],
            str(order.get("order_id", "")),
            f"{order.get('entry_price', 0):.4f}",
            f"{order.get('stop_loss', 0):.4f}",
            f"{order.get('take_profit', 0):.4f}",
            str(order.get("sent_at", "")),
            str(order.get("status", "")),
        )

    return table


def _resolve_order(sent_orders: List[Dict], token: str) -> Tuple[Optional[Dict], Optional[int]]:
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(sent_orders):
            return sent_orders[index], index
        return None, None

    for idx, order in enumerate(sent_orders):
        if str(order.get("order_id")) == token:
            return order, idx

    return None, None


def _render_help(console: Console) -> None:
    console.print("Commands:")
    console.print("  help                     Show this help")
    console.print("  refresh                  Reload file")
    console.print("  info <index|order_id>    Show order details")
    console.print("  status <index|order_id>  Fetch status from Bybit and update file")
    console.print("  cancel <index|order_id>  Cancel order on Bybit and update file")
    console.print("  quit                     Exit")


def _show_order_detail(console: Console, order: Dict) -> None:
    console.print("Order details:")
    for key, value in order.items():
        console.print(f"- {key}: {value}")


def run_tui(file_path: Path) -> None:
    console = Console()

    while True:
        data = _load_sent_orders(file_path)
        sent_orders = data.get("sent_orders", [])
        last_updated = data.get("last_updated") or "N/A"

        console.clear()
        console.print("Sent Orders TUI")
        console.print(f"File: {file_path}")
        console.print(f"Last Updated: {last_updated}\n")
        console.print(_build_table(sent_orders))
        console.print("")

        command = console.input("command> ").strip()
        if not command:
            continue

        if command in ("quit", "exit"):
            return

        if command == "help":
            _render_help(console)
            console.input("Press Enter to continue...")
            continue

        if command == "refresh":
            continue

        parts = command.split()
        action = parts[0]
        if action not in ("info", "status", "cancel"):
            console.print("Unknown command. Type 'help' for list of commands.")
            console.input("Press Enter to continue...")
            continue

        if len(parts) < 2:
            console.print("Missing argument. Use index or order_id.")
            console.input("Press Enter to continue...")
            continue

        order, order_index = _resolve_order(sent_orders, parts[1])
        if not order:
            console.print("Order not found.")
            console.input("Press Enter to continue...")
            continue

        if action == "info":
            _show_order_detail(console, order)
            console.input("Press Enter to continue...")
            continue

        order_id = str(order.get("order_id"))
        if action == "status":
            result = get_order_status_by_id(order_id)
            if result.get("success"):
                status = str(result.get("status") or "unknown").upper()
                sent_orders[order_index]["status"] = status
                _save_sent_orders(file_path, data)
                console.print(f"Updated status: {status}")
            else:
                console.print(result)
            console.input("Press Enter to continue...")
            continue

        if action == "cancel":
            result = cancel_order_by_id(order_id)
            if result.get("success"):
                sent_orders[order_index]["status"] = "CANCELED"
                _save_sent_orders(file_path, data)
                console.print("Order canceled.")
            else:
                console.print(result)
            console.input("Press Enter to continue...")
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Sent orders TUI")
    parser.add_argument(
        "--file",
        default="res/logs/sent_orders.json",
        help="Path to sent_orders.json",
    )
    args = parser.parse_args()
    run_tui(Path(args.file))


if __name__ == "__main__":
    main()
