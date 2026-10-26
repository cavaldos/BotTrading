"""Symbol helpers shared by data files, strategies and exchanges."""

from __future__ import annotations

import re

_TIMEFRAME_SUFFIX = re.compile(r'_\d+[mhd]$')


def strip_timeframe(symbol: str) -> str:
    """'BTCUSDT_1m' -> 'BTCUSDT', 'BTC/USDT:USDT_5m' -> 'BTC/USDT:USDT' (other symbols unchanged)."""
    return _TIMEFRAME_SUFFIX.sub('', str(symbol).strip())


def symbol_from_filename(filename: str) -> str:
    """'BTCUSDT_1m.csv' -> 'BTCUSDT' (the part before the last '_')."""
    name = str(filename).replace('.csv', '')
    parts = name.rsplit('_', 1)
    return parts[0] if len(parts) > 1 else name
