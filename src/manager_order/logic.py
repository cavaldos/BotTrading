"""Reusable order manager replay logic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


REQUIRED_TRADE_COLUMNS = {
    "Symbol",
    "Type",
    "Entry_Time",
    "Entry_Price",
    "Stop_Loss",
    "Take_Profit",
    "Exit_Time",
    "Exit_Price",
    "Result",
}


@dataclass
class ManagerConfig:
    one_r_trigger: float = 1.0
    sideway_window: int = 20
    sideway_max_range_r: float = 1.2
    sideway_touch_tolerance: float = 0.15
    sideway_min_touches_per_side: int = 2
    sideway_min_switches: int = 3
    sideway_min_bars_after_entry: int = 8


@dataclass
class CandleData:
    times_ns: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray

    def is_empty(self) -> bool:
        return self.times_ns.size == 0


@dataclass(frozen=True)
class ManagedTradeResult:
    exit_time: Optional[pd.Timestamp]
    exit_price: Optional[float]
    result: str
    reason: str
    one_r_reached: bool = False


def empty_candle_data() -> CandleData:
    return CandleData(
        times_ns=np.array([], dtype=np.int64),
        opens=np.array([], dtype=np.float64),
        highs=np.array([], dtype=np.float64),
        lows=np.array([], dtype=np.float64),
        closes=np.array([], dtype=np.float64),
    )


def parse_timestamp(value: Any) -> Optional[pd.Timestamp]:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is not None:
            return ts.tz_localize(None)
        return ts
    return None


def to_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(parsed):
        return None
    return parsed


def normalize_result(value: Any) -> str:
    raw = str(value).strip().upper()
    if raw == "WIN":
        return "Win"
    if raw == "LOSS":
        return "Loss"
    return "Open"


def extract_base_symbol(symbol: str) -> str:
    cleaned = str(symbol).strip().upper()
    if "_" in cleaned:
        return cleaned.split("_", 1)[0]
    if "/" in cleaned:
        return cleaned.split("/", 1)[0]
    return cleaned


def format_timestamp(value: Any) -> str:
    ts = parse_timestamp(value)
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def load_candle_data(data_file: Path) -> CandleData:
    df = pd.read_csv(data_file, usecols=("Time", "Open", "High", "Low", "Close"))
    df.columns = [str(col).strip() for col in df.columns]

    df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce").dt.tz_localize(None)
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Time", "Open", "High", "Low", "Close"]).sort_values("Time")
    df = df.reset_index(drop=True)

    return CandleData(
        times_ns=df["Time"].astype("int64").to_numpy(),
        opens=df["Open"].to_numpy(dtype=np.float64),
        highs=df["High"].to_numpy(dtype=np.float64),
        lows=df["Low"].to_numpy(dtype=np.float64),
        closes=df["Close"].to_numpy(dtype=np.float64),
    )


def sideway_detected(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    risk_r: float,
    cfg: ManagerConfig,
) -> bool:
    box_high = float(np.max(highs))
    box_low = float(np.min(lows))
    box_range = box_high - box_low

    if box_range <= 0:
        return False

    if risk_r > 0 and box_range > (cfg.sideway_max_range_r * risk_r):
        return False

    touch_band = max(box_range * cfg.sideway_touch_tolerance, 1e-12)
    upper_touches = int(np.sum(highs >= (box_high - touch_band)))
    lower_touches = int(np.sum(lows <= (box_low + touch_band)))

    if upper_touches < cfg.sideway_min_touches_per_side:
        return False
    if lower_touches < cfg.sideway_min_touches_per_side:
        return False

    mid = (box_high + box_low) / 2.0
    states = np.where(closes >= mid, 1, -1)
    switches = int(np.sum(states[1:] != states[:-1]))
    return switches >= cfg.sideway_min_switches


def result_from_exit(trade_type: str, entry_price: float, exit_price: float) -> str:
    if abs(exit_price - entry_price) <= 1e-12:
        return "Open"
    if trade_type == "LONG":
        return "Win" if exit_price > entry_price else "Loss"
    if trade_type == "SHORT":
        return "Win" if exit_price < entry_price else "Loss"
    return "Open"


def calc_r_multiple(
    trade_type: str,
    entry_price: float,
    stop_loss: float,
    exit_price: float,
) -> Optional[float]:
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    if trade_type == "LONG":
        return (exit_price - entry_price) / risk
    if trade_type == "SHORT":
        return (entry_price - exit_price) / risk
    return None


def _result(
    exit_time: Optional[pd.Timestamp],
    exit_price: Optional[float],
    result: str,
    reason: str,
    one_r_reached: bool = False,
) -> ManagedTradeResult:
    return ManagedTradeResult(
        exit_time=exit_time,
        exit_price=exit_price,
        result=result,
        reason=reason,
        one_r_reached=one_r_reached,
    )


def _fallback_to_original(
    original_exit_time: Optional[pd.Timestamp],
    original_exit_price: Optional[float],
    original_result: str,
    reason: str,
) -> ManagedTradeResult:
    return _result(
        exit_time=original_exit_time,
        exit_price=original_exit_price,
        result=original_result,
        reason=reason,
        one_r_reached=False,
    )


def replay_trade(row: pd.Series, candles: CandleData, cfg: ManagerConfig) -> ManagedTradeResult:
    trade_type = str(row.get("Type", "")).strip().upper()
    entry_price = to_float(row.get("Entry_Price"))
    stop_loss = to_float(row.get("Stop_Loss"))
    take_profit = to_float(row.get("Take_Profit"))

    entry_time = parse_timestamp(row.get("Entry_Time"))
    original_exit_time = parse_timestamp(row.get("Exit_Time"))
    original_exit_price = to_float(row.get("Exit_Price"))
    original_result = normalize_result(row.get("Result"))

    if trade_type not in {"LONG", "SHORT"}:
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "INVALID_TRADE_TYPE")

    if entry_price is None or stop_loss is None or take_profit is None or entry_time is None:
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "INVALID_TRADE_DATA")

    risk_r = abs(entry_price - stop_loss)
    if risk_r <= 0:
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "INVALID_RISK")

    if candles.is_empty():
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "NO_CANDLE_DATA")

    times_ns = candles.times_ns
    n = len(times_ns)
    entry_ns = np.int64(entry_time.value)
    entry_idx = int(np.searchsorted(times_ns, entry_ns, side="left"))

    if entry_idx >= n:
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "ENTRY_NOT_IN_CANDLES")

    start_idx = entry_idx + 1
    if start_idx >= n:
        fallback_price = float(candles.closes[entry_idx])
        return _result(
            exit_time=pd.Timestamp(times_ns[entry_idx], unit="ns"),
            exit_price=fallback_price,
            result=result_from_exit(trade_type, entry_price, fallback_price),
            reason="NO_FUTURE_CANDLES",
        )

    if original_exit_time is not None:
        exit_ns = np.int64(original_exit_time.value)
        end_idx = int(np.searchsorted(times_ns, exit_ns, side="right") - 1)
        end_idx = min(end_idx, n - 1)
    else:
        end_idx = n - 1

    if end_idx < start_idx:
        return _fallback_to_original(original_exit_time, original_exit_price, original_result, "EXIT_BEFORE_ENTRY")

    highs = candles.highs
    lows = candles.lows
    opens = candles.opens
    closes = candles.closes
    is_long = trade_type == "LONG"

    one_r_price = entry_price + (cfg.one_r_trigger * risk_r if is_long else -cfg.one_r_trigger * risk_r)
    reached_one_r = False

    for i in range(start_idx, end_idx + 1):
        high = float(highs[i])
        low = float(lows[i])
        close = float(closes[i])
        open_price = float(opens[i])

        if is_long:
            if low <= stop_loss:
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=stop_loss,
                    result="Loss",
                    reason="SL",
                    one_r_reached=reached_one_r,
                )
            if high >= take_profit:
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=take_profit,
                    result="Win",
                    reason="TP",
                    one_r_reached=reached_one_r,
                )

            if high >= one_r_price:
                reached_one_r = True

            if reached_one_r and i > start_idx and close < open_price and low < float(lows[i - 1]):
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=close,
                    result=result_from_exit(trade_type, entry_price, close),
                    reason="R1_SWEEP",
                    one_r_reached=True,
                )
        else:
            if high >= stop_loss:
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=stop_loss,
                    result="Loss",
                    reason="SL",
                    one_r_reached=reached_one_r,
                )
            if low <= take_profit:
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=take_profit,
                    result="Win",
                    reason="TP",
                    one_r_reached=reached_one_r,
                )

            if low <= one_r_price:
                reached_one_r = True

            if reached_one_r and i > start_idx and close > open_price and high > float(highs[i - 1]):
                return _result(
                    exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                    exit_price=close,
                    result=result_from_exit(trade_type, entry_price, close),
                    reason="R1_SWEEP",
                    one_r_reached=True,
                )

        bars_since_entry = i - start_idx + 1
        if bars_since_entry < cfg.sideway_min_bars_after_entry:
            continue
        if bars_since_entry < cfg.sideway_window:
            continue

        window_start = i - cfg.sideway_window + 1
        if sideway_detected(
            highs=highs[window_start : i + 1],
            lows=lows[window_start : i + 1],
            closes=closes[window_start : i + 1],
            risk_r=risk_r,
            cfg=cfg,
        ):
            return _result(
                exit_time=pd.Timestamp(times_ns[i], unit="ns"),
                exit_price=close,
                result=result_from_exit(trade_type, entry_price, close),
                reason="SIDEWAY",
                one_r_reached=reached_one_r,
            )

    if original_exit_time is not None and original_exit_price is not None:
        return _result(
            exit_time=original_exit_time,
            exit_price=original_exit_price,
            result=original_result,
            reason="ORIGINAL_EXIT",
            one_r_reached=reached_one_r,
        )

    fallback_close = float(closes[end_idx])
    return _result(
        exit_time=pd.Timestamp(times_ns[end_idx], unit="ns"),
        exit_price=fallback_close,
        result=result_from_exit(trade_type, entry_price, fallback_close),
        reason="NO_TRIGGER",
        one_r_reached=reached_one_r,
    )


def build_managed_record(row: pd.Series, managed: ManagedTradeResult) -> Dict[str, Any]:
    base_symbol = extract_base_symbol(str(row.get("Symbol", "")))
    trade_type = str(row.get("Type", "")).strip().upper()
    entry_price = to_float(row.get("Entry_Price"))
    stop_loss = to_float(row.get("Stop_Loss"))
    original_exit_price = to_float(row.get("Exit_Price"))
    managed_exit_price = to_float(managed.exit_price)

    original_r = None
    managed_r = None
    if entry_price is not None and stop_loss is not None and original_exit_price is not None:
        original_r = calc_r_multiple(trade_type, entry_price, stop_loss, original_exit_price)
    if entry_price is not None and stop_loss is not None and managed_exit_price is not None:
        managed_r = calc_r_multiple(trade_type, entry_price, stop_loss, managed_exit_price)

    return {
        "Base_Symbol": base_symbol,
        "Managed_Exit_Time": format_timestamp(managed.exit_time),
        "Managed_Exit_Price": managed_exit_price,
        "Managed_Result": managed.result,
        "Managed_Reason": managed.reason,
        "Managed_R": managed_r,
        "Original_R": original_r,
        "R_Delta": (managed_r - original_r) if managed_r is not None and original_r is not None else None,
        "OneR_Reached": bool(managed.one_r_reached),
    }


def win_rate(series: pd.Series) -> float:
    closed = series[series.isin(["Win", "Loss"])]
    if len(closed) == 0:
        return 0.0
    wins = int((closed == "Win").sum())
    return (wins / len(closed)) * 100.0


def numeric_mean(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return 0.0
    return float(valid.mean())


def compute_streaks(results: list[str]) -> Tuple[int, int, float, float]:
    longest_win_streak = 0
    longest_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    win_streaks: list[int] = []
    loss_streaks: list[int] = []

    for result in results:
        if result == "WIN":
            current_win_streak += 1
            if current_loss_streak > 0:
                loss_streaks.append(current_loss_streak)
                current_loss_streak = 0
            longest_win_streak = max(longest_win_streak, current_win_streak)
        elif result == "LOSS":
            if current_win_streak > 0:
                win_streaks.append(current_win_streak)
                current_win_streak = 0
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        else:
            if current_win_streak > 0:
                win_streaks.append(current_win_streak)
                current_win_streak = 0
            if current_loss_streak > 0:
                loss_streaks.append(current_loss_streak)
                current_loss_streak = 0

    if current_win_streak > 0:
        win_streaks.append(current_win_streak)
    if current_loss_streak > 0:
        loss_streaks.append(current_loss_streak)

    avg_win_streak = (sum(win_streaks) / len(win_streaks)) if win_streaks else 0.0
    avg_loss_streak = (sum(loss_streaks) / len(loss_streaks)) if loss_streaks else 0.0
    return longest_win_streak, longest_loss_streak, avg_win_streak, avg_loss_streak


def compute_trade_results_row(df: pd.DataFrame, symbol: str, sl_multiplier: float) -> Dict[str, Any]:
    managed_results = df["Managed_Result"].map(normalize_result)
    managed_upper = managed_results.str.upper()

    wins = int((managed_upper == "WIN").sum())
    losses = int((managed_upper == "LOSS").sum())
    open_trades = int((managed_upper == "OPEN").sum())
    total_trades = int(len(df))
    closed_trades = wins + losses
    win_rate_value = round((wins / closed_trades * 100) if closed_trades > 0 else 0.0, 2)

    entry_prices = pd.to_numeric(df["Entry_Price"], errors="coerce")
    stop_losses = pd.to_numeric(df["Stop_Loss"], errors="coerce")
    entry_safe = entry_prices.where(entry_prices != 0)
    sl_percent = ((entry_prices - stop_losses).abs() / entry_safe) * 100.0
    avg_sl_percent = round(numeric_mean(sl_percent), 2)

    rr_source = df["RR"] if "RR" in df.columns else pd.Series(np.nan, index=df.index, dtype=np.float64)
    rr_values = pd.to_numeric(rr_source, errors="coerce")
    avg_rr = round(numeric_mean(rr_values), 2)
    win_rr_sum = float(rr_values.where(managed_upper == "WIN", 0.0).sum(skipna=True))
    roe = round(win_rr_sum - losses, 2)

    streak_results = [str(x).upper() for x in managed_results.tolist()]
    longest_win_streak, longest_loss_streak, avg_win_streak, avg_loss_streak = compute_streaks(streak_results)

    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "open_trades": open_trades,
        "win_rate": win_rate_value,
        "avg_sl_percent": avg_sl_percent,
        "avg_rr": avg_rr,
        "sl_multiplier": sl_multiplier,
        "roe": roe,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "avg_win_streak": round(avg_win_streak, 2),
        "avg_loss_streak": round(avg_loss_streak, 2),
    }


def compute_managed_summary(df: pd.DataFrame, file_name: str) -> Dict[str, Any]:
    reason_counts = df["Managed_Reason"].value_counts().to_dict()
    return {
        "file": file_name,
        "total_trades": int(len(df)),
        "managed_r1_sweep": int(reason_counts.get("R1_SWEEP", 0)),
        "managed_sideway": int(reason_counts.get("SIDEWAY", 0)),
        "managed_total": int(reason_counts.get("R1_SWEEP", 0) + reason_counts.get("SIDEWAY", 0)),
        "win_rate_original": round(win_rate(df["Result"].map(normalize_result)), 2),
        "win_rate_managed": round(win_rate(df["Managed_Result"].map(normalize_result)), 2),
        "avg_original_r": round(numeric_mean(df["Original_R"]), 4),
        "avg_managed_r": round(numeric_mean(df["Managed_R"]), 4),
    }
