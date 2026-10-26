"""
Download candles from your main exchange, the one in .env (EXCHANGE=bybit | binance | lighter).

    python -m src.utils.getdata                       # exchange from .env, settings from backtest.yaml
    python -m src.utils.getdata my_data.yaml          # other settings file (same keys as backtest.yaml)
    python -m src.utils.getdata --exchange binance    # override the exchange once

Settings (backtest.yaml): currencies, timeframes, folder_path, mode (by_candles | by_date),
candles_limit, start_date. Files are saved as <folder_path>/<SYMBOL>_<TF>.csv.
WARNING: everything in folder_path is deleted before downloading.

Each exchange also runs on its own:
    python -m src.exchanges.bybit.candles
    python -m src.exchanges.binance.candles
    python -m src.exchanges.lighter.candles
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv  # pyright: ignore[reportMissingModuleSource]

from src.config import load_yaml_config
from src.exchanges import CANDLE_SOURCES, active_exchange_name, get_candle_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main_exchange() -> str:
    """The exchange in .env (EXCHANGE=...); falls back to live.yaml `exchange`, then bybit."""
    load_dotenv(PROJECT_ROOT / ".env")
    return active_exchange_name()


def getdata(config_path: Optional[str] = None, exchange: Optional[str] = None, max_workers: int = 5) -> str:
    """Download candles with the settings of `config_path` (default backtest.yaml). Returns the exchange used."""
    name = (exchange or main_exchange()).strip().lower()
    if name not in CANDLE_SOURCES:
        raise ValueError(f"No candle source for '{name}'. Known: {', '.join(sorted(CANDLE_SOURCES))}")

    config = load_yaml_config(config_path or "backtest", default_name="backtest.yaml")
    print(f"Exchange: {name} | folder: {config.get('folder_path', 'res/data')} | mode: {config.get('mode', 'by_candles')}")
    get_candle_source(name).getdata(config_path, max_workers=max_workers)
    return name


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download candles from the exchange in .env (EXCHANGE=...).")
    parser.add_argument("config", nargs="?", default=None, help="settings YAML (default: src/config/backtest.yaml)")
    parser.add_argument("--exchange", choices=sorted(CANDLE_SOURCES), help="use this exchange instead of .env")
    parser.add_argument("--workers", type=int, default=5, help="parallel downloads (default: 5)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    getdata(args.config, exchange=args.exchange, max_workers=args.workers)
