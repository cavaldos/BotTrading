from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.utils.rounding import round_price


# ==================== UTILITY ====================

def parse_timeframe_minutes(tf: str) -> int:
    """
    Convert timeframe string to minutes.
    E.g. '1m'->1, '5m'->5, '15m'->15, '1h'->60, '4h'->240, '1d'->1440
    """
    tf = tf.lower().strip()
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 1  # fallback to 1 minute


# ==================== DATA CLASSES ====================

@dataclass
class BaseConfig:
    """
    Base configuration with multi-timeframe and risk management settings.

    Only contains fields that are GENERIC to any trading strategy:
    - System paths
    - Multi-timeframe settings (anti-repainting)
    - Zone mitigation / entry detection
    - Risk management (SL/TP calculation)

    Strategy-specific parameters (indicators, filters, etc.) should be
    defined in the concrete strategy Config subclass.
    """
    # System
    data_folder: str = "res/data"
    output_folder: str = "res/output"

    # Multi-timeframe settings (anti-repainting)
    signal_timeframe: str = "5m"    # Timeframe for indicator calculation
    entry_timeframe: str = "1m"     # Timeframe for precise entry detection

    # Zone mitigation mode: "close" or "high_low"
    mitigation_mode: str = "close"

    # Entry detection
    touch_tolerance_pct: float = 0.0
    touch_confirmation_bars: int = 0
    entry_on_candle_close: bool = False

    # Risk management
    risk_reward_ratio: float = 2.0
    sl_buffer_pct: float = 0.1
    sl_multiplier: float = 1.05
    min_sl_pct: float = 0.05
    risk_per_trade: float = 10.0

    def get_strategy_info(self) -> Dict[str, Any]:
        return {
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
            "sl_multiplier": self.sl_multiplier,
            "signal_timeframe": self.signal_timeframe,
            "entry_timeframe": self.entry_timeframe,
        }


@dataclass
class BaseZone:
    """
    Generic price zone used by multi-timeframe entry logic.

    This represents any trading zone (Order Block, Supply/Demand, FVG, etc.)
    that has a high/low boundary and a directional bias.

    Concrete strategies should subclass this to add strategy-specific fields
    (e.g., structure_type, break_index, etc.).
    """
    high: float
    low: float
    time: datetime
    bias: str  # 'BULLISH' or 'BEARISH'


@dataclass
class BaseSignal:
    symbol: str
    trade_type: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    signal_timeframe: str = ""


# ==================== BASE STRATEGY ====================

class BaseStrategy(ABC):
    """
    Abstract base strategy with multi-timeframe entry logic.

    Provides reusable methods for:
    - Multi-TF precise entry detection (anti-repainting)
    - Zone touch / mitigation checks
    - SL/TP computation
    - Signal creation (single-TF and multi-TF modes)
    - Signal deduplication

    Subclasses only need to implement generate_signals() with their own
    indicator logic. Call the base methods for entry detection and signal
    creation to avoid re-implementing the multi-TF pipeline.
    """

    def __init__(self, config: BaseConfig):
        self.config = config

    @abstractmethod
    def generate_signals(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_entry: pd.DataFrame | None = None,
    ) -> Sequence[BaseSignal]:
        """
        Generate trade signals.

        Args:
            df: DataFrame for signal timeframe (e.g., 5m) - used for indicator calculation
            symbol: Trading symbol
            df_entry: Optional DataFrame for entry timeframe (e.g., 1m) - used for precise entry.
                     If provided, enables multi-timeframe mode to avoid repainting.

        Returns:
            List of signals
        """
        raise NotImplementedError

    # ==================== UTILITY METHODS ====================

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        ts = pd.Timestamp(value)
        if ts is pd.NaT:
            return None
        parsed = ts.to_pydatetime()
        if isinstance(parsed, datetime):
            return parsed
        return None

    def _deduplicate_signals(self, signals: List[BaseSignal]) -> List[BaseSignal]:
        seen_keys: set[tuple[Any, ...]] = set()
        unique_signals: List[BaseSignal] = []
        for signal in signals:
            key = (signal.entry_time, signal.trade_type, signal.signal_timeframe)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_signals.append(signal)
        return unique_signals

    # ==================== ZONE METHODS ====================

    def _price_touches_zone(
        self,
        zone: BaseZone,
        high: float,
        low: float,
        tolerance_pct: float = 0.0,
    ) -> bool:
        """Check if current candle's price range touches the zone (with optional tolerance)."""
        zone_range = max(0.0, zone.high - zone.low)
        tolerance = zone_range * max(0.0, tolerance_pct)
        return low <= (zone.high + tolerance) and high >= (zone.low - tolerance)

    def _zone_is_mitigated(
        self,
        zone: BaseZone,
        high: float,
        low: float,
        close: float,
    ) -> bool:
        """Check if zone is fully mitigated (invalidated by price)."""
        if self.config.mitigation_mode == "close":
            if zone.bias == 'BULLISH':
                return close < zone.low
            else:
                return close > zone.high
        else:
            if zone.bias == 'BULLISH':
                return low < zone.low
            else:
                return high > zone.high

    def _select_entry_price(
        self,
        zone: BaseZone,
        current_high: float,
        current_low: float,
        previous_close: float,
    ) -> Optional[float]:
        """
        Select entry price inside the overlap between candle range and zone.
        Prioritizes the price nearest to previous candle close.
        """
        overlap_low = max(zone.low, current_low)
        overlap_high = min(zone.high, current_high)

        if overlap_low > overlap_high:
            return None

        reference_price = previous_close
        if not np.isfinite(reference_price):
            reference_price = (overlap_low + overlap_high) / 2.0

        return min(max(reference_price, overlap_low), overlap_high)

    # ==================== MULTI-TF ENTRY ====================

    def _find_precise_entry(
        self,
        df_entry: pd.DataFrame,
        signal_candle_close_time: datetime,
        signal_tf_minutes: int,
        zone: BaseZone,
        max_bars_to_search: int = 60,
    ) -> Optional[Tuple[datetime, float]]:
        """
        Find the precise entry point using entry-TF candles AFTER the signal candle closes.

        ANTI-REPAINTING: Only searches for entries AFTER the signal timeframe candle
        has closed. We cannot know about a signal until the signal candle closes.

        Args:
            df_entry: DataFrame with entry-TF candles (must have Time, High, Low, Close)
            signal_candle_close_time: The CLOSE time of the signal candle
                For a 5m candle that opens at 08:30, the close time is 08:35
            signal_tf_minutes: Signal timeframe in minutes (e.g., 5 for 5m)
            zone: The price zone being tested (BaseZone or subclass)
            max_bars_to_search: Maximum entry-TF bars to search after signal close

        Returns:
            (entry_time, entry_price) for the first entry-TF candle that touches zone,
            or None if no valid entry found within max_bars_to_search.

        Example:
            Signal candle (5m) opens at 08:30, closes at 08:35
            signal_candle_close_time = 08:35

            We search 1m candles starting from 08:35:
            - 08:35 Close = 10.1 -> not in zone range -> skip
            - 08:36 Close = 9.95 -> in zone range -> RETURN (08:36, 9.95)

            We do NOT look at 08:31, 08:32, etc. because those are BEFORE
            the signal candle closed - that would be look-ahead bias!
        """
        if df_entry is None or df_entry.empty:
            return None

        df_entry = df_entry.copy()
        df_entry['Time'] = pd.to_datetime(df_entry['Time'])

        # Search window: from signal_candle_close_time onwards (AFTER signal close)
        window_start = signal_candle_close_time
        window_end = signal_candle_close_time + timedelta(minutes=max_bars_to_search)

        mask = (df_entry['Time'] >= window_start) & (df_entry['Time'] <= window_end)
        candles_in_window = df_entry[mask].sort_values('Time')

        if candles_in_window.empty:
            return None

        # Iterate through entry-TF candles in chronological order
        for _, candle in candles_in_window.iterrows():
            close_price = candle['Close']
            low_price = candle['Low']
            high_price = candle['High']
            candle_time = candle['Time']

            zone_tolerance = (zone.high - zone.low) * self.config.touch_tolerance_pct

            if zone.bias == 'BULLISH':
                # For long entry: price touches bullish zone from above
                if low_price <= (zone.high + zone_tolerance) and high_price >= (zone.low - zone_tolerance):
                    overlap_low = max(zone.low, low_price)
                    overlap_high = min(zone.high, high_price)
                    if overlap_low <= overlap_high:
                        entry_price = min(max(close_price, zone.low), zone.high)
                        entry_time = candle_time.to_pydatetime() if hasattr(candle_time, 'to_pydatetime') else candle_time
                        return (entry_time, entry_price)
            else:
                # For short entry: price touches bearish zone from below
                if high_price >= (zone.low - zone_tolerance) and low_price <= (zone.high + zone_tolerance):
                    overlap_low = max(zone.low, low_price)
                    overlap_high = min(zone.high, high_price)
                    if overlap_low <= overlap_high:
                        entry_price = min(max(close_price, zone.low), zone.high)
                        entry_time = candle_time.to_pydatetime() if hasattr(candle_time, 'to_pydatetime') else candle_time
                        return (entry_time, entry_price)

        return None

    # ==================== SL/TP COMPUTATION ====================

    def _compute_sl_tp(
        self,
        entry: float,
        zone: BaseZone,
        trade_type: str,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Optional[Tuple[float, float]]:
        """
        Compute stop-loss and take-profit for a trade signal.

        Args:
            entry: Entry price
            zone: The price zone that triggered the signal
            trade_type: 'LONG' or 'SHORT'
            candle_high: Current candle high (for single-TF mode SL validation).
                        If None, uses zone boundary instead (multi-TF mode).
            candle_low: Current candle low (for single-TF mode SL validation).
                       If None, uses zone boundary instead (multi-TF mode).

        Returns:
            (stop_loss, take_profit) tuple, or None if SL position is invalid
        """
        zone_range = zone.high - zone.low
        buffer = zone_range * self.config.sl_buffer_pct

        if trade_type == 'LONG':
            base_sl = zone.low - buffer
            base_risk = entry - base_sl
            stop_loss = entry - (base_risk * self.config.sl_multiplier)

            # SL validation: use candle low for single-TF, zone low for multi-TF
            reference_low = candle_low if candle_low is not None else zone.low
            if stop_loss > reference_low:
                stop_loss = reference_low - buffer

            actual_risk = entry - stop_loss
            take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
        else:
            base_sl = zone.high + buffer
            base_risk = base_sl - entry
            stop_loss = entry + (base_risk * self.config.sl_multiplier)

            reference_high = candle_high if candle_high is not None else zone.high
            if stop_loss < reference_high:
                stop_loss = reference_high + buffer

            actual_risk = stop_loss - entry
            take_profit = entry - (actual_risk * self.config.risk_reward_ratio)

        # Validate SL direction
        if trade_type == 'LONG' and stop_loss >= entry:
            return None
        if trade_type == 'SHORT' and stop_loss <= entry:
            return None

        # Enforce minimum SL percentage
        sl_pct = abs(entry - stop_loss) / entry * 100
        if sl_pct < self.config.min_sl_pct:
            min_sl_distance = entry * self.config.min_sl_pct / 100
            if trade_type == 'LONG':
                stop_loss = entry - min_sl_distance
            else:
                stop_loss = entry + min_sl_distance
            actual_risk = abs(entry - stop_loss)
            if trade_type == 'LONG':
                take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
            else:
                take_profit = entry - (actual_risk * self.config.risk_reward_ratio)

        return (stop_loss, take_profit)

    # ==================== SIGNAL CREATION ====================

    def _create_signal(
        self,
        symbol: str,
        zone: BaseZone,
        entry_time: datetime,
        current_high: float,
        current_low: float,
        current_close: float,
        previous_close: float,
    ) -> Optional[BaseSignal]:
        """
        Create a trade signal for SINGLE-TIMEFRAME mode.
        Entry is selected from candle/zone overlap or candle close.

        LONG:
        - Entry: Candle close (if entry_on_candle_close) or zone overlap price
        - SL: Below zone low with buffer
        - TP: Entry + (Entry - SL) * RR

        SHORT: (opposite)
        """
        trade_type = 'LONG' if zone.bias == 'BULLISH' else 'SHORT'

        if self.config.entry_on_candle_close:
            entry = current_close if np.isfinite(current_close) else None
        else:
            entry = self._select_entry_price(
                zone=zone,
                current_high=current_high,
                current_low=current_low,
                previous_close=previous_close,
            )
        if entry is None:
            return None

        result = self._compute_sl_tp(
            entry, zone, trade_type,
            candle_high=current_high,
            candle_low=current_low,
        )
        if result is None:
            return None
        stop_loss, take_profit = result

        return BaseSignal(
            symbol=symbol,
            trade_type=trade_type,
            entry_time=entry_time,
            entry_price=round_price(entry),
            stop_loss=round_price(stop_loss),
            take_profit=round_price(take_profit),
            signal_timeframe=self.config.signal_timeframe,
        )

    def _create_signal_from_entry(
        self,
        symbol: str,
        zone: BaseZone,
        entry_time: datetime,
        entry_price: float,
    ) -> Optional[BaseSignal]:
        """
        Create a trade signal for MULTI-TIMEFRAME mode with a precise entry price.
        Used when we already have a precise entry from entry-TF candles.

        Args:
            symbol: Trading symbol
            zone: The price zone that triggered the signal
            entry_time: Precise entry time from entry-TF candle
            entry_price: Precise entry price (Close of entry-TF candle)
        """
        trade_type = 'LONG' if zone.bias == 'BULLISH' else 'SHORT'

        # Multi-TF mode: no candle_high/candle_low → uses zone boundaries for SL
        result = self._compute_sl_tp(entry_price, zone, trade_type)
        if result is None:
            return None
        stop_loss, take_profit = result

        return BaseSignal(
            symbol=symbol,
            trade_type=trade_type,
            entry_time=entry_time,
            entry_price=round_price(entry_price),
            stop_loss=round_price(stop_loss),
            take_profit=round_price(take_profit),
            signal_timeframe=self.config.signal_timeframe,
        )
