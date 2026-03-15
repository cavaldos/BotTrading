"""CLI runner for order manager replay backtests."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, cast

import pandas as pd

from src.config import load_backtest_config, load_strategy_config
from src.manager_order.logic import (
    CandleData,
    ManagerConfig,
    REQUIRED_TRADE_COLUMNS,
    build_managed_record,
    compute_managed_summary,
    compute_trade_results_row,
    empty_candle_data,
    extract_base_symbol,
    load_candle_data,
    replay_trade,
)


def list_trade_files(output_folder: Path) -> List[Path]:
    return sorted(output_folder.glob("*_trades.csv"))


def parse_args() -> argparse.Namespace:
    backtest_cfg = load_backtest_config(include_strategy=False)
    default_data_folder = str(backtest_cfg.get("folder_path", "res/data"))
    default_output_folder = str(backtest_cfg.get("output_folder", "res/output"))

    parser = argparse.ArgumentParser(description="Order manager replay on historical trade files")
    parser.add_argument("--output-folder", default=default_output_folder, help="Folder containing *_trades.csv")
    parser.add_argument("--data-folder", default=default_data_folder, help="Folder containing <SYMBOL>_1m.csv")
    parser.add_argument(
        "--result-folder",
        default="res/output_managed",
        help="Output folder for reports and managed trade data",
    )
    parser.add_argument("--one-r-trigger", type=float, default=1.0, help="R threshold to activate sweep rule")
    parser.add_argument(
        "--adverse-enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable adverse momentum management rule",
    )
    parser.add_argument(
        "--adverse-action",
        choices=["tp_adjust", "close"],
        default="tp_adjust",
        help="Action after adverse trigger",
    )
    parser.add_argument("--adverse-trigger-r", type=float, default=0.9, help="Adverse trigger threshold in R")
    parser.add_argument("--adverse-tp-r", type=float, default=0.3, help="Adjusted TP distance from entry in R")
    parser.add_argument(
        "--adverse-body-ratio",
        type=float,
        default=0.7,
        help="Minimum candle body/range ratio for adverse momentum",
    )
    parser.add_argument(
        "--adverse-near-ratio",
        type=float,
        default=0.2,
        help="How close close-price must be to candle extreme",
    )
    parser.add_argument(
        "--adverse-require-break",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require break of previous candle extreme",
    )
    parser.add_argument(
        "--adverse-require-near",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require close near candle extreme",
    )
    return parser.parse_args()


def build_manager_config(args: argparse.Namespace) -> ManagerConfig:
    return ManagerConfig(
        one_r_trigger=float(args.one_r_trigger),
        sweep_exit_price_mode="next_open",
        adverse_enabled=bool(args.adverse_enabled),
        adverse_action=cast(Literal["tp_adjust", "close"], args.adverse_action),
        adverse_trigger_r=float(args.adverse_trigger_r),
        adverse_tp_r=float(args.adverse_tp_r),
        adverse_body_ratio=float(args.adverse_body_ratio),
        adverse_near_ratio=float(args.adverse_near_ratio),
        adverse_require_break=bool(args.adverse_require_break),
        adverse_require_near=bool(args.adverse_require_near),
    )


def get_sl_multiplier(strategy_cfg: Dict[str, Any]) -> float:
    try:
        return float(strategy_cfg.get("sl_multiplier", 1.05))
    except (TypeError, ValueError):
        return 1.05


def load_cached_candles(
    base_symbol: str,
    data_folder: Path,
    candle_cache: Dict[str, CandleData],
) -> CandleData:
    candles = candle_cache.get(base_symbol)
    if candles is not None:
        return candles

    data_file = data_folder / f"{base_symbol}_1m.csv"
    if data_file.exists():
        candles = load_candle_data(data_file)
    else:
        candles = empty_candle_data()

    candle_cache[base_symbol] = candles
    return candles


def process_trade_file(
    trade_file: Path,
    data_folder: Path,
    cfg: ManagerConfig,
    candle_cache: Dict[str, CandleData],
) -> Optional[pd.DataFrame]:
    df = pd.read_csv(trade_file)
    if df.empty:
        return None

    missing_columns = sorted(REQUIRED_TRADE_COLUMNS.difference(set(df.columns)))
    if missing_columns:
        joined = ", ".join(missing_columns)
        print(f"[SKIP] {trade_file.name}: missing required columns: {joined}")
        return None

    managed_records = []
    for _, row in df.iterrows():
        base_symbol = extract_base_symbol(str(row.get("Symbol", "")))
        candles = load_cached_candles(base_symbol, data_folder, candle_cache)
        managed = replay_trade(row=row, candles=candles, cfg=cfg)
        managed_records.append(build_managed_record(row, managed))

    managed_df = pd.DataFrame(managed_records)
    return pd.concat([df.reset_index(drop=True), managed_df], axis=1)


def _extract_timeframe(symbol: str) -> str:
    if "_" not in symbol:
        return "unknown"
    return symbol.split("_")[-1]


def append_markdown_report(
    markdown_path: Path,
    trade_result_rows: List[Dict[str, Any]],
    total_row: Optional[Dict[str, Any]],
    pattern_label: str,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    timeframe_order = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in trade_result_rows:
        timeframe = _extract_timeframe(str(row["symbol"]))
        grouped.setdefault(timeframe, []).append(row)

    sorted_timeframes = sorted(
        grouped.keys(),
        key=lambda tf: timeframe_order.index(tf) if tf in timeframe_order else 999,
    )

    symbol_order = [
        "LTCUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "ETHUSDT",
        "NEARUSDT",
        "DOTUSDT",
        "BTCUSDT",
        "AVAXUSDT",
        "SOLUSDT",
        "BCHUSDT",
    ]
    symbol_rank = {symbol: index for index, symbol in enumerate(symbol_order)}

    def symbol_sort_key(row: Dict[str, Any]) -> tuple[int, str]:
        symbol = str(row["symbol"])
        base = symbol.split("_", 1)[0]
        return symbol_rank.get(base, 999), symbol

    lines = [
        "",
        f"##  TRADE RESULTS - {timestamp} - {pattern_label}",
        "| Symbol         |  Total |   Wins | Losses | Open |   Win Rate |    %SL |   RR |   SLM |    ROE |  W-S |  L-S | W-AVG | L-AVG |",
        "|----------------|--------|--------|--------|------|------------|--------|------|-------|--------|------|------|-------|-------|",
    ]

    for timeframe in sorted_timeframes:
        for row in sorted(grouped[timeframe], key=symbol_sort_key):
            lines.append(
                "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>6} | {:>4} | {:>5} | {:>6} | {:>4} | {:>4} | {:>5} | {:>5} |".format(
                    str(row["symbol"]),
                    int(row["total_trades"]),
                    int(row["wins"]),
                    int(row["losses"]),
                    int(row["open_trades"]),
                    f"*{row['win_rate']}%*",
                    f"{row['avg_sl_percent']}%",
                    f"{row['avg_rr']}",
                    f"{row['sl_multiplier']}",
                    f"{row['roe']}",
                    int(row["longest_win_streak"]),
                    int(row["longest_loss_streak"]),
                    row["avg_win_streak"],
                    row["avg_loss_streak"],
                )
            )

    if total_row is not None:
        lines.append(
            "| {:<14} | {:>6} | {:>6} | {:>6} | {:>4} | {:>10} | {:>6} | {:>4} | {:>5} | {:>6} | {:>4} | {:>4} | {:>5} | {:>5} |".format(
                "**TOTAL**",
                int(total_row["total_trades"]),
                int(total_row["wins"]),
                int(total_row["losses"]),
                int(total_row["open_trades"]),
                f"*{total_row['win_rate']}%*",
                f"{total_row['avg_sl_percent']}%",
                f"{total_row['avg_rr']}",
                f"{total_row['sl_multiplier']}",
                f"{total_row['roe']}",
                int(total_row["longest_win_streak"]),
                int(total_row["longest_loss_streak"]),
                total_row["avg_win_streak"],
                total_row["avg_loss_streak"],
            )
        )

    lines.append("")

    existing = ""
    if markdown_path.exists():
        existing = markdown_path.read_text(encoding="utf-8")

    if not existing.strip():
        existing = "# ORDER MANAGER HISTORY\n"

    markdown_path.write_text(existing + "\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    cfg = build_manager_config(args)
    strategy_cfg = load_strategy_config()
    sl_multiplier = get_sl_multiplier(strategy_cfg)
    pattern_label = str(strategy_cfg.get("reversal_pattern_ids", [0]))

    output_folder = Path(args.output_folder)
    data_folder = Path(args.data_folder)
    result_folder = Path(args.result_folder)
    managed_data_folder = result_folder / "data"
    result_folder.mkdir(parents=True, exist_ok=True)
    managed_data_folder.mkdir(parents=True, exist_ok=True)

    trade_files = list_trade_files(output_folder)
    if not trade_files:
        print(f"No trade files found in {output_folder}")
        return

    print("=" * 72)
    print("ORDER MANAGER REPLAY")
    print("=" * 72)
    print(f"Trades folder : {output_folder}")
    print(f"Data folder   : {data_folder}")
    print(f"Result folder : {result_folder}")
    print(f"Managed data  : {managed_data_folder}")
    print("Rules:")
    print(f"  - R1 sweep trigger: {cfg.one_r_trigger}R")
    print(f"  - Sweep fill mode : {cfg.sweep_exit_price_mode}")
    print(f"  - Adverse rule    : {'ON' if cfg.adverse_enabled else 'OFF'}")
    if cfg.adverse_enabled:
        print(
            "  - Adverse config  : "
            f"trigger={cfg.adverse_trigger_r}R, "
            f"action={cfg.adverse_action}, "
            f"tp={cfg.adverse_tp_r}R, "
            f"body>={cfg.adverse_body_ratio}, "
            f"near={cfg.adverse_near_ratio}, "
            f"break={cfg.adverse_require_break}, "
            f"near_check={cfg.adverse_require_near}"
        )
    print("-" * 72)

    candle_cache: Dict[str, CandleData] = {}
    summary_rows: List[Dict[str, Any]] = []
    trade_result_rows: List[Dict[str, Any]] = []
    all_rows: List[pd.DataFrame] = []

    for trade_file in trade_files:
        combined_df = process_trade_file(trade_file, data_folder, cfg, candle_cache)
        if combined_df is None:
            continue

        out_file = managed_data_folder / f"{trade_file.stem}_managed.csv"
        combined_df.to_csv(out_file, index=False)
        all_rows.append(combined_df)

        summary = compute_managed_summary(combined_df, trade_file.name)
        summary_rows.append(summary)

        symbol_name = trade_file.stem.replace("_trades", "")
        trade_result_rows.append(compute_trade_results_row(combined_df, symbol_name, sl_multiplier))

        print(
            f"{trade_file.name:<24} "
            f"trades={summary['total_trades']:<4} "
            f"R1={summary['managed_r1_sweep']:<4} "
            f"ADV={summary.get('managed_adverse_triggered', 0):<4} "
            f"WR {summary['win_rate_original']:>5.1f}% -> {summary['win_rate_managed']:>5.1f}%"
        )

    if not summary_rows:
        print("No files were processed.")
        return

    summary_df = pd.DataFrame(summary_rows)
    summary_file = result_folder / "order_manager_summary.csv"
    summary_df.to_csv(summary_file, index=False)

    merged_df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    total_trade_row: Optional[Dict[str, Any]] = None

    if not merged_df.empty:
        global_summary = compute_managed_summary(merged_df, "__ALL__")
        summary_df = pd.concat([summary_df, pd.DataFrame([global_summary])], ignore_index=True)
        summary_df.to_csv(summary_file, index=False)
        total_trade_row = compute_trade_results_row(merged_df, "__TOTAL__", sl_multiplier)

        print("-" * 72)
        print(
            f"ALL FILES: trades={global_summary['total_trades']}, "
            f"R1={global_summary['managed_r1_sweep']}, "
            f"ADV={global_summary.get('managed_adverse_triggered', 0)}, "
            f"WR {global_summary['win_rate_original']:.2f}% -> {global_summary['win_rate_managed']:.2f}%"
        )

    markdown_file = result_folder / "report.md"
    append_markdown_report(markdown_file, trade_result_rows, total_trade_row, pattern_label)

    print("=" * 72)
    print(f"Saved managed files to: {result_folder}")
    print(f"Managed trade data: {managed_data_folder}")
    print(f"Summary: {summary_file}")
    print(f"Markdown report: {markdown_file}")


if __name__ == "__main__":
    main()
