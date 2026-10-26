"""
Dashboard module for Trade Application
Handles all UI rendering using Rich library
"""

import os
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text


# =============================================================================
# DASHBOARD LAYOUT
# =============================================================================

def create_dashboard() -> Layout:
    """Create responsive dashboard layout that fills terminal height."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
    )

    layout["body"].split_column(
        Layout(name="row1", ratio=3, minimum_size=8),
        Layout(name="row2", ratio=3, minimum_size=8),
        Layout(name="row3", ratio=4, minimum_size=8),
    )
    
    # Row 1: Process 1 | Stats
    layout["row1"].split_row(
        Layout(name="process1"),
        Layout(name="stats"),
    )
    
    # Row 2: Process 2 | Errors
    layout["row2"].split_row(
        Layout(name="process2"),
        Layout(name="errors"),
    )
    
    # Row 3: Logs | Trades
    layout["row3"].split_row(
        Layout(name="logs"),
        Layout(name="orders"),
    )
    
    return layout


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def format_currencies(currencies: list) -> str:
    """Format currencies list for display"""
    if len(currencies) <= 3:
        return ", ".join(currencies)
    return f"{currencies[0]}, {currencies[1]}, ... +{len(currencies) - 2} more"


# =============================================================================
# PANEL GENERATORS
# =============================================================================

def generate_header(state, timeframes: list, currencies: list, risk_reward_ratio: float) -> Panel:
    """Generate header panel"""
    uptime = datetime.now() - state.start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    timeframes_str = ", ".join(timeframes)
    
    header_text = Text()
    header_text.append("TRADE APPLICATION", style="bold white")
    header_text.append(f"  |  TF: {timeframes_str}", style="bold magenta")
    header_text.append(f"  |  Coins: {format_currencies(currencies)}", style="bold green")
    header_text.append(f"  |  RR: {risk_reward_ratio}", style="bold yellow")
    header_text.append(f"  |  Uptime: {uptime_str}", style="dim")
    header_text.append(f"  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
    
    return Panel(header_text, style="bold blue")


def generate_process1_panel(state, timeframes: list, fetch_interval: int) -> Panel:
    """Generate Process 1 status panel for all timeframes"""
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("TF", style="magenta", width=5)
    table.add_column("Status", style="dim", width=12)
    table.add_column("Next Fetch", style="cyan", width=10)
    table.add_column("Countdown", style="yellow", width=10)
    table.add_column("Cycles", style="green", width=6)
    table.add_column("Errors", style="red", width=6)
    
    for tf in timeframes:
        tf_state = state.timeframe_states[tf]
        
        if tf_state.status == "Waiting":
            status_style = "yellow"
        elif "Fetching" in tf_state.status or "Executing" in tf_state.status or "Running" in tf_state.status:
            status_style = "cyan"
        else:
            status_style = "green"
        
        errors_style = "red" if tf_state.errors > 0 else "green"
        
        table.add_row(
            Text(tf, style="magenta"),
            Text(tf_state.status, style=status_style),
            tf_state.next_fetch,
            Text(tf_state.countdown, style="bold yellow"),
            str(tf_state.cycles),
            Text(str(tf_state.errors), style=errors_style)
        )
    
    return Panel(table, title=f"[cyan]Process 1: Data & Execute ({fetch_interval}s interval)[/cyan]", border_style="cyan")


def generate_process2_panel(state, output_folder: str) -> Panel:
    """Generate Process 2 status panel"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="dim")
    table.add_column("Value")
    
    status_style = "green" if "Watching" in state.p2_status else "yellow"
    
    table.add_row("Status", Text(state.p2_status, style=status_style))
    table.add_row("Folder", output_folder)
    table.add_row("Last Event", state.p2_last_event)
    table.add_row("Orders Sent", Text(str(state.p2_orders_sent), style="green"))
    table.add_row("Total Events", str(state.p2_events))
    
    return Panel(table, title="[yellow]Process 2: Watcher[/yellow]", border_style="yellow")


def generate_stats_panel(trades: list) -> Panel:
     """Generate stats panel - show Win/Loss/Open/Passed counts"""
     
     # Count by result
     win_count = sum(1 for t in trades if t.get("Result") == "WIN")
     loss_count = sum(1 for t in trades if t.get("Result") == "LOSS")
     open_count = sum(1 for t in trades if t.get("Result") == "OPEN")
     passed_count = sum(1 for t in trades if t.get("Result") == "PASSED")
     total = len(trades)
     
     # Calculate win rate (closed trades only, not passed)
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
         Text("Passed", style="cyan bold"),
         Text(str(passed_count), style="cyan bold")
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
     
     return Panel(table, title="[bold green]Trade Stats[/bold green]", border_style="green")


def generate_logs_panel(state, state_lock) -> Panel:
    """Generate logs panel"""
    log_text = Text()
    
    with state_lock:
        for log in state.logs:
            if "[Process 1]" in log:
                style = "cyan"
            elif "[Process 2]" in log:
                style = "yellow"
            elif "ERROR" in log:
                style = "red"
            else:
                style = "white"
            
            log_text.append(log + "\n", style=style)
    
    if not state.logs:
        log_text.append("No logs yet...", style="dim")
    
    return Panel(log_text, title="[bold]Recent Logs[/bold]", border_style="dim")


def generate_errors_panel(state, state_lock) -> Panel:
    """Generate errors panel - display last 10 errors with details"""
    table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
    table.add_column("Time", style="dim", width=8)
    table.add_column("TF", style="magenta", width=4)
    table.add_column("Error Message", style="red")
    
    with state_lock:
        for error in state.error_logs:
            count = int(error.get("count", 1) or 1)
            message = error.get("message", "-")
            if count > 1:
                message = f"{message} [x{count}]"
            table.add_row(
                error.get("time", "-"),
                Text(error.get("timeframe", "-"), style="magenta"),
                Text(message, style="red")
            )
    
    if not state.error_logs:
        return Panel(
            Text("No errors yet...", style="dim green"),
            title="[bold red]Errors (0)[/bold red]",
            border_style="red"
        )
    
    error_count = len(state.error_logs)
    return Panel(
        table,
        title=f"[bold red]Errors ({error_count})[/bold red]",
        border_style="red"
    )


# =============================================================================
# DATA LOADING FUNCTIONS
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


def load_trades_for_sent_orders(output_folder: str, log_file: str) -> list:
    """Load trades from sent_orders.json with status (WIN/LOSS/OPEN)"""
    sent_orders = load_sent_orders(log_file)
    if not sent_orders:
        return []
    
    trades = []
    
    # Parse each sent order to create trade dict
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
            "Stop_Loss": order.get("stop_loss"),
            "Take_Profit": order.get("take_profit"),
            "RR": order.get("rr"),
            "Risk_Boost_Multiplier": order.get("risk_boost_multiplier", 1.0),
            "Signal_Timeframe": str(order.get("signal_timeframe", "")).strip(),
            "Result": status
        })
    
    # Sort by Entry_Time in descending order (newest first)
    trades.sort(key=lambda x: x.get("Entry_Time", ""), reverse=True)
    return trades


def generate_orders_panel(trades: list) -> Panel:
    """Generate orders log panel - display 10 most recent orders with status"""
    
    # Get 10 most recent trades
    recent_trades = trades[:10]
    
    table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
    table.add_column("Entry Time", style="dim", width=9)
    table.add_column("Symbol", style="cyan", width=14)
    table.add_column("Type", style="dim", width=5)
    table.add_column("Entry Price", style="green", width=11)
    table.add_column("RR", style="magenta", width=6)
    table.add_column("RBM", style="yellow", width=6)
    table.add_column("Result", style="dim", width=8)
    
    for trade in recent_trades:
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
        timeframe = trade.get("Signal_Timeframe", "")
        symbol_display = f"{symbol}_{timeframe}" if timeframe else symbol
        order_type = trade.get("Type", "-").upper()
        entry_price = trade.get("Entry_Price", "-")
        stop_loss = trade.get("Stop_Loss")
        take_profit = trade.get("Take_Profit")
        result = trade.get("Result", "Open")
        
        # Style for type
        type_style = "green" if order_type == "LONG" else "red" if order_type == "SHORT" else "white"
        
        # Style for result (WIN/LOSS/OPEN)
        if result == "WIN":
            result_style = "green bold"
        elif result == "LOSS":
            result_style = "red bold"
        elif result == "PASSED":
            result_style = "cyan bold"
        elif result == "PENDING":
            result_style = "magenta bold"
        elif result in ("REJECTED", "EXPIRED", "NOT_FILLED"):
            result_style = "dim"
        elif result == "FAILED":
            result_style = "red"
        else:  # OPEN
            result_style = "yellow bold"
        
        # Format entry price
        try:
            entry_price_float = float(entry_price)
            entry_price_str = f"{entry_price_float:,.4f}"
        except:
            entry_price_str = entry_price

        rr_value = "-"
        stored_rr = trade.get("RR")
        try:
            if stored_rr is not None and str(stored_rr).strip() != "":
                rr_value = f"{float(stored_rr):.2f}"
            else:
                entry_float = float(entry_price)
                sl_float = float(stop_loss)
                tp_float = float(take_profit)
                if order_type == "LONG":
                    risk = entry_float - sl_float
                    reward = tp_float - entry_float
                elif order_type == "SHORT":
                    risk = sl_float - entry_float
                    reward = entry_float - tp_float
                else:
                    risk = 0
                    reward = 0
                if risk > 0 and reward > 0:
                    rr_value = f"{reward / risk:.2f}"
        except:
            rr_value = "-"

        risk_boost_multiplier = "-"
        try:
            risk_boost_multiplier = f"{float(trade.get('Risk_Boost_Multiplier', 1.0)):.2f}"
        except:
            risk_boost_multiplier = "-"
        
        table.add_row(
            time_only,
            Text(symbol_display, style="cyan"),
            Text(order_type, style=type_style),
            Text(entry_price_str, style="green"),
            Text(rr_value, style="magenta"),
            Text(risk_boost_multiplier, style="yellow"),
            Text(result, style=result_style)
        )
    
    if not recent_trades:
        return Panel(Text("No trades yet...", style="dim"), 
                    title="[bold magenta]Trades Log GMT+0[/bold magenta]", 
                    border_style="magenta")
    
    return Panel(table, title="[bold magenta]Trades Log GMT+0[/bold magenta]", border_style="magenta")


# =============================================================================
# DASHBOARD UPDATE
# =============================================================================

def update_dashboard(
    layout: Layout,
    state,
    state_lock,
    timeframes: list,
    currencies: list,
    fetch_interval: int,
    output_folder: str,
    log_file: str,
    risk_reward_ratio: float
) -> Layout:
    """Update all panels in the dashboard"""
    # Load trades once for both stats and orders panels
    trades = load_trades_for_sent_orders(output_folder, log_file)
    
    layout["header"].update(generate_header(state, timeframes, currencies, risk_reward_ratio))
    # Row 1
    layout["process1"].update(generate_process1_panel(state, timeframes, fetch_interval))
    layout["stats"].update(generate_stats_panel(trades))
    # Row 2
    layout["process2"].update(generate_process2_panel(state, output_folder))
    layout["errors"].update(generate_orders_panel(trades))
    # Row 3
    layout["logs"].update(generate_logs_panel(state, state_lock))
    layout["orders"].update(generate_errors_panel(state, state_lock))
    return layout
