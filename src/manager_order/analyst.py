"""Analyze first-candle behavior after entry and simulate quick-management rules."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np
import pandas as pd

from src.config import load_backtest_config
from src.manager_order.logic import (
    CandleData,
    REQUIRED_TRADE_COLUMNS,
    calc_r_multiple,
    extract_base_symbol,
    load_candle_data,
    normalize_result,
    parse_timestamp,
    result_from_exit,
    to_float,
)


TIMEFRAME_ORDER = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]


@dataclass(frozen=True)
class AnalystConfig:
    near_tp_thresholds: Tuple[float, ...] = (0.7, 0.8, 0.9)


def parse_args() -> argparse.Namespace:
    backtest_cfg = load_backtest_config(include_strategy=False)
    default_data_folder = str(backtest_cfg.get("folder_path", "res/data"))
    default_output_folder = str(backtest_cfg.get("output_folder", "res/output"))

    parser = argparse.ArgumentParser(description="Analyze entry behavior and quick exit rules")
    parser.add_argument("--output-folder", default=default_output_folder, help="Folder containing *_trades.csv")
    parser.add_argument("--data-folder", default=default_data_folder, help="Folder containing <SYMBOL>_1m.csv")
    parser.add_argument(
        "--result-folder",
        default="res/output_managed/analyst",
        help="Output folder for analyst reports",
    )
    parser.add_argument(
        "--near-tp-thresholds",
        default="0.7,0.8,0.9",
        help="Comma-separated thresholds to define near-TP (e.g. 0.7,0.8,0.9)",
    )
    return parser.parse_args()


def list_trade_files(output_folder: Path) -> List[Path]:
    return sorted(output_folder.glob("*_trades.csv"))


def parse_thresholds(raw: str) -> Tuple[float, ...]:
    values: List[float] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        if value <= 0:
            continue
        values.append(value)

    if not values:
        return (0.7, 0.8, 0.9)
    return tuple(sorted(set(values)))


def load_cached_candles(base_symbol: str, data_folder: Path, candle_cache: Dict[str, CandleData]) -> CandleData:
    candles = candle_cache.get(base_symbol)
    if candles is not None:
        return candles

    data_file = data_folder / f"{base_symbol}_1m.csv"
    if data_file.exists():
        candles = load_candle_data(data_file)
    else:
        candles = CandleData(
            times_ns=np.array([], dtype=np.int64),
            opens=np.array([], dtype=np.float64),
            highs=np.array([], dtype=np.float64),
            lows=np.array([], dtype=np.float64),
            closes=np.array([], dtype=np.float64),
        )
    candle_cache[base_symbol] = candles
    return candles


def _threshold_col_name(threshold: float) -> str:
    pct = int(round(threshold * 100.0))
    return f"near_tp_then_loss_{pct}"


def _safe_pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _safe_mean(series: Any) -> float:
    numeric_values: Any = pd.to_numeric(pd.Series(series), errors="coerce")
    values = np.asarray(numeric_values, dtype=np.float64)
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return 0.0
    return float(valid.mean())


def _safe_win_rate(series: Any) -> float:
    normalized = pd.Series(series)
    closed = normalized[normalized.isin(["Win", "Loss"])]
    if len(closed) == 0:
        return 0.0
    return float((closed == "Win").mean() * 100.0)


def _first_direction(trade_type: str, entry_price: float, first_close: float) -> str:
    if trade_type == "LONG":
        if first_close > entry_price:
            return "with"
        if first_close < entry_price:
            return "against"
        return "flat"

    if trade_type == "SHORT":
        if first_close < entry_price:
            return "with"
        if first_close > entry_price:
            return "against"
        return "flat"

    return "unknown"


def _simulate_close_if_first_against(
    trade_type: str,
    entry_price: float,
    stop_loss: float,
    first_close: float,
) -> Tuple[str, Optional[float]]:
    result = result_from_exit(trade_type, entry_price, first_close)
    if result == "Open":
        return "BE", 0.0
    r_value = calc_r_multiple(trade_type, entry_price, stop_loss, first_close)
    return result, r_value


def _simulate_move_be_if_first_with(
    trade_type: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    original_exit_price: Optional[float],
    original_result: str,
    highs: np.ndarray,
    lows: np.ndarray,
    start_idx: int,
    end_idx: int,
) -> Tuple[str, Optional[float]]:
    if trade_type == "LONG":
        tp_r = calc_r_multiple(trade_type, entry_price, stop_loss, take_profit)
        for i in range(start_idx + 1, end_idx + 1):
            if float(lows[i]) <= entry_price:
                return "BE", 0.0
            if float(highs[i]) >= take_profit:
                return "Win", tp_r
    elif trade_type == "SHORT":
        tp_r = calc_r_multiple(trade_type, entry_price, stop_loss, take_profit)
        for i in range(start_idx + 1, end_idx + 1):
            if float(highs[i]) >= entry_price:
                return "BE", 0.0
            if float(lows[i]) <= take_profit:
                return "Win", tp_r
    else:
        return "NA", None

    if original_exit_price is not None:
        fallback_result = result_from_exit(trade_type, entry_price, original_exit_price)
        if fallback_result == "Open":
            return "BE", 0.0
        fallback_r = calc_r_multiple(trade_type, entry_price, stop_loss, original_exit_price)
        return fallback_result, fallback_r

    normalized = normalize_result(original_result)
    if normalized == "Open":
        return "Open", None
    return normalized, None


def analyze_trade_row(row: pd.Series, candles: CandleData, cfg: AnalystConfig) -> Optional[Dict[str, Any]]:
    trade_type = str(row.get("Type", "")).strip().upper()
    if trade_type not in {"LONG", "SHORT"}:
        return None

    symbol = str(row.get("Symbol", "")).strip().upper()
    timeframe = symbol.split("_")[-1] if "_" in symbol else "unknown"
    base_symbol = extract_base_symbol(symbol)

    entry_time = parse_timestamp(row.get("Entry_Time"))
    exit_time = parse_timestamp(row.get("Exit_Time"))

    entry_price = to_float(row.get("Entry_Price"))
    stop_loss = to_float(row.get("Stop_Loss"))
    take_profit = to_float(row.get("Take_Profit"))
    original_exit_price = to_float(row.get("Exit_Price"))
    original_result = normalize_result(row.get("Result"))

    if entry_time is None or entry_price is None or stop_loss is None or take_profit is None:
        return None
    if candles.is_empty():
        return None

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    entry_ns = np.int64(entry_time.value)
    times_ns = candles.times_ns
    n_candles = len(times_ns)

    if entry_ns > times_ns[-1]:
        return None

    start_idx = int(np.searchsorted(times_ns, entry_ns, side="right"))
    if start_idx >= n_candles:
        return None

    if exit_time is not None:
        exit_ns = np.int64(exit_time.value)
        end_idx = int(np.searchsorted(times_ns, exit_ns, side="right") - 1)
        end_idx = min(end_idx, n_candles - 1)
    else:
        end_idx = n_candles - 1

    if end_idx < start_idx:
        end_idx = start_idx

    highs = candles.highs
    lows = candles.lows
    closes = candles.closes

    first_close = float(closes[start_idx])
    first_dir = _first_direction(trade_type, entry_price, first_close)
    first_positive = first_dir == "with"

    path_high = float(np.max(highs[start_idx : end_idx + 1]))
    path_low = float(np.min(lows[start_idx : end_idx + 1]))

    if trade_type == "LONG":
        moved_with = path_high > entry_price
        moved_against = path_low < entry_price
        tp_progress = (path_high - entry_price) / (take_profit - entry_price) if take_profit > entry_price else np.nan
    else:
        moved_with = path_low < entry_price
        moved_against = path_high > entry_price
        tp_progress = (entry_price - path_low) / (entry_price - take_profit) if take_profit < entry_price else np.nan

    recovered_after_against = bool(first_dir == "against" and moved_with)

    original_r = None
    if original_exit_price is not None:
        original_r = calc_r_multiple(trade_type, entry_price, stop_loss, original_exit_price)

    strategy_a_result = "NA"
    strategy_a_r = None
    if first_dir == "against":
        strategy_a_result, strategy_a_r = _simulate_close_if_first_against(
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            first_close=first_close,
        )

    strategy_b_result = "NA"
    strategy_b_r = None
    if first_dir == "with":
        strategy_b_result, strategy_b_r = _simulate_move_be_if_first_with(
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            original_exit_price=original_exit_price,
            original_result=original_result,
            highs=highs,
            lows=lows,
            start_idx=start_idx,
            end_idx=end_idx,
        )

    if first_dir == "against":
        combined_result = strategy_a_result
        combined_r = strategy_a_r
    elif first_dir == "with":
        combined_result = strategy_b_result
        combined_r = strategy_b_r
    else:
        combined_result = original_result
        combined_r = original_r

    record: Dict[str, Any] = {
        "symbol": symbol,
        "base_symbol": base_symbol,
        "timeframe": timeframe,
        "type": trade_type,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "original_result": original_result,
        "original_r": original_r,
        "first_dir": first_dir,
        "first_positive": first_positive,
        "moved_with": moved_with,
        "moved_against": moved_against,
        "recovered_after_against": recovered_after_against,
        "tp_progress_ratio": tp_progress,
        "strategy_a_result": strategy_a_result,
        "strategy_a_r": strategy_a_r,
        "strategy_b_result": strategy_b_result,
        "strategy_b_r": strategy_b_r,
        "combined_result": combined_result,
        "combined_r": combined_r,
    }

    for threshold in cfg.near_tp_thresholds:
        col = _threshold_col_name(threshold)
        near_hit = bool(
            original_result == "Loss"
            and not np.isnan(tp_progress)
            and float(tp_progress) >= threshold
        )
        record[col] = near_hit

    return record


def process_trade_file(
    trade_file: Path,
    data_folder: Path,
    cfg: AnalystConfig,
    candle_cache: Dict[str, CandleData],
) -> List[Dict[str, Any]]:
    df = pd.read_csv(trade_file)
    if df.empty:
        return []

    missing_columns = sorted(REQUIRED_TRADE_COLUMNS.difference(set(df.columns)))
    if missing_columns:
        joined = ", ".join(missing_columns)
        print(f"[SKIP] {trade_file.name}: missing required columns: {joined}")
        return []

    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        base_symbol = extract_base_symbol(str(row.get("Symbol", "")))
        candles = load_cached_candles(base_symbol, data_folder, candle_cache)
        analyzed = analyze_trade_row(row=row, candles=candles, cfg=cfg)
        if analyzed is not None:
            records.append(analyzed)

    return records


def _count_values(series: Any, value: str) -> int:
    normalized = pd.Series(series)
    return int((normalized == value).sum())


def summarize_scope(df: pd.DataFrame, scope: str, cfg: AnalystConfig) -> Dict[str, Any]:
    total = int(len(df))
    first_with = int((df["first_dir"] == "with").sum())
    first_against = int((df["first_dir"] == "against").sum())
    first_flat = int((df["first_dir"] == "flat").sum())

    with_win = int(((df["first_dir"] == "with") & (df["original_result"] == "Win")).sum())
    with_loss = int(((df["first_dir"] == "with") & (df["original_result"] == "Loss")).sum())
    against_win = int(((df["first_dir"] == "against") & (df["original_result"] == "Win")).sum())
    against_loss = int(((df["first_dir"] == "against") & (df["original_result"] == "Loss")).sum())

    recovered = int(df["recovered_after_against"].sum())
    recovered_win = int(((df["recovered_after_against"]) & (df["original_result"] == "Win")).sum())
    recovered_loss = int(((df["recovered_after_against"]) & (df["original_result"] == "Loss")).sum())

    strategy_a_df = df[df["strategy_a_result"] != "NA"]
    strategy_b_df = df[df["strategy_b_result"] != "NA"]

    strategy_a_win = _count_values(strategy_a_df["strategy_a_result"], "Win")
    strategy_a_loss = _count_values(strategy_a_df["strategy_a_result"], "Loss")
    strategy_a_be = _count_values(strategy_a_df["strategy_a_result"], "BE")

    strategy_b_win = _count_values(strategy_b_df["strategy_b_result"], "Win")
    strategy_b_loss = _count_values(strategy_b_df["strategy_b_result"], "Loss")
    strategy_b_be = _count_values(strategy_b_df["strategy_b_result"], "BE")
    strategy_b_open = _count_values(strategy_b_df["strategy_b_result"], "Open")

    combined_win = _count_values(df["combined_result"], "Win")
    combined_loss = _count_values(df["combined_result"], "Loss")
    combined_be = _count_values(df["combined_result"], "BE")
    combined_open = _count_values(df["combined_result"], "Open")

    summary: Dict[str, Any] = {
        "scope": scope,
        "total_trades": total,
        "original_win_rate": round(_safe_win_rate(df["original_result"]), 2),
        "original_avg_r": round(_safe_mean(df["original_r"]), 4),
        "first_with_count": first_with,
        "first_with_pct": round(_safe_pct(first_with, total), 2),
        "first_against_count": first_against,
        "first_against_pct": round(_safe_pct(first_against, total), 2),
        "first_flat_count": first_flat,
        "first_flat_pct": round(_safe_pct(first_flat, total), 2),
        "first_with_win": with_win,
        "first_with_loss": with_loss,
        "first_with_win_rate": round(_safe_pct(with_win, with_win + with_loss), 2),
        "first_against_win": against_win,
        "first_against_loss": against_loss,
        "first_against_win_rate": round(_safe_pct(against_win, against_win + against_loss), 2),
        "against_then_recovered": recovered,
        "against_then_recovered_pct_of_against": round(_safe_pct(recovered, first_against), 2),
        "against_then_recovered_win": recovered_win,
        "against_then_recovered_loss": recovered_loss,
        "against_then_recovered_win_rate": round(_safe_pct(recovered_win, recovered_win + recovered_loss), 2),
        "strategy_a_count": int(len(strategy_a_df)),
        "strategy_a_win": strategy_a_win,
        "strategy_a_loss": strategy_a_loss,
        "strategy_a_be": strategy_a_be,
        "strategy_a_win_rate": round(_safe_pct(strategy_a_win, strategy_a_win + strategy_a_loss), 2),
        "strategy_a_avg_r": round(_safe_mean(strategy_a_df["strategy_a_r"]), 4),
        "strategy_b_count": int(len(strategy_b_df)),
        "strategy_b_win": strategy_b_win,
        "strategy_b_loss": strategy_b_loss,
        "strategy_b_be": strategy_b_be,
        "strategy_b_open": strategy_b_open,
        "strategy_b_win_rate": round(_safe_pct(strategy_b_win, strategy_b_win + strategy_b_loss), 2),
        "strategy_b_avg_r": round(_safe_mean(strategy_b_df["strategy_b_r"]), 4),
        "combined_win": combined_win,
        "combined_loss": combined_loss,
        "combined_be": combined_be,
        "combined_open": combined_open,
        "combined_win_rate": round(_safe_pct(combined_win, combined_win + combined_loss), 2),
        "combined_avg_r": round(_safe_mean(df["combined_r"]), 4),
    }

    for threshold in cfg.near_tp_thresholds:
        col = _threshold_col_name(threshold)
        summary[col] = int(df[col].sum()) if col in df.columns else 0

    return summary


def _timeframe_sort_key(timeframe: str) -> Tuple[int, str]:
    if timeframe in TIMEFRAME_ORDER:
        return TIMEFRAME_ORDER.index(timeframe), timeframe
    return 999, timeframe


def build_summary_tables(trades_df: pd.DataFrame, cfg: AnalystConfig) -> pd.DataFrame:
    summaries = [summarize_scope(trades_df, "ALL", cfg)]

    timeframe_values = sorted({str(x) for x in trades_df["timeframe"].dropna().unique()}, key=_timeframe_sort_key)
    for timeframe in timeframe_values:
        sub_df = cast(pd.DataFrame, trades_df.loc[trades_df["timeframe"] == timeframe].copy())
        summaries.append(summarize_scope(sub_df, f"TIMEFRAME:{timeframe}", cfg))

    for side in ["LONG", "SHORT"]:
        sub_df = cast(pd.DataFrame, trades_df.loc[trades_df["type"] == side].copy())
        if not sub_df.empty:
            summaries.append(summarize_scope(sub_df, f"SIDE:{side}", cfg))

    return pd.DataFrame(summaries)


def append_markdown_report(markdown_path: Path, summary_df: pd.DataFrame, cfg: AnalystConfig) -> None:
    threshold_cols = [_threshold_col_name(th) for th in cfg.near_tp_thresholds]

    lines = [
        "",
        "## ENTRY BEHAVIOR ANALYST",
        "| Scope | Total | WR Orig | WR Combined | First With | First Against | First Flat | Recovered | A WinRate | B WinRate | Combined AvgR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for _, row in summary_df.iterrows():
        lines.append(
            "| {scope} | {total} | {orig_wr}% | {combined_wr}% | {with_count} | {against_count} | {flat_count} | {recover} | {a_wr}% | {b_wr}% | {combined_r} |".format(
                scope=row["scope"],
                total=int(row["total_trades"]),
                orig_wr=row["original_win_rate"],
                combined_wr=row["combined_win_rate"],
                with_count=int(row["first_with_count"]),
                against_count=int(row["first_against_count"]),
                flat_count=int(row["first_flat_count"]),
                recover=int(row["against_then_recovered"]),
                a_wr=row["strategy_a_win_rate"],
                b_wr=row["strategy_b_win_rate"],
                combined_r=row["combined_avg_r"],
            )
        )

    if threshold_cols:
        lines.append("")
        lines.append("Near TP then SL counts:")
        for col in threshold_cols:
            if col in summary_df.columns:
                all_row = summary_df.loc[summary_df["scope"] == "ALL"]
                if not all_row.empty:
                    lines.append(f"- {col}: {int(all_row.iloc[0][col])}")

    lines.append("")

    existing = ""
    if markdown_path.exists():
        existing = markdown_path.read_text(encoding="utf-8")
    if not existing.strip():
        existing = "# ORDER ANALYST HISTORY\n"

    markdown_path.write_text(existing + "\n".join(lines), encoding="utf-8")


def analyze_all_trades(output_folder: Path, data_folder: Path, cfg: AnalystConfig) -> pd.DataFrame:
    trade_files = list_trade_files(output_folder)
    if not trade_files:
        return pd.DataFrame()

    candle_cache: Dict[str, CandleData] = {}
    records: List[Dict[str, Any]] = []

    for trade_file in trade_files:
        file_records = process_trade_file(trade_file, data_folder, cfg, candle_cache)
        records.extend(file_records)

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def _print_key_summary(summary_df: pd.DataFrame, cfg: AnalystConfig) -> None:
    if summary_df.empty:
        return

    all_row = summary_df.loc[summary_df["scope"] == "ALL"]
    if all_row.empty:
        return

    row = all_row.iloc[0]
    print("=" * 72)
    print("ENTRY BEHAVIOR ANALYST")
    print("=" * 72)
    print(f"Total trades          : {int(row['total_trades'])}")
    print(
        "First candle direction: "
        f"with={int(row['first_with_count'])} ({row['first_with_pct']}%), "
        f"against={int(row['first_against_count'])} ({row['first_against_pct']}%), "
        f"flat={int(row['first_flat_count'])} ({row['first_flat_pct']}%)"
    )
    print(
        "First with -> Win/Loss: "
        f"{int(row['first_with_win'])}/{int(row['first_with_loss'])} (WR {row['first_with_win_rate']}%)"
    )
    print(
        "First against -> Win/Loss: "
        f"{int(row['first_against_win'])}/{int(row['first_against_loss'])} (WR {row['first_against_win_rate']}%)"
    )
    print(
        "Against then recovered : "
        f"{int(row['against_then_recovered'])} "
        f"(WR {row['against_then_recovered_win_rate']}%)"
    )
    for threshold in cfg.near_tp_thresholds:
        col = _threshold_col_name(threshold)
        if col in row.index:
            print(f"{col:<22}: {int(row[col])}")
    print(
        "Combined strategy (A+B): "
        f"Win={int(row['combined_win'])}, Loss={int(row['combined_loss'])}, "
        f"BE={int(row['combined_be'])}, Open={int(row['combined_open'])}, "
        f"WR={row['combined_win_rate']}%, AvgR={row['combined_avg_r']}"
    )


def main() -> None:
    args = parse_args()
    cfg = AnalystConfig(near_tp_thresholds=parse_thresholds(args.near_tp_thresholds))

    output_folder = Path(args.output_folder)
    data_folder = Path(args.data_folder)
    result_folder = Path(args.result_folder)
    result_folder.mkdir(parents=True, exist_ok=True)

    trades_df = analyze_all_trades(output_folder=output_folder, data_folder=data_folder, cfg=cfg)
    if trades_df.empty:
        print(f"No analyzable trades found in {output_folder}")
        return

    summary_df = build_summary_tables(trades_df, cfg)

    detail_path = result_folder / "analyst_trade_details.csv"
    summary_path = result_folder / "analyst_summary.csv"
    markdown_path = result_folder / "analyst_report.md"

    trades_df.to_csv(detail_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    append_markdown_report(markdown_path, summary_df, cfg)

    _print_key_summary(summary_df, cfg)
    print("-" * 72)
    print(f"Saved detail data : {detail_path}")
    print(f"Saved summary     : {summary_path}")
    print(f"Saved markdown    : {markdown_path}")


if __name__ == "__main__":
    main()
