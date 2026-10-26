import sys
import types
import unittest
from dataclasses import dataclass

from src.strategy import (
    BaseConfig,
    BaseSignal,
    BaseStrategy,
    available_strategies,
    build_strategy_config,
    create_strategy,
    load_strategy,
    strategy_label,
)
from src.strategy import registry


def _install_dummy_strategy(name: str) -> type:
    """A strategy package created in memory: the same thing a folder src/strategy/<name>/ provides."""

    @dataclass
    class Config(BaseConfig):
        threshold: float = 1.0

    class Strategy(BaseStrategy):
        def generate_signals(self, df, symbol, df_entry=None):
            return [BaseSignal(symbol, "LONG", df["Time"].iloc[-1], 10.0, 9.0, 12.0, self.config.signal_timeframe)]

    module = types.ModuleType(f"src.strategy.{name}")
    module.STRATEGY_NAME = "Dummy"
    module.Config = Config
    module.Strategy = Strategy
    sys.modules[module.__name__] = module
    registry._cache.pop(name, None)
    return Strategy


class StrategyRegistryTest(unittest.TestCase):
    def test_smc_is_the_default(self):
        self.assertIn("smc", available_strategies())
        config = build_strategy_config(data_folder="d", output_folder="o", strategy_config={"swing_length": 7})
        self.assertEqual(config.strategy, "smc")
        self.assertEqual(config.swing_length, 7)
        self.assertEqual(strategy_label(config), "SMC")
        self.assertIsInstance(create_strategy(config), load_strategy("smc").strategy_cls)

    def test_unknown_keys_are_ignored(self):
        config = build_strategy_config(data_folder="d", output_folder="o", strategy_config={"not_a_field": 1})
        self.assertFalse(hasattr(config, "not_a_field"))

    def test_overrides_win(self):
        config = build_strategy_config(
            data_folder="d", output_folder="o",
            strategy_config={"signal_timeframe": ["3m", "5m"], "entry_timeframe": "1m"},
            overrides={"signal_timeframe": "5m"},
        )
        self.assertEqual(config.signal_timeframe, "5m")

    def test_unknown_strategy(self):
        with self.assertRaises(ValueError):
            load_strategy("does_not_exist")
        with self.assertRaises(ValueError):
            build_strategy_config(data_folder="d", output_folder="o", strategy_config={"strategy": "does_not_exist"})

    def test_plugin_strategy_is_picked_from_settings(self):
        strategy_cls = _install_dummy_strategy("dummy_plugin")
        try:
            config = build_strategy_config(
                data_folder="d", output_folder="o",
                strategy_config={"strategy": "dummy_plugin", "threshold": 2.5, "swing_length": 99},
            )
            self.assertEqual(config.strategy, "dummy_plugin")
            self.assertEqual(config.threshold, 2.5)
            self.assertFalse(hasattr(config, "swing_length"))  # SMC setting, not this strategy's
            self.assertIsInstance(create_strategy(config), strategy_cls)
            self.assertEqual(strategy_label(config), "Dummy")
        finally:
            sys.modules.pop("src.strategy.dummy_plugin", None)
            registry._cache.pop("dummy_plugin", None)


if __name__ == "__main__":
    unittest.main()
