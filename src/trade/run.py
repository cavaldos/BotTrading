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

from src.utils.get_data import (remove_all_files, fetch_and_save_candles, append_latest_candle)
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

# Timeframe settings - support multiple timeframes
TIMEFRAMES_CONFIG = CONFIG["timeframe"]
if isinstance(TIMEFRAMES_CONFIG.get("value"), list):
    TIMEFRAMES = TIMEFRAMES_CONFIG["value"]
else:
    TIMEFRAMES = [TIMEFRAMES_CONFIG["value"]]

TIMEFRAME_MINUTES_MAP = {tf: parse_timeframe(tf) for tf in TIMEFRAMES}
DELAY_SECONDS = TIMEFRAMES_CONFIG["delay_seconds"]
RUN_ON_START = TIMEFRAMES_CONFIG["run_on_start"]

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


class TimeframeState:
     """State for a single timeframe"""
     def __init__(self, timeframe: str):
         self.timeframe = timeframe
         self.status = "Starting..."
         self.last_run = "-"
         self.next_candle = "-"
         self.countdown = "-"
         self.cycles = 0
         self.errors = 0


class AppState:
     """Shared state for UI"""
     def __init__(self):
         # Timeframe states
         self.timeframe_states = {tf: TimeframeState(tf) for tf in TIMEFRAMES}
         
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
     
     timeframes_str = ", ".join(TIMEFRAMES)
     
     header_text = Text()
     header_text.append("TRADE APPLICATION", style="bold white")
     header_text.append(f"  |  TF: {timeframes_str}", style="bold magenta")
     header_text.append(f"  |  Coins: {format_currencies(CURRENCIES)}", style="bold green")
     header_text.append(f"  |  Uptime: {uptime_str}", style="dim")
     header_text.append(f"  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
     
     return Panel(header_text, style="bold blue")


def generate_process1_panel() -> Panel:
     """Generate Process 1 status panel for all timeframes"""
     table = Table(show_header=True, box=None, padding=(0, 1))
     table.add_column("TF", style="magenta", width=5)
     table.add_column("Status", style="dim", width=12)
     table.add_column("Next Candle", style="cyan", width=10)
     table.add_column("Countdown", style="yellow", width=10)
     table.add_column("Cycles", style="green", width=6)
     table.add_column("Errors", style="red", width=6)
     
     for tf in TIMEFRAMES:
         tf_state = state.timeframe_states[tf]
         
         if tf_state.status == "Waiting":
             status_style = "yellow"
         elif "Fetching" in tf_state.status or "Executing" in tf_state.status:
             status_style = "cyan"
         else:
             status_style = "green"
         
         errors_style = "red" if tf_state.errors > 0 else "green"
         
         table.add_row(
             Text(tf, style="magenta"),
             Text(tf_state.status, style=status_style),
             tf_state.next_candle,
             Text(tf_state.countdown, style="bold yellow"),
             str(tf_state.cycles),
             Text(str(tf_state.errors), style=errors_style)
         )
     
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


def load_sent_orders() -> list:
     """Load sent orders from JSON log"""
     log_path = Path(LOG_FILE)
     if not log_path.exists():
         return []
     
     try:
         with open(log_path, 'r', encoding='utf-8') as f:
             data = json.load(f)
             orders = data.get("sent_orders", [])
             return orders if isinstance(orders, list) else []
     except:
         return []


def load_trades_for_sent_orders() -> list:
     """Load trades from CSV that match sent orders, mark as Passed if not in CSV"""
     sent_orders = load_sent_orders()
     if not sent_orders:
         return []
     
     trades = []
     found_keys = set()
     output_path = Path(OUTPUT_FOLDER)
     
     # First, load all trades from CSV
     try:
         import csv
         if output_path.exists():
             trade_files = output_path.glob("*_trades.csv")
             
             for trade_file in trade_files:
                 try:
                     with open(trade_file, 'r', encoding='utf-8') as f:
                         reader = csv.DictReader(f)
                         for row in reader:
                             # Build key from trade: "SYMBOL|TYPE|ENTRY_TIME"
                             symbol = row.get("Symbol", "")
                             trade_type = row.get("Type", "")
                             entry_time = row.get("Entry_Time", "")
                             
                             trade_key = f"{symbol}|{trade_type}|{entry_time}"
                             
                             # Check if this trade was sent
                             for order in sent_orders:
                                 if order.get("key", "") == trade_key:
                                     trades.append(row)
                                     found_keys.add(trade_key)
                                     break
                 except Exception as e:
                     continue
     except:
         pass
     
     # Second, add sent orders not found in CSV with Result = "Passed"
     for order in sent_orders:
         key = order.get("key", "")
         if key and key not in found_keys:
             # Parse the key to get symbol, type, entry_time
             parts = key.split("|")
             if len(parts) >= 3:
                 symbol = parts[0]
                 trade_type = parts[1]
                 entry_time = parts[2]
                 
                 trades.append({
                     "Symbol": symbol,
                     "Type": trade_type,
                     "Entry_Time": entry_time,
                     "Entry_Price": str(order.get("entry_price", "-")),
                     "Result": "Passed"
                 })
     
     # Sort by Entry_Time in descending order (newest first)
     trades.sort(key=lambda x: x.get("Entry_Time", ""), reverse=True)
     return trades


def generate_orders_panel() -> Panel:
     """Generate orders log panel - hiển thị 10 lệnh mới nhất với status"""
     trades = load_trades_for_sent_orders()
     
     # Lấy 10 lệnh mới nhất
     recent_trades = trades[:10]
     
     table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
     table.add_column("Entry Time", style="dim", width=9)
     table.add_column("Symbol", style="cyan", width=10)
     table.add_column("Type", style="dim", width=5)
     table.add_column("Entry Price", style="green", width=11)
     table.add_column("Result", style="dim", width=8)
     
     for trade in recent_trades:
         # Extract time from Entry_Time (format: "2026-01-15 17:40:00")
         entry_time = trade.get("Entry_Time", "")
         if entry_time:
             try:
                 time_only = entry_time.split(" ")[1] if " " in entry_time else entry_time
             except:
                 time_only = entry_time
         else:
             time_only = "-"
         
         symbol = trade.get("Symbol", "-")
         order_type = trade.get("Type", "-").upper()
         entry_price = trade.get("Entry_Price", "-")
         result = trade.get("Result", "Open")
         
         # Style for type
         type_style = "green" if order_type == "LONG" else "red" if order_type == "SHORT" else "white"
         
         # Style for result
         if result == "Win":
             result_style = "green bold"
         elif result == "Loss":
             result_style = "red bold"
         elif result == "Passed":
             result_style = "blue bold"
         else:  # Open
             result_style = "yellow bold"
         
         # Format entry price
         try:
             entry_price_float = float(entry_price)
             entry_price_str = f"{entry_price_float:,.2f}"
         except:
             entry_price_str = entry_price
         
         table.add_row(
             time_only,
             Text(symbol, style="cyan"),
             Text(order_type, style=type_style),
             Text(entry_price_str, style="green"),
             Text(result, style=result_style)
         )
     
     if not recent_trades:
         return Panel(Text("No trades yet...", style="dim"), 
                     title="[bold magenta]Trades Log[/bold magenta]", 
                     border_style="magenta")
     
     return Panel(table, title="[bold magenta]Trades Log[/bold magenta]", border_style="magenta")


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

def getdata(timeframe: str):
      """Fetch data from API for a specific timeframe"""
      if not os.path.exists(DATA_FOLDER):
          os.makedirs(DATA_FOLDER)
      
      # Check if data files exist (first run vs subsequent runs)
      has_data_files = any(
          os.path.isfile(os.path.join(DATA_FOLDER, f"{curr}_{timeframe}.csv"))
          for curr in CURRENCIES
      )
      
      if not has_data_files:
          # First run: remove old data and fetch all candles
          fetch_and_save_candles(CURRENCIES, [timeframe], DATA_FOLDER, CANDLES_LIMIT)
      else:
          # Subsequent runs: only append latest candle and remove second row
          append_latest_candle(CURRENCIES, [timeframe], DATA_FOLDER)


def run_cycle(timeframe: str):
     """Run one cycle of data fetch and execute for a specific timeframe"""
     tf_state = state.timeframe_states[timeframe]
     try:
         tf_state.status = "Fetching data..."
         state.add_log(f"TF[{timeframe}]", f"Fetching data...")
         getdata(timeframe)
         
         tf_state.status = "Executing..."
         state.add_log(f"TF[{timeframe}]", "Running execute...")
         execute(DATA_FOLDER, OUTPUT_FOLDER)
         
         tf_state.cycles += 1
         tf_state.last_run = datetime.now().strftime("%H:%M:%S")
         state.add_log(f"TF[{timeframe}]", "Cycle completed")
         return True
         
     except Exception as e:
         tf_state.errors += 1
         state.add_log(f"TF[{timeframe}]", f"ERROR: {str(e)}")
         return False


def process_1_data_and_execute(timeframe: str):
     """Process 1: Fetch data and run execute at candle close times for a specific timeframe"""
     tf_state = state.timeframe_states[timeframe]
     timeframe_minutes = TIMEFRAME_MINUTES_MAP[timeframe]
     
     state.add_log(f"TF[{timeframe}]", f"Started (Delay: {DELAY_SECONDS}s)")
     
     # Run immediately on start if configured
     if RUN_ON_START:
         state.add_log(f"TF[{timeframe}]", "Running initial cycle...")
         run_cycle(timeframe)
     
     while state.running:
         # Calculate next candle close time
         next_candle = get_next_candle_close_time(timeframe_minutes)
         target_time = next_candle + timedelta(seconds=DELAY_SECONDS)
         
         tf_state.next_candle = next_candle.strftime("%H:%M:%S")
         tf_state.status = "Waiting"
         state.add_log(f"TF[{timeframe}]", f"Waiting for next candle: {next_candle.strftime('%H:%M:%S')}")
         
         # Wait until target time (candle close + delay)
         while state.running:
             now = datetime.now()
             if now >= target_time:
                 break
             
             wait_seconds = (target_time - now).total_seconds()
             tf_state.countdown = format_countdown(wait_seconds)
             time.sleep(0.5)
         
         if not state.running:
             break
         
         # Run the cycle
         state.add_log(f"TF[{timeframe}]", f"Candle closed, running cycle...")
         run_cycle(timeframe)
         
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
     
     remove_all_files(OUTPUT_FOLDER)
     
     # Create threads for each timeframe
     tf_threads = []
     for timeframe in TIMEFRAMES:
         thread = threading.Thread(
             target=process_1_data_and_execute,
             args=(timeframe,),
             daemon=True,
             name=f"DataExecuteThread-{timeframe}"
         )
         tf_threads.append(thread)
     
     # Create watcher thread
     thread_2 = threading.Thread(
         target=process_2_watch_output,
         daemon=True,
         name="OutputWatcherThread"
     )
     
     # Start all threads
     for thread in tf_threads:
         thread.start()
     thread_2.start()
     
     timeframes_str = ", ".join(TIMEFRAMES)
     state.add_log("Main", f"Application started (TF: {timeframes_str})")
     
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
