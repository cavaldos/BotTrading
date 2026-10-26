"""
Find a strategy package by name and build its config / instance.

strategy.yaml picks the strategy:

    strategy: smc        # -> package src/strategy/smc/

A strategy package only has to export (from its __init__.py):

    STRATEGY_NAME  display name, e.g. "SMC"
    Config         dataclass, subclass of BaseConfig (its fields = the strategy.yaml keys)
    Strategy       subclass of BaseStrategy with generate_signals(df, symbol, df_entry=None)

Backtest, live trading, chart snapshots and the verify tools all go through
`build_strategy_config` + `create_strategy`, so a new strategy needs no change
anywhere else.
"""

from __future__ import annotations

import importlib
import re
import warnings
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping, Optional, Type

from src.strategy.base import BaseConfig, BaseStrategy

DEFAULT_STRATEGY = "smc"
STRATEGY_KEY = "strategy"  # key in strategy.yaml

# Backward-compatibility aliases: old YAML key -> new field name
_FIELD_ALIASES: dict[str, str] = {
    "ob_mitigation": "mitigation_mode",
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class StrategySpec:
    key: str                          # package name, e.g. "smc"
    label: str                        # STRATEGY_NAME, e.g. "SMC"
    config_cls: Type[BaseConfig]
    strategy_cls: Type[BaseStrategy]


_cache: dict[str, StrategySpec] = {}


def strategy_key(strategy_config: Optional[Mapping[str, Any]] = None) -> str:
    """The strategy package named in strategy settings (default: smc)."""
    value = (strategy_config or {}).get(STRATEGY_KEY) if strategy_config else None
    return str(value or DEFAULT_STRATEGY).strip().lower()


def available_strategies() -> list[str]:
    """Strategy packages found in src/strategy/ (folders with an __init__.py)."""
    return sorted(
        path.parent.name
        for path in _PACKAGE_DIR.glob("*/__init__.py")
        if _NAME_RE.match(path.parent.name)
    )


def load_strategy(name: Optional[str] = None) -> StrategySpec:
    """Import src/strategy/<name>/ and check that it exports Config, Strategy and STRATEGY_NAME."""
    key = str(name or DEFAULT_STRATEGY).strip().lower()
    if key in _cache:
        return _cache[key]
    if not _NAME_RE.match(key):
        raise ValueError(f"Invalid strategy name '{name}'")
    try:
        module = importlib.import_module(f"src.strategy.{key}")
    except ModuleNotFoundError as error:
        if error.name != f"src.strategy.{key}":
            raise
        raise ValueError(
            f"Unknown strategy '{key}'. Available: {', '.join(available_strategies()) or '-'}"
        ) from None

    config_cls = getattr(module, "Config", None)
    strategy_cls = getattr(module, "Strategy", None)
    if not (isinstance(config_cls, type) and issubclass(config_cls, BaseConfig)):
        raise TypeError(f"src.strategy.{key} must export Config (a BaseConfig subclass)")
    if not (isinstance(strategy_cls, type) and issubclass(strategy_cls, BaseStrategy)):
        raise TypeError(f"src.strategy.{key} must export Strategy (a BaseStrategy subclass)")

    spec = StrategySpec(
        key=key,
        label=str(getattr(module, "STRATEGY_NAME", key.upper())),
        config_cls=config_cls,
        strategy_cls=strategy_cls,
    )
    _cache[key] = spec
    return spec


def build_strategy_config(
    *,
    data_folder: str,
    output_folder: str,
    strategy_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> BaseConfig:
    """Config of the strategy named in `strategy_config` from its settings plus runtime overrides.

    Keys the strategy's Config does not have are ignored.
    """
    if not data_folder:
        raise ValueError("data_folder is required")
    if not output_folder:
        raise ValueError("output_folder is required")

    merged: dict[str, Any] = dict(strategy_config or {})
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                merged[key] = value

    spec = load_strategy(strategy_key(merged))
    merged[STRATEGY_KEY] = spec.key
    merged["data_folder"] = data_folder
    merged["output_folder"] = output_folder

    # Apply backward-compat aliases (e.g. ob_mitigation -> mitigation_mode)
    for old_key, new_key in _FIELD_ALIASES.items():
        if old_key in merged and new_key not in merged:
            merged[new_key] = merged.pop(old_key)
        elif old_key in merged and new_key in merged:
            merged.pop(old_key)  # both present: the new key wins

    config_fields = {item.name for item in fields(spec.config_cls)}
    return spec.config_cls(**{key: value for key, value in merged.items() if key in config_fields})


def create_strategy(config: BaseConfig) -> BaseStrategy:
    """Instance of the strategy that `config` belongs to."""
    spec = load_strategy(config.strategy or DEFAULT_STRATEGY)
    if not isinstance(config, spec.config_cls):
        warnings.warn(
            f"Config {type(config).__name__} is not {spec.config_cls.__name__} of strategy '{spec.key}'",
            stacklevel=2,
        )
    return spec.strategy_cls(config)


def strategy_label(config_or_settings: BaseConfig | Mapping[str, Any] | None = None) -> str:
    """Display name ('SMC') of a config or of strategy settings."""
    if isinstance(config_or_settings, BaseConfig):
        return load_strategy(config_or_settings.strategy or DEFAULT_STRATEGY).label
    return load_strategy(strategy_key(config_or_settings)).label
