"""
Trade Statistics Dashboard
===========================
Chương trình độc lập để thống kê các lệnh giao dịch.
Hiển thị 2 bảng:
  - Table 1: All Trades (từ CSV files)
  - Table 2: Sent Orders (từ sent_orders.json - các lệnh đã gửi Telegram)

Usage:
    python -m src.trade.stats
"""

import json
from pathlib import Path
from collections import defaultdict

import pandas as pd
import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


# =============================================================================
# CONFIG LOADER
# =============================================================================

def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# Load config
CONFIG = load_config()

# Settings from config
OUTPUT_FOLDER = CONFIG["output"]["folder"]
LOG_FILE = CONFIG["output"]["log_file"]
CURRENCIES = CONFIG["data"]["currencies"]

# Timeframe settings
TIMEFRAMES_CONFIG = CONFIG["timeframe"]
if isinstance(TIMEFRAMES_CONFIG.get("value"), list):
    TIMEFRAMES = TIMEFRAMES_CONFIG["value"]
else:
    TIMEFRAMES = [TIMEFRAMES_CONFIG["value"]]


# =============================================================================
# DATA LOADING & PROCESSING - CSV FILES (All Trades)
# =============================================================================

def load_trades_from_csv(output_folder: str) -> list[dict]:
    """
    Load tất cả trades từ các file CSV trong output folder.
    Returns: List of trade dictionaries with coin, timeframe, and result info.
    """
    trades = []
    output_path = Path(output_folder)
    
    if not output_path.exists():
        return trades
    
    csv_files = list(output_path.glob("*.csv"))
    
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            
            if df.empty or 'Result' not in df.columns:
                continue
            
            # Parse filename: BTCUSDT_1m_trades.csv -> coin=BTCUSDT, timeframe=1m
            filename = csv_file.stem  # BTCUSDT_1m_trades
            parts = filename.replace('_trades', '').split('_')
            
            if len(parts) >= 2:
                coin = parts[0]
                timeframe = parts[1]
            else:
                continue
            
            for _, row in df.iterrows():
                result = str(row.get('Result', '')).strip().upper()
                trade_type = str(row.get('Type', '')).strip().upper()
                
                trades.append({
                    'coin': coin,
                    'timeframe': timeframe,
                    'result': result,
                    'type': trade_type,
                    'entry_time': row.get('Entry_Time', ''),
                    'entry_price': row.get('Entry_Price', 0),
                })
                
        except Exception:
            continue
    
    return trades


def calculate_statistics(trades: list[dict], coins: list[str], timeframes: list[str]) -> dict:
    """
    Tính toán thống kê cho mỗi cặp (coin, timeframe).
    Returns: Nested dict stats[coin][timeframe] = {total, win, loss, open, passed, win_rate}
    """
    stats = defaultdict(lambda: defaultdict(lambda: {
        'total': 0,
        'win': 0,
        'loss': 0,
        'open': 0,
        'passed': 0,
        'win_rate': 0.0
    }))
    
    for trade in trades:
        coin = trade['coin']
        tf = trade['timeframe']
        result = trade['result']
        
        if coin not in coins or tf not in timeframes:
            continue
        
        stats[coin][tf]['total'] += 1
        
        if result == 'WIN':
            stats[coin][tf]['win'] += 1
        elif result == 'LOSS':
            stats[coin][tf]['loss'] += 1
        elif result == 'OPEN':
            stats[coin][tf]['open'] += 1
        elif result == 'PASSED':
            stats[coin][tf]['passed'] += 1
    
    # Calculate win rate for each cell
    for coin in coins:
        for tf in timeframes:
            cell = stats[coin][tf]
            closed_trades = cell['win'] + cell['loss']
            if closed_trades > 0:
                cell['win_rate'] = (cell['win'] / closed_trades) * 100
            else:
                cell['win_rate'] = 0.0
    
    return stats


# =============================================================================
# DATA LOADING & PROCESSING - SENT ORDERS (Telegram)
# =============================================================================

def load_all_sent_orders(log_file: str) -> list[dict]:
    """
    Load tất cả các lệnh đã gửi Telegram từ sent_orders.json.
    Parse key format: {SYMBOL_TIMEFRAME}|{TYPE}|{ENTRY_TIME}
    Returns: List of trade dictionaries với coin, timeframe, result info.
    """
    trades = []
    log_path = Path(log_file)
    
    if not log_path.exists():
        return trades
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            sent_orders = data.get("sent_orders", [])
            
            for order in sent_orders:
                key = order.get("key", "")
                status = order.get("status", "")
                
                if not key:
                    continue
                
                # Parse key: "BNBUSDT_15m|LONG|2026-01-19 12:15:00"
                parts = key.split('|')
                if len(parts) != 3:
                    continue
                
                symbol_timeframe = parts[0].split('_')
                if len(symbol_timeframe) != 2:
                    continue
                
                coin = symbol_timeframe[0]
                timeframe = symbol_timeframe[1]
                trade_type = parts[1]
                entry_time = parts[2]
                entry_price = order.get("entry_price", 0)
                
                # Map status to result format
                result = str(status).strip().upper()
                if result not in ["WIN", "LOSS", "OPEN"]:
                    result = "OPEN"
                
                trades.append({
                    'coin': coin,
                    'timeframe': timeframe,
                    'result': result,
                    'type': trade_type,
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                })
                
    except Exception:
        pass
    
    return trades





# =============================================================================
# UI RENDERING - ALL TRADES TABLE
# =============================================================================

def format_num(value: int, style: str) -> str:
    """Format a number with color - dim if 0, otherwise use specified style."""
    if value == 0:
        return "[dim]0[/dim]"
    return f"[{style}]{value}[/{style}]"


def format_cell(cell_stats: dict) -> str:
    """Format statistics for a single cell (All Trades table)."""
    total = cell_stats['total']
    win = cell_stats['win']
    loss = cell_stats['loss']
    open_count = cell_stats['open']
    passed = cell_stats['passed']
    win_rate = cell_stats['win_rate']
    
    if total == 0:
        return "[dim]-[/dim]"
    
    # Line 1: W/L/O/P counts - dim if 0
    line1 = (
        f"{format_num(win, 'green')}/"
        f"{format_num(loss, 'red')}/"
        f"{format_num(open_count, 'yellow')}/"
        f"{format_num(passed, 'cyan')}"
    )
    
    # Line 2: Win Rate - always white
    line2 = f"[white]{win_rate:.0f}%[/white]"
    
    return f"{line1}\n{line2}"


def format_summary_row(stats: dict, coins: list[str], timeframe: str) -> str:
    """Format summary statistics for a timeframe column (All Trades table)."""
    total_win = sum(stats[coin][timeframe]['win'] for coin in coins)
    total_loss = sum(stats[coin][timeframe]['loss'] for coin in coins)
    total_open = sum(stats[coin][timeframe]['open'] for coin in coins)
    total_passed = sum(stats[coin][timeframe]['passed'] for coin in coins)
    total_trades = total_win + total_loss + total_open + total_passed
    
    if total_trades == 0:
        return "[dim]-[/dim]"
    
    # Line 1: Totals - dim if 0
    line1 = (
        f"{format_num(total_win, 'green')}/"
        f"{format_num(total_loss, 'red')}/"
        f"{format_num(total_open, 'yellow')}/"
        f"{format_num(total_passed, 'cyan')}"
    )
    
    # Line 2: Win Rate - always white
    closed = total_win + total_loss
    if closed > 0:
        win_rate = (total_win / closed) * 100
        line2 = f"[white]{win_rate:.0f}%[/white]"
    else:
        line2 = "[dim]-[/dim]"
    
    return f"{line1}\n{line2}"


def format_coin_summary(stats: dict, coin: str, timeframes: list[str]) -> str:
    """Format summary statistics for a coin row (All Trades table)."""
    total_win = sum(stats[coin][tf]['win'] for tf in timeframes)
    total_loss = sum(stats[coin][tf]['loss'] for tf in timeframes)
    total_open = sum(stats[coin][tf]['open'] for tf in timeframes)
    total_passed = sum(stats[coin][tf]['passed'] for tf in timeframes)
    total_trades = total_win + total_loss + total_open + total_passed
    
    if total_trades == 0:
        return "[dim]-[/dim]"
    
    # Line 1: Totals - dim if 0
    line1 = (
        f"{format_num(total_win, 'green')}/"
        f"{format_num(total_loss, 'red')}/"
        f"{format_num(total_open, 'yellow')}/"
        f"{format_num(total_passed, 'cyan')}"
    )
    
    # Line 2: Win Rate - always white
    closed = total_win + total_loss
    if closed > 0:
        win_rate = (total_win / closed) * 100
        line2 = f"[white]{win_rate:.0f}%[/white]"
    else:
        line2 = "[dim]-[/dim]"
    
    return f"{line1}\n{line2}"


def create_all_trades_table(stats: dict, coins: list[str], timeframes: list[str]) -> Table:
    """Create Rich table with All Trades statistics."""
    table = Table(
        title="All Trades (from CSV)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
        title_style="bold white on blue",
        expand=False,
        padding=(0, 1)
    )
    
    # Add columns: Coin | TF1 | TF2 | ... | Total
    table.add_column("Coin", style="bold white", justify="left", width=10)
    for tf in timeframes:
        table.add_column(tf, justify="center", no_wrap=True)
    table.add_column("Total", style="bold", justify="center", no_wrap=True)
    
    # Add rows for each coin
    for coin in coins:
        row = [f"[bold cyan]{coin}[/bold cyan]"]
        
        for tf in timeframes:
            cell_stats = stats[coin][tf]
            row.append(format_cell(cell_stats))
        
        # Add coin total
        row.append(format_coin_summary(stats, coin, timeframes))
        
        table.add_row(*row)
    
    # Add separator
    table.add_section()
    
    # Add summary row
    summary_row = ["[bold white]TOTAL[/bold white]"]
    for tf in timeframes:
        summary_row.append(format_summary_row(stats, coins, tf))
    
    # Grand total
    total_win = sum(stats[coin][tf]['win'] for coin in coins for tf in timeframes)
    total_loss = sum(stats[coin][tf]['loss'] for coin in coins for tf in timeframes)
    total_open = sum(stats[coin][tf]['open'] for coin in coins for tf in timeframes)
    total_passed = sum(stats[coin][tf]['passed'] for coin in coins for tf in timeframes)
    
    line1 = (
        f"{format_num(total_win, 'green')}/"
        f"{format_num(total_loss, 'red')}/"
        f"{format_num(total_open, 'yellow')}/"
        f"{format_num(total_passed, 'cyan')}"
    )
    
    closed = total_win + total_loss
    if closed > 0:
        win_rate = (total_win / closed) * 100
        line2 = f"[white]{win_rate:.0f}%[/white]"
    else:
        line2 = "[dim]-[/dim]"
    
    grand_total = f"{line1}\n{line2}"
    summary_row.append(grand_total)
    
    table.add_row(*summary_row)
    
    return table


# =============================================================================
# UI RENDERING - SENT ORDERS TABLE
# =============================================================================

def create_sent_orders_table(stats: dict, coins: list[str], timeframes: list[str]) -> Table:
    """Create Rich table with Sent Orders statistics (same format as All Trades)."""
    table = Table(
        title="Sent Orders (Telegram)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        border_style="green",
        title_style="bold white on green",
        expand=False,
        padding=(0, 1)
    )
    
    # Add columns: Coin | TF1 | TF2 | ... | Total
    table.add_column("Coin", style="bold white", justify="left", width=10)
    for tf in timeframes:
        table.add_column(tf, justify="center", no_wrap=True)
    table.add_column("Total", style="bold", justify="center", no_wrap=True)
    
    # Add rows for each coin (reuse format_cell from All Trades)
    for coin in coins:
        row = [f"[bold cyan]{coin}[/bold cyan]"]
        
        for tf in timeframes:
            cell_stats = stats[coin][tf]
            row.append(format_cell(cell_stats))
        
        # Add coin total
        row.append(format_coin_summary(stats, coin, timeframes))
        
        table.add_row(*row)
    
    # Add separator
    table.add_section()
    
    # Add summary row
    summary_row = ["[bold white]TOTAL[/bold white]"]
    for tf in timeframes:
        summary_row.append(format_summary_row(stats, coins, tf))
    
    # Grand total
    total_win = sum(stats[coin][tf]['win'] for coin in coins for tf in timeframes)
    total_loss = sum(stats[coin][tf]['loss'] for coin in coins for tf in timeframes)
    total_open = sum(stats[coin][tf]['open'] for coin in coins for tf in timeframes)
    total_passed = sum(stats[coin][tf]['passed'] for coin in coins for tf in timeframes)
    
    line1 = (
        f"{format_num(total_win, 'green')}/"
        f"{format_num(total_loss, 'red')}/"
        f"{format_num(total_open, 'yellow')}/"
        f"{format_num(total_passed, 'cyan')}"
    )
    
    closed = total_win + total_loss
    if closed > 0:
        win_rate = (total_win / closed) * 100
        line2 = f"[white]{win_rate:.0f}%[/white]"
    else:
        line2 = "[dim]-[/dim]"
    
    grand_total = f"{line1}\n{line2}"
    summary_row.append(grand_total)
    
    table.add_row(*summary_row)
    
    return table


# =============================================================================
# LEGENDS
# =============================================================================

def create_all_trades_legend() -> Panel:
    """Create legend panel for All Trades table."""
    legend = Text()
    legend.append("Format: ", style="dim")
    legend.append("W", style="green")
    legend.append("/", style="dim")
    legend.append("L", style="red")
    legend.append("/", style="dim")
    legend.append("O", style="yellow")
    legend.append("/", style="dim")
    legend.append("P", style="cyan")
    legend.append(" = Win/Loss/Open/Passed", style="dim")
    
    return Panel(legend, title="Legend - All Trades", border_style="dim", expand=False)


def create_sent_orders_legend() -> Panel:
    """Create legend panel for Sent Orders table."""
    legend = Text()
    legend.append("Format: ", style="dim")
    legend.append("W", style="green")
    legend.append("/", style="dim")
    legend.append("L", style="red")
    legend.append("/", style="dim")
    legend.append("O", style="yellow")
    legend.append("/", style="dim")
    legend.append("P", style="cyan")
    legend.append(" = Win/Loss/Open/Passed", style="dim")
    
    return Panel(legend, title="Legend - Sent Orders", border_style="dim", expand=False)


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    console = Console()
    
    # Header
    console.print()
    console.print(Panel.fit(
        "[bold white]Trade Statistics Dashboard[/bold white]\n"
        f"[dim]CSV: {OUTPUT_FOLDER} | Orders: {LOG_FILE}[/dim]",
        border_style="blue"
    ))
    console.print()
    
    # ===========================================
    # TABLE 1: All Trades (from CSV)
    # ===========================================
    trades = load_trades_from_csv(OUTPUT_FOLDER)
    
    if trades:
        stats = calculate_statistics(trades, CURRENCIES, TIMEFRAMES)
        table1 = create_all_trades_table(stats, CURRENCIES, TIMEFRAMES)
        console.print(table1)
        console.print()
        console.print(create_all_trades_legend())
        console.print(f"[dim]Total trades: {len(trades)}[/dim]")
    else:
        console.print("[yellow]No trades found in CSV files.[/yellow]")
    
    console.print()
    console.print()
    
    # ===========================================
    # TABLE 2: Sent Orders (Telegram)
    # ===========================================
    sent_trades = load_all_sent_orders(LOG_FILE)
    
    if sent_trades:
        sent_stats = calculate_statistics(sent_trades, CURRENCIES, TIMEFRAMES)
        table2 = create_sent_orders_table(sent_stats, CURRENCIES, TIMEFRAMES)
        console.print(table2)
        console.print()
        console.print(create_sent_orders_legend())
        console.print(f"[dim]Total sent orders: {len(sent_trades)}[/dim]")
    else:
        console.print("[yellow]No sent orders found.[/yellow]")
    
    console.print()


if __name__ == "__main__":
    main()
