import os
import time
import math
import threading
from queue import Queue, Empty
from datetime import datetime, timedelta, timezone
from collections import deque
from pathlib import Path
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from rich.console import Console
from rich.live import Live

from src.utils.remove_all_files import remove_all_files
from src.utils.get_data import (fetch_and_save_candles_parallel, append_latest_candle_parallel)
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
WAIT_FOR_CLOSE = TIMEFRAMES_CONFIG.get("wait_for_close", True)  # Default: wait for candle close
CLOSE_DELAY = TIMEFRAMES_CONFIG.get("close_delay", 3)  # Seconds after candle close to wait


# Dashboard settings
REFRESH_RATE = CONFIG["dashboard"]["refresh_rate"]
MAX_LOGS = CONFIG["dashboard"]["max_logs"]

# Strategy settings
STRATEGY_CONFIG = CONFIG["strategy"]
RISK_REWARD_RATIO = STRATEGY_CONFIG.get("risk_reward_ratio", 2.0)
# Structure Detection
SWING_LENGTH = STRATEGY_CONFIG.get("swing_length", 10)
INTERNAL_LENGTH = STRATEGY_CONFIG.get("internal_length", 3)
# EQH/EQL Detection
EQHL_LENGTH = STRATEGY_CONFIG.get("eqhl_length", 3)
EQHL_THRESHOLD = STRATEGY_CONFIG.get("eqhl_threshold", 0.3)
# Order Block
OB_MITIGATION = STRATEGY_CONFIG.get("ob_mitigation", "high_low")
OB_BODY_RATIO = STRATEGY_CONFIG.get("ob_body_ratio", 0.35)
# Entry filters
REQUIRE_LIQUIDITY = STRATEGY_CONFIG.get("require_liquidity", True)
REQUIRE_REVERSAL_PATTERN = STRATEGY_CONFIG.get("require_reversal_pattern", True)
ENABLE_SIMPLE_REVERSAL = STRATEGY_CONFIG.get("enable_simple_reversal", True)
SIMPLE_REVERSAL_WICK_RATIO = STRATEGY_CONFIG.get("simple_reversal_wick_ratio", 0.25)
TOUCH_TOLERANCE_PCT = STRATEGY_CONFIG.get("touch_tolerance_pct", 0.0)
TOUCH_CONFIRMATION_BARS = STRATEGY_CONFIG.get("touch_confirmation_bars", 0)
# FVG
FVG_ATR_FILTER = STRATEGY_CONFIG.get("fvg_atr_filter", True)
# Risk Management
SL_BUFFER_PCT = STRATEGY_CONFIG.get("sl_buffer_pct", 0.1)
SL_MULTIPLIER = STRATEGY_CONFIG.get("sl_multiplier", 1.05)
MIN_SL_PCT = STRATEGY_CONFIG.get("min_sl_pct", 0.05)
RISK_PER_TRADE = STRATEGY_CONFIG.get("risk_per_trade", 10.0)


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


def _next_candle_close_utc(timeframe: str) -> datetime:
    """
    Calculate the next candle close time in UTC for a given timeframe.
    
    Examples (timeframe=5m, current UTC=06:07:30):
      Current candle opened at 06:05, closes at 06:10:00 UTC
      Returns: 06:10:00 UTC
    
    Examples (timeframe=1h, current UTC=14:23:00):
      Current candle opened at 14:00, closes at 15:00:00 UTC
      Returns: 15:00:00 UTC
    """
    tf_minutes = TIMEFRAME_MINUTES_MAP.get(timeframe, parse_timeframe(timeframe))
    now = datetime.now(timezone.utc)
    
    # Calculate seconds since midnight UTC
    midnight_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = (now - midnight_utc).total_seconds()
    tf_seconds = tf_minutes * 60
    
    # Current candle started at floor(elapsed / tf_seconds) * tf_seconds
    current_candle_start = math.floor(elapsed_seconds / tf_seconds) * tf_seconds
    # Next candle close = current candle start + tf_seconds
    next_close_seconds = current_candle_start + tf_seconds
    
    next_close = midnight_utc + timedelta(seconds=next_close_seconds)
    
    # If we're exactly at close time, the candle just closed - next close is one period later
    if (now - next_close).total_seconds() >= 0:
        next_close += timedelta(seconds=tf_seconds)
    
    return next_close


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

# Process 2 queue config
EVENT_DEBOUNCE_SECONDS = 0.25
STATUS_SYNC_INTERVAL = 30


# =============================================================================
# FILE WATCHER
# =============================================================================

class OutputFolderHandler(FileSystemEventHandler):
    """Handler to watch changes in output folder"""

    def __init__(self, event_queue: Queue):
        self.event_queue = event_queue

    def on_modified(self, event):
        self._handle_event(event)

    def on_created(self, event):
        self._handle_event(event)

    def _handle_event(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith(".csv"):
            return
        self.event_queue.put(event.src_path)


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
         execute(
             DATA_FOLDER, OUTPUT_FOLDER, timeframe,
             risk_reward_ratio=RISK_REWARD_RATIO,
             swing_length=SWING_LENGTH,
             internal_length=INTERNAL_LENGTH,
             eqhl_length=EQHL_LENGTH,
             eqhl_threshold=EQHL_THRESHOLD,
             ob_mitigation=OB_MITIGATION,
             ob_body_ratio=OB_BODY_RATIO,
             require_liquidity=REQUIRE_LIQUIDITY,
             require_reversal_pattern=REQUIRE_REVERSAL_PATTERN,
             enable_simple_reversal=ENABLE_SIMPLE_REVERSAL,
             simple_reversal_wick_ratio=SIMPLE_REVERSAL_WICK_RATIO,
             touch_tolerance_pct=TOUCH_TOLERANCE_PCT,
             touch_confirmation_bars=TOUCH_CONFIRMATION_BARS,
             fvg_atr_filter=FVG_ATR_FILTER,
             sl_buffer_pct=SL_BUFFER_PCT,
             sl_multiplier=SL_MULTIPLIER,
             min_sl_pct=MIN_SL_PCT,
             risk_per_trade=RISK_PER_TRADE
         )
         
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
     """
     Process 1: Fetch data and run execute for a specific timeframe.
     
     Two modes:
     - wait_for_close=True (default): Waits until the current candle closes,
       then fetches data and runs strategy exactly once per candle. This prevents
       repainting by ensuring the strategy only sees completed candles.
     - wait_for_close=False: Runs every FETCH_INTERVAL seconds (legacy behavior).
     """
     tf_state = state.timeframe_states[timeframe]
     
     if WAIT_FOR_CLOSE:
         state.add_log(f"TF[{timeframe}]", f"Started (Wait-for-close mode, delay: {CLOSE_DELAY}s)")
     else:
         state.add_log(f"TF[{timeframe}]", f"Started (Interval: {FETCH_INTERVAL}s)")
     
     # First run: always execute immediately to populate data
     tf_state.status = "Running..."
     run_cycle(timeframe)
     
     if not state.running:
         return
     
     while state.running:
         if WAIT_FOR_CLOSE:
             # === WAIT-FOR-CLOSE MODE ===
             # Calculate when the next candle closes
             next_close = _next_candle_close_utc(timeframe)
             # Add delay buffer to ensure exchange has finalized the candle
             target_time = next_close + timedelta(seconds=CLOSE_DELAY)
             
             now = datetime.now(timezone.utc)
             wait_seconds = max(0, (target_time - now).total_seconds())
             
             tf_state.next_fetch = next_close.strftime("%H:%M:%S")
             tf_state.status = "Waiting for candle close"
             state.add_log(f"TF[{timeframe}]", f"Next candle close: {next_close.strftime('%H:%M:%S')} UTC (wait {int(wait_seconds)}s)")
             
             # Wait with countdown (check shutdown every 1 second)
             while wait_seconds > 0 and not state.shutdown_event.is_set():
                 tf_state.countdown = format_countdown(wait_seconds)
                 sleep_time = min(1.0, wait_seconds)
                 if state.shutdown_event.wait(timeout=sleep_time):
                     break
                 wait_seconds -= sleep_time
             
             tf_state.countdown = "00:00"
             
             if not state.running:
                 break
             
             # Candle has closed → fetch + execute
             tf_state.status = "Running..."
             run_cycle(timeframe)
             
         else:
             # === LEGACY INTERVAL MODE ===
             # Calculate next fetch time
             next_fetch_time = datetime.now() + timedelta(seconds=FETCH_INTERVAL)
             tf_state.next_fetch = next_fetch_time.strftime("%H:%M:%S")
             tf_state.status = "Waiting"
             
             # Wait for next interval with countdown
             wait_remaining = FETCH_INTERVAL
             while wait_remaining > 0:
                 tf_state.countdown = format_countdown(wait_remaining)
                 if state.shutdown_event.wait(timeout=1):
                     break
                 wait_remaining -= 1
             
             tf_state.countdown = "00:00"
             
             if not state.running:
                 break
             
             tf_state.status = "Running..."
             run_cycle(timeframe)


def process_2_watch_output():
    """Process 2: Watch output folder"""
    state.add_log("Process 2", "Started")

    event_queue: Queue[str] = Queue()
    event_handler = OutputFolderHandler(event_queue)
    observer = Observer()
    observer.schedule(event_handler, OUTPUT_FOLDER, recursive=True)
    observer.start()
    
    state.p2_status = "Watching..."
    state.add_log("Process 2", f"Watching: {OUTPUT_FOLDER}")

    worker = threading.Thread(
        target=process_2_event_worker,
        args=(event_queue,),
        daemon=True,
        name="OutputWatcherWorker",
    )
    worker.start()

    try:
        # Wait for shutdown signal instead of busy-wait loop
        state.shutdown_event.wait()
    finally:
        observer.stop()
        observer.join()


def process_2_event_worker(event_queue: Queue[str]):
    pending_files: set[str] = set()
    last_status_sync = time.time()
    last_batch_at = 0.0

    while not state.shutdown_event.is_set():
        try:
            path = event_queue.get(timeout=0.1)
            pending_files.add(path)
            state.p2_events += 1
            state.p2_last_event = datetime.now().strftime("%H:%M:%S")
        except Empty:
            pass

        now = time.time()
        if pending_files and (now - last_batch_at) >= EVENT_DEBOUNCE_SECONDS:
            state.p2_status = "Processing..."
            changed_files = [Path(path) for path in pending_files]
            filename_list = ", ".join(sorted({Path(path).name for path in pending_files}))
            state.add_log("Process 2", f"Batch change detected: {filename_list}")
            pending_files.clear()
            last_batch_at = now

            with send_lock:
                try:
                    new_orders_sent = check_and_send_open_orders(
                        OUTPUT_FOLDER,
                        LOG_FILE,
                        changed_files=changed_files,
                        update_status=False,
                    )
                    state.p2_orders_sent += new_orders_sent
                    state.add_log("Process 2", f"Sent {new_orders_sent} new orders")
                except Exception as e:
                    state.add_log("Process 2", f"ERROR: {str(e)}")

            state.p2_status = "Watching..."

        if (now - last_status_sync) >= STATUS_SYNC_INTERVAL:
            last_status_sync = now
            with send_lock:
                try:
                    check_and_send_open_orders(OUTPUT_FOLDER, LOG_FILE)
                except Exception as e:
                    state.add_log("Process 2", f"ERROR: {str(e)}")


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
