"""
Ranger-style TUI for sent_orders.json.
Vim keybindings: j/k to navigate, Enter/l to view details, h to go back, q to quit
"""

from __future__ import annotations

import argparse
import curses
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.live.bybit.exchange import get_exchange
from src.live.bybit.tool.order_manager import find_order_by_id


class OrdersTUI:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.exchange = get_exchange()
        self.sent_orders: List[Dict] = []
        self.hydrated_orders: List[Dict] = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.view_mode = "list"  # "list" or "detail"
        self.last_updated = "N/A"

    def _load_orders(self):
        if not self.file_path.exists():
            self.sent_orders = []
            self.last_updated = "N/A"
            return

        with self.file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            self.sent_orders = data.get("sent_orders", [])
            self.last_updated = data.get("last_updated", "N/A")

        self._hydrate_orders()

    def _hydrate_orders(self):
        self.hydrated_orders = []
        for order in self.sent_orders:
            order_id = str(order.get("order_id", ""))
            exchange_order, source = find_order_by_id(self.exchange, order_id)
            if exchange_order:
                merged = dict(order)
                merged.update({
                    "symbol": exchange_order.get("symbol"),
                    "side": exchange_order.get("side"),
                    "type": exchange_order.get("type"),
                    "price": exchange_order.get("price"),
                    "amount": exchange_order.get("amount"),
                    "filled": exchange_order.get("filled"),
                    "exchange_status": exchange_order.get("status") or source,
                    "_exchange_order": exchange_order,
                    "_source": source,
                })
                self.hydrated_orders.append(merged)
            else:
                fallback = dict(order)
                fallback.setdefault("exchange_status", "N/A")
                fallback["_exchange_order"] = None
                fallback["_source"] = None
                self.hydrated_orders.append(fallback)

    def _format_ts(self, ts) -> str:
        if not ts:
            return "N/A"
        try:
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = ts / 1000
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            return str(ts)
        except Exception:
            return str(ts)

    def _get_position_for_symbol(self, symbol: str) -> Optional[Dict]:
        if not symbol:
            return None
        try:
            positions = self.exchange.fetch_positions()
            for pos in positions:
                if pos.get("symbol") == symbol:
                    contracts = float(pos.get("contracts", 0) or 0)
                    if abs(contracts) > 0:
                        return pos
        except Exception:
            pass
        return None

    def _draw_header(self, stdscr, width: int):
        header = " 📊 SENT ORDERS TUI (ranger-style) "
        stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(0, 0, "═" * width)
        stdscr.addstr(0, (width - len(header)) // 2, header)
        stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

    def _draw_footer(self, stdscr, height: int, width: int):
        if self.view_mode == "list":
            help_text = " j/k:↑↓ | Enter/l:View | r:Refresh | p:Positions | q:Quit "
        else:
            help_text = " h/Esc:Back | j/k:↑↓ | r:Refresh | q:Quit "

        footer_line = f" File: {self.file_path} | Updated: {self.last_updated} "

        stdscr.attron(curses.color_pair(2))
        stdscr.addstr(height - 2, 0, footer_line.ljust(width)[:width-1])
        stdscr.addstr(height - 1, 0, help_text.center(width)[:width-1])
        stdscr.attroff(curses.color_pair(2))

    def _draw_order_list(self, stdscr, start_y: int, height: int, width: int):
        list_width = width // 3
        visible_height = height - start_y - 3

        # Header
        stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
        header = f"{'#':>3} {'Symbol':<12} {'Side':<5} {'Status':<10}"
        stdscr.addstr(start_y, 0, header[:list_width].ljust(list_width))
        stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        # Adjust scroll
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_height:
            self.scroll_offset = self.selected_index - visible_height + 1

        # Draw orders
        for i in range(visible_height):
            idx = self.scroll_offset + i
            y = start_y + 1 + i

            if idx >= len(self.hydrated_orders):
                stdscr.addstr(y, 0, " " * list_width)
                continue

            order = self.hydrated_orders[idx]
            symbol = str(order.get("symbol", "N/A"))[:10]
            side = str(order.get("side", "N/A"))[:4].upper()
            status = str(order.get("exchange_status", "N/A"))[:8].upper()

            line = f"{idx+1:>3} {symbol:<12} {side:<5} {status:<10}"

            if idx == self.selected_index:
                stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
                stdscr.addstr(y, 0, line[:list_width].ljust(list_width))
                stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
            else:
                # Color by side
                if side in ("BUY", "LONG"):
                    stdscr.attron(curses.color_pair(5))
                elif side in ("SELL", "SHOR"):
                    stdscr.attron(curses.color_pair(6))
                stdscr.addstr(y, 0, line[:list_width].ljust(list_width))
                stdscr.attroff(curses.color_pair(5))
                stdscr.attroff(curses.color_pair(6))

        # Vertical separator
        for y in range(start_y, height - 2):
            try:
                stdscr.addch(y, list_width, "│")
            except curses.error:
                pass

    def _draw_preview(self, stdscr, start_y: int, height: int, width: int):
        list_width = width // 3
        preview_start = list_width + 2
        preview_width = width - preview_start - 1
        visible_height = height - start_y - 3

        if not self.hydrated_orders:
            stdscr.addstr(start_y + 2, preview_start, "No orders to display")
            return

        order = self.hydrated_orders[self.selected_index]
        exchange_order = order.get("_exchange_order", {}) or {}
        info = exchange_order.get("info", {}) if exchange_order else {}
        symbol = order.get("symbol")

        lines = []
        lines.append(("═" * 30, 3))
        lines.append(("ORDER DETAILS", 1))
        lines.append(("═" * 30, 3))
        lines.append((f"Order ID   : {order.get('order_id', 'N/A')}", 0))
        lines.append((f"Symbol     : {symbol or 'N/A'}", 0))

        side = str(order.get("side", "N/A")).upper()
        side_color = 5 if side in ("BUY", "LONG") else 6
        lines.append((f"Side       : {side}", side_color))

        lines.append((f"Type       : {str(order.get('type', 'N/A')).upper()}", 0))
        lines.append((f"Status     : {str(order.get('exchange_status', 'N/A')).upper()}", 0))
        lines.append(("", 0))
        lines.append((f"Price      : {float(order.get('price') or 0):.6f}", 0))
        lines.append((f"Amount     : {float(order.get('amount') or 0):.6f}", 0))
        lines.append((f"Filled     : {float(order.get('filled') or 0):.6f}", 0))

        if exchange_order:
            lines.append((f"Avg Price  : {float(exchange_order.get('average') or 0):.6f}", 0))
            lines.append((f"Cost       : ${float(exchange_order.get('cost') or 0):.4f}", 0))
            lines.append((f"Created    : {self._format_ts(exchange_order.get('timestamp'))}", 0))

        sl = info.get("stopLoss")
        tp = info.get("takeProfit")
        if sl or tp:
            lines.append(("", 0))
            lines.append(("─" * 25, 3))
            if sl and sl != "0":
                lines.append((f"Stop Loss  : {float(sl):.6f}", 6))
            if tp and tp != "0":
                lines.append((f"Take Profit: {float(tp):.6f}", 5))

        # Position info
        position = self._get_position_for_symbol(symbol)
        if position:
            pos_info = position.get("info", {})
            lines.append(("", 0))
            lines.append(("═" * 30, 3))
            lines.append(("POSITION INFO", 1))
            lines.append(("═" * 30, 3))

            pos_side = position.get("side", "N/A").upper()
            pos_side_color = 5 if pos_side == "LONG" else 6
            lines.append((f"Side       : {pos_side}", pos_side_color))

            contracts = abs(float(position.get("contracts", 0) or 0))
            entry_price = float(pos_info.get("avgPrice", 0) or position.get("entryPrice", 0) or 0)
            mark_price = float(position.get("markPrice", 0) or 0)
            liq_price = float(position.get("liquidationPrice", 0) or 0)
            leverage = pos_info.get("leverage", position.get("leverage", "N/A"))

            lines.append((f"Size       : {contracts:.6f}", 0))
            lines.append((f"Leverage   : {leverage}x", 0))
            lines.append((f"Entry      : {entry_price:.6f}", 0))
            lines.append((f"Mark       : {mark_price:.6f}", 0))
            if liq_price:
                lines.append((f"Liq. Price : {liq_price:.6f}", 6))

            unrealised_pnl = float(pos_info.get("unrealisedPnl", 0) or 0)
            position_value = float(pos_info.get("positionValue", 0) or 0)
            pnl_pct = 0
            if position_value:
                pnl_pct = (unrealised_pnl / position_value) * 100 * float(leverage or 1)

            pnl_color = 5 if unrealised_pnl >= 0 else 6
            lines.append(("", 0))
            lines.append((f"PnL        : ${unrealised_pnl:+.4f}", pnl_color))
            lines.append((f"PnL %      : {pnl_pct:+.2f}%", pnl_color))

            pos_sl = pos_info.get("stopLoss")
            pos_tp = pos_info.get("takeProfit")
            if pos_sl or pos_tp:
                lines.append(("", 0))
                if pos_sl and pos_sl != "0":
                    lines.append((f"Pos SL     : {float(pos_sl):.6f}", 6))
                if pos_tp and pos_tp != "0":
                    lines.append((f"Pos TP     : {float(pos_tp):.6f}", 5))

            created = pos_info.get("createdTime")
            if created:
                lines.append((f"Entry Time : {self._format_ts(int(created))}", 0))

        # Draw lines
        for i, (line, color) in enumerate(lines[:visible_height]):
            y = start_y + i
            if y >= height - 2:
                break
            try:
                if color:
                    stdscr.attron(curses.color_pair(color))
                stdscr.addstr(y, preview_start, line[:preview_width])
                if color:
                    stdscr.attroff(curses.color_pair(color))
            except curses.error:
                pass

    def _draw_positions_view(self, stdscr, start_y: int, height: int, width: int):
        try:
            positions = self.exchange.fetch_positions()
            active = [p for p in positions if abs(float(p.get("contracts", 0) or 0)) > 0]
        except Exception as e:
            stdscr.addstr(start_y + 2, 2, f"Error fetching positions: {e}")
            return

        stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(start_y, 2, f"═══ OPEN POSITIONS ({len(active)}) ═══")
        stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

        if not active:
            stdscr.addstr(start_y + 2, 4, "No open positions")
            return

        # Header
        header = f"{'Symbol':<14} {'Side':<6} {'Size':<12} {'Entry':<12} {'Mark':<12} {'PnL':>12} {'PnL%':>8}"
        stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(start_y + 2, 2, header[:width-4])
        stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        for i, pos in enumerate(active[:height - start_y - 6]):
            info = pos.get("info", {})
            symbol = pos.get("symbol", "N/A")[:12]
            side = pos.get("side", "N/A").upper()[:4]
            contracts = abs(float(pos.get("contracts", 0) or 0))
            entry = float(info.get("avgPrice", 0) or pos.get("entryPrice", 0) or 0)
            mark = float(pos.get("markPrice", 0) or 0)
            pnl = float(info.get("unrealisedPnl", 0) or 0)
            pos_value = float(info.get("positionValue", 0) or 0)
            leverage = float(info.get("leverage", 1) or 1)
            pnl_pct = (pnl / pos_value * 100 * leverage) if pos_value else 0

            line = f"{symbol:<14} {side:<6} {contracts:<12.4f} {entry:<12.2f} {mark:<12.2f} {pnl:>+12.2f} {pnl_pct:>+7.2f}%"

            color = 5 if pnl >= 0 else 6
            stdscr.attron(curses.color_pair(color))
            stdscr.addstr(start_y + 3 + i, 2, line[:width-4])
            stdscr.attroff(curses.color_pair(color))

    def run(self, stdscr):
        curses.curs_set(0)
        stdscr.timeout(100)

        # Init colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)      # Header
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Footer
        curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Column headers
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)   # Selected
        curses.init_pair(5, curses.COLOR_GREEN, -1)     # Buy/Long/Profit
        curses.init_pair(6, curses.COLOR_RED, -1)       # Sell/Short/Loss

        self._load_orders()

        while True:
            stdscr.clear()
            height, width = stdscr.getmaxyx()

            self._draw_header(stdscr, width)
            self._draw_footer(stdscr, height, width)

            if self.view_mode == "list":
                self._draw_order_list(stdscr, 2, height, width)
                self._draw_preview(stdscr, 2, height, width)
            elif self.view_mode == "positions":
                self._draw_positions_view(stdscr, 2, height, width)

            stdscr.refresh()

            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                break

            if key == -1:
                continue

            # Navigation
            if key in (ord('q'), ord('Q')):
                break
            elif key in (ord('j'), curses.KEY_DOWN):
                if self.view_mode == "list" and self.selected_index < len(self.hydrated_orders) - 1:
                    self.selected_index += 1
            elif key in (ord('k'), curses.KEY_UP):
                if self.view_mode == "list" and self.selected_index > 0:
                    self.selected_index -= 1
            elif key in (ord('g'),):  # Go to top
                self.selected_index = 0
                self.scroll_offset = 0
            elif key in (ord('G'),):  # Go to bottom
                if self.hydrated_orders:
                    self.selected_index = len(self.hydrated_orders) - 1
            elif key in (ord('h'), 27):  # Esc or h to go back
                self.view_mode = "list"
            elif key in (ord('l'), ord('\n'), curses.KEY_ENTER, 10):  # Enter or l
                pass  # Preview is always shown in list mode
            elif key in (ord('r'), ord('R')):  # Refresh
                self._load_orders()
            elif key in (ord('p'), ord('P')):  # Positions
                self.view_mode = "positions" if self.view_mode != "positions" else "list"


def run_tui(file_path: Path) -> None:
    tui = OrdersTUI(file_path)
    curses.wrapper(tui.run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ranger-style TUI for sent orders")
    parser.add_argument(
        "--file",
        default="res/logs/sent_orders.json",
        help="Path to sent_orders.json",
    )
    args = parser.parse_args()
    run_tui(Path(args.file))


if __name__ == "__main__":
    main()
