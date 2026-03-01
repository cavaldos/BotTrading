from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Sequence

import pandas as pd


@dataclass
class BaseConfig:
    data_folder: str = "res/data"
    output_folder: str = "res/output"
    risk_reward_ratio: float = 2.0

    # Multi-timeframe settings to avoid repainting
    signal_timeframe: str = "5m"    # Timeframe for indicator calculation (CHoCH, OB, EQH/EQL)
    entry_timeframe: str = "1m"     # Timeframe for precise entry detection

    swing_length: int = 10
    internal_length: int = 3
    eqhl_length: int = 3
    eqhl_threshold: float = 0.3
    ob_mitigation: str = "close"
    ob_body_ratio: float = 0.35

    require_liquidity: bool = True
    require_reversal_pattern: bool = True
    enable_simple_reversal: bool = True
    simple_reversal_wick_ratio: float = 0.25
    touch_tolerance_pct: float = 0.0
    touch_confirmation_bars: int = 0
    entry_on_candle_close: bool = False

    fvg_atr_filter: bool = True

    sl_buffer_pct: float = 0.1
    sl_multiplier: float = 1.05
    min_sl_pct: float = 0.05
    risk_per_trade: float = 10.0
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])

    def get_strategy_info(self) -> Dict[str, Any]:
        return {
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
            "sl_multiplier": self.sl_multiplier,
            "signal_timeframe": self.signal_timeframe,
            "entry_timeframe": self.entry_timeframe,
        }


@dataclass
class BaseSignal:
    symbol: str
    trade_type: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    signal_timeframe: str = ""


class BaseStrategy(ABC):
    def __init__(self, config: BaseConfig):
        self.config = config

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, symbol: str, df_entry: pd.DataFrame | None = None) -> Sequence[BaseSignal]:
        """
        Generate trade signals.
        
        Args:
            df: DataFrame for signal timeframe (e.g., 5m) - used for indicator calculation
            symbol: Trading symbol
            df_entry: Optional DataFrame for entry timeframe (e.g., 1m) - used for precise entry detection
                     If provided, enables multi-timeframe mode to avoid repainting
        
        Returns:
            List of signals
        """
        raise NotImplementedError
