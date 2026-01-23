import os
import time
import threading
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from rich.console import Console
from rich.live import Live

from src.utils.get_data import (remove_all_files, fetch_and_save_candles_parallel, append_latest_candle_parallel)
from src.trade.dashboard import create_dashboard, update_dashboard
from src.trade.core.execute import execute
from src.trade.core.send import check_and_send_open_orders


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
FETCH_INTERVAL = TIMEFRAMES_CONFIG.get("fetch_interval", 60)  # Default 60 seconds


# Dashboard settings
REFRESH_RATE = CONFIG["dashboard"]["refresh_rate"]
MAX_LOGS = CONFIG["dashboard"]["max_logs"]

# Strategy settings
RISK_REWARD_RATIO = CONFIG["strategy"]["risk_reward_ratio"]
MIN_SL_PCT = CONFIG["strategy"].get("min_sl_pct", 0.05)
OB_BODY_RATIO = CONFIG["strategy"].get("ob_body_ratio", 0.35)


# =============================================================================
# TIMEFRAME UTILITIES
# =============================================================================

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
         self.next_fetch = "-"
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
         
         # Error logs (store last 10 errors with details)
         self.error_logs = deque(maxlen=10)
         
         # App state
         self.running = True
         self.start_time = datetime.now()
         
         # Shutdown event for clean thread termination
         self.shutdown_event = threading.Event()

     def add_log(self, process: str, message: str):
         timestamp = datetime.now().strftime("%H:%M:%S")
         with state_lock:
             self.logs.appendleft(f"[{timestamp}] [{process}] {message}")

     def add_error(self, timeframe: str, error_message: str):
         """Add error with details to error_logs"""
         timestamp = datetime.now().strftime("%H:%M:%S")
         with state_lock:
             self.error_logs.appendleft({
                 "time": timestamp,
                 "timeframe": timeframe,
                 "message": str(error_message)[:100]  # Truncate long messages
             })


state = AppState()
console = Console()


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
          # First run: remove old data and fetch all candles (parallel)
          fetch_and_save_candles_parallel(CURRENCIES, [timeframe], DATA_FOLDER, CANDLES_LIMIT)
      else:
          # Subsequent runs: only append latest candle and remove second row (parallel)
          append_latest_candle_parallel(CURRENCIES, [timeframe], DATA_FOLDER)


def run_cycle(timeframe: str):
     """Run one cycle of data fetch and execute for a specific timeframe"""
     tf_state = state.timeframe_states[timeframe]
     try:
         tf_state.status = "Fetching data..."
         state.add_log(f"TF[{timeframe}]", f"Fetching data...")
         getdata(timeframe)
         
         tf_state.status = "Executing..."
         state.add_log(f"TF[{timeframe}]", "Running execute...")
         execute(DATA_FOLDER, OUTPUT_FOLDER, timeframe, RISK_REWARD_RATIO, MIN_SL_PCT, OB_BODY_RATIO)
         
         tf_state.cycles += 1
         tf_state.last_run = datetime.now().strftime("%H:%M:%S")
         state.add_log(f"TF[{timeframe}]", "Cycle completed")
         return True
         
     except Exception as e:
         tf_state.errors += 1
         error_msg = str(e)
         state.add_log(f"TF[{timeframe}]", f"ERROR: {error_msg}")
         state.add_error(timeframe, error_msg)
         return False


def process_1_data_and_execute(timeframe: str):
     """Process 1: Fetch data and run execute every FETCH_INTERVAL seconds for a specific timeframe"""
     tf_state = state.timeframe_states[timeframe]
     
     state.add_log(f"TF[{timeframe}]", f"Started (Interval: {FETCH_INTERVAL}s)")
     
     while state.running:
         # Run the cycle immediately
         tf_state.status = "Running..."
         run_cycle(timeframe)
         
         if not state.running:
             break
         
         # Calculate next fetch time
         next_fetch_time = datetime.now() + timedelta(seconds=FETCH_INTERVAL)
         tf_state.next_fetch = next_fetch_time.strftime("%H:%M:%S")
         tf_state.status = "Waiting"
         
         # Wait for next interval with countdown (using Event for clean shutdown)
         wait_remaining = FETCH_INTERVAL
         while wait_remaining > 0:
             tf_state.countdown = format_countdown(wait_remaining)
             # Wait 1 second or until shutdown signal
             if state.shutdown_event.wait(timeout=1):
                 break
             wait_remaining -= 1
         
         tf_state.countdown = "00:00"


def process_2_watch_output():
    """Process 2: Watch output folder"""
    state.add_log("Process 2", "Started")
    
    
    event_handler = OutputFolderHandler(OUTPUT_FOLDER, LOG_FILE)
    observer = Observer()
    observer.schedule(event_handler, OUTPUT_FOLDER, recursive=True)
    observer.start()
    
    state.p2_status = "Watching..."
    state.add_log("Process 2", f"Watching: {OUTPUT_FOLDER}")
    
    try:
        # Wait for shutdown signal instead of busy-wait loop
        state.shutdown_event.wait()
    finally:
        observer.stop()
        observer.join()


# =============================================================================
# MAIN
# =============================================================================

def main():
     
     remove_all_files(OUTPUT_FOLDER)
     remove_all_files(DATA_FOLDER)
     
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
                    update_dashboard(
                         layout,
                         state,
                         state_lock,
                         TIMEFRAMES,
                         CURRENCIES,
                         FETCH_INTERVAL,
                         OUTPUT_FOLDER,
                         LOG_FILE,
                         RISK_REWARD_RATIO
                    )
                    time.sleep(1.0 / REFRESH_RATE)
     except KeyboardInterrupt:
         state.running = False
         state.shutdown_event.set()  # Signal all threads to stop immediately
         console.print("\n[bold red]Shutting down...[/bold red]")
         time.sleep(0.5)  # Brief wait for threads to respond
         console.print("[bold green]Application terminated.[/bold green]")


if __name__ == "__main__":
    main()
