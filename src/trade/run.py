import os
import time
import json
import threading
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from src.utils.get_data import (remove_all_files, fetch_and_save_candles)
from src.trade.execute import execute
from src.trade.send import check_and_send_open_orders


# =============================================================================
# CONFIG LOADER
# =============================================================================

def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def parse_timeframe(timeframe: str) -> int:
    """
    Convert timeframe string to minutes
    Examples: "1m" -> 1, "5m" -> 5, "15m" -> 15, "1h" -> 60, "4h" -> 240, "1d" -> 1440
    """
    timeframe = timeframe.lower().strip()
    
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    elif timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    elif timeframe.endswith("d"):
        return int(timeframe[:-1]) * 1440
    else:
        raise ValueError(f"Invalid timeframe format: {timeframe}")


# Load config
CONFIG = load_config()

# Data settings
DATA_FOLDER = CONFIG["data"]["folder"]
CURRENCIES = CONFIG["data"]["currencies"]
CANDLES_LIMIT = CONFIG["data"]["candles_limit"]

# Output settings
OUTPUT_FOLDER = CONFIG["output"]["folder"]
LOG_FILE = CONFIG["output"]["log_file"]

# Timeframe settings
TIMEFRAME = CONFIG["timeframe"]["value"]
TIMEFRAME_MINUTES = parse_timeframe(TIMEFRAME)
DELAY_SECONDS = CONFIG["timeframe"]["delay_seconds"]
RUN_ON_START = CONFIG["timeframe"]["run_on_start"]

# Dashboard settings
REFRESH_RATE = CONFIG["dashboard"]["refresh_rate"]
MAX_LOGS = CONFIG["dashboard"]["max_logs"]


# =============================================================================
# TIMEFRAME UTILITIES
# =============================================================================

def get_next_candle_close_time(timeframe_minutes: int) -> datetime:
    """
    Calculate next candle close time
    Example: If timeframe=5 and current time is 6:07 -> Next close is 6:10
    """
    now = datetime.now()
    
    if timeframe_minutes >= 1440:  # Daily or higher
        # Next day at 00:00
        next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif timeframe_minutes >= 60:  # Hourly
        hours = timeframe_minutes // 60
        current_hour = now.hour
        next_hour = ((current_hour // hours) + 1) * hours
        
        if next_hour >= 24:
            next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_time = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    else:  # Minutes
        current_minute = now.hour * 60 + now.minute
        next_minute = ((current_minute // timeframe_minutes) + 1) * timeframe_minutes
        
        next_hour = next_minute // 60
        next_min = next_minute % 60
        
        if next_hour >= 24:
            next_time = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_time = now.replace(hour=next_hour, minute=next_min, second=0, microsecond=0)
    
    return next_time


def get_seconds_until_next_candle(timeframe_minutes: int, delay_seconds: int = 0) -> float:
    """Calculate seconds until next candle close + delay"""
    next_close = get_next_candle_close_time(timeframe_minutes)
    target_time = next_close + timedelta(seconds=delay_seconds)
    
    wait_seconds = (target_time - datetime.now()).total_seconds()
    return max(0, wait_seconds)


def format_countdown(seconds: float) -> str:
    """Format seconds to MM:SS or HH:MM:SS"""
    if seconds < 0:
        return "00:00"
    
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# =============================================================================
# APP STATE
# =============================================================================

# Lock to avoid race condition
send_lock = threading.Lock()
state_lock = threading.Lock()


class AppState:
    """Shared state for UI"""
    def __init__(self):
        # Process 1 state
        self.p1_status = "Starting..."
        self.p1_last_run = "-"
        self.p1_next_candle = "-"
        self.p1_countdown = "-"
        self.p1_cycles = 0
        self.p1_errors = 0
        
        # Process 2 state
        self.p2_status = "Starting..."
        self.p2_last_event = "-"
        self.p2_orders_sent = 0
        self.p2_events = 0
        
        # Logs
        self.logs = deque(maxlen=MAX_LOGS)
        
        # App state
        self.running = True
        self.start_time = datetime.now()

    def add_log(self, process: str, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with state_lock:
            self.logs.appendleft(f"[{timestamp}] [{process}] {message}")


state = AppState()
console = Console()


# =============================================================================
# DASHBOARD
# =============================================================================

def create_dashboard() -> Layout:
    """Create the dashboard layout"""
    layout = Layout()
    
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", size=14),
        Layout(name="bottom", size=13),
    )
    
    layout["body"].split_row(
        Layout(name="process1"),
        Layout(name="process2"),
    )
    
    layout["bottom"].split_row(
        Layout(name="logs"),
        Layout(name="orders"),
    )
    
    return layout


def format_currencies(currencies: list) -> str:
    """Format currencies list for display"""
    if len(currencies) <= 3:
        return ", ".join(currencies)
    return f"{currencies[0]}, {currencies[1]}, ... +{len(currencies) - 2} more"


def generate_header() -> Panel:
    """Generate header panel"""
    uptime = datetime.now() - state.start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    header_text = Text()
    header_text.append("TRADE APPLICATION", style="bold white")
    header_text.append(f"  |  TF: {TIMEFRAME}", style="bold magenta")
    header_text.append(f"  |  Coins: {format_currencies(CURRENCIES)}", style="bold green")
    header_text.append(f"  |  Uptime: {uptime_str}", style="dim")
    header_text.append(f"  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
    
    return Panel(header_text, style="bold blue")


def generate_process1_panel() -> Panel:
    """Generate Process 1 status panel"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="dim")
    table.add_column("Value")
    
    if state.p1_status == "Waiting":
        status_style = "yellow"
    elif "Fetching" in state.p1_status or "Executing" in state.p1_status:
        status_style = "cyan"
    else:
        status_style = "green"
    
    # Format coins display
    coins_display = ", ".join(CURRENCIES) if len(CURRENCIES) <= 2 else f"{len(CURRENCIES)} coins"
    
    table.add_row("Status", Text(state.p1_status, style=status_style))
    table.add_row("Coins", Text(coins_display, style="green"))
    table.add_row("Timeframe", Text(TIMEFRAME, style="magenta"))
    table.add_row("Next Candle", state.p1_next_candle)
    table.add_row("Countdown", Text(state.p1_countdown, style="bold yellow"))
    table.add_row("Last Run", state.p1_last_run)
    table.add_row("Cycles", str(state.p1_cycles))
    table.add_row("Errors", Text(str(state.p1_errors), style="red" if state.p1_errors > 0 else "green"))
    
    return Panel(table, title="[cyan]Process 1: Data & Execute[/cyan]", border_style="cyan")


def generate_process2_panel() -> Panel:
    """Generate Process 2 status panel"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="dim")
    table.add_column("Value")
    
    status_style = "green" if "Watching" in state.p2_status else "yellow"
    
    table.add_row("Status", Text(state.p2_status, style=status_style))
    table.add_row("Folder", OUTPUT_FOLDER)
    table.add_row("Last Event", state.p2_last_event)
    table.add_row("Orders Sent", Text(str(state.p2_orders_sent), style="green"))
    table.add_row("Total Events", str(state.p2_events))
    
    return Panel(table, title="[yellow]Process 2: Watcher[/yellow]", border_style="yellow")


def generate_logs_panel() -> Panel:
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


def load_orders_from_log() -> list:
    """Load orders from log file"""
    log_path = Path(LOG_FILE)
    if not log_path.exists():
        return []
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            orders = data.get("sent_orders", [])
            # Chỉ lấy format mới (list of dicts)
            if orders and isinstance(orders[0], dict):
                return orders
            return []
    except:
        return []


def generate_orders_panel() -> Panel:
    """Generate orders log panel - hiển thị 10 lệnh mới nhất"""
    orders = load_orders_from_log()
    
    # Lấy 10 lệnh mới nhất (reverse để mới nhất lên đầu)
    recent_orders = orders[-10:][::-1]
    
    table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
    table.add_column("Time", style="dim", width=8)
    table.add_column("Symbol", style="green")
    table.add_column("Type", width=5)
    
    for order in recent_orders:
        # Chỉ lấy giờ (không lấy ngày)
        sent_at = order.get("sent_at", "")
        if sent_at:
            try:
                time_only = sent_at.split(" ")[1] if " " in sent_at else sent_at
            except:
                time_only = sent_at
        else:
            time_only = "-"
        
        # Parse symbol và type từ key (format: "SYMBOL|TYPE|ENTRY_TIME")
        key = order.get("key", "")
        parts = key.split("|")
        symbol = parts[0] if len(parts) > 0 else "-"
        order_type = parts[1] if len(parts) > 1 else "-"
        
        # Style cho type
        type_style = "green" if order_type.upper() == "BUY" else "red"
        
        table.add_row(
            time_only,
            symbol,
            Text(order_type, style=type_style)
        )
    
    if not recent_orders:
        return Panel(Text("No orders sent yet...", style="dim"), 
                    title="[bold magenta]Orders Log[/bold magenta]", 
                    border_style="magenta")
    
    return Panel(table, title="[bold magenta]Orders Log[/bold magenta]", border_style="magenta")


def update_dashboard(layout: Layout) -> Layout:
    """Update all panels in the dashboard"""
    layout["header"].update(generate_header())
    layout["process1"].update(generate_process1_panel())
    layout["process2"].update(generate_process2_panel())
    layout["logs"].update(generate_logs_panel())
    layout["orders"].update(generate_orders_panel())
    return layout


# =============================================================================
# FILE WATCHER
# =============================================================================

class OutputFolderHandler(FileSystemEventHandler):
    """Handler to watch changes in output folder"""
    
    def __init__(self, output_folder, log_file):
        self.output_folder = output_folder
        self.log_file = log_file
        self.last_processed = 0
        self.debounce_seconds = 2
    
    def on_any_event(self, event):
        if event.is_directory:
            return
        
        current_time = time.time()
        if current_time - self.last_processed < self.debounce_seconds:
            return
        
        self.last_processed = current_time
        state.p2_events += 1
        state.p2_last_event = datetime.now().strftime("%H:%M:%S")
        state.p2_status = "Processing..."
        
        filename = os.path.basename(event.src_path)
        state.add_log("Process 2", f"Change detected: {filename}")
        
        with send_lock:
            try:
                new_orders_sent = check_and_send_open_orders(self.output_folder, self.log_file)
                state.p2_orders_sent += new_orders_sent
                state.add_log("Process 2", f"Sent {new_orders_sent} new orders")
            except Exception as e:
                state.add_log("Process 2", f"ERROR: {str(e)}")
        
        state.p2_status = "Watching..."


# =============================================================================
# PROCESSES
# =============================================================================

def getdata():
    """Fetch data from API"""
    remove_all_files(DATA_FOLDER)
    remove_all_files(OUTPUT_FOLDER)
    
    if not os.path.exists(DATA_FOLDER):
        os.makedirs(DATA_FOLDER)
    
    fetch_and_save_candles(CURRENCIES, [TIMEFRAME], DATA_FOLDER, CANDLES_LIMIT)


def run_cycle():
    """Run one cycle of data fetch and execute"""
    try:
        state.p1_status = "Fetching data..."
        state.add_log("Process 1", f"Fetching data ({TIMEFRAME})...")
        getdata()
        
        state.p1_status = "Executing..."
        state.add_log("Process 1", "Running execute...")
        execute(DATA_FOLDER, OUTPUT_FOLDER)
        
        state.p1_cycles += 1
        state.p1_last_run = datetime.now().strftime("%H:%M:%S")
        state.add_log("Process 1", "Cycle completed")
        return True
        
    except Exception as e:
        state.p1_errors += 1
        state.add_log("Process 1", f"ERROR: {str(e)}")
        return False


def process_1_data_and_execute():
    """Process 1: Fetch data and run execute at candle close times"""
    state.add_log("Process 1", f"Started (TF: {TIMEFRAME}, Delay: {DELAY_SECONDS}s)")
    
    # Run immediately on start if configured
    if RUN_ON_START:
        state.add_log("Process 1", "Running initial cycle...")
        run_cycle()
    
    while state.running:
        # Calculate next candle close time
        next_candle = get_next_candle_close_time(TIMEFRAME_MINUTES)
        target_time = next_candle + timedelta(seconds=DELAY_SECONDS)
        
        state.p1_next_candle = next_candle.strftime("%H:%M:%S")
        state.p1_status = "Waiting"
        state.add_log("Process 1", f"Waiting for next candle: {next_candle.strftime('%H:%M:%S')}")
        
        # Wait until target time (candle close + delay)
        while state.running:
            now = datetime.now()
            if now >= target_time:
                break
            
            wait_seconds = (target_time - now).total_seconds()
            state.p1_countdown = format_countdown(wait_seconds)
            time.sleep(0.5)
        
        if not state.running:
            break
        
        # Run the cycle
        state.add_log("Process 1", f"Candle closed, running cycle...")
        run_cycle()
        
        # Wait until we're past this candle period to avoid re-triggering
        # Sleep at least 2 seconds to ensure we're in the next candle period
        time.sleep(2)


def process_2_watch_output():
    """Process 2: Watch output folder"""
    state.add_log("Process 2", "Started")
    
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)
    
    event_handler = OutputFolderHandler(OUTPUT_FOLDER, LOG_FILE)
    observer = Observer()
    observer.schedule(event_handler, OUTPUT_FOLDER, recursive=True)
    observer.start()
    
    state.p2_status = "Watching..."
    state.add_log("Process 2", f"Watching: {OUTPUT_FOLDER}")
    
    try:
        while state.running:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()


# =============================================================================
# MAIN
# =============================================================================

def main():
    # Ensure required folders exist
    for folder in [DATA_FOLDER, OUTPUT_FOLDER, os.path.dirname(LOG_FILE)]:
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
    
    # Create threads
    thread_1 = threading.Thread(
        target=process_1_data_and_execute,
        daemon=True,
        name="DataExecuteThread"
    )
    
    thread_2 = threading.Thread(
        target=process_2_watch_output,
        daemon=True,
        name="OutputWatcherThread"
    )
    
    # Start threads
    thread_1.start()
    thread_2.start()
    
    state.add_log("Main", f"Application started (Config: {TIMEFRAME})")
    
    # Create dashboard
    layout = create_dashboard()
    
    try:
        with Live(layout, refresh_per_second=REFRESH_RATE, screen=True) as live:
            while True:
                update_dashboard(layout)
                time.sleep(1.0 / REFRESH_RATE)
    except KeyboardInterrupt:
        state.running = False
        console.print("\n[bold red]Shutting down...[/bold red]")
        time.sleep(1)
        console.print("[bold green]Application terminated.[/bold green]")


if __name__ == "__main__":
    main()
