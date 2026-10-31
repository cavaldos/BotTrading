import unittest
from datetime import datetime

import numpy as np
import pandas as pd

from src.core.timeframe import convert_timeframe
from src.strategy.base import BaseZone
from src.strategy.smc.key_levels import (
    FVG,
    SWING,
    KeyLevelParams,
    KeyLevels,
    LevelSide,
    build_key_levels,
    pick_stop,
    pick_target,
)
from src.strategy.smc.strategy import Config, Strategy

START = pd.Timestamp("2026-01-01 00:00")
SWINGS_ONLY = KeyLevelParams(sources=("swing",), swing_length=2)


def candles(rows):
    """1m candles from (high, low, close) rows; open = previous close."""
    highs, lows, closes = zip(*rows)
    opens = (closes[0],) + closes[:-1]
    return pd.DataFrame({
        "Time": pd.date_range(START, periods=len(rows), freq="1min"),
        "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": 1.0,
    })


def t(minute: int) -> int:
    return int((START + pd.Timedelta(minutes=minute)).value)


def side(rows):
    """LevelSide from (low, high, kind, start_minute, end_minute) rows."""
    low, high, kind, start, end = zip(*rows) if rows else ((), (), (), (), ())
    return LevelSide(
        low=np.array(low, dtype=np.float64),
        high=np.array(high, dtype=np.float64),
        kind=np.array(kind, dtype=np.int8),
        start_ns=np.array([t(m) for m in start], dtype=np.int64),
        end_ns=np.array([t(m) for m in end], dtype=np.int64),
    )


def levels(tf, support=(), resistance=()):
    return KeyLevels(timeframe=tf, bar_ns=60_000_000_000, support=side(list(support)), resistance=side(list(resistance)))


class BuildKeyLevelsTest(unittest.TestCase):
    def setUp(self):
        # swing high 9 at bar 2 (traded through by bar 7), swing low 3 at bar 4 (by bar 9)
        self.df = candles([(5, 4, 4.5), (6, 5, 5.5), (9, 8, 8.5), (7, 6, 6.5), (6, 3, 5.5),
                           (7, 5, 6.5), (8, 6, 7.5), (10, 7, 9.5), (9, 6, 7.0), (8, 2, 2.5)])
        self.levels = build_key_levels(self.df, "1m", SWINGS_ONLY)

    def active_prices(self, is_support, minute):
        lv = self.levels.support if is_support else self.levels.resistance
        return lv.low[lv.active(t(minute))].tolist()

    def test_known_only_after_confirmation_and_until_traded_through(self):
        self.assertEqual(self.active_prices(False, 4), [])     # confirmed at the close of bar 4
        self.assertEqual(self.active_prices(False, 5), [9.0])
        self.assertEqual(self.active_prices(False, 7), [9.0])  # bar 7 has not closed yet
        self.assertEqual(self.active_prices(False, 8), [])     # bar 7 went above 9
        self.assertEqual(self.active_prices(True, 6), [])      # confirmed at the close of bar 6
        self.assertEqual(self.active_prices(True, 9), [3.0])
        self.assertEqual(self.active_prices(True, 10), [])     # bar 9 went below 3

    def test_fair_value_gap_zone(self):
        # bullish FVG at bar 3: gap between bar 1 high (11) and bar 3 low (12); bar 6 fills it
        df = candles([(10.5, 9.5, 10), (11, 10, 10.8), (13, 10.4, 12.8), (14, 12, 13.5),
                      (14.5, 13, 14), (14, 12.5, 13), (13, 10.5, 11)])
        lv = build_key_levels(df, "1m", KeyLevelParams(sources=("fvg",), fvg_auto_threshold=False))
        support = lv.support
        self.assertEqual((support.low.tolist(), support.high.tolist(), support.kind.tolist()), ([11.0], [12.0], [FVG]))
        self.assertEqual((support.start_ns.tolist(), support.end_ns.tolist()), ([t(4)], [t(6)]))

    def test_builds_from_finer_candles(self):
        wave = [10 + 3 * np.sin(2 * np.pi * i / 40) for i in range(95)]   # 1m peaks at minutes 10, 50, 90
        df = candles([(c + 0.1, c - 0.1, c) for c in wave])
        lv = build_key_levels(df, "10m", SWINGS_ONLY)
        # 10m swing high = candle 50..59, known when candle 70..79 closes
        self.assertEqual(len(lv.resistance.high), 1)
        self.assertAlmostEqual(lv.resistance.high[0], wave[50] + 0.1)
        self.assertEqual(lv.resistance.start_ns.tolist(), [t(80)])
        three_minute = convert_timeframe(df, "3m")
        self.assertIsNone(build_key_levels(three_minute, "10m", SWINGS_ONLY))  # 10 is not a multiple of 3


class PickLevelsTest(unittest.TestCase):
    def setUp(self):
        self.lv = levels(
            "15m",
            support=[(95.0, 95.0, SWING, 0, 999), (98.0, 99.0, FVG, 0, 999), (99.9, 99.9, SWING, 0, 999)],
            resistance=[(101.0, 101.0, SWING, 0, 999), (104.0, 105.0, FVG, 0, 999)],
        )

    def test_stop_skips_levels_closer_than_min_sl(self):
        stop = pick_stop(self.lv, entry=100.0, trade_type="LONG", decision_ns=t(10), buffer_pct=0.0, min_sl_pct=0.2)
        self.assertEqual(stop, (98.0, "15m FVG"))    # 99.9 is only 0.1% away
        stop = pick_stop(self.lv, entry=100.0, trade_type="LONG", decision_ns=t(10), buffer_pct=0.0, min_sl_pct=2.5)
        self.assertEqual(stop, (95.0, "15m swing low"))
        self.assertIsNone(pick_stop(self.lv, entry=100.0, trade_type="LONG", decision_ns=t(10), buffer_pct=0.0,
                                    min_sl_pct=0.2, max_sl_pct=1.0))   # nearest one is 2% away
        short = pick_stop(self.lv, entry=100.0, trade_type="SHORT", decision_ns=t(10), buffer_pct=1.0, min_sl_pct=0.2)
        self.assertAlmostEqual(short[0], 102.01)
        self.assertEqual(short[1], "15m swing high")

    def test_target_needs_min_rr(self):
        target = pick_target(self.lv, entry=100.0, stop_loss=98.0, trade_type="LONG", decision_ns=t(10),
                             buffer_pct=0.0, min_rr=0.0)
        self.assertEqual(target, (101.0, 0.5, "15m swing high"))
        target = pick_target(self.lv, entry=100.0, stop_loss=98.0, trade_type="LONG", decision_ns=t(10),
                             buffer_pct=0.0, min_rr=1.0)
        self.assertEqual(target, (104.0, 2.0, "15m FVG"))   # near edge of the zone
        target = pick_target(self.lv, entry=100.0, stop_loss=102.0, trade_type="SHORT", decision_ns=t(10),
                             buffer_pct=0.0, min_rr=0.5)
        self.assertEqual(target, (99.0, 0.5, "15m FVG"))
        self.assertIsNone(pick_target(self.lv, entry=100.0, stop_loss=98.0, trade_type="LONG", decision_ns=t(10),
                                      buffer_pct=0.0, min_rr=3.0))


class StrategyLevelPlanTest(unittest.TestCase):
    zone = BaseZone(high=99.8, low=99.5, time=datetime(2026, 1, 1), bias="BULLISH")

    def strategy(self, **overrides):
        settings = dict(level_timeframe=["15m", "10m"], sl_mode="level_or_zone", tp_mode="htf_level_or_rr",
                        level_sl_buffer_pct=0.0, min_sl_pct=0.2, sl_multiplier=1.0, sl_buffer_pct=0.0,
                        risk_reward_ratio=2.0, level_tp_min_rr=1.0, level_lookback_bars=0)
        settings.update(overrides)
        strategy = Strategy(Config(**settings))
        strategy._tp_context = {"decision_time": (START + pd.Timedelta(minutes=10)).to_pydatetime()}
        return strategy

    def test_first_timeframe_with_a_level_wins(self):
        strategy = self.strategy()
        m15 = levels("15m", resistance=[(103.0, 103.0, SWING, 0, 999)])     # no support on 15m
        m10 = levels("10m", support=[(98.5, 98.5, SWING, 0, 999)], resistance=[(102.0, 102.0, SWING, 0, 999)])
        strategy._key_levels = [m15, m10]
        plan = strategy._build_trade_plan(100.0, self.zone, "LONG")
        self.assertEqual((plan.stop_loss, plan.sl_source), (98.5, "10m swing low"))
        self.assertEqual((plan.take_profit, plan.tp_source), (103.0, "15m swing high"))
        self.assertAlmostEqual(plan.rr_ratio, 2.0)

    def test_fallback_to_entry_zone_and_fixed_rr(self):
        strategy = self.strategy()
        strategy._key_levels = [levels("15m"), levels("10m")]
        plan = strategy._build_trade_plan(100.0, self.zone, "LONG")
        self.assertEqual((plan.sl_source, plan.tp_source), ("OB", "RR"))
        self.assertAlmostEqual(plan.stop_loss, 99.5)
        self.assertAlmostEqual(plan.take_profit, 101.0)

    def test_strict_modes_skip_the_trade(self):
        strategy = self.strategy(sl_mode="level")
        strategy._key_levels = [levels("15m"), levels("10m")]
        self.assertIsNone(strategy._build_trade_plan(100.0, self.zone, "LONG"))
        strategy = self.strategy(tp_mode="htf_level")
        strategy._key_levels = [levels("15m", support=[(98.0, 98.0, SWING, 0, 999)])]
        self.assertIsNone(strategy._build_trade_plan(100.0, self.zone, "LONG"))

    def test_off_by_default(self):
        strategy = Strategy(Config())
        self.assertEqual(strategy._build_key_levels(candles([(2, 1, 1.5)] * 50)), [])


def random_walk(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.0015, n)))
    opens = np.concatenate(([closes[0]], closes[:-1]))
    wick = np.abs(rng.normal(0.0, 0.0008, n)) * closes
    return pd.DataFrame({
        "Time": pd.date_range(START, periods=n, freq="1min"),
        "Open": opens,
        "High": np.maximum(opens, closes) + wick,
        "Low": np.minimum(opens, closes) - wick,
        "Close": closes,
        "Volume": 1.0,
    })


class NoLookAheadTest(unittest.TestCase):
    """Signals decided before a cut must not change when later candles are removed."""

    def signals(self, df_entry):
        config = Config(
            signal_timeframe="5m", entry_timeframe="1m",
            require_liquidity=False, require_opposite_liquidity_target=False, require_reversal_pattern=False,
            level_timeframe=["15m", "10m"], sl_mode="level_or_zone", tp_mode="htf_level_or_rr",
            level_tp_min_rr=1.0, min_sl_pct=0.1,
        )
        signal_df = convert_timeframe(df_entry, "5m")
        return Strategy(config).generate_signals(signal_df, "TEST", df_entry=df_entry)

    @staticmethod
    def key(signal):
        return (signal.entry_time, signal.trade_type, signal.entry_price, signal.stop_loss,
                signal.take_profit, signal.sl_source, signal.tp_source)

    def test_truncated_data_gives_the_same_signals(self):
        df = random_walk(6000, seed=7)
        full = self.signals(df)
        sources = {s.sl_source.split(" ")[0] for s in full} | {s.tp_source.split(" ")[0] for s in full}
        self.assertTrue({"15m", "10m"} <= sources, sources)
        for cut in (1500, 2730, 3990, 5010):   # multiples of 30 minutes: every timeframe's candles are whole
            cut_time = START + pd.Timedelta(minutes=cut)
            part = self.signals(df.iloc[:cut].reset_index(drop=True))
            expected = [self.key(s) for s in full if pd.Timestamp(s.entry_time) + pd.Timedelta(minutes=1) <= cut_time]
            got = [self.key(s) for s in part]
            self.assertEqual(got, expected, f"cut at minute {cut}")


if __name__ == "__main__":
    unittest.main()
