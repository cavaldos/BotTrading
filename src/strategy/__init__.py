"""
Strategies are plug-ins: each one is a package src/strategy/<name>/ and
strategy.yaml chooses it with `strategy: <name>` (default: smc).

    src/strategy/
        base.py        BaseConfig, BaseStrategy, BaseSignal, BaseZone, TradePlan
        registry.py    load_strategy, build_strategy_config, create_strategy
        smc/           the SMC strategy (Config, Strategy, STRATEGY_NAME)

To add a strategy, create src/strategy/<name>/__init__.py exporting:

    STRATEGY_NAME = "My strategy"
    @dataclass
    class Config(BaseConfig): ...        # its own settings, read from strategy.yaml
    class Strategy(BaseStrategy):
        def generate_signals(self, df, symbol, df_entry=None) -> list[BaseSignal]: ...

then set `strategy: <name>` in strategy.yaml. The backtest, the live bot and
the tools pick it up through this module:

    config = build_strategy_config(data_folder=..., output_folder=..., strategy_config=settings)
    strategy = create_strategy(config)
    signals = strategy.generate_signals(df_signal, symbol, df_entry=df_entry)
"""

from __future__ import annotations

from src.strategy.base import BaseConfig, BaseSignal, BaseStrategy, BaseZone, TradePlan, parse_timeframe_minutes
from src.strategy.registry import (
    DEFAULT_STRATEGY,
    StrategySpec,
    available_strategies,
    build_strategy_config,
    create_strategy,
    load_strategy,
    strategy_key,
    strategy_label,
)

__all__ = [
    "BaseConfig",
    "BaseSignal",
    "BaseStrategy",
    "BaseZone",
    "TradePlan",
    "parse_timeframe_minutes",
    "DEFAULT_STRATEGY",
    "StrategySpec",
    "available_strategies",
    "build_strategy_config",
    "create_strategy",
    "load_strategy",
    "strategy_key",
    "strategy_label",
]
