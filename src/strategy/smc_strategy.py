"""
SMC Strategy - Order Block + CHoCH + Liquidity
=====================================================
Smart Money Concepts trading strategy based on:
- Market Structure (BOS/CHoCH)
- Order Blocks (OB) - specifically CHoCH Order Blocks
- Equal Highs/Lows (EQH/EQL) - Liquidity zones

Entry Logic:
-----------
LONG Setup:
1. CHoCH Bullish detected (trend reversal from bearish to bullish)
2. Identify Order Block that created the CHoCH
3. After CHoCH, EQH must form while waiting for price to return to OB
4. Price touches OB zone -> Entry LONG
5. SL: Below OB with buffer (avoid stop hunt)
6. TP: Entry + (Entry - SL) * RR (fixed RR = 2)

SHORT Setup: (opposite)
1. CHoCH Bearish detected
2. Identify Order Block that created the CHoCH
3. After CHoCH, EQL must form while waiting for price to return to OB
4. Price touches OB zone -> Entry SHORT
5. SL: Above OB with buffer
6. TP: Entry - (SL - Entry) * RR

INTERFACE CONTRACT (must export for main.py):
- Config: dataclass with strategy configuration
- Signal: dataclass representing a trade signal
- Strategy: class with generate_signals(df, symbol) -> List[Signal] method

Uses indicator modules from src/indicators for OB, CHoCH, BOS, EQH/EQL detection.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Optional, Any, Tuple, cast
from dataclasses import dataclass, field
from numba import njit

from src.strategy.base_strategy import (
    BaseConfig,
    BaseSignal,
    BaseStrategy,
    BaseZone,
    parse_timeframe_minutes,
)
from src.indicators.reversal_patterns import check_reversal_pattern_jit, check_pattern_by_ids_jit
from src.utils.rounding import round_price

from src.indicators.CHOCH import detect_choch, CHoCH
from src.indicators.BOS import detect_bos, BOS
from src.indicators.order_block import detect_order_blocks as detect_order_blocks_indicator
from src.indicators.order_block import OrderBlock as IndicatorOrderBlock
from src.indicators.eqh_eql import detect_eqh_eql, EqualHL as IndicatorEqualHL


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "SMC"


# ==================== CONFIGURATION ====================
@dataclass
class Config(BaseConfig):
    """
    SMC Trading strategy configuration.

    Inherits generic fields from BaseConfig:
    - data_folder, output_folder
    - signal_timeframe, entry_timeframe
    - mitigation_mode, touch_tolerance_pct, touch_confirmation_bars, entry_on_candle_close
    - risk_reward_ratio, sl_buffer_pct, sl_multiplier, min_sl_pct, risk_per_trade

    Below are SMC-specific parameters only.
    """
    # === STRUCTURE DETECTION ===
    swing_length: int = 10          # Lookback for swing structure
    internal_length: int = 3        # Lookback for internal structure

    # === EQH/EQL DETECTION ===
    eqhl_length: int = 3            # Smaller length for EQH/EQL detection
    eqhl_threshold: float = 0.3     # ATR multiplier for EQH/EQL sensitivity

    # === ORDER BLOCK ===
    ob_body_ratio: float = 0.35     # Candle body size / candle range must be > 35%

    # === ENTRY FILTERS ===
    require_liquidity: bool = True          # Require EQH/EQL liquidity event after OB break
    require_reversal_pattern: bool = True   # Require reversal pattern before entry
    enable_simple_reversal: bool = True     # Allow simpler rejection/displacement reversal
    simple_reversal_wick_ratio: float = 0.25  # Min wick/range ratio for simple reversal

    # === FVG ===
    fvg_atr_filter: bool = True     # (kept for compatibility; logic removed from strategy)

    # === REVERSAL PATTERNS ===
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])

    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "signal_timeframe": self.signal_timeframe,
            "entry_timeframe": self.entry_timeframe,
            "swing_length": self.swing_length,
            "internal_length": self.internal_length,
            "eqhl_length": self.eqhl_length,
            "eqhl_threshold": self.eqhl_threshold,
            "ob_detection": "LuxAlgo extreme candle + trending candle filter",
            "ob_body_ratio": self.ob_body_ratio,
            "require_liquidity": self.require_liquidity,
            "require_reversal_pattern": self.require_reversal_pattern,
            "enable_simple_reversal": self.enable_simple_reversal,
            "simple_reversal_wick_ratio": self.simple_reversal_wick_ratio,
            "touch_tolerance_pct": self.touch_tolerance_pct,
            "touch_confirmation_bars": self.touch_confirmation_bars,
            "entry_on_candle_close": self.entry_on_candle_close,
            "sl_buffer_pct": self.sl_buffer_pct,
            "sl_multiplier": self.sl_multiplier,
            "min_sl_pct": self.min_sl_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
            "reversal_pattern_ids": self.reversal_pattern_ids,
        }


# ==================== DATA CLASSES ====================
@dataclass
class StrategyOrderBlock(BaseZone):
    """
    Represents an Order Block zone used in SMC strategy logic.
    Extends BaseZone with SMC-specific tracking fields.
    """
    index: int = 0
    structure_type: str = 'BOS'     # 'CHoCH' or 'BOS'
    break_index: int = 0
    is_mitigated: bool = False
    has_liquidity: bool = False
    liquidity_confirmed_index: int = -1
    entry_triggered: bool = False
    last_touch_index: int = -1


@dataclass
class Signal(BaseSignal):
    """
    Represents a trade signal from SMC strategy.
    Only contains entry information - no exit data.
    """
    pass


# ==================== NUMBA JIT HELPER ====================

@njit
def _check_trending_candle_jit(opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                               lows: np.ndarray, candle_idx: int, body_ratio_threshold: float) -> bool:
    """
    Check if a candle is a trending candle.
    Body = abs(close - open), Range = high - low
    Returns True if body/range > body_ratio_threshold.
    """
    body = abs(closes[candle_idx] - opens[candle_idx])
    candle_range = highs[candle_idx] - lows[candle_idx]
    if candle_range == 0:
        return False
    return (body / candle_range) > body_ratio_threshold


# ==================== STRATEGY CLASS ====================

class Strategy(BaseStrategy):
    """
    SMC Strategy - generates trade signals based on:
    - CHoCH/BOS (from src/indicators)
    - Order Blocks (from src/indicators, filtered by ob_body_ratio)
    - Equal Highs/Lows (from src/indicators)

    Multi-timeframe entry logic, zone touch/mitigation, SL/TP computation,
    and signal creation are inherited from BaseStrategy.
    """
    def __init__(self, config: Config):
        super().__init__(config)
        self.config: Config = config  # type narrowing: BaseConfig → Config

    def generate_signals(self, df: pd.DataFrame, symbol: str, df_entry: pd.DataFrame | None = None) -> List[Signal]:
        """
        Generate trade signals based on SMC concepts.

        MULTI-TIMEFRAME MODE (when df_entry is provided):
        - Uses df (signal timeframe, e.g., 5m) for indicator calculation
        - Uses df_entry (entry timeframe, e.g., 1m) for precise entry detection
        - This prevents repainting by finding the FIRST entry-TF candle that touches OB

        SINGLE-TIMEFRAME MODE (when df_entry is None):
        - Uses df for both indicator calculation and entry
        - This is the legacy behavior

        EVENT-TIME GATED: Pre-computes indicators, but consumes them causally.
        Each event (OB, EQH/EQL) is only activated at the bar it becomes known.
        Mitigation is computed bar-by-bar — never uses indicator's pre-computed is_mitigated.

        NO LOOK-AHEAD BIAS:
        - OB activated at break_index (structure break bar)
        - EQH/EQL activated at confirmed_index (confirmation bar, not pivot bar)
        - OB mitigation checked at each bar using current price only
        - has_liquidity updated incrementally as events arrive
        - CHoCH is not required during retrace
        """
        df = df.copy()
        signals: List[BaseSignal] = []

        # Determine if we're in multi-timeframe mode
        use_multi_tf = df_entry is not None and not df_entry.empty
        signal_tf_minutes = parse_timeframe_minutes(self.config.signal_timeframe) if use_multi_tf else 0

        highs = df['High'].values.astype(np.float64)
        lows = df['Low'].values.astype(np.float64)
        closes = df['Close'].values.astype(np.float64)
        opens = df['Open'].values.astype(np.float64)
        times = df['Time'].values
        n = len(df)

        if n == 0:
            return []

        # ========== 1. PRE-COMPUTE INDICATORS ==========
        mitigation_mode = 'CLOSE' if self.config.mitigation_mode == 'close' else 'HIGHLOW'

        choch_list = detect_choch(
            df,
            swing_length=self.config.swing_length,
            internal_length=self.config.internal_length,
            detect_swing=True,
            detect_internal=True,
        )

        bos_list = detect_bos(
            df,
            swing_length=self.config.swing_length,
            internal_length=self.config.internal_length,
            detect_swing=True,
            detect_internal=True,
        )

        ob_list = detect_order_blocks_indicator(
            df,
            swing_length=self.config.swing_length,
            internal_length=self.config.internal_length,
            mitigation_mode=mitigation_mode,
            detect_swing=True,
            detect_internal=True,
        )

        eqhl_list = detect_eqh_eql(
            df,
            bars_confirmation=self.config.eqhl_length,
            threshold=self.config.eqhl_threshold,
        )

        # ========== 2. BUILD EVENT QUEUES (indexed by activation bar) ==========

        # OBs: filter by body_ratio, group by break_index (activation bar)
        # IGNORE indicator's is_mitigated — we compute mitigation ourselves bar-by-bar
        obs_by_break: dict[int, List[StrategyOrderBlock]] = {}
        for ob in ob_list:
            if not _check_trending_candle_jit(opens, closes, highs, lows,
                                              ob.index, self.config.ob_body_ratio):
                continue

            structure_type = self._classify_ob_structure(ob, choch_list, bos_list)

            sob = StrategyOrderBlock(
                high=ob.high,
                low=ob.low,
                time=ob.time,
                bias=ob.bias,
                index=ob.index,
                structure_type=structure_type,
                break_index=ob.break_index,
                is_mitigated=False,
                has_liquidity=False,
                liquidity_confirmed_index=-1,
            )
            obs_by_break.setdefault(ob.break_index, []).append(sob)

        # EQH/EQL: group by confirmed_index (confirmation bar, NOT pivot bar)
        eqhl_by_confirm: dict[int, List[IndicatorEqualHL]] = {}
        for eqhl in eqhl_list:
            ci = eqhl.confirmed_index
            eqhl_by_confirm.setdefault(ci, []).append(eqhl)

        # ========== 3. CAUSAL BAR-BY-BAR LOOP ==========
        active_obs: List[StrategyOrderBlock] = []

        for i in range(n):
            current_time = self._parse_datetime(times[i])
            if current_time is None:
                continue

            # 3a. Activate OBs whose structure break happens at this bar
            for ob in obs_by_break.get(i, []):
                active_obs.append(ob)

            # 3c. Process EQH/EQL events confirmed at this bar → update has_liquidity
            for eqhl in eqhl_by_confirm.get(i, []):
                for ob in active_obs:
                    if ob.has_liquidity:
                        continue
                    if eqhl.confirmed_index <= ob.break_index:
                        continue
                    if ob.bias == 'BULLISH' and eqhl.type == 'EQH':
                        ob.has_liquidity = True
                        ob.liquidity_confirmed_index = int(eqhl.confirmed_index)
                    elif ob.bias == 'BEARISH' and eqhl.type == 'EQL':
                        ob.has_liquidity = True
                        ob.liquidity_confirmed_index = int(eqhl.confirmed_index)

            # 3d. Check mitigation & entry for each active OB
            remaining_obs = []
            for ob in active_obs:
                # Use base class zone mitigation check
                if self._zone_is_mitigated(ob, highs[i], lows[i], closes[i]):
                    continue

                # Use base class zone touch check
                touches_ob = self._price_touches_zone(
                    ob,
                    highs[i],
                    lows[i],
                    self.config.touch_tolerance_pct,
                )

                if touches_ob:
                    ob.last_touch_index = i

                touch_window = max(0, self.config.touch_confirmation_bars)
                touch_recent = ob.last_touch_index >= 0 and (i - ob.last_touch_index) <= touch_window
                entry_window_active = touch_recent if touch_window > 0 else touches_ob

                if not ob.entry_triggered and entry_window_active:
                    liquidity_ok = (
                        (not self.config.require_liquidity)
                        or (
                            ob.has_liquidity
                            and ob.liquidity_confirmed_index >= 0
                            and ob.liquidity_confirmed_index < i
                        )
                    )

                    pattern_ok = self._is_reversal_candle(opens, closes, highs, lows, i, ob.bias)
                    simple_reversal_ok = self.config.enable_simple_reversal and self._is_simple_reversal(
                        opens, closes, highs, lows, i, ob.bias
                    )
                    reversal_ok = (
                        (pattern_ok or simple_reversal_ok)
                        if self.config.require_reversal_pattern
                        else True
                    )

                    if liquidity_ok and reversal_ok:
                        # MULTI-TIMEFRAME MODE: Find precise entry from entry-TF candles
                        if use_multi_tf and df_entry is not None:
                            signal_candle_close_time = current_time + timedelta(minutes=signal_tf_minutes)

                            # Use base class multi-TF entry detection
                            precise_entry = self._find_precise_entry(
                                df_entry=df_entry,
                                signal_candle_close_time=signal_candle_close_time,
                                signal_tf_minutes=signal_tf_minutes,
                                zone=ob,
                            )
                            if precise_entry:
                                entry_time_1m, entry_price_1m = precise_entry
                                # Use base class signal creation (multi-TF)
                                signal = self._create_signal_from_entry(
                                    symbol=symbol,
                                    zone=ob,
                                    entry_time=entry_time_1m,
                                    entry_price=entry_price_1m,
                                )
                                if signal:
                                    signals.append(signal)
                                    ob.entry_triggered = True
                        else:
                            # SINGLE-TIMEFRAME MODE: Use base class signal creation
                            signal = self._create_signal(
                                symbol=symbol,
                                zone=ob,
                                entry_time=current_time,
                                current_high=highs[i],
                                current_low=lows[i],
                                current_close=closes[i],
                                previous_close=closes[i - 1] if i > 0 else closes[i],
                            )
                            if signal:
                                signals.append(signal)
                                ob.entry_triggered = True

                remaining_obs.append(ob)

            active_obs = remaining_obs

        # ========== 4. DEDUPLICATE SIGNALS ==========
        deduped = self._deduplicate_signals(signals)

        return cast(List[Signal], deduped)

    # ==================== SMC-SPECIFIC HELPER METHODS ====================

    def _classify_ob_structure(self, ob: IndicatorOrderBlock,
                               choch_list: List[CHoCH],
                               bos_list: List[BOS]) -> str:
        """Classify whether an OB came from CHoCH or BOS by matching break_index."""
        for c in choch_list:
            if c.break_index == ob.break_index and c.bias == ob.bias:
                return 'CHoCH'
        for b in bos_list:
            if b.break_index == ob.break_index and b.bias == ob.bias:
                return 'BOS'
        return 'BOS'

    def _is_reversal_candle(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, current_idx: int, ob_bias: str) -> bool:
        """Check if current candle is a reversal pattern."""
        is_bullish = (ob_bias == 'BULLISH')
        pattern_ids = np.array(self.config.reversal_pattern_ids, dtype=np.int64)

        if len(pattern_ids) == 1 and pattern_ids[0] == 0:
            return check_reversal_pattern_jit(opens, closes, highs, lows, current_idx, is_bullish)
        return check_pattern_by_ids_jit(opens, closes, highs, lows, current_idx, is_bullish, pattern_ids)

    def _is_simple_reversal(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                            lows: np.ndarray, current_idx: int, ob_bias: str) -> bool:
        """
        Lightweight reversal check to catch clean rejection setups that strict
        candlestick patterns can miss.

        Bullish: bullish close + (meaningful lower wick OR stronger close than previous bar)
        Bearish: bearish close + (meaningful upper wick OR weaker close than previous bar)
        """
        if current_idx <= 0:
            return False

        o_curr = opens[current_idx]
        c_curr = closes[current_idx]
        h_curr = highs[current_idx]
        l_curr = lows[current_idx]
        c_prev = closes[current_idx - 1]

        candle_range = h_curr - l_curr
        if candle_range <= 0:
            return False

        wick_ratio = max(0.0, self.config.simple_reversal_wick_ratio)

        if ob_bias == 'BULLISH':
            lower_wick = min(o_curr, c_curr) - l_curr
            wick_ok = (lower_wick / candle_range) >= wick_ratio
            displacement_ok = c_curr > c_prev
            return c_curr > o_curr and (wick_ok or displacement_ok)

        upper_wick = h_curr - max(o_curr, c_curr)
        wick_ok = (upper_wick / candle_range) >= wick_ratio
        displacement_ok = c_curr < c_prev
        return c_curr < o_curr and (wick_ok or displacement_ok)
