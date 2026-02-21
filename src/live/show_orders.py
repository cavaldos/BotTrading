"""
Show Orders - Trade Orders Display
=================================
Chương trình độc lập để hiển thị danh sách lệnh từ sent_orders.json.

Usage:
    python -m src.live.show_orders
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.config import load_live_config


# =============================================================================
# DATA LOADING
# =============================================================================

def load_sent_orders(log_file: str) -> list:
    """Load sent orders from JSON log"""
    log_path = Path(log_file)
    if not log_path.exists():
        return []
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            orders = data.get("sent_orders", [])
            return orders if isinstance(orders, list) else []
    except:
        return []


def parse_orders_to_trades(sent_orders: list) -> list:
    """Parse sent_orders to trades list"""
    trades = []
    
    for order in sent_orders:
        key = order.get("key", "")
        if not key:
            continue
        
        # Parse key: "SYMBOL|TYPE|ENTRY_TIME"
        parts = key.split("|")
        if len(parts) < 3:
            continue
        
        symbol = parts[0]
        trade_type = parts[1]
        entry_time = parts[2]
        
        # Get status from sent_orders.json (WIN/LOSS/OPEN)
        status = order.get("status", "OPEN").upper()
        
        trades.append({
            "Symbol": symbol,
            "Type": trade_type,
            "Entry_Time": entry_time,
            "Entry_Price": str(order.get("entry_price", "-")),
            "Result": status
        })
    
    # Sort by Entry_Time in descending order (newest first)
    trades.sort(key=lambda x: x.get("Entry_Time", ""), reverse=True)
    return trades


# =============================================================================
# TABLE GENERATOR
# =============================================================================

def generate_orders_table(trades: list) -> Table:
    """Generate orders table - display all orders with status"""
    
    table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
    table.add_column("STT", style="dim", width=4)
    table.add_column("Entry Time", style="dim", width=9)
    table.add_column("Symbol", style="cyan", width=10)
    table.add_column("Type", style="dim", width=5)
    table.add_column("Entry Price", style="green", width=11)
    table.add_column("Result", style="dim", width=8)
    
    for idx, trade in enumerate(trades, 1):
        # Extract time from Entry_Time (format: "2026-01-15 17:40:00")
        entry_time = trade.get("Entry_Time", "")
        if entry_time:
            try:
                time_only = entry_time.split(" ")[1] if " " in entry_time else entry_time
                time_only = time_only.rsplit(":", 1)[0]  # Remove seconds
            except:
                time_only = entry_time
        else:
            time_only = "-"
        
        symbol = trade.get("Symbol", "-")
        order_type = trade.get("Type", "-").upper()
        entry_price = trade.get("Entry_Price", "-")
        result = trade.get("Result", "OPEN")
        
        # Style for type
        type_style = "green" if order_type == "LONG" else "red" if order_type == "SHORT" else "white"
        
        # Style for result (WIN/LOSS/OPEN)
        if result == "WIN":
            result_style = "green bold"
        elif result == "LOSS":
            result_style = "red bold"
        else:  # OPEN
            result_style = "yellow bold"
        
        # Format entry price
        try:
            entry_price_float = float(entry_price)
            entry_price_str = f"{entry_price_float:,.2f}"
        except:
            entry_price_str = entry_price
        
        table.add_row(
            str(idx),
            time_only,
            Text(symbol, style="cyan"),
            Text(order_type, style=type_style),
            Text(entry_price_str, style="green"),
            Text(result, style=result_style)
        )
    
    return table


def generate_stats_panel(trades: list) -> Table:
    """Generate stats panel - show Win/Loss/Open counts"""
    
    # Count by result
    win_count = sum(1 for t in trades if t.get("Result") == "WIN")
    loss_count = sum(1 for t in trades if t.get("Result") == "LOSS")
    open_count = sum(1 for t in trades if t.get("Result") == "OPEN")
    total = len(trades)
    
    # Calculate win rate
    closed_trades = win_count + loss_count
    win_rate = (win_count / closed_trades * 100) if closed_trades > 0 else 0
    
    table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    table.add_column("Label", style="dim")
    table.add_column("Value", justify="right")
    
    table.add_row(
        Text("Win", style="green bold"),
        Text(str(win_count), style="green bold")
    )
    table.add_row(
        Text("Loss", style="red bold"),
        Text(str(loss_count), style="red bold")
    )
    table.add_row(
        Text("Open", style="yellow bold"),
        Text(str(open_count), style="yellow bold")
    )
    table.add_row(
        Text("-" * 10, style="dim"),
        Text("-" * 5, style="dim")
    )
    table.add_row(
        Text("Total", style="white bold"),
        Text(str(total), style="white bold")
    )
    table.add_row(
        Text("Win Rate", style="cyan"),
        Text(f"{win_rate:.1f}%", style="cyan bold")
    )
    
    return table


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point"""
    console = Console()
    
    # Load config
    config = load_live_config(include_strategy=False)
    LOG_FILE = config["output"]["log_file"]
    
    # Get current time in GMT+0 (UTC)
    utc_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    # Header
    header_text = Text()
    header_text.append("TRADE ORDERS", style="bold white")
    header_text.append(f"  |  Log: {LOG_FILE}", style="dim")
    header_text.append(f"  |  GMT+0: {utc_time}", style="dim")
    
    console.print()
    console.print(header_text)
    console.print()
    
    # Load data
    sent_orders = load_sent_orders(LOG_FILE)
    trades = parse_orders_to_trades(sent_orders)
    
    # Generate and display stats
    stats_table = generate_stats_panel(trades)
    console.print(stats_table)
    console.print()
    
    # Generate and display orders
    if trades:
        orders_table = generate_orders_table(trades)
        console.print(orders_table)
        console.print()
        console.print(f"[dim]Total orders: {len(trades)}[/dim]")
    else:
        console.print("[yellow]No orders found.[/yellow]")
    
    console.print()


if __name__ == "__main__":
    main()
