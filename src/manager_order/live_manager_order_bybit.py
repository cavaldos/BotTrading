"""Standalone Bybit live order manager.

Continuously reads exchange state and manages open Bybit positions using
exchange-derived R multiples only:

- 1R is fixed from exchange entry price and the original exchange stop loss
- Close as a failsafe when unrealized PnL <= -1R
- Activate trailing when unrealized PnL >= +1R
  - First lock +0.2R profit into stop loss
  - Then trail SL using best market price with a 0.8R gap
  - If trailing is active and PnL falls back to <= 0R, close position immediately
- Ratchet TP in steps as price nears the current exchange TP
  - At 90% of TP distance, move SL to 80% of TP distance
  - Extend TP by 20% of the original TP distance per step

This tool manages active Bybit positions only. It does not read local
sent-order logs or strategy output files to calculate risk.

Usage:
    python -m src.manager_order.live_manager_order_bybit

Examples:
    python -m src.manager_order.live_manager_order_bybit --once --dry-run
    python -m src.manager_order.live_manager_order_bybit --second-offset 2
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
from typing import Any, Dict, Iterable, Optional, cast

import numpy as np
import pandas as pd
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import load_live_config
from src.live.bybit.exchange import format_symbol, get_exchange, set_position_sl_tp
from src.manager_order.logic import CandleData, ManagerConfig, replay_trade


MANAGED_EXIT_REASONS = {"R_LOSS_LIMIT", "TRAIL_ZERO_R_CLOSE"}
STATE_SCHEMA_VERSION = 4
DEFAULT_SECOND_OFFSET = 2
ONE_MINUTE_MS = 60_000
DEFAULT_LOSS_LIMIT_R = 1.0
DEFAULT_TRAIL_ACTIVATE_R = 1.0
DEFAULT_TRAIL_LOCK_R = 0.2
DEFAULT_TRAIL_GAP_R = 0.8
DEFAULT_TP_RATCHET_TRIGGER_PCT = 0.90
DEFAULT_TP_RATCHET_LOCK_PCT = 0.80
DEFAULT_TP_RATCHET_EXTEND_PCT = 0.20
DEFAULT_TP_RATCHET_MAX_STEPS = 3
DEFAULT_REPAIR_MISSING_PROTECTION = True
DEFAULT_REPAIR_LOCK_ENTRY_ON_PROFIT = True


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
    unrealized_pnl: Optional[float]
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
    original_stop_loss: Optional[float] = None
    original_take_profit: Optional[float] = None
    risk_distance: Optional[float] = None
    trail_active: bool = False
    best_mark_price: Optional[float] = None
    trail_stop_loss: Optional[float] = None
    last_sl_update: Optional[str] = None
    tp_ratchet_steps: int = 0
    last_tp_update: Optional[str] = None


@dataclass
class PositionView:
    key: str
    symbol: str
    side: str
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: Optional[float]
    current_r: Optional[float]
    risk_usd: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    anchor_time: str
    last_reason: str = "PENDING"
    note: str = ""
    exit_price: Optional[float] = None
    one_r_reached: bool = False
    trail_active: bool = False
    trail_stop_loss: Optional[float] = None
    best_mark_price: Optional[float] = None
    original_stop_loss: Optional[float] = None
    original_take_profit: Optional[float] = None
    tp_ratchet_steps: int = 0
    last_tp_update: Optional[str] = None


@dataclass
class RuntimeViewState:
    dry_run: bool
    loss_limit_r: float = DEFAULT_LOSS_LIMIT_R
    trail_activate_r: float = DEFAULT_TRAIL_ACTIVATE_R
    trail_lock_r: float = DEFAULT_TRAIL_LOCK_R
    trail_gap_r: float = DEFAULT_TRAIL_GAP_R
    repair_missing_protection: bool = DEFAULT_REPAIR_MISSING_PROTECTION
    repair_risk_usd: float = 1.0
    repair_rr: float = 2.0
    repair_lock_entry_on_profit: bool = DEFAULT_REPAIR_LOCK_ENTRY_ON_PROFIT
    tp_ratchet_enabled: bool = True
    tp_ratchet_trigger_pct: float = DEFAULT_TP_RATCHET_TRIGGER_PCT
    tp_ratchet_lock_pct: float = DEFAULT_TP_RATCHET_LOCK_PCT
    tp_ratchet_extend_pct: float = DEFAULT_TP_RATCHET_EXTEND_PCT
    tp_ratchet_max_steps: int = DEFAULT_TP_RATCHET_MAX_STEPS
    trail_close_non_positive: bool = True
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
    live_cfg = load_live_config(include_strategy=True)
    output_folder = Path(str(live_cfg.get("output", {}).get("folder", "res/logs/output")))
    log_root = output_folder.parent if output_folder.parent != Path("") else Path("res/logs")
    strategy_cfg = live_cfg.get("strategy", {}) if isinstance(live_cfg.get("strategy"), dict) else {}
    default_repair_risk_usd = float(strategy_cfg.get("risk_per_trade", 1.0) or 1.0)
    default_repair_rr = float(strategy_cfg.get("risk_reward_ratio", 2.0) or 2.0)

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
    parser.add_argument(
        "--loss-limit-r",
        type=float,
        default=DEFAULT_LOSS_LIMIT_R,
        help="Failsafe close when unrealized PnL is less than or equal to negative this R multiple",
    )
    parser.add_argument(
        "--trail-activate-r",
        type=float,
        default=DEFAULT_TRAIL_ACTIVATE_R,
        help="Activate trailing SL when unrealized PnL reaches this R multiple",
    )
    parser.add_argument(
        "--trail-lock-r",
        type=float,
        default=DEFAULT_TRAIL_LOCK_R,
        help="Initial locked profit when trailing activates, as fraction of 1R",
    )
    parser.add_argument(
        "--trail-gap-r",
        type=float,
        default=DEFAULT_TRAIL_GAP_R,
        help="Trailing distance from best price, as fraction of 1R",
    )
    parser.add_argument(
        "--repair-missing-protection",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_REPAIR_MISSING_PROTECTION,
        help="Automatically restore or synthesize missing exchange SL/TP before managing the position",
    )
    parser.add_argument(
        "--repair-risk-usd",
        type=float,
        default=default_repair_risk_usd,
        help="Fallback USD risk used to synthesize SL when the exchange has no original SL",
    )
    parser.add_argument(
        "--repair-rr",
        type=float,
        default=default_repair_rr,
        help="Fallback RR used to synthesize TP when the exchange has no original TP",
    )
    parser.add_argument(
        "--repair-lock-entry-on-profit",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_REPAIR_LOCK_ENTRY_ON_PROFIT,
        help="When protection is missing and the trade is in profit, prefer moving repaired SL to entry first",
    )
    parser.add_argument(
        "--tp-ratchet-enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ratchet SL and extend TP in steps as price approaches the current exchange TP",
    )
    parser.add_argument(
        "--tp-ratchet-trigger-pct",
        type=float,
        default=DEFAULT_TP_RATCHET_TRIGGER_PCT,
        help="Trigger TP ratchet when price reaches this fraction of the current TP distance (0.90 or 90)",
    )
    parser.add_argument(
        "--tp-ratchet-lock-pct",
        type=float,
        default=DEFAULT_TP_RATCHET_LOCK_PCT,
        help="Move SL to this fraction of the current TP distance when ratchet triggers (0.80 or 80)",
    )
    parser.add_argument(
        "--tp-ratchet-extend-pct",
        type=float,
        default=DEFAULT_TP_RATCHET_EXTEND_PCT,
        help="Extend TP by this fraction of the original TP distance on each ratchet step (0.20 or 20)",
    )
    parser.add_argument(
        "--tp-ratchet-max-steps",
        type=int,
        default=DEFAULT_TP_RATCHET_MAX_STEPS,
        help="Maximum TP ratchet steps per position",
    )
    parser.add_argument(
        "--trail-close-non-positive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Close position when trailing is active and unrealized PnL falls back to 0R or below",
    )
    return parser.parse_args()


def build_manager_config(args: argparse.Namespace) -> ManagerConfig:
    return ManagerConfig(
        one_r_trigger=1.0,
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


def parse_float_allow_zero(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def parse_unrealized_pnl(position: dict[str, Any], info: dict[str, Any]) -> Optional[float]:
    candidates = [
        position.get("unrealizedPnl"),
        position.get("unrealisedPnl"),
        position.get("info", {}).get("unrealizedPnl") if isinstance(position.get("info"), dict) else None,
        info.get("unrealizedPnl"),
        info.get("unrealisedPnl"),
    ]
    for candidate in candidates:
        parsed = parse_float_allow_zero(candidate)
        if parsed is not None:
            return parsed
    return None


def estimate_unrealized_pnl(
    trade_type: str,
    entry_price: float,
    mark_price: float,
    size: float,
) -> Optional[float]:
    if mark_price <= 0 or entry_price <= 0 or size <= 0:
        return None
    if trade_type == "LONG":
        return (mark_price - entry_price) * size
    if trade_type == "SHORT":
        return (entry_price - mark_price) * size
    return None


def normalize_threshold(value: float, fallback: float) -> float:
    parsed = abs(float(value))
    return parsed if parsed > 0 else abs(float(fallback))


def normalize_ratio(value: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(parsed) or parsed <= 0:
        return float(fallback)
    return parsed


def normalize_fraction(value: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(parsed) or parsed <= 0:
        return float(fallback)
    if parsed > 1.0 and parsed <= 100.0:
        parsed = parsed / 100.0
    if parsed <= 0 or parsed > 1.0:
        return float(fallback)
    return parsed


def normalize_non_negative_int(value: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return parsed if parsed >= 0 else int(fallback)


def normalize_positive_float(value: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(parsed) or parsed <= 0:
        return float(fallback)
    return parsed


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def compute_risk_distance(entry_price: float, stop_loss: Optional[float]) -> Optional[float]:
    if stop_loss is None:
        return None
    risk = abs(float(entry_price) - float(stop_loss))
    if risk <= 1e-12:
        return None
    return risk


def compute_risk_usd(risk_distance: Optional[float], size: float) -> Optional[float]:
    if risk_distance is None or risk_distance <= 1e-12 or size <= 0:
        return None
    risk_usd = float(risk_distance) * float(size)
    if risk_usd <= 1e-12 or math.isnan(risk_usd):
        return None
    return risk_usd


def compute_current_r(unrealized_pnl: Optional[float], risk_usd: Optional[float]) -> Optional[float]:
    if unrealized_pnl is None or risk_usd is None or risk_usd <= 1e-12:
        return None
    current_r = float(unrealized_pnl) / float(risk_usd)
    if math.isnan(current_r):
        return None
    return current_r


def compute_target_distance(trade_type: str, entry_price: float, target_price: Optional[float]) -> Optional[float]:
    if target_price is None:
        return None
    if trade_type == "LONG":
        distance = float(target_price) - float(entry_price)
    else:
        distance = float(entry_price) - float(target_price)
    if math.isnan(distance) or distance <= 1e-12:
        return None
    return distance


def compute_stop_loss_from_distance(trade_type: str, entry_price: float, risk_distance: float) -> float:
    if trade_type == "LONG":
        return float(entry_price) - float(risk_distance)
    return float(entry_price) + float(risk_distance)


def compute_price_at_distance(trade_type: str, entry_price: float, distance: float) -> float:
    if trade_type == "LONG":
        return float(entry_price) + float(distance)
    return float(entry_price) - float(distance)


def compute_locked_stop_loss(trade_type: str, entry_price: float, risk_distance: float, lock_r: float) -> float:
    if trade_type == "LONG":
        return float(entry_price) + (float(lock_r) * float(risk_distance))
    return float(entry_price) - (float(lock_r) * float(risk_distance))


def compute_trailing_stop_from_best(trade_type: str, best_mark: float, risk_distance: float, gap_r: float) -> float:
    if trade_type == "LONG":
        return float(best_mark) - (float(gap_r) * float(risk_distance))
    return float(best_mark) + (float(gap_r) * float(risk_distance))


def is_improved_stop_loss(trade_type: str, candidate_stop_loss: Optional[float], current_stop_loss: Optional[float]) -> bool:
    if candidate_stop_loss is None:
        return False
    if current_stop_loss is None:
        return True
    if trade_type == "LONG":
        return float(candidate_stop_loss) > float(current_stop_loss) + 1e-12
    return float(candidate_stop_loss) < float(current_stop_loss) - 1e-12


def is_improved_take_profit(trade_type: str, candidate_take_profit: Optional[float], current_take_profit: Optional[float]) -> bool:
    if candidate_take_profit is None:
        return False
    if current_take_profit is None:
        return True
    if trade_type == "LONG":
        return float(candidate_take_profit) > float(current_take_profit) + 1e-12
    return float(candidate_take_profit) < float(current_take_profit) - 1e-12


def best_take_profit(trade_type: str, first: Optional[float], second: Optional[float]) -> Optional[float]:
    if first is None:
        return second
    if second is None:
        return first
    if trade_type == "LONG":
        return max(float(first), float(second))
    return min(float(first), float(second))


def best_stop_loss(trade_type: str, first: Optional[float], second: Optional[float]) -> Optional[float]:
    if first is None:
        return second
    if second is None:
        return first
    if trade_type == "LONG":
        return max(float(first), float(second))
    return min(float(first), float(second))


def clamp_stop_loss_against_mark(
    trade_type: str,
    candidate_stop_loss: Optional[float],
    mark_price: float,
    risk_distance: Optional[float],
) -> Optional[float]:
    if candidate_stop_loss is None:
        return None
    if mark_price <= 0:
        return candidate_stop_loss

    dynamic_buffer = float(risk_distance or 0.0) * 0.02
    mark_buffer = float(mark_price) * 0.0005
    buffer = max(dynamic_buffer, mark_buffer, 1e-8)

    if trade_type == "LONG":
        max_allowed = float(mark_price) - buffer
        return min(float(candidate_stop_loss), max_allowed)

    min_allowed = float(mark_price) + buffer
    return max(float(candidate_stop_loss), min_allowed)


def clamp_take_profit_away_from_mark(
    trade_type: str,
    candidate_take_profit: Optional[float],
    mark_price: float,
    reference_distance: Optional[float],
) -> Optional[float]:
    if candidate_take_profit is None:
        return None
    if mark_price <= 0:
        return candidate_take_profit

    dynamic_buffer = float(reference_distance or 0.0) * 0.05
    mark_buffer = float(mark_price) * 0.0005
    buffer = max(dynamic_buffer, mark_buffer, 1e-8)

    if trade_type == "LONG":
        min_allowed = float(mark_price) + buffer
        return max(float(candidate_take_profit), min_allowed)

    max_allowed = float(mark_price) - buffer
    return min(float(candidate_take_profit), max_allowed)


def to_exchange_side(trade_type: str) -> str:
    return "buy" if trade_type == "LONG" else "sell"


def apply_position_stop_loss(
    exchange,
    position: PositionSnapshot,
    stop_loss: float,
    take_profit: Optional[float],
) -> dict[str, Any]:
    normalized_stop_loss = float(stop_loss)
    normalized_take_profit = take_profit

    try:
        normalized_stop_loss = float(exchange.price_to_precision(position.symbol, normalized_stop_loss))
    except Exception:
        normalized_stop_loss = float(stop_loss)

    if normalized_take_profit is not None:
        try:
            normalized_take_profit = float(exchange.price_to_precision(position.symbol, float(normalized_take_profit)))
        except Exception:
            normalized_take_profit = float(normalized_take_profit)

    if normalized_take_profit is None:
        result = set_position_sl_tp(
            exchange=exchange,
            symbol=position.symbol,
            side=to_exchange_side(position.trade_type),
            stop_loss=normalized_stop_loss,
        )
    else:
        result = set_position_sl_tp(
            exchange=exchange,
            symbol=position.symbol,
            side=to_exchange_side(position.trade_type),
            stop_loss=normalized_stop_loss,
            take_profit=float(normalized_take_profit),
        )

    payload: dict[str, Any] = {
        "success": bool(result.get("success")),
        "stop_loss": normalized_stop_loss,
        "take_profit": normalized_take_profit,
        "result": result,
    }
    if not payload["success"]:
        payload["error"] = str(result.get("error") or "set stop loss failed")
    return payload


def build_protection_repair_plan(
    position: PositionSnapshot,
    entry_price: float,
    active_stop_loss: Optional[float],
    active_take_profit: Optional[float],
    original_stop_loss: Optional[float],
    original_take_profit: Optional[float],
    risk_distance: Optional[float],
    unrealized_pnl: Optional[float],
    repair_risk_usd: float,
    repair_rr: float,
    repair_lock_entry_on_profit: bool,
) -> dict[str, Any]:
    derived_risk_distance = risk_distance
    reference_stop_loss = original_stop_loss
    source_parts: list[str] = []

    if reference_stop_loss is not None:
        source_parts.append("original_sl")
    elif active_stop_loss is not None:
        reference_stop_loss = active_stop_loss
        source_parts.append("active_sl")

    if derived_risk_distance is None and reference_stop_loss is not None:
        derived_risk_distance = compute_risk_distance(entry_price, reference_stop_loss)

    if derived_risk_distance is None and position.size > 0:
        derived_risk_distance = float(repair_risk_usd) / float(position.size)
        source_parts.append("fallback_risk_usd")

    if reference_stop_loss is None and derived_risk_distance is not None:
        reference_stop_loss = compute_stop_loss_from_distance(position.trade_type, entry_price, derived_risk_distance)

    applied_stop_loss = active_stop_loss if active_stop_loss is not None else reference_stop_loss
    entry_lock_applied = False
    if repair_lock_entry_on_profit and unrealized_pnl is not None and unrealized_pnl > 0:
        applied_stop_loss = best_stop_loss(position.trade_type, applied_stop_loss, entry_price)
        entry_lock_applied = True
        source_parts.append("entry_lock")

    applied_stop_loss = clamp_stop_loss_against_mark(
        position.trade_type,
        applied_stop_loss,
        position.mark_price,
        derived_risk_distance,
    )

    reward_distance = compute_target_distance(position.trade_type, entry_price, original_take_profit)
    if reward_distance is None:
        reward_distance = compute_target_distance(position.trade_type, entry_price, active_take_profit)
        if reward_distance is not None:
            source_parts.append("active_tp")
    else:
        source_parts.append("original_tp")

    if reward_distance is None and derived_risk_distance is not None:
        reward_distance = float(derived_risk_distance) * float(repair_rr)
        source_parts.append("fallback_rr")

    reference_take_profit = original_take_profit
    if reference_take_profit is None and reward_distance is not None:
        reference_take_profit = compute_price_at_distance(position.trade_type, entry_price, reward_distance)

    applied_take_profit = active_take_profit if active_take_profit is not None else reference_take_profit
    applied_take_profit = clamp_take_profit_away_from_mark(
        position.trade_type,
        applied_take_profit,
        position.mark_price,
        reward_distance,
    )

    if reference_take_profit is None:
        reference_take_profit = applied_take_profit

    return {
        "reference_stop_loss": reference_stop_loss,
        "reference_take_profit": reference_take_profit,
        "applied_stop_loss": applied_stop_loss,
        "applied_take_profit": applied_take_profit,
        "risk_distance": derived_risk_distance,
        "reward_distance": reward_distance,
        "entry_lock_applied": entry_lock_applied,
        "source": "+".join(source_parts) if source_parts else "none",
    }


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
    return cast(pd.Timestamp, pd.Timestamp(dt))


def floor_to_minute(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if not isinstance(ts, pd.Timestamp) or pd.isna(ts):
        fallback = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0))
        return cast(pd.Timestamp, fallback)
    py_dt = ts.to_pydatetime()
    if py_dt.tzinfo is not None:
        py_dt = py_dt.astimezone(timezone.utc).replace(tzinfo=None)
    floored = pd.Timestamp(py_dt.replace(second=0, microsecond=0))
    return cast(pd.Timestamp, floored)


def utc_now_naive() -> pd.Timestamp:
    return cast(pd.Timestamp, pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)))


def timestamp_to_string(value: pd.Timestamp) -> str:
    ts = pd.Timestamp(value)
    if not isinstance(ts, pd.Timestamp) or pd.isna(ts):
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
        return cast(pd.Timestamp, pd.Timestamp(py_dt))
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


def format_r_multiple(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}R"


def format_percent(value: float) -> str:
    return f"{float(value) * 100:.0f}%"


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
    if status.startswith("FAILED"):
        return "bold red"
    if status.startswith("DRY"):
        return "bold cyan"
    if status.startswith("TRAIL"):
        return "bold cyan"
    if status.startswith("PROTECTION"):
        return "bold cyan"
    if status.startswith("TP_"):
        return "bold cyan"
    if status in {"R_LOSS_LIMIT", "TRAIL_ZERO_R_CLOSE"}:
        return "bold cyan"
    if status in {"TP_SEEN", "SL_SEEN"}:
        return "bold yellow"
    if status in {"HOLD", "TRAILING"}:
        return "dim"
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
        f"Close <= -{runtime_state.loss_limit_r:.2f}R",
    )
    table.add_row(
        "Trailing",
        f"on >= {runtime_state.trail_activate_r:.2f}R",
        "Trail R",
        (
            f"lock {runtime_state.trail_lock_r:.2f}R | gap {runtime_state.trail_gap_r:.2f}R"
            f" | close<=0R={runtime_state.trail_close_non_positive}"
        ),
    )
    table.add_row(
        "Repair",
        "ON" if runtime_state.repair_missing_protection else "OFF",
        "Repair Rule",
        (
            f"risk ${runtime_state.repair_risk_usd:.2f} | RR {runtime_state.repair_rr:.2f}"
            f" | lockBE={runtime_state.repair_lock_entry_on_profit}"
        ),
    )
    table.add_row(
        "TP Ratchet",
        "ON" if runtime_state.tp_ratchet_enabled else "OFF",
        "TP Rule",
        (
            f"{format_percent(runtime_state.tp_ratchet_trigger_pct)} -> "
            f"SL {format_percent(runtime_state.tp_ratchet_lock_pct)} | "
            f"TP +{format_percent(runtime_state.tp_ratchet_extend_pct)} | "
            f"steps {runtime_state.tp_ratchet_max_steps}"
        ),
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
    table.add_column("Mark", justify="right", width=12)
    table.add_column("SL", justify="right", width=12)
    table.add_column("TP", justify="right", width=12)
    table.add_column("Risk USD", justify="right", width=10)
    table.add_column("R Now", justify="right", width=8)
    table.add_column("Trail", width=7)
    table.add_column("PnL USD", justify="right", width=12)
    table.add_column("Decision", width=18)
    table.add_column("Note", overflow="fold")

    if not runtime_state.positions:
        table.add_row("No active positions", "", "", "", "", "", "", "", "", "", "", "", "")
    else:
        for view in sorted(runtime_state.positions.values(), key=lambda item: item.symbol):
            side_style = "green" if view.side == "LONG" else "red"
            note = view.note
            if view.exit_price is not None:
                exit_info = f"exit {format_number(view.exit_price)}"
                note = f"{note} | {exit_info}" if note else exit_info
            if view.best_mark_price is not None:
                best_info = f"best {format_number(view.best_mark_price)}"
                note = f"{note} | {best_info}" if note else best_info
            if view.original_stop_loss is not None:
                origin_sl_info = f"orig SL {format_number(view.original_stop_loss)}"
                note = f"{note} | {origin_sl_info}" if note else origin_sl_info
            if view.original_take_profit is not None:
                origin_tp_info = f"orig TP {format_number(view.original_take_profit)}"
                note = f"{note} | {origin_tp_info}" if note else origin_tp_info
            if view.tp_ratchet_steps > 0:
                step_info = f"tp step {view.tp_ratchet_steps}"
                note = f"{note} | {step_info}" if note else step_info

            pnl_style = "dim"
            pnl_value = "-"
            if view.unrealized_pnl is not None:
                pnl_style = "green" if view.unrealized_pnl >= 0 else "red"
                pnl_value = format_number(view.unrealized_pnl)

            current_r_style = "dim"
            current_r_value = "-"
            if view.current_r is not None:
                current_r_style = "green" if view.current_r >= 0 else "red"
                current_r_value = format_r_multiple(view.current_r)

            trail_status = Text("ON", style="green") if view.trail_active else Text("OFF", style="dim")

            table.add_row(
                view.symbol,
                Text(view.side, style=side_style),
                format_number(view.size, quantity=True),
                format_number(view.entry_price),
                format_number(view.mark_price) if view.mark_price > 0 else "-",
                format_number(view.stop_loss),
                format_number(view.take_profit),
                format_number(view.risk_usd),
                Text(current_r_value, style=current_r_style),
                trail_status,
                Text(pnl_value, style=pnl_style),
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
        Layout(build_header_panel(runtime_state, cfg), size=11),
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

        mark_price = parse_optional_float(position.get("markPrice"))
        if mark_price is None:
            mark_price = parse_optional_float(info.get("markPrice"))
        if mark_price is None:
            mark_price = parse_optional_float(info.get("mark_price"))
        if mark_price is None:
            mark_price = 0.0

        unrealized_pnl = parse_unrealized_pnl(position, info)
        if unrealized_pnl is None:
            unrealized_pnl = estimate_unrealized_pnl(
                trade_type=side,
                entry_price=entry_price,
                mark_price=mark_price,
                size=size,
            )

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
                unrealized_pnl=unrealized_pnl,
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
                unrealized_pnl=position.unrealized_pnl,
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
            original_stop_loss = position.stop_loss
            original_take_profit = position.take_profit
            risk_distance = compute_risk_distance(reference_entry_price, original_stop_loss)
            trail_active = False
            best_mark_price = position.mark_price if position.mark_price > 0 else None
            trail_stop_loss = position.stop_loss
            last_sl_update = None
            tp_ratchet_steps = 0
            last_tp_update = None
        else:
            previous_size = parse_optional_float(stored.get("reference_size")) or 0.0
            previous_entry = parse_optional_float(stored.get("reference_entry_price")) or position.entry_price
            stored_anchor = string_to_timestamp(stored.get("anchor_time"))

            if not stored_anchor:
                reset_reason = "STATE_REPAIRED"
                anchor_time = anchor_source
                reference_entry_price = position.entry_price
                original_stop_loss = position.stop_loss
                original_take_profit = position.take_profit
                risk_distance = compute_risk_distance(reference_entry_price, original_stop_loss)
                trail_active = False
                best_mark_price = position.mark_price if position.mark_price > 0 else None
                trail_stop_loss = position.stop_loss
                last_sl_update = None
                tp_ratchet_steps = 0
                last_tp_update = None
            elif not is_close(previous_size, position.size) or not is_close(previous_entry, position.entry_price):
                reset_reason = "POSITION_SCALED"
                anchor_time = anchor_source
                reference_entry_price = position.entry_price
                original_stop_loss = position.stop_loss
                original_take_profit = position.take_profit
                risk_distance = compute_risk_distance(reference_entry_price, original_stop_loss)
                trail_active = False
                best_mark_price = position.mark_price if position.mark_price > 0 else None
                trail_stop_loss = position.stop_loss
                last_sl_update = None
                tp_ratchet_steps = 0
                last_tp_update = None
            else:
                anchor_time = stored_anchor
                if anchor_time > cycle_floor:
                    anchor_time = cycle_floor
                reference_entry_price = previous_entry

                original_stop_loss = parse_optional_float(stored.get("original_stop_loss"))
                if original_stop_loss is None:
                    original_stop_loss = position.stop_loss

                original_take_profit = parse_optional_float(stored.get("original_take_profit"))
                if original_take_profit is None:
                    original_take_profit = position.take_profit

                risk_distance = parse_optional_float(stored.get("risk_distance"))
                if risk_distance is None:
                    risk_distance = compute_risk_distance(reference_entry_price, original_stop_loss)

                trail_active = parse_bool(stored.get("trail_active"))
                best_mark_price = parse_optional_float(stored.get("best_mark_price"))
                if position.mark_price > 0:
                    if best_mark_price is None:
                        best_mark_price = position.mark_price
                    elif position.trade_type == "LONG":
                        best_mark_price = max(best_mark_price, position.mark_price)
                    else:
                        best_mark_price = min(best_mark_price, position.mark_price)

                trail_stop_loss = parse_optional_float(stored.get("trail_stop_loss"))
                if trail_stop_loss is None:
                    trail_stop_loss = parse_optional_float(stored.get("stop_loss"))
                if trail_stop_loss is None:
                    trail_stop_loss = position.stop_loss

                last_sl_update_raw = stored.get("last_sl_update")
                last_sl_update = str(last_sl_update_raw).strip() if last_sl_update_raw not in (None, "") else None

                tp_ratchet_steps = normalize_non_negative_int(stored.get("tp_ratchet_steps", 0), 0)
                last_tp_update_raw = stored.get("last_tp_update")
                last_tp_update = str(last_tp_update_raw).strip() if last_tp_update_raw not in (None, "") else None

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
            "original_stop_loss": original_stop_loss,
            "original_take_profit": original_take_profit,
            "risk_distance": risk_distance,
            "trail_active": bool(trail_active),
            "best_mark_price": best_mark_price,
            "trail_stop_loss": trail_stop_loss,
            "last_sl_update": last_sl_update,
            "tp_ratchet_steps": int(tp_ratchet_steps),
            "last_tp_update": last_tp_update,
        }

        managed_positions.append(
            ManagedPosition(
                snapshot=position,
                anchor_time=anchor_time,
                reference_entry_price=reference_entry_price,
                reset_reason=reset_reason,
                original_stop_loss=original_stop_loss,
                original_take_profit=original_take_profit,
                risk_distance=risk_distance,
                trail_active=bool(trail_active),
                best_mark_price=best_mark_price,
                trail_stop_loss=trail_stop_loss,
                last_sl_update=last_sl_update,
                tp_ratchet_steps=int(tp_ratchet_steps),
                last_tp_update=last_tp_update,
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
    if not isinstance(timestamp, pd.Timestamp) or pd.isna(timestamp):
        raise ValueError("Invalid timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    normalized = cast(pd.Timestamp, timestamp)
    return int(normalized.timestamp() * 1000)


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
    start_time = cast(pd.Timestamp, floor_to_minute(anchor_time) - pd.Timedelta(minutes=1))
    cursor_ms = to_millis(start_time)
    current_open_ms = to_millis(cast(pd.Timestamp, floor_to_minute(cycle_time)))
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
                "original_stop_loss": view.original_stop_loss,
                "original_take_profit": view.original_take_profit,
                "stop_loss": view.stop_loss,
                "take_profit": view.take_profit,
                "risk_usd": view.risk_usd,
                "current_r": view.current_r,
                "anchor_time": view.anchor_time,
                "one_r_reached": bool(view.one_r_reached),
                "last_reason": view.last_reason,
                "managed_exit_price": view.exit_price,
                "note": view.note,
                "trail_active": bool(view.trail_active),
                "trail_stop_loss": view.trail_stop_loss,
                "best_mark_price": view.best_mark_price,
                "tp_ratchet_steps": int(view.tp_ratchet_steps),
                "last_tp_update": view.last_tp_update or "",
                "position_idx": snapshot.position_idx if snapshot is not None else None,
                "mark_price": snapshot.mark_price if snapshot is not None else None,
                "unrealized_pnl": snapshot.unrealized_pnl if snapshot is not None else None,
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
    loss_limit_r: float,
    trail_activate_r: float,
    trail_lock_r: float,
    trail_gap_r: float,
    repair_missing_protection: bool,
    repair_risk_usd: float,
    repair_rr: float,
    repair_lock_entry_on_profit: bool,
    tp_ratchet_enabled: bool,
    tp_ratchet_trigger_pct: float,
    tp_ratchet_lock_pct: float,
    tp_ratchet_extend_pct: float,
    tp_ratchet_max_steps: int,
    trail_close_non_positive: bool,
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
    loss_limit_r = normalize_ratio(loss_limit_r, DEFAULT_LOSS_LIMIT_R)
    trail_activate_r = normalize_ratio(trail_activate_r, DEFAULT_TRAIL_ACTIVATE_R)
    trail_lock_r = normalize_ratio(trail_lock_r, DEFAULT_TRAIL_LOCK_R)
    trail_gap_r = normalize_ratio(trail_gap_r, DEFAULT_TRAIL_GAP_R)
    repair_missing_protection = bool(repair_missing_protection)
    repair_risk_usd = normalize_positive_float(repair_risk_usd, 1.0)
    repair_rr = normalize_positive_float(repair_rr, 2.0)
    repair_lock_entry_on_profit = bool(repair_lock_entry_on_profit)
    tp_ratchet_enabled = bool(tp_ratchet_enabled)
    tp_ratchet_trigger_pct = normalize_fraction(tp_ratchet_trigger_pct, DEFAULT_TP_RATCHET_TRIGGER_PCT)
    tp_ratchet_lock_pct = normalize_fraction(tp_ratchet_lock_pct, DEFAULT_TP_RATCHET_LOCK_PCT)
    tp_ratchet_extend_pct = normalize_fraction(tp_ratchet_extend_pct, DEFAULT_TP_RATCHET_EXTEND_PCT)
    tp_ratchet_max_steps = normalize_non_negative_int(tp_ratchet_max_steps, DEFAULT_TP_RATCHET_MAX_STEPS)
    trail_close_non_positive = bool(trail_close_non_positive)
    if runtime_state is not None:
        runtime_state.loss_limit_r = loss_limit_r
        runtime_state.trail_activate_r = trail_activate_r
        runtime_state.trail_lock_r = trail_lock_r
        runtime_state.trail_gap_r = trail_gap_r
        runtime_state.repair_missing_protection = repair_missing_protection
        runtime_state.repair_risk_usd = repair_risk_usd
        runtime_state.repair_rr = repair_rr
        runtime_state.repair_lock_entry_on_profit = repair_lock_entry_on_profit
        runtime_state.tp_ratchet_enabled = tp_ratchet_enabled
        runtime_state.tp_ratchet_trigger_pct = tp_ratchet_trigger_pct
        runtime_state.tp_ratchet_lock_pct = tp_ratchet_lock_pct
        runtime_state.tp_ratchet_extend_pct = tp_ratchet_extend_pct
        runtime_state.tp_ratchet_max_steps = tp_ratchet_max_steps
        runtime_state.trail_close_non_positive = trail_close_non_positive

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
            mark_price=snapshot.mark_price,
            unrealized_pnl=snapshot.unrealized_pnl,
            current_r=None,
            risk_usd=compute_risk_usd(managed_position.risk_distance, snapshot.size),
            stop_loss=best_stop_loss(snapshot.trade_type, managed_position.trail_stop_loss, snapshot.stop_loss),
            take_profit=snapshot.take_profit,
            anchor_time=timestamp_to_string(managed_position.anchor_time),
            note=note,
            trail_active=bool(managed_position.trail_active),
            trail_stop_loss=managed_position.trail_stop_loss,
            best_mark_price=managed_position.best_mark_price,
            original_stop_loss=managed_position.original_stop_loss,
            original_take_profit=managed_position.original_take_profit,
            tp_ratchet_steps=int(managed_position.tp_ratchet_steps),
            last_tp_update=managed_position.last_tp_update,
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
    state_positions = state.setdefault("positions", {})

    for managed_position in managed_positions:
        snapshot = managed_position.snapshot
        view = position_views[snapshot.key]
        cycle_stats["evaluated"] += 1

        state_entry = state_positions.get(snapshot.key)
        if not isinstance(state_entry, dict):
            state_entry = {}
        stored_stop_loss = parse_optional_float(state_entry.get("stop_loss"))
        stored_take_profit = parse_optional_float(state_entry.get("take_profit"))

        risk_distance = managed_position.risk_distance
        if risk_distance is None:
            risk_distance = compute_risk_distance(managed_position.reference_entry_price, managed_position.original_stop_loss)
        risk_usd = compute_risk_usd(risk_distance, snapshot.size)

        active_stop_loss = best_stop_loss(
            snapshot.trade_type,
            managed_position.trail_stop_loss,
            best_stop_loss(snapshot.trade_type, snapshot.stop_loss, stored_stop_loss),
        )
        active_take_profit = snapshot.take_profit if snapshot.take_profit is not None else stored_take_profit
        best_mark_price = managed_position.best_mark_price
        if snapshot.mark_price > 0:
            if best_mark_price is None:
                best_mark_price = snapshot.mark_price
            elif snapshot.trade_type == "LONG":
                best_mark_price = max(best_mark_price, snapshot.mark_price)
            else:
                best_mark_price = min(best_mark_price, snapshot.mark_price)

        trail_active = bool(managed_position.trail_active)
        last_sl_update = managed_position.last_sl_update
        original_stop_loss = managed_position.original_stop_loss
        original_take_profit = managed_position.original_take_profit
        tp_ratchet_steps = int(managed_position.tp_ratchet_steps)
        last_tp_update = managed_position.last_tp_update

        pnl_usd = snapshot.unrealized_pnl
        if pnl_usd is None:
            pnl_usd = estimate_unrealized_pnl(
                trade_type=snapshot.trade_type,
                entry_price=managed_position.reference_entry_price,
                mark_price=snapshot.mark_price,
                size=snapshot.size,
            )

        note_parts: list[str] = []
        if view.note:
            note_parts.append(view.note)

        protection_repaired = False
        protection_repair_failed = False
        protection_repair_error = ""

        repair_needed = repair_missing_protection and (
            active_stop_loss is None
            or active_take_profit is None
            or risk_distance is None
            or original_stop_loss is None
            or original_take_profit is None
        )

        if repair_needed:
            repair_plan = build_protection_repair_plan(
                position=snapshot,
                entry_price=managed_position.reference_entry_price,
                active_stop_loss=active_stop_loss,
                active_take_profit=active_take_profit,
                original_stop_loss=original_stop_loss,
                original_take_profit=original_take_profit,
                risk_distance=risk_distance,
                unrealized_pnl=pnl_usd,
                repair_risk_usd=repair_risk_usd,
                repair_rr=repair_rr,
                repair_lock_entry_on_profit=repair_lock_entry_on_profit,
            )

            repaired_reference_sl = parse_optional_float(repair_plan.get("reference_stop_loss"))
            repaired_reference_tp = parse_optional_float(repair_plan.get("reference_take_profit"))
            repaired_applied_sl = parse_optional_float(repair_plan.get("applied_stop_loss"))
            repaired_applied_tp = parse_optional_float(repair_plan.get("applied_take_profit"))
            repaired_risk_distance = parse_optional_float(repair_plan.get("risk_distance"))
            repair_source = str(repair_plan.get("source") or "none")
            entry_lock_applied = bool(repair_plan.get("entry_lock_applied"))

            if repaired_applied_sl is None or repaired_applied_tp is None or repaired_risk_distance is None:
                protection_repair_failed = True
                protection_repair_error = "unable to synthesize complete SL/TP protection"
                note_parts.append(f"protection repair failed: {protection_repair_error}")
                append_jsonl(
                    log_path,
                    {
                        "event": "PROTECTION_REPAIR_FAILED",
                        "cycle_time": cycle_label,
                        "key": snapshot.key,
                        "symbol": snapshot.symbol,
                        "side": snapshot.trade_type,
                        "entry_price": managed_position.reference_entry_price,
                        "mark_price": snapshot.mark_price,
                        "source": repair_source,
                        "error": protection_repair_error,
                        "dry_run": dry_run,
                    },
                )
            else:
                sl_update_needed = active_stop_loss is None or not is_close(active_stop_loss, repaired_applied_sl)
                tp_update_needed = active_take_profit is None or not is_close(active_take_profit, repaired_applied_tp)
                exchange_update_needed = sl_update_needed or tp_update_needed

                if exchange_update_needed:
                    if dry_run:
                        repair_result = {
                            "success": True,
                            "stop_loss": repaired_applied_sl,
                            "take_profit": repaired_applied_tp,
                            "result": {"dry_run": True},
                        }
                    else:
                        repair_result = apply_position_stop_loss(
                            exchange,
                            snapshot,
                            repaired_applied_sl,
                            repaired_applied_tp,
                        )

                    if repair_result.get("success"):
                        active_stop_loss = parse_float_allow_zero(repair_result.get("stop_loss")) or repaired_applied_sl
                        active_take_profit = parse_optional_float(repair_result.get("take_profit")) or repaired_applied_tp
                        original_stop_loss = original_stop_loss or repaired_reference_sl
                        original_take_profit = original_take_profit or repaired_reference_tp or active_take_profit
                        risk_distance = repaired_risk_distance
                        protection_repaired = True
                        last_sl_update = cycle_label
                        if active_take_profit is not None:
                            last_tp_update = cycle_label
                        note_parts.append(
                            f"protection repaired SL {format_number(active_stop_loss)} | TP {format_number(active_take_profit)}"
                        )
                        append_jsonl(
                            log_path,
                            {
                                "event": "PROTECTION_REPAIRED",
                                "cycle_time": cycle_label,
                                "key": snapshot.key,
                                "symbol": snapshot.symbol,
                                "side": snapshot.trade_type,
                                "entry_price": managed_position.reference_entry_price,
                                "mark_price": snapshot.mark_price,
                                "new_stop_loss": active_stop_loss,
                                "new_take_profit": active_take_profit,
                                "reference_stop_loss": original_stop_loss,
                                "reference_take_profit": original_take_profit,
                                "risk_distance": risk_distance,
                                "repair_risk_usd": repair_risk_usd,
                                "repair_rr": repair_rr,
                                "entry_lock_applied": entry_lock_applied,
                                "source": repair_source,
                                "dry_run": dry_run,
                            },
                        )
                        push_runtime_event(
                            runtime_state,
                            "PROTECTION_FIX",
                            f"{snapshot.symbol} repaired SL/TP on exchange",
                        )
                    else:
                        protection_repair_failed = True
                        protection_repair_error = str(repair_result.get("error") or "exchange protection repair failed")
                        note_parts.append(f"protection repair error: {protection_repair_error}")
                        append_jsonl(
                            log_path,
                            {
                                "event": "PROTECTION_REPAIR_FAILED",
                                "cycle_time": cycle_label,
                                "key": snapshot.key,
                                "symbol": snapshot.symbol,
                                "side": snapshot.trade_type,
                                "entry_price": managed_position.reference_entry_price,
                                "mark_price": snapshot.mark_price,
                                "attempted_stop_loss": repaired_applied_sl,
                                "attempted_take_profit": repaired_applied_tp,
                                "source": repair_source,
                                "error": protection_repair_error,
                                "dry_run": dry_run,
                            },
                        )
                        push_runtime_event(
                            runtime_state,
                            "PROTECTION_FAIL",
                            f"{snapshot.symbol} protection repair failed",
                        )
                else:
                    original_stop_loss = original_stop_loss or repaired_reference_sl
                    original_take_profit = original_take_profit or repaired_reference_tp or active_take_profit
                    risk_distance = repaired_risk_distance
                    protection_repaired = True
                    note_parts.append("protection references repaired")

        risk_distance = risk_distance or compute_risk_distance(managed_position.reference_entry_price, original_stop_loss)
        risk_usd = compute_risk_usd(risk_distance, snapshot.size)
        current_r = compute_current_r(pnl_usd, risk_usd)

        view.unrealized_pnl = pnl_usd
        view.current_r = current_r
        view.risk_usd = risk_usd
        view.stop_loss = active_stop_loss
        view.take_profit = active_take_profit
        view.trail_active = trail_active
        view.trail_stop_loss = active_stop_loss
        view.best_mark_price = best_mark_price
        view.original_stop_loss = original_stop_loss
        view.original_take_profit = original_take_profit
        view.tp_ratchet_steps = tp_ratchet_steps
        view.last_tp_update = last_tp_update

        if pnl_usd is None:
            cycle_stats["skipped"] += 1
            view.last_reason = "SKIPPED"
            skip_note = "missing unrealized pnl"
            view.note = " | ".join(note_parts + [skip_note]) if note_parts else skip_note
            state_entry.update(
                {
                    "stop_loss": active_stop_loss,
                    "take_profit": active_take_profit,
                    "last_seen_at": cycle_label,
                    "original_stop_loss": original_stop_loss,
                    "original_take_profit": original_take_profit,
                    "risk_distance": risk_distance,
                    "trail_active": trail_active,
                    "best_mark_price": best_mark_price,
                    "trail_stop_loss": active_stop_loss,
                    "last_sl_update": last_sl_update,
                    "tp_ratchet_steps": tp_ratchet_steps,
                    "last_tp_update": last_tp_update,
                }
            )
            state_positions[snapshot.key] = state_entry
            append_jsonl(
                log_path,
                {
                    "event": "POSITION_SKIPPED",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "side": snapshot.trade_type,
                    "reason": "MISSING_UNREALIZED_PNL",
                },
            )
            continue

        if current_r is None or risk_usd is None:
            cycle_stats["skipped"] += 1
            view.last_reason = "SKIPPED_RISK"
            skip_note = "missing exchange-derived 1R (entry/original SL/size)"
            view.note = " | ".join(note_parts + [skip_note]) if note_parts else skip_note
            state_entry.update(
                {
                    "stop_loss": active_stop_loss,
                    "take_profit": active_take_profit,
                    "last_seen_at": cycle_label,
                    "original_stop_loss": original_stop_loss,
                    "original_take_profit": original_take_profit,
                    "risk_distance": risk_distance,
                    "trail_active": trail_active,
                    "best_mark_price": best_mark_price,
                    "trail_stop_loss": active_stop_loss,
                    "last_sl_update": last_sl_update,
                    "tp_ratchet_steps": tp_ratchet_steps,
                    "last_tp_update": last_tp_update,
                }
            )
            state_positions[snapshot.key] = state_entry
            append_jsonl(
                log_path,
                {
                    "event": "POSITION_SKIPPED",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "side": snapshot.trade_type,
                    "reason": "MISSING_EXCHANGE_RISK",
                    "entry_price": managed_position.reference_entry_price,
                    "original_stop_loss": original_stop_loss,
                    "original_take_profit": original_take_profit,
                    "size": snapshot.size,
                },
            )
            continue

        trailing_sl_moved = False
        trailing_sl_failed = False
        trailing_error = ""
        tp_ratchet_moved = False
        tp_ratchet_failed = False
        tp_ratchet_error = ""

        if not trail_active and current_r >= trail_activate_r and risk_distance is not None and risk_distance > 0:
            trail_active = True
            note_parts.append(f"trail activated ({format_r_multiple(current_r)} >= +{trail_activate_r:.2f}R)")
            append_jsonl(
                log_path,
                {
                    "event": "TRAIL_ACTIVATED",
                    "cycle_time": cycle_label,
                    "key": snapshot.key,
                    "symbol": snapshot.symbol,
                    "side": snapshot.trade_type,
                    "unrealized_pnl": pnl_usd,
                    "current_r": current_r,
                    "risk_usd": risk_usd,
                    "entry_price": managed_position.reference_entry_price,
                    "risk_distance": risk_distance,
                },
            )
            push_runtime_event(runtime_state, "TRAIL_ACTIVE", f"{snapshot.symbol} trailing activated")
        elif not trail_active and current_r >= trail_activate_r:
            note_parts.append("trail waiting: missing valid exchange 1R")

        if trail_active and risk_distance is not None and risk_distance > 0:
            lock_stop = compute_locked_stop_loss(
                snapshot.trade_type,
                managed_position.reference_entry_price,
                risk_distance,
                trail_lock_r,
            )
            lock_stop = best_stop_loss(snapshot.trade_type, lock_stop, active_stop_loss)
            lock_stop = clamp_stop_loss_against_mark(snapshot.trade_type, lock_stop, snapshot.mark_price, risk_distance)

            if lock_stop is not None and is_improved_stop_loss(snapshot.trade_type, lock_stop, active_stop_loss):
                if dry_run:
                    sl_update = {
                        "success": True,
                        "stop_loss": lock_stop,
                        "take_profit": active_take_profit,
                        "result": {"dry_run": True},
                    }
                else:
                    sl_update = apply_position_stop_loss(exchange, snapshot, lock_stop, active_take_profit)

                if sl_update.get("success"):
                    updated_stop_loss = parse_float_allow_zero(sl_update.get("stop_loss"))
                    if updated_stop_loss is not None:
                        active_stop_loss = updated_stop_loss
                    updated_take_profit = parse_optional_float(sl_update.get("take_profit"))
                    if updated_take_profit is not None:
                        active_take_profit = updated_take_profit
                    last_sl_update = cycle_label
                    trailing_sl_moved = True
                    note_parts.append(f"SL -> {format_number(active_stop_loss)}")
                    append_jsonl(
                        log_path,
                        {
                            "event": "TRAIL_SL_UPDATED",
                            "cycle_time": cycle_label,
                            "key": snapshot.key,
                            "symbol": snapshot.symbol,
                            "side": snapshot.trade_type,
                            "mode": "LOCK_20PCT_1R",
                            "new_stop_loss": active_stop_loss,
                            "risk_distance": risk_distance,
                            "risk_usd": risk_usd,
                            "current_r": current_r,
                            "unrealized_pnl": pnl_usd,
                            "dry_run": dry_run,
                        },
                    )
                    push_runtime_event(runtime_state, "TRAIL_SL", f"{snapshot.symbol} SL -> {format_number(active_stop_loss)}")
                else:
                    trailing_sl_failed = True
                    trailing_error = str(sl_update.get("error") or "trail update failed")
                    note_parts.append(f"trail error: {trailing_error}")
                    append_jsonl(
                        log_path,
                        {
                            "event": "TRAIL_SL_UPDATE_FAILED",
                            "cycle_time": cycle_label,
                            "key": snapshot.key,
                            "symbol": snapshot.symbol,
                            "side": snapshot.trade_type,
                            "mode": "LOCK_20PCT_1R",
                            "attempted_stop_loss": lock_stop,
                            "error": trailing_error,
                            "dry_run": dry_run,
                        },
                    )

        if trail_active and risk_distance is not None and risk_distance > 0 and best_mark_price is not None:
            dynamic_stop = compute_trailing_stop_from_best(
                snapshot.trade_type,
                best_mark_price,
                risk_distance,
                trail_gap_r,
            )
            lock_floor = compute_locked_stop_loss(
                snapshot.trade_type,
                managed_position.reference_entry_price,
                risk_distance,
                trail_lock_r,
            )
            dynamic_stop = best_stop_loss(snapshot.trade_type, dynamic_stop, lock_floor)
            dynamic_stop = clamp_stop_loss_against_mark(snapshot.trade_type, dynamic_stop, snapshot.mark_price, risk_distance)

            if dynamic_stop is not None and is_improved_stop_loss(snapshot.trade_type, dynamic_stop, active_stop_loss):
                if dry_run:
                    sl_update = {
                        "success": True,
                        "stop_loss": dynamic_stop,
                        "take_profit": active_take_profit,
                        "result": {"dry_run": True},
                    }
                else:
                    sl_update = apply_position_stop_loss(exchange, snapshot, dynamic_stop, active_take_profit)

                if sl_update.get("success"):
                    updated_stop_loss = parse_float_allow_zero(sl_update.get("stop_loss"))
                    if updated_stop_loss is not None:
                        active_stop_loss = updated_stop_loss
                    updated_take_profit = parse_optional_float(sl_update.get("take_profit"))
                    if updated_take_profit is not None:
                        active_take_profit = updated_take_profit
                    last_sl_update = cycle_label
                    trailing_sl_moved = True
                    note_parts.append(f"SL trail -> {format_number(active_stop_loss)}")
                    append_jsonl(
                        log_path,
                        {
                            "event": "TRAIL_SL_UPDATED",
                            "cycle_time": cycle_label,
                            "key": snapshot.key,
                            "symbol": snapshot.symbol,
                            "side": snapshot.trade_type,
                            "mode": "DYNAMIC_TRAIL",
                            "new_stop_loss": active_stop_loss,
                            "best_mark_price": best_mark_price,
                            "risk_distance": risk_distance,
                            "risk_usd": risk_usd,
                            "current_r": current_r,
                            "unrealized_pnl": pnl_usd,
                            "dry_run": dry_run,
                        },
                    )
                    push_runtime_event(runtime_state, "TRAIL_SL", f"{snapshot.symbol} SL trail -> {format_number(active_stop_loss)}")
                else:
                    trailing_sl_failed = True
                    trailing_error = str(sl_update.get("error") or "dynamic trail update failed")
                    note_parts.append(f"trail error: {trailing_error}")
                    append_jsonl(
                        log_path,
                        {
                            "event": "TRAIL_SL_UPDATE_FAILED",
                            "cycle_time": cycle_label,
                            "key": snapshot.key,
                            "symbol": snapshot.symbol,
                            "side": snapshot.trade_type,
                            "mode": "DYNAMIC_TRAIL",
                            "attempted_stop_loss": dynamic_stop,
                            "error": trailing_error,
                            "dry_run": dry_run,
                        },
                    )

        if tp_ratchet_enabled and tp_ratchet_max_steps > 0 and tp_ratchet_steps < tp_ratchet_max_steps:
            current_tp_distance = compute_target_distance(
                snapshot.trade_type,
                managed_position.reference_entry_price,
                active_take_profit,
            )
            original_tp_distance = compute_target_distance(
                snapshot.trade_type,
                managed_position.reference_entry_price,
                original_take_profit,
            )
            best_tp_distance = compute_target_distance(
                snapshot.trade_type,
                managed_position.reference_entry_price,
                best_mark_price,
            )

            if (
                current_tp_distance is not None
                and original_tp_distance is not None
                and best_tp_distance is not None
                and best_tp_distance >= current_tp_distance * tp_ratchet_trigger_pct
            ):
                ratchet_stop = compute_price_at_distance(
                    snapshot.trade_type,
                    managed_position.reference_entry_price,
                    current_tp_distance * tp_ratchet_lock_pct,
                )
                ratchet_take_profit = compute_price_at_distance(
                    snapshot.trade_type,
                    managed_position.reference_entry_price,
                    current_tp_distance + (original_tp_distance * tp_ratchet_extend_pct),
                )
                ratchet_stop = best_stop_loss(snapshot.trade_type, ratchet_stop, active_stop_loss)
                ratchet_stop = clamp_stop_loss_against_mark(
                    snapshot.trade_type,
                    ratchet_stop,
                    snapshot.mark_price,
                    risk_distance,
                )

                if is_improved_stop_loss(snapshot.trade_type, ratchet_stop, active_stop_loss) or is_improved_take_profit(
                    snapshot.trade_type,
                    ratchet_take_profit,
                    active_take_profit,
                ):
                    if dry_run:
                        tp_update = {
                            "success": True,
                            "stop_loss": ratchet_stop,
                            "take_profit": ratchet_take_profit,
                            "result": {"dry_run": True},
                        }
                    else:
                        if ratchet_stop is None:
                            tp_ratchet_failed = True
                            tp_ratchet_error = "tp ratchet missing stop loss"
                            tp_update = {"success": False, "error": tp_ratchet_error}
                        else:
                            tp_update = apply_position_stop_loss(
                                exchange,
                                snapshot,
                                ratchet_stop,
                                ratchet_take_profit,
                            )

                    if tp_update.get("success"):
                        updated_stop_loss = parse_float_allow_zero(tp_update.get("stop_loss"))
                        if updated_stop_loss is not None:
                            active_stop_loss = updated_stop_loss
                        updated_take_profit = parse_optional_float(tp_update.get("take_profit"))
                        if updated_take_profit is not None:
                            active_take_profit = updated_take_profit
                        tp_ratchet_steps += 1
                        last_sl_update = cycle_label
                        last_tp_update = cycle_label
                        tp_ratchet_moved = True
                        note_parts.append(
                            f"TP ratchet {tp_ratchet_steps}/{tp_ratchet_max_steps}: "
                            f"SL {format_number(active_stop_loss)} | TP {format_number(active_take_profit)}"
                        )
                        append_jsonl(
                            log_path,
                            {
                                "event": "TP_RATCHET_UPDATED",
                                "cycle_time": cycle_label,
                                "key": snapshot.key,
                                "symbol": snapshot.symbol,
                                "side": snapshot.trade_type,
                                "step": tp_ratchet_steps,
                                "max_steps": tp_ratchet_max_steps,
                                "trigger_pct": tp_ratchet_trigger_pct,
                                "lock_pct": tp_ratchet_lock_pct,
                                "extend_pct": tp_ratchet_extend_pct,
                                "best_mark_price": best_mark_price,
                                "new_stop_loss": active_stop_loss,
                                "new_take_profit": active_take_profit,
                                "current_tp_distance": current_tp_distance,
                                "original_tp_distance": original_tp_distance,
                                "current_r": current_r,
                                "risk_usd": risk_usd,
                                "dry_run": dry_run,
                            },
                        )
                        push_runtime_event(
                            runtime_state,
                            "TP_RATCHET",
                            f"{snapshot.symbol} step {tp_ratchet_steps} -> TP {format_number(active_take_profit)}",
                        )
                    else:
                        tp_ratchet_failed = True
                        tp_ratchet_error = str(tp_update.get("error") or "tp ratchet update failed")
                        note_parts.append(f"tp ratchet error: {tp_ratchet_error}")
                        append_jsonl(
                            log_path,
                            {
                                "event": "TP_RATCHET_UPDATE_FAILED",
                                "cycle_time": cycle_label,
                                "key": snapshot.key,
                                "symbol": snapshot.symbol,
                                "side": snapshot.trade_type,
                                "step": tp_ratchet_steps,
                                "max_steps": tp_ratchet_max_steps,
                                "trigger_pct": tp_ratchet_trigger_pct,
                                "lock_pct": tp_ratchet_lock_pct,
                                "extend_pct": tp_ratchet_extend_pct,
                                "attempted_stop_loss": ratchet_stop,
                                "attempted_take_profit": ratchet_take_profit,
                                "error": tp_ratchet_error,
                                "dry_run": dry_run,
                            },
                        )

        view.stop_loss = active_stop_loss
        view.take_profit = active_take_profit
        view.trail_active = trail_active
        view.trail_stop_loss = active_stop_loss
        view.best_mark_price = best_mark_price
        view.tp_ratchet_steps = tp_ratchet_steps
        view.last_tp_update = last_tp_update

        state_entry.update(
            {
                "stop_loss": active_stop_loss,
                "take_profit": active_take_profit,
                "last_seen_at": cycle_label,
                "original_stop_loss": original_stop_loss,
                "original_take_profit": original_take_profit,
                "risk_distance": risk_distance,
                "trail_active": trail_active,
                "best_mark_price": best_mark_price,
                "trail_stop_loss": active_stop_loss,
                "last_sl_update": last_sl_update,
                "tp_ratchet_steps": tp_ratchet_steps,
                "last_tp_update": last_tp_update,
            }
        )
        state_positions[snapshot.key] = state_entry

        trigger_reason = ""
        if trail_close_non_positive and trail_active and current_r <= 0:
            trigger_reason = "TRAIL_ZERO_R_CLOSE"
            note_parts.append(f"trail active and r {format_r_multiple(current_r)} <= +0.00R")
        elif current_r <= -loss_limit_r:
            trigger_reason = "R_LOSS_LIMIT"
            note_parts.append(f"r {format_r_multiple(current_r)} <= -{loss_limit_r:.2f}R")

        if not trigger_reason:
            if protection_repair_failed:
                view.last_reason = "PROTECTION_REPAIR_FAILED"
            elif trailing_sl_failed:
                view.last_reason = "TRAIL_SL_FAILED"
            elif protection_repaired:
                view.last_reason = "PROTECTION_REPAIRED"
            elif tp_ratchet_failed:
                view.last_reason = "TP_RATCHET_FAILED"
            elif tp_ratchet_moved:
                view.last_reason = "TP_RATCHET_MOVED"
            elif trailing_sl_moved:
                view.last_reason = "TRAIL_SL_MOVED"
            elif trail_active:
                view.last_reason = "TRAILING"
            else:
                view.last_reason = "HOLD"
            if not note_parts:
                note_parts.append(f"pnl {format_number(pnl_usd)} USD | r {format_r_multiple(current_r)}")
            view.note = " | ".join(note_parts)
            continue

        if trigger_reason not in MANAGED_EXIT_REASONS:
            view.last_reason = "SKIPPED"
            view.note = " | ".join(note_parts) if note_parts else "unsupported trigger"
            continue

        cycle_stats["triggered"] += 1
        view.last_reason = trigger_reason
        view.note = " | ".join(note_parts) if note_parts else view.note

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
            "stop_loss": active_stop_loss,
            "take_profit": active_take_profit,
            "anchor_time": timestamp_to_string(managed_position.anchor_time),
            "managed_reason": trigger_reason,
            "unrealized_pnl": pnl_usd,
            "current_r": current_r,
            "risk_usd": risk_usd,
            "loss_limit_r": loss_limit_r,
            "trail_active": trail_active,
            "trail_activate_r": trail_activate_r,
            "trail_lock_r": trail_lock_r,
            "trail_gap_r": trail_gap_r,
            "best_mark_price": best_mark_price,
            "risk_distance": risk_distance,
            "original_stop_loss": original_stop_loss,
            "original_take_profit": original_take_profit,
            "repair_missing_protection": repair_missing_protection,
            "repair_risk_usd": repair_risk_usd,
            "repair_rr": repair_rr,
            "repair_lock_entry_on_profit": repair_lock_entry_on_profit,
            "tp_ratchet_enabled": tp_ratchet_enabled,
            "tp_ratchet_trigger_pct": tp_ratchet_trigger_pct,
            "tp_ratchet_lock_pct": tp_ratchet_lock_pct,
            "tp_ratchet_extend_pct": tp_ratchet_extend_pct,
            "tp_ratchet_max_steps": tp_ratchet_max_steps,
            "tp_ratchet_steps": tp_ratchet_steps,
            "related_order_ids": [extract_order_id(order) for order in orders_to_cancel if extract_order_id(order)],
            "dry_run": dry_run,
        }

        if dry_run:
            view.last_reason = f"DRY_{trigger_reason}"
            view.note = "would cancel TP/SL and close market"
            append_jsonl(log_path, event_payload)
            push_runtime_event(
                runtime_state,
                "DRY_TRIGGER",
                f"{snapshot.symbol} {trigger_reason} -> would close {format_number(snapshot.size, quantity=True)}",
            )
            continue

        cancel_results = cancel_orders_for_position(exchange, snapshot, orders_to_cancel)
        close_result = close_position_market(exchange, snapshot)
        event_payload["cancel_results"] = cancel_results
        event_payload["close_result"] = close_result
        append_jsonl(log_path, event_payload)

        if close_result.get("success"):
            cycle_stats["closed"] += 1
            view.last_reason = f"CLOSED_{trigger_reason}"
            view.note = "position closed by manager"
            state_positions.pop(snapshot.key, None)
            push_runtime_event(
                runtime_state,
                "POSITION_CLOSED",
                f"{snapshot.symbol} closed by manager ({trigger_reason})",
            )
        else:
            view.last_reason = f"FAILED_{trigger_reason}"
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
        runtime_state.status = "Waiting for next cycle"

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
    print(
        f"Rules         : close <= -{float(args.loss_limit_r):.2f}R"
    )
    print(
        f"Trailing      : on >= {float(args.trail_activate_r):.2f}R, "
        f"lock {float(args.trail_lock_r):.2f}R, gap {float(args.trail_gap_r):.2f}R, "
        f"close<=0R={bool(args.trail_close_non_positive)}"
    )
    print(
        f"Protection    : repair={bool(args.repair_missing_protection)}, "
        f"risk=${float(args.repair_risk_usd):.2f}, rr={float(args.repair_rr):.2f}, "
        f"lockBE={bool(args.repair_lock_entry_on_profit)}"
    )
    print(
        "TP ratchet    : "
        f"enabled={bool(args.tp_ratchet_enabled)}, "
        f"trigger={format_percent(normalize_fraction(float(args.tp_ratchet_trigger_pct), DEFAULT_TP_RATCHET_TRIGGER_PCT))}, "
        f"lock={format_percent(normalize_fraction(float(args.tp_ratchet_lock_pct), DEFAULT_TP_RATCHET_LOCK_PCT))}, "
        f"extend=+{format_percent(normalize_fraction(float(args.tp_ratchet_extend_pct), DEFAULT_TP_RATCHET_EXTEND_PCT))}, "
        f"steps={int(args.tp_ratchet_max_steps)}"
    )

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
                loss_limit_r=float(args.loss_limit_r),
                trail_activate_r=float(args.trail_activate_r),
                trail_lock_r=float(args.trail_lock_r),
                trail_gap_r=float(args.trail_gap_r),
                repair_missing_protection=bool(args.repair_missing_protection),
                repair_risk_usd=float(args.repair_risk_usd),
                repair_rr=float(args.repair_rr),
                repair_lock_entry_on_profit=bool(args.repair_lock_entry_on_profit),
                tp_ratchet_enabled=bool(args.tp_ratchet_enabled),
                tp_ratchet_trigger_pct=float(args.tp_ratchet_trigger_pct),
                tp_ratchet_lock_pct=float(args.tp_ratchet_lock_pct),
                tp_ratchet_extend_pct=float(args.tp_ratchet_extend_pct),
                tp_ratchet_max_steps=int(args.tp_ratchet_max_steps),
                trail_close_non_positive=bool(args.trail_close_non_positive),
                limit_per_request=int(args.limit_per_request),
                max_batches=int(args.max_batches),
            )
            return

        runtime_state = RuntimeViewState(
            dry_run=bool(args.dry_run),
            loss_limit_r=float(args.loss_limit_r),
            trail_activate_r=float(args.trail_activate_r),
            trail_lock_r=float(args.trail_lock_r),
            trail_gap_r=float(args.trail_gap_r),
            repair_missing_protection=bool(args.repair_missing_protection),
            repair_risk_usd=float(args.repair_risk_usd),
            repair_rr=float(args.repair_rr),
            repair_lock_entry_on_profit=bool(args.repair_lock_entry_on_profit),
            tp_ratchet_enabled=bool(args.tp_ratchet_enabled),
            tp_ratchet_trigger_pct=float(args.tp_ratchet_trigger_pct),
            tp_ratchet_lock_pct=float(args.tp_ratchet_lock_pct),
            tp_ratchet_extend_pct=float(args.tp_ratchet_extend_pct),
            tp_ratchet_max_steps=int(args.tp_ratchet_max_steps),
            trail_close_non_positive=bool(args.trail_close_non_positive),
        )
        runtime_state.status = "Waiting for next cycle"
        push_runtime_event(runtime_state, "STARTED", "Manager started (2s interval)")

        cycle_interval = 2.0  # seconds between cycles

        with Live(render_dashboard(runtime_state, cfg), refresh_per_second=4, screen=True) as live:
            while True:
                target = datetime.now(timezone.utc) + timedelta(seconds=cycle_interval)
                runtime_state.next_run_at = target
                runtime_state.status = "Waiting for next cycle"

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
                        loss_limit_r=float(args.loss_limit_r),
                        trail_activate_r=float(args.trail_activate_r),
                        trail_lock_r=float(args.trail_lock_r),
                        trail_gap_r=float(args.trail_gap_r),
                        repair_missing_protection=bool(args.repair_missing_protection),
                        repair_risk_usd=float(args.repair_risk_usd),
                        repair_rr=float(args.repair_rr),
                        repair_lock_entry_on_profit=bool(args.repair_lock_entry_on_profit),
                        tp_ratchet_enabled=bool(args.tp_ratchet_enabled),
                        tp_ratchet_trigger_pct=float(args.tp_ratchet_trigger_pct),
                        tp_ratchet_lock_pct=float(args.tp_ratchet_lock_pct),
                        tp_ratchet_extend_pct=float(args.tp_ratchet_extend_pct),
                        tp_ratchet_max_steps=int(args.tp_ratchet_max_steps),
                        trail_close_non_positive=bool(args.trail_close_non_positive),
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