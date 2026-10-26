"""Standalone Bybit live order manager.

Runs once per minute on closed 1m candles, reads only exchange state,
and applies the same extra exit rule as the backtest order manager:

- R1 sweep exit

This tool manages active Bybit positions only. It does not read local
sent-order logs and it does not integrate into the live dashboard.

Usage:
    python -m src.exchanges.bybit.tools.live_order_manager

Examples:
    python -m src.exchanges.bybit.tools.live_order_manager --once --dry-run
    python -m src.exchanges.bybit.tools.live_order_manager --second-offset 2
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import load_live_config
from src.exchanges.bybit.exchange import format_symbol, get_exchange
from src.manager_order.logic import CandleData, ManagerConfig, replay_trade


MANAGED_EXIT_REASONS = {"R1_SWEEP"}
STATE_SCHEMA_VERSION = 2
DEFAULT_SECOND_OFFSET = 2
ONE_MINUTE_MS = 60_000


@dataclass(frozen=True)
class PositionSnapshot:
    key: str
    symbol: str
    trade_type: str
    close_side: str
    position_idx: int
    size: float
    entry_price: float
    mark_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    created_time: Optional[pd.Timestamp]
    updated_time: Optional[pd.Timestamp]


@dataclass(frozen=True)
class ManagedPosition:
    snapshot: PositionSnapshot
    anchor_time: pd.Timestamp
    reference_entry_price: float
    reset_reason: Optional[str]


@dataclass
class PositionView:
    key: str
    symbol: str
    side: str
    size: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    anchor_time: str
    last_reason: str = "PENDING"
    note: str = ""
    exit_price: Optional[float] = None
    one_r_reached: bool = False


@dataclass
class RuntimeViewState:
    dry_run: bool
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    next_run_at: Optional[datetime] = None
    status: str = "Starting"
    last_cycle_time: Optional[str] = None
    cycle_summary: dict[str, Any] = field(
        default_factory=lambda: {
            "positions": 0,
            "evaluated": 0,
            "triggered": 0,
            "closed": 0,
            "skipped": 0,
        }
    )
    positions: dict[str, PositionView] = field(default_factory=dict)
    recent_events: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=14))
    run_count: int = 0
    error_count: int = 0


class SingleInstanceLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.handle: Optional[Any] = None

    def __enter__(self) -> "SingleInstanceLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another live order manager instance is already running: {self.lock_path}") from exc

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def parse_args() -> argparse.Namespace:
    live_cfg = load_live_config(include_strategy=False)
    output_folder = Path(str(live_cfg.get("output", {}).get("folder", "res/logs/output")))
    log_root = output_folder.parent if output_folder.parent != Path("") else Path("res/logs")

    parser = argparse.ArgumentParser(description="Standalone Bybit live order manager")
    parser.add_argument("--once", action="store_true", help="Run one management cycle immediately and exit")
    parser.add_argument("--dry-run", action="store_true", help="Detect manager exits but do not cancel or close positions")
    parser.add_argument(
        "--second-offset",
        type=int,
        default=DEFAULT_SECOND_OFFSET,
        help="Run each minute at this second offset (default: 2)",
    )
    parser.add_argument(
        "--state-file",
        default=str(log_root / "bybit_live_order_manager_state.json"),
        help="Persistent state file for position anchors",
    )
    parser.add_argument(
        "--log-file",
        default=str(log_root / "bybit_live_order_manager.jsonl"),
        help="JSONL event log file",
    )
    parser.add_argument(
        "--positions-file",
        default=str(log_root / "bybit_live_order_manager_positions.csv"),
        help="Snapshot CSV for currently managed open positions",
    )
    parser.add_argument(
        "--lock-file",
        default=str(log_root / "bybit_live_order_manager.lock"),
        help="Single-instance lock file",
    )
    parser.add_argument(
        "--limit-per-request",
        type=int,
        default=1000,
        help="Max candles per fetch_ohlcv request",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=10,
        help="Max paginated fetch_ohlcv batches per symbol",
    )
    parser.add_argument("--one-r-trigger", type=float, default=1.0, help="R threshold to activate sweep rule")
    return parser.parse_args()


def build_manager_config(args: argparse.Namespace) -> ManagerConfig:
    return ManagerConfig(
        one_r_trigger=float(args.one_r_trigger),
        sweep_exit_price_mode="next_open",
    )


def parse_optional_float(value: Any) -> Optional[float]:
    if value in (None, "", "0", "0.0"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or abs(parsed) <= 1e-12:
        return None
    return parsed


def parse_exchange_timestamp_ms(value: Any) -> Optional[pd.Timestamp]:
    if value in (None, "", 0, "0"):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    dt = datetime.fromtimestamp(parsed / 1000, tz=timezone.utc).replace(tzinfo=None)
    return pd.Timestamp(dt)


def floor_to_minute(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)).floor("min")
    py_dt = ts.to_pydatetime()
    if py_dt.tzinfo is not None:
        py_dt = py_dt.astimezone(timezone.utc).replace(tzinfo=None)
    return pd.Timestamp(py_dt).floor("min")


def utc_now_naive() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))


def timestamp_to_string(value: pd.Timestamp) -> str:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return ""
    return ts.to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")


def string_to_timestamp(value: Any) -> Optional[pd.Timestamp]:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    if isinstance(ts, pd.Timestamp):
        py_dt = ts.to_pydatetime()
        if py_dt.tzinfo is not None:
            py_dt = py_dt.astimezone(timezone.utc).replace(tzinfo=None)
        return pd.Timestamp(py_dt)
    return None


def state_template() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "positions": {},
        "last_updated": None,
    }


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return state_template()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return state_template()
    if not isinstance(data, dict):
        return state_template()
    if int(data.get("schema_version", 0) or 0) != STATE_SCHEMA_VERSION:
        return state_template()
    positions = data.get("positions")
    if not isinstance(positions, dict):
        data["positions"] = {}
    data.setdefault("schema_version", STATE_SCHEMA_VERSION)
    data.setdefault("last_updated", None)
    return data


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = timestamp_to_string(utc_now_naive())
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")


def append_jsonl(log_path: Path, event: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("logged_at", timestamp_to_string(utc_now_naive()))
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def push_runtime_event(runtime_state: Optional[RuntimeViewState], event: str, message: str) -> None:
    if runtime_state is None:
        return
    runtime_state.recent_events.appendleft(
        {
            "time": runtime_state.current_time.strftime("%H:%M:%S"),
            "event": event,
            "message": message,
        }
    )


def format_number(value: Optional[float], *, quantity: bool = False) -> str:
    if value is None:
        return "-"
    magnitude = abs(float(value))
    if quantity:
        if magnitude >= 100:
            precision = 2
        elif magnitude >= 1:
            precision = 4
        else:
            precision = 6
    else:
        if magnitude >= 100:
            precision = 2
        elif magnitude >= 1:
            precision = 4
        else:
            precision = 6
    return f"{float(value):,.{precision}f}"


def format_runtime(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_countdown(value: Optional[datetime], now: datetime) -> str:
    if value is None:
        return "-"
    remaining = int(max(0, (value - now).total_seconds()))
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def status_style(status: str) -> str:
    if status.startswith("CLOSED"):
        return "bold green"
    if status.startswith("DRY"):
        return "bold cyan"
    if status == "R1_SWEEP":
        return "bold cyan"
    if status in {"TP_SEEN", "SL_SEEN"}:
        return "bold yellow"
    if status.startswith("SKIPPED"):
        return "yellow"
    if status == "NO_TRIGGER":
        return "dim"
    if status == "PENDING":
        return "blue"
    return "white"


def build_header_panel(runtime_state: RuntimeViewState, cfg: ManagerConfig) -> Panel:
    table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
    table.add_column("Label", style="bold cyan", width=14)
    table.add_column("Value", style="white")
    table.add_column("Label2", style="bold cyan", width=14)
    table.add_column("Value2", style="white")

    table.add_row("Status", runtime_state.status, "Mode", "DRY RUN" if runtime_state.dry_run else "LIVE")
    table.add_row("UTC Now", format_runtime(runtime_state.current_time), "Next Run", format_runtime(runtime_state.next_run_at))
    table.add_row(
        "Countdown",
        format_countdown(runtime_state.next_run_at, runtime_state.current_time),
        "Last Cycle",
        runtime_state.last_cycle_time or "-",
    )
    table.add_row(
        "Runs / Errors",
        f"{runtime_state.run_count} / {runtime_state.error_count}",
        "Rules",
        f"1R={cfg.one_r_trigger} | fill={cfg.sweep_exit_price_mode}",
    )
    return Panel(table, title="[bold cyan]Bybit Live Order Manager[/bold cyan]", border_style="cyan")


def build_summary_panel(runtime_state: RuntimeViewState) -> Panel:
    summary = runtime_state.cycle_summary
    table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
    table.add_column("Metric", style="bold magenta")
    table.add_column("Value", justify="right", style="white")
    table.add_row("Open Positions", str(summary.get("positions", 0)))
    table.add_row("Evaluated", str(summary.get("evaluated", 0)))
    table.add_row("Triggered", str(summary.get("triggered", 0)))
    table.add_row("Closed", str(summary.get("closed", 0)))
    table.add_row("Skipped", str(summary.get("skipped", 0)))
    uptime_seconds = int((runtime_state.current_time - runtime_state.started_at).total_seconds())
    hours, remainder = divmod(max(0, uptime_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    table.add_row("Uptime", f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    return Panel(table, title="[bold magenta]Cycle Summary[/bold magenta]", border_style="magenta")


def build_positions_panel(runtime_state: RuntimeViewState) -> Panel:
    table = Table(show_header=True, box=None, expand=True, padding=(0, 1))
    table.add_column("Symbol", style="cyan", width=14)
    table.add_column("Side", width=6)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Entry", justify="right", width=12)
    table.add_column("SL", justify="right", width=12)
    table.add_column("TP", justify="right", width=12)
    table.add_column("Anchor", width=8)
    table.add_column("Decision", width=12)
    table.add_column("Note", overflow="fold")

    if not runtime_state.positions:
        table.add_row("No active positions", "", "", "", "", "", "", "", "")
    else:
        for view in sorted(runtime_state.positions.values(), key=lambda item: item.symbol):
            side_style = "green" if view.side == "LONG" else "red"
            note = view.note
            if view.exit_price is not None:
                exit_info = f"exit {format_number(view.exit_price)}"
                note = f"{note} | {exit_info}" if note else exit_info
            table.add_row(
                view.symbol,
                Text(view.side, style=side_style),
                format_number(view.size, quantity=True),
                format_number(view.entry_price),
                format_number(view.stop_loss),
                format_number(view.take_profit),
                view.anchor_time[-8:] if view.anchor_time else "-",
                Text(view.last_reason, style=status_style(view.last_reason)),
                note or "-",
            )

    return Panel(table, title="[bold yellow]Managed Positions[/bold yellow]", border_style="yellow")


def build_events_panel(runtime_state: RuntimeViewState) -> Panel:
    table = Table(show_header=True, box=None, expand=True, padding=(0, 1))
    table.add_column("Time", style="dim", width=8)
    table.add_column("Event", style="bold green", width=18)
    table.add_column("Detail", overflow="fold")

    if not runtime_state.recent_events:
        table.add_row("-", "WAITING", "No events yet")
    else:
        for event in list(runtime_state.recent_events):
            table.add_row(event["time"], event["event"], event["message"])

    return Panel(table, title="[bold green]Recent Events[/bold green]", border_style="green")


def render_dashboard(runtime_state: RuntimeViewState, cfg: ManagerConfig) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(build_header_panel(runtime_state, cfg), size=8),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=3),
    )
    layout["left"].split_column(
        Layout(build_summary_panel(runtime_state), size=10),
        Layout(build_events_panel(runtime_state)),
    )
    layout["right"].update(build_positions_panel(runtime_state))
    return layout


def is_close(a: float, b: float, rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> bool:
    return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)


def extract_active_positions(exchange) -> list[PositionSnapshot]:
    raw_positions = exchange.fetch_positions()
    snapshots: list[PositionSnapshot] = []

    for position in raw_positions:
        try:
            size = abs(float(position.get("contracts", 0) or 0))
        except (TypeError, ValueError):
            continue

        if size <= 0:
            continue

        info = position.get("info", {}) or {}
        symbol = position.get("symbol") or ""
        if not symbol:
            continue

        side = str(position.get("side", "")).strip().upper()
        if side not in {"LONG", "SHORT"}:
            api_side = str(info.get("side", "")).strip().upper()
            if api_side == "BUY":
                side = "LONG"
            elif api_side == "SELL":
                side = "SHORT"

        if side not in {"LONG", "SHORT"}:
            continue

        entry_price = parse_optional_float(info.get("avgPrice"))
        if entry_price is None:
            entry_price = parse_optional_float(position.get("entryPrice"))
        if entry_price is None:
            continue

        position_idx_raw = info.get("positionIdx", 0)
        try:
            position_idx = int(position_idx_raw or 0)
        except (TypeError, ValueError):
            position_idx = 0

        mark_price = parse_optional_float(position.get("markPrice")) or 0.0
        stop_loss = parse_optional_float(info.get("stopLoss"))
        take_profit = parse_optional_float(info.get("takeProfit"))
        created_time = parse_exchange_timestamp_ms(info.get("createdTime"))
        updated_time = parse_exchange_timestamp_ms(info.get("updatedTime"))
        close_side = "sell" if side == "LONG" else "buy"
        key = f"{symbol}|{position_idx}" if position_idx else f"{symbol}|{side}"

        snapshots.append(
            PositionSnapshot(
                key=key,
                symbol=symbol,
                trade_type=side,
                close_side=close_side,
                position_idx=position_idx,
                size=size,
                entry_price=entry_price,
                mark_price=mark_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                created_time=created_time,
                updated_time=updated_time,
            )
        )

    snapshots.sort(key=lambda item: item.key)
    return snapshots


def extract_order_id(order: dict[str, Any]) -> Optional[str]:
    if not isinstance(order, dict):
        return None
    info = order.get("info", {}) or {}
    order_id = order.get("id") or info.get("orderId")
    return str(order_id) if order_id else None


def parse_trigger_price(order: dict[str, Any]) -> Optional[float]:
    info = order.get("info", {}) or {}
    candidates = [
        order.get("price"),
        order.get("triggerPrice"),
        order.get("stopPrice"),
        info.get("triggerPrice"),
        info.get("stopPrice"),
        info.get("trigger_price"),
        info.get("stop_price"),
    ]
    for candidate in candidates:
        parsed = parse_optional_float(candidate)
        if parsed is not None:
            return parsed
    return None


def order_matches_position(position: PositionSnapshot, order: dict[str, Any]) -> bool:
    if str(order.get("symbol") or "") != position.symbol:
        return False

    info = order.get("info", {}) or {}
    stop_type = str(info.get("stopOrderType", "")).strip()
    reduce_only = str(info.get("reduceOnly", "")).lower() == "true" or bool(order.get("reduceOnly"))
    if stop_type not in {"TakeProfit", "StopLoss"} and not reduce_only:
        return False

    order_position_idx = info.get("positionIdx")
    try:
        order_position_idx_int = int(order_position_idx or 0)
    except (TypeError, ValueError):
        order_position_idx_int = 0

    if position.position_idx in {1, 2} and order_position_idx_int not in {0, position.position_idx}:
        return False

    expected_side = "SELL" if position.trade_type == "LONG" else "BUY"
    order_side = str(order.get("side", "")).strip().upper()
    if order_side and order_side != expected_side:
        return False

    return True


def enrich_positions_with_orders(
    positions: Iterable[PositionSnapshot],
    open_orders: Iterable[dict[str, Any]],
) -> list[PositionSnapshot]:
    orders = list(open_orders)
    enriched: list[PositionSnapshot] = []

    for position in positions:
        stop_loss = position.stop_loss
        take_profit = position.take_profit

        for order in orders:
            if not order_matches_position(position, order):
                continue
            info = order.get("info", {}) or {}
            stop_type = str(info.get("stopOrderType", "")).strip()
            trigger_price = parse_trigger_price(order)
            if trigger_price is None:
                continue
            if stop_type == "StopLoss" and stop_loss is None:
                stop_loss = trigger_price
            elif stop_type == "TakeProfit" and take_profit is None:
                take_profit = trigger_price

        enriched.append(
            PositionSnapshot(
                key=position.key,
                symbol=position.symbol,
                trade_type=position.trade_type,
                close_side=position.close_side,
                position_idx=position.position_idx,
                size=position.size,
                entry_price=position.entry_price,
                mark_price=position.mark_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                created_time=position.created_time,
                updated_time=position.updated_time,
            )
        )

    return enriched


def sync_position_state(
    positions: Iterable[PositionSnapshot],
    state: dict[str, Any],
    cycle_time: pd.Timestamp,
) -> tuple[list[ManagedPosition], list[dict[str, Any]], list[str]]:
    state_positions = state.setdefault("positions", {})
    active_keys: set[str] = set()
    managed_positions: list[ManagedPosition] = []
    resets: list[dict[str, Any]] = []
    cycle_floor = floor_to_minute(cycle_time)

    for position in positions:
        active_keys.add(position.key)
        anchor_source = position.created_time or position.updated_time or cycle_time
        if anchor_source > cycle_floor:
            anchor_source = cycle_floor

        stored = state_positions.get(position.key)
        reset_reason: Optional[str] = None

        if not isinstance(stored, dict):
            reset_reason = "NEW_POSITION"
            anchor_time = anchor_source
            reference_entry_price = position.entry_price
        else:
            previous_size = parse_optional_float(stored.get("reference_size")) or 0.0
            previous_entry = parse_optional_float(stored.get("reference_entry_price")) or position.entry_price
            stored_anchor = string_to_timestamp(stored.get("anchor_time"))

            if not stored_anchor:
                reset_reason = "STATE_REPAIRED"
                anchor_time = anchor_source
                reference_entry_price = position.entry_price
            elif not is_close(previous_size, position.size) or not is_close(previous_entry, position.entry_price):
                reset_reason = "POSITION_SCALED"
                anchor_time = anchor_source
                reference_entry_price = position.entry_price
            else:
                anchor_time = stored_anchor
                if anchor_time > cycle_floor:
                    anchor_time = cycle_floor
                reference_entry_price = previous_entry

        state_positions[position.key] = {
            "symbol": position.symbol,
            "side": position.trade_type,
            "position_idx": position.position_idx,
            "anchor_time": timestamp_to_string(anchor_time),
            "reference_entry_price": reference_entry_price,
            "reference_size": position.size,
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "last_seen_at": timestamp_to_string(cycle_time),
        }

        managed_positions.append(
            ManagedPosition(
                snapshot=position,
                anchor_time=anchor_time,
                reference_entry_price=reference_entry_price,
                reset_reason=reset_reason,
            )
        )

        if reset_reason:
            resets.append(
                {
                    "key": position.key,
                    "symbol": position.symbol,
                    "side": position.trade_type,
                    "reason": reset_reason,
                    "anchor_time": timestamp_to_string(anchor_time),
                    "entry_price": reference_entry_price,
                    "size": position.size,
                    "position_idx": position.position_idx,
                }
            )

    stale_keys = sorted(set(state_positions.keys()) - active_keys)
    for key in stale_keys:
        state_positions.pop(key, None)

    return managed_positions, resets, stale_keys


def to_millis(ts: pd.Timestamp) -> int:
    timestamp = pd.Timestamp(ts)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.timestamp() * 1000)


def build_candle_data(rows: list[list[Any]]) -> CandleData:
    if not rows:
        return CandleData(
            times_ns=np.array([], dtype=np.int64),
            opens=np.array([], dtype=np.float64),
            highs=np.array([], dtype=np.float64),
            lows=np.array([], dtype=np.float64),
            closes=np.array([], dtype=np.float64),
        )

    rows = sorted(rows, key=lambda item: int(item[0]))
    times = pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True).tz_localize(None)
    opens = np.array([float(row[1]) for row in rows], dtype=np.float64)
    highs = np.array([float(row[2]) for row in rows], dtype=np.float64)
    lows = np.array([float(row[3]) for row in rows], dtype=np.float64)
    closes = np.array([float(row[4]) for row in rows], dtype=np.float64)

    return CandleData(
        times_ns=times.astype("int64").to_numpy(),
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
    )


def fetch_closed_1m_candles(
    exchange,
    symbol: str,
    anchor_time: pd.Timestamp,
    cycle_time: pd.Timestamp,
    limit_per_request: int,
    max_batches: int,
) -> CandleData:
    formatted_symbol = format_symbol(symbol)
    start_time = floor_to_minute(anchor_time) - pd.Timedelta(minutes=1)
    cursor_ms = to_millis(start_time)
    current_open_ms = to_millis(floor_to_minute(cycle_time))
    rows_by_timestamp: dict[int, list[Any]] = {}

    for _ in range(max(1, int(max_batches))):
        batch = exchange.fetch_ohlcv(
            formatted_symbol,
            timeframe="1m",
            since=cursor_ms,
            limit=max(1, min(int(limit_per_request), 1000)),
            params={"category": "linear"},
        )

        if not batch:
            break

        last_timestamp = None
        for row in batch:
            timestamp_ms = int(row[0])
            rows_by_timestamp[timestamp_ms] = row[:5]
            last_timestamp = timestamp_ms

        if last_timestamp is None:
            break
        if last_timestamp >= current_open_ms:
            break
        if len(batch) < max(1, min(int(limit_per_request), 1000)):
            break
        if last_timestamp < cursor_ms:
            break
        cursor_ms = last_timestamp + ONE_MINUTE_MS

    closed_rows: list[list[Any]] = []
    cycle_time_aware = cycle_time.tz_localize("UTC") if cycle_time.tzinfo is None else cycle_time.tz_convert("UTC")

    for timestamp_ms in sorted(rows_by_timestamp):
        candle_time = pd.Timestamp(timestamp_ms, unit="ms", tz="UTC")
        if candle_time + pd.Timedelta(minutes=1) > cycle_time_aware:
            continue
        closed_rows.append(rows_by_timestamp[timestamp_ms])

    return build_candle_data(closed_rows)


def build_candle_cache(
    exchange,
    managed_positions: Iterable[ManagedPosition],
    cycle_time: pd.Timestamp,
    limit_per_request: int,
    max_batches: int,
) -> dict[str, CandleData]:
    earliest_anchor_by_symbol: dict[str, pd.Timestamp] = {}
    for managed_position in managed_positions:
        symbol = managed_position.snapshot.symbol
        current_anchor = earliest_anchor_by_symbol.get(symbol)
        if current_anchor is None or managed_position.anchor_time < current_anchor:
            earliest_anchor_by_symbol[symbol] = managed_position.anchor_time

    cache: dict[str, CandleData] = {}
    for symbol, anchor_time in earliest_anchor_by_symbol.items():
        cache[symbol] = fetch_closed_1m_candles(
            exchange=exchange,
            symbol=symbol,
            anchor_time=anchor_time,
            cycle_time=cycle_time,
            limit_per_request=limit_per_request,
            max_batches=max_batches,
        )
    return cache


def build_trade_row(managed_position: ManagedPosition) -> pd.Series:
    snapshot = managed_position.snapshot
    return pd.Series(
        {
            "Symbol": snapshot.symbol,
            "Type": snapshot.trade_type,
            "Entry_Time": timestamp_to_string(managed_position.anchor_time),
            "Entry_Price": managed_position.reference_entry_price,
            "Stop_Loss": snapshot.stop_loss,
            "Take_Profit": snapshot.take_profit,
            "Exit_Time": None,
            "Exit_Price": None,
            "Result": "Open",
        }
    )


def related_open_orders(position: PositionSnapshot, open_orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for order in open_orders:
        if not order_matches_position(position, order):
            continue
        order_id = extract_order_id(order)
        if not order_id or order_id in seen_ids:
            continue
        seen_ids.add(order_id)
        matches.append(order)
    return matches


def cancel_orders_for_position(exchange, position: PositionSnapshot, orders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for order in orders:
        order_id = extract_order_id(order)
        if not order_id:
            continue
        try:
            result = exchange.cancel_order(order_id, position.symbol)
            results.append({"success": True, "order_id": order_id, "result": result})
        except Exception as exc:
            results.append({"success": False, "order_id": order_id, "error": str(exc)})
    return results


def close_position_market(exchange, position: PositionSnapshot) -> dict[str, Any]:
    amount = position.size
    try:
        amount = float(exchange.amount_to_precision(position.symbol, amount))
    except Exception:
        amount = float(position.size)

    params: dict[str, Any] = {"reduceOnly": True}
    if position.position_idx in {0, 1, 2}:
        params["positionIdx"] = position.position_idx

    try:
        order = exchange.create_order(
            symbol=position.symbol,
            type="market",
            side=position.close_side,
            amount=amount,
            params=params,
        )
        return {
            "success": True,
            "order_id": extract_order_id(order),
            "amount": amount,
            "order": order,
        }
    except Exception as exc:
        return {
            "success": False,
            "amount": amount,
            "error": str(exc),
        }


def save_positions_snapshot(
    positions_path: Path,
    cycle_label: str,
    managed_positions: Iterable[ManagedPosition],
    position_views: dict[str, PositionView],
) -> None:
    by_key = {managed.snapshot.key: managed for managed in managed_positions}
    rows: list[dict[str, Any]] = []
    for key in sorted(position_views.keys()):
        view = position_views[key]
        managed = by_key.get(key)
        snapshot = managed.snapshot if managed is not None else None
        rows.append(
            {
                "cycle_time": cycle_label,
                "key": key,
                "symbol": view.symbol,
                "side": view.side,
                "size": view.size,
                "entry_price": view.entry_price,
                "stop_loss": view.stop_loss,
                "take_profit": view.take_profit,
                "anchor_time": view.anchor_time,
                "one_r_reached": bool(view.one_r_reached),
                "last_reason": view.last_reason,
                "managed_exit_price": view.exit_price,
                "note": view.note,
                "position_idx": snapshot.position_idx if snapshot is not None else None,
                "mark_price": snapshot.mark_price if snapshot is not None else None,
                "created_time": timestamp_to_string(snapshot.created_time) if snapshot is not None and snapshot.created_time is not None else "",
                "updated_time": timestamp_to_string(snapshot.updated_time) if snapshot is not None and snapshot.updated_time is not None else "",
            }
        )

    positions_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(positions_path, index=False)


def next_run_at(second_offset: int) -> datetime:
    now = datetime.now(timezone.utc)
    target = now.replace(second=0, microsecond=0) + timedelta(seconds=max(0, second_offset))
    if now >= target:
        target += timedelta(minutes=1)
    return target


def sleep_until(target_time: datetime) -> None:
    while True:
        remaining = (target_time - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def run_cycle(
    exchange,
    cfg: ManagerConfig,
    state: dict[str, Any],
    state_path: Path,
    log_path: Path,
    positions_path: Path,
    dry_run: bool,
    limit_per_request: int,
    max_batches: int,
    runtime_state: Optional[RuntimeViewState] = None,
) -> None:
    cycle_time = utc_now_naive()
    cycle_label = timestamp_to_string(cycle_time)
    if runtime_state is not None:
        runtime_state.current_time = datetime.now(timezone.utc)
        runtime_state.status = "Running cycle"

    raw_positions = extract_active_positions(exchange)
    try:
        open_orders = exchange.fetch_open_orders()
    except Exception as exc:
        open_orders = []
        append_jsonl(
            log_path,
            {
                "event": "OPEN_ORDERS_FETCH_FAILED",
                "cycle_time": cycle_label,
                "error": str(exc),
            },
        )
        push_runtime_event(runtime_state, "FETCH_WARNING", f"Open orders fetch failed: {exc}")

    positions = enrich_positions_with_orders(raw_positions, open_orders)
    managed_positions, resets, stale_keys = sync_position_state(positions, state, cycle_time)
    candle_cache = build_candle_cache(exchange, managed_positions, cycle_time, limit_per_request, max_batches)
    position_views: dict[str, PositionView] = {}

    for managed_position in managed_positions:
        snapshot = managed_position.snapshot
        note = f"reset {managed_position.reset_reason}" if managed_position.reset_reason else ""
        position_views[snapshot.key] = PositionView(
            key=snapshot.key,
            symbol=snapshot.symbol,
            side=snapshot.trade_type,
            size=snapshot.size,
            entry_price=managed_position.reference_entry_price,
            stop_loss=snapshot.stop_loss,
            take_profit=snapshot.take_profit,
            anchor_time=timestamp_to_string(managed_position.anchor_time),
            note=note,
        )

    for reset in resets:
        append_jsonl(log_path, {"event": "POSITION_RESET", "cycle_time": cycle_label, **reset})
        push_runtime_event(
            runtime_state,
            "POSITION_RESET",
            f"{reset['symbol']} {reset['side']} -> {reset['reason']} at {reset['anchor_time']}",
        )

    for stale_key in stale_keys:
        append_jsonl(
            log_path,
            {
                "event": "POSITION_CLEARED",
                "cycle_time": cycle_label,
                "key": stale_key,
            },
        )
        push_runtime_event(runtime_state, "POSITION_CLEARED", stale_key)

    cycle_stats: dict[str, int] = {
        "positions": len(positions),
        "evaluated": 0,
        "triggered": 0,
        "closed": 0,
        "skipped": 0,
    }

    for managed_position in managed_positions:
        snapshot = managed_position.snapshot
        view = position_views[snapshot.key]

        if snapshot.stop_loss is None or snapshot.take_profit is None:
            cycle_stats["skipped"] += 1
            view.last_reason = "SKIPPED"
            view.note = "missing SL or TP on exchange"
            append_jsonl(
                log_path,
                {
                    "event": "POSITION_SKIPPED",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "side": snapshot.trade_type,
                    "reason": "MISSING_SL_OR_TP",
                    "stop_loss": snapshot.stop_loss,
                    "take_profit": snapshot.take_profit,
                },
            )
            continue

        candles = candle_cache.get(snapshot.symbol)
        if candles is None or candles.is_empty():
            cycle_stats["skipped"] += 1
            view.last_reason = "SKIPPED"
            view.note = "no closed 1m candles fetched"
            append_jsonl(
                log_path,
                {
                    "event": "POSITION_SKIPPED",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "side": snapshot.trade_type,
                    "reason": "NO_CANDLES",
                    "anchor_time": timestamp_to_string(managed_position.anchor_time),
                },
            )
            continue

        row = build_trade_row(managed_position)
        managed = replay_trade(row=row, candles=candles, cfg=cfg)
        cycle_stats["evaluated"] += 1
        view.last_reason = managed.reason
        view.exit_price = managed.exit_price
        view.one_r_reached = managed.one_r_reached

        if managed.reason in {"TP", "SL"}:
            view.last_reason = f"{managed.reason}_SEEN"
            view.note = "exchange still shows position open"
            append_jsonl(
                log_path,
                {
                    "event": "POSITION_ANOMALY",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "reason": managed.reason,
                    "managed_exit_time": timestamp_to_string(managed.exit_time) if managed.exit_time is not None else None,
                    "managed_exit_price": managed.exit_price,
                },
            )
            push_runtime_event(
                runtime_state,
                "POSITION_ANOMALY",
                f"{snapshot.symbol} replay saw {managed.reason} but position is still open",
            )
            continue

        if managed.reason == "NO_TRIGGER":
            view.note = "waiting"
        elif managed.reason == "NO_FUTURE_CANDLES":
            view.note = "awaiting next closed candle"

        if managed.reason not in MANAGED_EXIT_REASONS:
            continue

        cycle_stats["triggered"] += 1
        orders_to_cancel = related_open_orders(snapshot, open_orders)
        event_payload = {
            "event": "MANAGER_TRIGGERED",
            "cycle_time": cycle_label,
            "key": snapshot.key,
            "symbol": snapshot.symbol,
            "side": snapshot.trade_type,
            "position_idx": snapshot.position_idx,
            "size": snapshot.size,
            "entry_price": managed_position.reference_entry_price,
            "mark_price": snapshot.mark_price,
            "stop_loss": snapshot.stop_loss,
            "take_profit": snapshot.take_profit,
            "anchor_time": timestamp_to_string(managed_position.anchor_time),
            "managed_reason": managed.reason,
            "managed_exit_time": timestamp_to_string(managed.exit_time) if managed.exit_time is not None else None,
            "managed_exit_price": managed.exit_price,
            "one_r_reached": managed.one_r_reached,
            "related_order_ids": [extract_order_id(order) for order in orders_to_cancel if extract_order_id(order)],
            "dry_run": dry_run,
        }

        if dry_run:
            view.last_reason = f"DRY_{managed.reason}"
            view.note = "would cancel TP/SL and close market"
            append_jsonl(log_path, event_payload)
            push_runtime_event(
                runtime_state,
                "DRY_TRIGGER",
                f"{snapshot.symbol} {managed.reason} -> would close {format_number(snapshot.size, quantity=True)}",
            )
            continue

        cancel_results = cancel_orders_for_position(exchange, snapshot, orders_to_cancel)
        close_result = close_position_market(exchange, snapshot)
        event_payload["cancel_results"] = cancel_results
        event_payload["close_result"] = close_result
        append_jsonl(log_path, event_payload)

        if close_result.get("success"):
            cycle_stats["closed"] += 1
            view.last_reason = f"CLOSED_{managed.reason}"
            view.note = "position closed by manager"
            push_runtime_event(
                runtime_state,
                "POSITION_CLOSED",
                f"{snapshot.symbol} closed by manager ({managed.reason})",
            )
        else:
            view.last_reason = f"FAILED_{managed.reason}"
            view.note = str(close_result.get("error") or "close failed")
            push_runtime_event(
                runtime_state,
                "CLOSE_FAILED",
                f"{snapshot.symbol} close failed: {close_result.get('error')}",
            )

    save_state(state_path, state)
    save_positions_snapshot(
        positions_path=positions_path,
        cycle_label=cycle_label,
        managed_positions=managed_positions,
        position_views=position_views,
    )
    append_jsonl(
        log_path,
        {
            "event": "CYCLE_SUMMARY",
            "cycle_time": cycle_label,
            **cycle_stats,
        },
    )
    if runtime_state is not None:
        runtime_state.current_time = datetime.now(timezone.utc)
        runtime_state.last_cycle_time = cycle_label
        runtime_state.cycle_summary = dict(cycle_stats)
        runtime_state.positions = position_views
        runtime_state.run_count += 1
        runtime_state.status = "Waiting for next minute"

    if runtime_state is None:
        print(
            f"[{cycle_label}] positions={cycle_stats['positions']} "
            f"evaluated={cycle_stats['evaluated']} "
            f"triggered={cycle_stats['triggered']} "
            f"closed={cycle_stats['closed']} "
            f"skipped={cycle_stats['skipped']}"
        )


def main() -> None:
    args = parse_args()
    cfg = build_manager_config(args)
    state_path = Path(args.state_file)
    log_path = Path(args.log_file)
    positions_path = Path(args.positions_file)
    lock_path = Path(args.lock_file)

    exchange = get_exchange()
    state = load_state(state_path)

    print("=" * 72)
    print("BYBIT LIVE ORDER MANAGER")
    print("=" * 72)
    print(f"State file    : {state_path}")
    print(f"Log file      : {log_path}")
    print(f"Positions file: {positions_path}")
    print(f"Dry run       : {bool(args.dry_run)}")
    print(f"Second offset : {int(args.second_offset)}")
    print(f"Rules         : one_r={cfg.one_r_trigger}R, fill={cfg.sweep_exit_price_mode}")

    with SingleInstanceLock(lock_path):
        if args.once:
            run_cycle(
                exchange=exchange,
                cfg=cfg,
                state=state,
                state_path=state_path,
                log_path=log_path,
                positions_path=positions_path,
                dry_run=bool(args.dry_run),
                limit_per_request=int(args.limit_per_request),
                max_batches=int(args.max_batches),
            )
            return

        runtime_state = RuntimeViewState(dry_run=bool(args.dry_run))
        runtime_state.status = "Waiting for next minute"
        push_runtime_event(runtime_state, "STARTED", "Manager started")

        with Live(render_dashboard(runtime_state, cfg), refresh_per_second=4, screen=True) as live:
            while True:
                target = next_run_at(int(args.second_offset))
                runtime_state.next_run_at = target
                runtime_state.status = "Waiting for next minute"

                while True:
                    runtime_state.current_time = datetime.now(timezone.utc)
                    live.update(render_dashboard(runtime_state, cfg))
                    remaining = (target - runtime_state.current_time).total_seconds()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.25))

                runtime_state.current_time = datetime.now(timezone.utc)
                runtime_state.status = "Running cycle"
                live.update(render_dashboard(runtime_state, cfg))

                try:
                    run_cycle(
                        exchange=exchange,
                        cfg=cfg,
                        state=state,
                        state_path=state_path,
                        log_path=log_path,
                        positions_path=positions_path,
                        dry_run=bool(args.dry_run),
                        limit_per_request=int(args.limit_per_request),
                        max_batches=int(args.max_batches),
                        runtime_state=runtime_state,
                    )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    runtime_state.current_time = datetime.now(timezone.utc)
                    runtime_state.error_count += 1
                    runtime_state.status = "Cycle failed"
                    error_payload = {
                        "event": "CYCLE_FAILED",
                        "error": str(exc),
                        "cycle_time": timestamp_to_string(utc_now_naive()),
                    }
                    append_jsonl(log_path, error_payload)
                    push_runtime_event(runtime_state, "CYCLE_FAILED", str(exc))

                runtime_state.current_time = datetime.now(timezone.utc)
                live.update(render_dashboard(runtime_state, cfg))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user")
