from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.config import load_backtest_config, load_strategy_config
from src.core.timeframe import convert_timeframe


OHLCV_COLUMNS = ["Time", "Open", "High", "Low", "Close", "Volume"]
TIMEFRAME_PATTERN = re.compile(r"^\s*(\d+)\s*(m|min|h|d|w)\s*$", re.IGNORECASE)


def normalize_timeframe(timeframe: str) -> str | None:
    match = TIMEFRAME_PATTERN.match(str(timeframe))
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "min":
        unit = "m"
    return f"{value}{unit}"


def load_runtime_config() -> tuple[Path, str, list[str]]:
    backtest_cfg = load_backtest_config(include_strategy=False)
    strategy_cfg = load_strategy_config()

    input_folder = Path(str(backtest_cfg.get("folder_path", "res/data3")).strip() or "res/data3")
    entry_timeframe = normalize_timeframe(str(strategy_cfg.get("entry_timeframe", "1m"))) or "1m"

    raw_signal_timeframes = strategy_cfg.get("signal_timeframe", ["2m", "3m", "5m"])
    if isinstance(raw_signal_timeframes, str):
        raw_signal_timeframes = [raw_signal_timeframes]

    normalized_signal_timeframes: list[str] = []
    for tf in raw_signal_timeframes:
        normalized = normalize_timeframe(str(tf))
        if normalized and normalized not in normalized_signal_timeframes:
            normalized_signal_timeframes.append(normalized)

    return input_folder, entry_timeframe, normalized_signal_timeframes


def convert_folder(
    input_folder: Path | str,
    target_timeframes: list[str],
    entry_timeframe: str = "1m",
    output_folder: Path | str | None = None,
) -> None:
    input_folder = Path(input_folder)
    output_folder = output_folder or input_folder
    output_folder = Path(output_folder)

    if not input_folder.exists():
        print(f"[ERROR] Input folder not found: {input_folder}")
        return

    output_folder.mkdir(parents=True, exist_ok=True)

    source_files = sorted(input_folder.glob(f"*_{entry_timeframe}.csv"))
    if not source_files:
        print(f"[WARNING] No source files found: *_{entry_timeframe}.csv in {input_folder}")
        return

    target_timeframes = [tf for tf in target_timeframes if tf != entry_timeframe]
    if not target_timeframes:
        print("[WARNING] No target timeframe to convert.")
        return

    print(f"Input folder: {input_folder}")
    print(f"Output folder: {output_folder}")
    print(f"Entry timeframe: {entry_timeframe}")
    print(f"Signal timeframes: {', '.join(target_timeframes)}")
    print("=" * 60)

    for source_file in source_files:
        base_symbol = source_file.stem.rsplit("_", 1)[0]

        try:
            df = pd.read_csv(source_file, skipinitialspace=True)
            df.columns = [col.strip() for col in df.columns]
        except Exception as exc:
            print(f"[ERROR] Read failed {source_file.name}: {exc}")
            continue

        if any(column not in df.columns for column in OHLCV_COLUMNS):
            print(f"[SKIP] {source_file.name} missing OHLCV columns")
            continue

        print(f"[OK] Loaded {source_file.name} ({len(df)} rows)")

        for timeframe in target_timeframes:
            output_file = output_folder / f"{base_symbol}_{timeframe}.csv"
            try:
                converted = convert_timeframe(df[OHLCV_COLUMNS], timeframe)
                converted.to_csv(output_file, index=False)
                print(f"  -> {output_file.name} ({len(converted)} rows)")
            except Exception as exc:
                print(f"  [ERROR] Convert {source_file.name} -> {timeframe}: {exc}")

    print("=" * 60)
    print("Done converting timeframe data.")


def build_target_timeframes(signal_timeframes: list[str]) -> list[str]:
    target_timeframes: list[str] = []
    for tf in signal_timeframes:
        normalized = normalize_timeframe(str(tf))
        if not normalized:
            print(f"[WARNING] Invalid timeframe skipped: {tf}")
            continue
        if normalized not in target_timeframes:
            target_timeframes.append(normalized)

    return target_timeframes


def main(
    *,
    input_folder: Path | str,
    output_folder: Path | str | None,
    entry_timeframe: str,
    signal_timeframes: list[str],
) -> None:
    normalized_entry_tf = normalize_timeframe(entry_timeframe) or "1m"
    target_timeframes = build_target_timeframes(signal_timeframes)

    convert_folder(
        input_folder=input_folder,
        target_timeframes=target_timeframes,
        entry_timeframe=normalized_entry_tf,
        output_folder=output_folder,
    )


if __name__ == "__main__":
    config_input_folder, config_entry_tf, config_signal_tfs = load_runtime_config()

    INPUT_FOLDER = config_input_folder
    OUTPUT_FOLDER = "res/data_converted"  # You can change this to a different folder if desired
    ENTRY_TIMEFRAME = config_entry_tf
    SIGNAL_TIMEFRAMES = config_signal_tfs or ["2m", "3m", "5m"]

    main(
        input_folder=INPUT_FOLDER,
        output_folder=OUTPUT_FOLDER,
        entry_timeframe=ENTRY_TIMEFRAME,
        signal_timeframes=SIGNAL_TIMEFRAMES,
    )
