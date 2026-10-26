import os
import time
import math
import sys
import builtins
import threading
from queue import Queue, Empty
from datetime import datetime, timedelta, timezone
from collections import deque
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from rich.console import Console
from rich.live import Live

from src.config import load_live_config, load_strategy_config
from src.utils.remove_all_files import remove_all_files
from src.utils.getdata.get_data import (fetch_and_save_candles_parallel, append_latest_candle_parallel)
from src.live.dashboard import create_dashboard, update_dashboard
from src.live.core.execute import execute
from src.live.core.approval import ApprovalHub
from src.live.core.discord_notify import send_custom_message
from src.live.core.send import (
    check_and_send_open_orders,
    expire_stale_pending,
    record_signal_fill,
    set_approval_hub,
    set_order_tracker,
    set_snapshot_worker,
)
from src.live.core.order_tracker import OrderTracker, format_order_message, load_tracking_config
from src.live.core.snapshot import SnapshotWorker, load_snapshot_config
from src.live.core.telegram_approval import TelegramChannel, load_approval_config, send_telegram_text
from src.live.web.orders import ManualOrderService, load_web_config
from src.live.web.server import WebApiServer


# =============================================================================
# CONFIG
# =============================================================================


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


def normalize_timeframe_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    normalized = []
    for item in value:
        tf = str(item).strip()
        if tf:
            normalized.append(tf)
    return normalized


# Load config (live setup + shared strategy settings)
CONFIG = load_live_config(include_strategy=False)
STRATEGY_CONFIG = load_strategy_config()

if "exchange" in CONFIG:
    os.environ["EXCHANGE"] = str(CONFIG.get("exchange", "binance")).lower()

exchange_name = os.environ.get("EXCHANGE")

if exchange_name == "bybit":
    bybit_market_category = str(CONFIG.get("data", {}).get("market_category", "linear")).strip().lower()
    os.environ["BYBIT_MARKET_CATEGORY"] = bybit_market_category

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
    RUNTIME_TIMEFRAMES = TIMEFRAMES_CONFIG["value"]
else:
    RUNTIME_TIMEFRAMES = [TIMEFRAMES_CONFIG["value"]]

# Strategy-driven timeframe settings (loaded from strategy.yaml)
SIGNAL_TIMEFRAMES = normalize_timeframe_list(
    STRATEGY_CONFIG.get("signal_timeframe", STRATEGY_CONFIG.get("indicator_timeframe", []))
)
ENTRY_TIMEFRAME = str(STRATEGY_CONFIG.get("entry_timeframe", "")).strip()
USE_STRATEGY_TIMEFRAMES = bool(SIGNAL_TIMEFRAMES and ENTRY_TIMEFRAME)

# In strategy-timeframe mode, run only one scheduler thread on entry timeframe
# to avoid concurrent writes to the same data files.
if USE_STRATEGY_TIMEFRAMES:
    TIMEFRAMES = [ENTRY_TIMEFRAME]
else:
    TIMEFRAMES = RUNTIME_TIMEFRAMES

TIMEFRAME_MINUTES_MAP = {tf: parse_timeframe(tf) for tf in TIMEFRAMES}
FETCH_INTERVAL = int(TIMEFRAMES_CONFIG.get("fetch_interval", 60))  # Default 60 seconds
WAIT_FOR_CLOSE = TIMEFRAMES_CONFIG.get("wait_for_close", False)  # Default: run by fetch interval
CLOSE_DELAY = TIMEFRAMES_CONFIG.get("close_delay", 3)  # Seconds after candle close to wait

if FETCH_INTERVAL <= 0:
    raise ValueError("timeframe.fetch_interval must be > 0")


# Dashboard settings
REFRESH_RATE = CONFIG["dashboard"]["refresh_rate"]
MAX_LOGS = CONFIG["dashboard"]["max_logs"]

RISK_REWARD_RATIO = float(STRATEGY_CONFIG.get("risk_reward_ratio", 2.0) or 2.0)

# Chart snapshot settings (image attached to Discord entry messages)
SNAPSHOT_CONFIG = load_snapshot_config(CONFIG)

# Manual approval (web chart first, Telegram as fallback) before placing orders
APPROVAL_CONFIG = load_approval_config(CONFIG)

# Trading API for the web chart (approve signals, manual orders)
WEB_CONFIG = load_web_config(CONFIG)

# Following approved / web orders on the exchange until they fill or not
TRACKING_CONFIG = load_tracking_config(CONFIG)
DEFAULT_RISK_PER_TRADE = float(STRATEGY_CONFIG.get("risk_per_trade", 5) or 5)


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
          message = str(error_message).strip()[:100]
          with state_lock:
              for existing in list(self.error_logs):
                  if existing.get("timeframe") == timeframe and existing.get("message") == message:
                      existing["time"] = timestamp
                      existing["count"] = int(existing.get("count", 1)) + 1
                      self.error_logs.remove(existing)
                      self.error_logs.appendleft(existing)
                      return

              self.error_logs.appendleft({
                  "time": timestamp,
                  "timeframe": timeframe,
                  "message": message,
                  "count": 1,
              })


state = AppState()
console = Console()


_ORIGINAL_PRINT = builtins.print
_PRINT_CAPTURE_ENABLED = False
_PRINT_ERROR_KEYWORDS = (
    "typingerror",
    "loweringerror",
    "failed in nopython mode",
    "exception:",
    "error:",
    "runtimeerror",
    "valueerror",
    "keyerror",
    "indexerror",
    "attributeerror",
    "typeerror",
    "assertionerror",
    "zerodivisionerror",
)
_NUMBA_IR_KEYWORDS = (
    "$",
    "label ",
    "const(",
    "cast(",
    "return_value",
    "return_const",
    "binop_",
    "compare_op",
    "arg(",
    "global(",
    "name=",
)


def _is_truthy_env_value(value: str) -> bool:
    return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _collect_numba_debug_env_vars() -> dict[str, str]:
    debug_tokens = ("DEBUG", "DUMP", "TRACE", "DEVELOPER", "FULL_TRACEBACK")
    debug_vars: dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = key.upper()
        if not upper_key.startswith("NUMBA_"):
            continue
        if not any(token in upper_key for token in debug_tokens):
            continue
        text_value = str(value)
        if _is_truthy_env_value(text_value):
            debug_vars[key] = text_value
    return dict(sorted(debug_vars.items()))


def _is_numba_diagnostic_line(stripped: str) -> bool:
    lowered = stripped.lower()

    if any(keyword in stripped for keyword in _NUMBA_IR_KEYWORDS):
        return True
    if lowered.startswith("#"):
        return True
    if lowered.startswith("traceback (most recent call last):"):
        return True
    if lowered.startswith("file \"") and ", line " in lowered:
        return True
    if lowered.startswith("during:"):
        return True
    if lowered.startswith("^"):
        return True
    if lowered.startswith("return ") and "data_closes[" in lowered:
        return True
    if lowered.startswith("del "):
        return True
    return False


def _summarize_print_error(stripped: str) -> str:
    lowered = stripped.lower()
    if "typingerror" in lowered:
        return "Numba TypingError detected"
    if "loweringerror" in lowered:
        return "Numba LoweringError detected"
    if "failed in nopython mode" in lowered:
        return "Numba failed in nopython mode"
    return stripped


def _classify_print_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if _is_numba_diagnostic_line(stripped):
        return None

    lowered = stripped.lower()
    if ":" in lowered:
        prefix = lowered.split(":", 1)[0].strip()
        if prefix.endswith("error") or prefix.endswith("exception"):
            return _summarize_print_error(stripped)
    if any(keyword in lowered for keyword in _PRINT_ERROR_KEYWORDS):
        return _summarize_print_error(stripped)
    return None


def _log_numba_env_warnings() -> None:
    debug_vars = _collect_numba_debug_env_vars()
    if not debug_vars:
        return
    joined = ", ".join(f"{key}={value}" for key, value in debug_vars.items())
    state.add_log("Main", f"Warning: active Numba debug env -> {joined}")


def _capture_print_to_errors(*args, **kwargs):
    """Capture all runtime print() calls into dashboard error logs."""
    file_obj = kwargs.get("file", None)
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")

    if sep is None:
        sep = " "
    if end is None:
        end = "\n"

    message = sep.join(str(arg) for arg in args)
    if end:
        message += end

    for line in message.splitlines():
        error_message = _classify_print_line(line)
        if error_message:
            state.add_error("PRT", error_message)

    # Preserve explicit output to custom file handles.
    if file_obj not in (None, sys.stdout, sys.stderr):
        _ORIGINAL_PRINT(*args, **kwargs)


def install_print_capture():
    global _PRINT_CAPTURE_ENABLED
    if _PRINT_CAPTURE_ENABLED:
        return
    builtins.print = _capture_print_to_errors
    _PRINT_CAPTURE_ENABLED = True


def restore_print_capture():
    global _PRINT_CAPTURE_ENABLED
    if not _PRINT_CAPTURE_ENABLED:
        return
    builtins.print = _ORIGINAL_PRINT
    _PRINT_CAPTURE_ENABLED = False

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
      """Fetch data from API for a specific timeframe
      
      In multi-TF mode, fetches entry_timeframe data (e.g., 1m) regardless of
      the timeframe parameter, since signal_timeframe is computed via conversion.
      """
      if not os.path.exists(DATA_FOLDER):
          os.makedirs(DATA_FOLDER)
      
      # In strategy-timeframe mode, always fetch entry_timeframe data
      fetch_tf = ENTRY_TIMEFRAME if USE_STRATEGY_TIMEFRAMES else timeframe
      use_closed_candles = WAIT_FOR_CLOSE and os.environ.get("EXCHANGE") in {"binance", "bybit", "lighter"}
      fetch_kwargs = {"closed_only": True} if use_closed_candles else {}
      
      # Check if data files exist (first run vs subsequent runs)
      has_data_files = any(
          os.path.isfile(os.path.join(DATA_FOLDER, f"{curr}_{fetch_tf}.csv"))
          for curr in CURRENCIES
      )
      
      if not has_data_files:
          # First run: remove old data and fetch all candles (parallel)
          fetch_and_save_candles_parallel(
              CURRENCIES,
              [fetch_tf],
              DATA_FOLDER,
              CANDLES_LIMIT,
              **fetch_kwargs,
          )
      else:
          # Subsequent runs: only append latest candle and remove second row (parallel)
          append_latest_candle_parallel(
              CURRENCIES,
              [fetch_tf],
              DATA_FOLDER,
              **fetch_kwargs,
          )


def run_cycle(timeframe: str):
     """Run one cycle of data fetch and execute for a specific timeframe"""
     tf_state = state.timeframe_states[timeframe]
     try:
         tf_state.status = "Fetching data..."
         state.add_log(f"TF[{timeframe}]", f"Fetching data...")
         getdata(timeframe)
         
         tf_state.status = "Executing..."
         state.add_log(f"TF[{timeframe}]", "Running execute...")
         
         # Determine timeframe params for execute
         # In strategy-timeframe mode: pass signal_timeframes and entry_timeframe
         # In single-TF mode: pass timeframe as before
         exec_timeframe = "" if USE_STRATEGY_TIMEFRAMES else timeframe
         exec_signal_tf = SIGNAL_TIMEFRAMES if USE_STRATEGY_TIMEFRAMES else None
         exec_entry_tf = ENTRY_TIMEFRAME if USE_STRATEGY_TIMEFRAMES else ""
         
         execute(
             DATA_FOLDER, OUTPUT_FOLDER, exec_timeframe,
             signal_timeframe=exec_signal_tf,
             entry_timeframe=exec_entry_tf,
             strategy_config=STRATEGY_CONFIG,
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
      - wait_for_close=True: Waits until the current candle closes,
        then fetches data and runs strategy exactly once per candle. This prevents
        repainting by ensuring the strategy only sees completed candles.
      - wait_for_close=False (default): Runs every FETCH_INTERVAL seconds.
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

def start_snapshot_worker() -> SnapshotWorker | None:
    """Start the chart snapshot worker; on failure keep sending text-only messages."""
    if not SNAPSHOT_CONFIG.get("enabled"):
        return None
    try:
        worker = SnapshotWorker(
            SNAPSHOT_CONFIG,
            data_folder=DATA_FOLDER,
            entry_timeframe=ENTRY_TIMEFRAME or (TIMEFRAMES[0] if TIMEFRAMES else "1m"),
            strategy_config=STRATEGY_CONFIG,
            on_error=lambda msg: state.add_error("SNAP", msg),
        ).start()
    except Exception as e:
        state.add_error("SNAP", f"Snapshot disabled: {e}")
        return None
    set_snapshot_worker(worker)
    state.add_log("Main", "Chart snapshots enabled")
    return worker


class TradingServices:
    """Approval hub, its Telegram channel, the order tracker and the web trading API (each optional)."""

    # Order tracker events announced on Telegram + Discord
    ANNOUNCED_CHANGES = ("partial", "filled", "not_filled", "cancelled", "unknown")

    def __init__(self):
        self.hub: ApprovalHub | None = None
        self.telegram: TelegramChannel | None = None
        self.tracker: OrderTracker | None = None
        self.web: WebApiServer | None = None

    def notify_text(self, text: str) -> None:
        """Send a message to Telegram + Discord (web orders, fills; also an alert if someone else uses the web)."""
        if self.telegram is not None:
            self.telegram.notify(text)
        else:
            send_telegram_text(os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), text)
        send_custom_message(f"```\n{text}\n```")

    def on_order_change(self, change: str, order) -> None:
        """Order tracker listener: save the fill result of bot signals and announce it."""
        if change not in self.ANNOUNCED_CHANGES:
            return
        if order.source == "signal":
            record_signal_fill(LOG_FILE, order)
        self.notify_text(format_order_message(order, change))

    def start(self) -> "TradingServices":
        """Raises when approval is on but no channel can show signals (never auto-trade instead)."""
        expired = expire_stale_pending(LOG_FILE)
        if expired:
            state.add_log("Approval", f"Marked {expired} pending signal(s) from the last run as EXPIRED")

        if APPROVAL_CONFIG.get("enabled"):
            self.hub = ApprovalHub(
                timeout_minutes=float(APPROVAL_CONFIG.get("timeout_minutes", 5)),
                on_log=lambda msg: state.add_log("Approval", msg),
                on_error=lambda msg: state.add_error("APR", msg),
            ).start()
            if APPROVAL_CONFIG.get("telegram", True):
                try:
                    self.telegram = TelegramChannel(
                        token=os.environ.get("TELEGRAM_TOKEN", ""),
                        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
                        hub=self.hub,
                        allowed_user_ids=APPROVAL_CONFIG.get("allowed_user_ids") or [],
                        on_log=lambda msg: state.add_log("Telegram", msg),
                        on_error=lambda msg: state.add_error("TG", msg),
                    ).start()
                except Exception as e:
                    state.add_error("TG", f"Telegram disabled: {e}")

        if self.hub is not None or WEB_CONFIG.get("enabled"):
            self.tracker = OrderTracker(
                file=str(TRACKING_CONFIG.get("file")),
                poll_seconds=float(TRACKING_CONFIG.get("poll_seconds", 5)),
                limit_timeout_minutes=float(TRACKING_CONFIG.get("limit_timeout_minutes", 30)),
                cancel_if_target_hit=bool(TRACKING_CONFIG.get("cancel_if_target_hit", True)),
                on_log=lambda msg: state.add_log("Orders", msg),
                on_error=lambda msg: state.add_error("ORD", msg),
            )
            self.tracker.add_listener(self.on_order_change)
            self.tracker.start()
            set_order_tracker(self.tracker)

        if WEB_CONFIG.get("enabled"):
            try:
                max_risk = float(WEB_CONFIG.get("max_risk_per_trade") or 0) or DEFAULT_RISK_PER_TRADE * 2
                orders = ManualOrderService(
                    symbols=CURRENCIES,
                    default_risk=DEFAULT_RISK_PER_TRADE,
                    max_risk=max_risk,
                    leverage=int(WEB_CONFIG.get("leverage", 20)),
                    cooldown_seconds=float(WEB_CONFIG.get("order_cooldown_seconds", 3)),
                    log_file=str(WEB_CONFIG.get("manual_orders_file")),
                    notify=self.notify_text,
                    tracker=self.tracker,
                )
                self.web = WebApiServer(
                    host=str(WEB_CONFIG.get("host", "127.0.0.1")),
                    port=int(WEB_CONFIG.get("port", 8787)),
                    token=os.environ.get("WEB_API_TOKEN", ""),
                    orders=orders,
                    tracker=self.tracker,
                    hub=self.hub,
                    on_log=lambda msg: state.add_log("Web", msg),
                    on_error=lambda msg: state.add_error("WEB", msg),
                )
                self.web.start()
            except Exception as e:
                self.web = None
                state.add_error("WEB", f"Web API disabled: {e}")

        if self.hub is not None and self.telegram is None and self.web is None:
            self.stop()
            raise RuntimeError("approval.enabled is true but neither the web API nor Telegram could start")

        set_approval_hub(self.hub)
        if self.hub is not None:
            channels = " + ".join(n for n, c in (("Web", self.web), ("Telegram", self.telegram)) if c is not None)
            state.add_log("Main", f"Manual approval on {channels} ({self.hub.timeout_minutes:g} min timeout)")
        return self

    def stop(self) -> None:
        set_approval_hub(None)
        set_order_tracker(None)
        # Hub first: pending signals expire and the channels can still show it.
        if self.hub is not None:
            self.hub.stop()
        if self.tracker is not None:
            self.tracker.stop()  # orders left open are followed again on the next start
        if self.telegram is not None:
            self.telegram.stop()
        if self.web is not None:
            self.web.stop()


def main():
    try:
        services = TradingServices().start()
    except Exception as e:
        console.print(f"[bold red]{e}[/bold red]")
        console.print("Check TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / WEB_API_TOKEN in .env and the web/approval sections of live.yaml.")
        sys.exit(1)

    install_print_capture()
    _log_numba_env_warnings()

    remove_all_files(OUTPUT_FOLDER)
    remove_all_files(DATA_FOLDER)

    snapshot_worker = start_snapshot_worker()

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
    if USE_STRATEGY_TIMEFRAMES:
        signal_label = ",".join(SIGNAL_TIMEFRAMES)
        state.add_log("Main", f"Application started (Strategy TF: signal=[{signal_label}], entry={ENTRY_TIMEFRAME})")
    else:
        state.add_log("Main", f"Application started (TF: {timeframes_str})")

    # Create dashboard
    layout = create_dashboard()

    try:
        with Live(layout, refresh_per_second=REFRESH_RATE, screen=True):
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
    finally:
        if snapshot_worker is not None:
            set_snapshot_worker(None)
            snapshot_worker.stop()
        services.stop()
        restore_print_capture()


if __name__ == "__main__":
    main()
