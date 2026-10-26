"""Bybit USDT perpetuals (ccxt). Trading: adapter.BybitAdapter, candles: candles.py."""

import os


def configure(live_config: dict) -> None:
    """Bybit settings from live.yaml: data.market_category (linear | inverse | spot)."""
    category = str((live_config.get("data") or {}).get("market_category", "linear")).strip().lower()
    os.environ["BYBIT_MARKET_CATEGORY"] = category or "linear"
