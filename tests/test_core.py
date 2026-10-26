import unittest

import pandas as pd

from src.core.symbols import strip_timeframe, symbol_from_filename
from src.core.timeframe import convert_timeframe, normalize_timeframes, timeframe_to_minutes


class TimeframeTest(unittest.TestCase):
    def test_minutes(self):
        cases = {"1m": 1, "5m": 5, "15min": 15, "1h": 60, "4H": 240, "1d": 1440, "1w": 10080}
        for text, minutes in cases.items():
            self.assertEqual(timeframe_to_minutes(text), minutes, text)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            timeframe_to_minutes("abc")
        self.assertEqual(timeframe_to_minutes("abc", default=60), 60)
        self.assertIsNone(timeframe_to_minutes("", default=None))

    def test_normalize(self):
        self.assertEqual(normalize_timeframes("5m"), ["5m"])
        self.assertEqual(normalize_timeframes([" 1m", "", "5m"]), ["1m", "5m"])
        self.assertEqual(normalize_timeframes(None, default=["5m"]), ["5m"])

    def test_convert_uses_open_time_labels(self):
        times = pd.date_range("2026-01-01 00:00", periods=10, freq="1min")
        df = pd.DataFrame({"Time": times, "Open": range(10), "High": range(10), "Low": range(10),
                           "Close": range(10), "Volume": [1] * 10})
        out = convert_timeframe(df, "5m")
        self.assertEqual(list(out["Time"].dt.strftime("%H:%M")), ["00:00", "00:05"])
        self.assertEqual(list(out["Open"]), [0, 5])
        self.assertEqual(list(out["Close"]), [4, 9])
        self.assertEqual(list(out["Volume"]), [5, 5])


class SymbolTest(unittest.TestCase):
    def test_strip_timeframe(self):
        self.assertEqual(strip_timeframe("BTCUSDT_1m"), "BTCUSDT")
        self.assertEqual(strip_timeframe("BTC/USDT:USDT_15m"), "BTC/USDT:USDT")
        self.assertEqual(strip_timeframe("ETHUSDT"), "ETHUSDT")

    def test_symbol_from_filename(self):
        self.assertEqual(symbol_from_filename("BTCUSDT_1m.csv"), "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
