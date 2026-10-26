"""
Exchanges: everything that talks to a specific exchange lives in its own package.

    src/exchanges/<name>/
        adapter.py   ExchangeAdapter subclass (trading; see base.py for the contract)
        candles.py   candle download (fetch_and_save_candles_parallel, append_latest_candle_parallel, ...)
        exchange.py  native client + low-level helpers
        tools/       dashboards, order managers, history (run by hand)

The rest of the bot only uses the functions below. Which exchange is used comes
from `exchange:` in live.yaml (or the EXCHANGE environment variable):

    from src.exchanges import get_exchange, get_candle_source
    exchange = get_exchange()            # ExchangeAdapter of the active exchange
    exchange.place_market_order(...)
    get_candle_source().append_latest_candle_parallel(...)

To add an exchange: create the package, then add one line to TRADING_ADAPTERS
(and CANDLE_SOURCES for its data feed).
"""

from __future__ import annotations

import importlib
import os
import threading
from types import ModuleType
from typing import Any, Mapping, Optional

from src.exchanges.base import ExchangeAdapter, OrderState

# name -> "module:Class". Imported only when used, so a missing SDK of one
# exchange never breaks another.
TRADING_ADAPTERS: dict[str, str] = {
    "bybit": "src.exchanges.bybit.adapter:BybitAdapter",
    "lighter": "src.exchanges.lighter.adapter:LighterAdapter",
}

# name -> candle download module (same public functions in each)
CANDLE_SOURCES: dict[str, str] = {
    "binance": "src.exchanges.binance.candles",
    "bybit": "src.exchanges.bybit.candles",
    "lighter": "src.exchanges.lighter.candles",
}

DEFAULT_EXCHANGE = "bybit"

_active_name: Optional[str] = None
_adapters: dict[str, ExchangeAdapter] = {}
_lock = threading.Lock()


def _normalize(name: Any) -> str:
    return str(name or "").strip().lower()


def _configured_name() -> str:
    env_name = _normalize(os.environ.get("EXCHANGE"))
    if env_name:
        return env_name
    try:
        from src.config import load_live_config

        return _normalize(load_live_config(include_strategy=False).get("exchange")) or DEFAULT_EXCHANGE
    except Exception:
        return DEFAULT_EXCHANGE


def active_exchange_name() -> str:
    """Name of the exchange in use: set_active_exchange() > EXCHANGE env > live.yaml > bybit."""
    return _active_name or _configured_name()


def set_active_exchange(name: str) -> str:
    """Select the exchange for this process (the live runner calls it with live.yaml's `exchange`)."""
    global _active_name
    name = _normalize(name)
    if name not in TRADING_ADAPTERS and name not in CANDLE_SOURCES and name not in _adapters:
        raise ValueError(f"Unknown exchange '{name}'. Known: {', '.join(sorted(set(TRADING_ADAPTERS) | set(CANDLE_SOURCES)))}")
    _active_name = name
    os.environ["EXCHANGE"] = name  # tools started from this process see the same exchange
    return name


def configure_exchange(live_config: Mapping[str, Any]) -> str:
    """Select the exchange from live.yaml and apply its own settings (each package's `configure`)."""
    name = set_active_exchange(live_config.get("exchange") or _configured_name())
    try:
        package = importlib.import_module(f"src.exchanges.{name}")
    except ModuleNotFoundError:
        return name  # an adapter registered at runtime (register_exchange)
    configure = getattr(package, "configure", None)
    if callable(configure):
        configure(live_config)
    return name


def _load(target: str) -> Any:
    module_name, _, attr = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr) if attr else module


def get_exchange(name: Optional[str] = None) -> ExchangeAdapter:
    """The trading adapter of `name` (default: the active exchange), created once per process."""
    name = _normalize(name) or active_exchange_name()
    with _lock:
        adapter = _adapters.get(name)
        if adapter is None:
            target = TRADING_ADAPTERS.get(name)
            if target is None:
                supported = ", ".join(sorted(TRADING_ADAPTERS))
                raise ValueError(f"Trading on '{name}' is not supported. Supported: {supported}")
            adapter = _load(target)()
            _adapters[name] = adapter
        return adapter


def register_exchange(name: str, adapter: ExchangeAdapter) -> None:
    """Use this adapter instance for `name` (tests, paper trading, a custom exchange)."""
    with _lock:
        _adapters[_normalize(name)] = adapter


def get_candle_source(name: Optional[str] = None) -> ModuleType:
    """The candle download module of `name` (default: the active exchange)."""
    name = _normalize(name) or active_exchange_name()
    target = CANDLE_SOURCES.get(name)
    if target is None:
        raise ValueError(f"No candle source for '{name}'. Known: {', '.join(sorted(CANDLE_SOURCES))}")
    return _load(target)


__all__ = [
    "ExchangeAdapter",
    "OrderState",
    "TRADING_ADAPTERS",
    "CANDLE_SOURCES",
    "active_exchange_name",
    "set_active_exchange",
    "configure_exchange",
    "get_exchange",
    "register_exchange",
    "get_candle_source",
]
