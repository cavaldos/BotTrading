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
from typing import List, Optional, Any, Tuple
from dataclasses import dataclass, field
from numba import njit

from src.strategy.base_strategy import BaseConfig, BaseSignal, BaseStrategy
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
    """
    # === SYSTEM SETTINGS (set by main.py) ===
    data_folder: str = "res/data"
    output_folder: str = "res/output"
    risk_reward_ratio: float = 2.0  # Loaded from YAML config
    
    # === MULTI-TIMEFRAME SETTINGS (anti-repainting) ===
    signal_timeframe: str = "5m"    # Timeframe for indicator calculation
    entry_timeframe: str = "1m"     # Timeframe for precise entry detection
    
    # === STRUCTURE DETECTION ===
    swing_length: int = 10          # Lookback for swing structure (adjusted for 15m/30m)
    internal_length: int = 3        # Lookback for internal structure
    
    # === EQH/EQL DETECTION ===
    eqhl_length: int = 3            # Smaller length for EQH/EQL detection (faster detection)
    eqhl_threshold: float = 0.3     # ATR multiplier for EQH/EQL sensitivity (increased)
    
    # === ORDER BLOCK ===
    ob_mitigation: str = "close" # "close" or "high_low"
    ob_body_ratio: float = 0.35      # Candle body size / candle range must be > 35% (trending candle filter)

    # === ENTRY FILTERS ===
    require_liquidity: bool = True    # Require EQH/EQL liquidity event after OB break
    require_reversal_pattern: bool = True  # Require reversal pattern before entry
    enable_simple_reversal: bool = True    # Allow simpler rejection/displacement reversal
    simple_reversal_wick_ratio: float = 0.25  # Min wick/range ratio for simple reversal
    touch_tolerance_pct: float = 0.0    # OB touch tolerance as % of OB range
    touch_confirmation_bars: int = 0    # Allow entry within N bars after first touch (0 = same bar only)
    entry_on_candle_close: bool = False # If True, execute at signal candle close instead of intrabar OB overlap
    
    # === FVG ===
    fvg_atr_filter: bool = True     # (kept for compatibility; logic removed from strategy)

    # === RISK MANAGEMENT ===
    sl_buffer_pct: float = 0.1      # Buffer SL (% of OB range)
    sl_multiplier: float = 1.05     # Hệ số mở rộng SL (1.0 = bình thường, 1.1 = mở rộng 10% tránh stop hunt)
    min_sl_pct: float = 0.05         # Minimum SL % - không đặt lệnh nếu SL < x% giá entry
    risk_per_trade: float = 10.0    # USD
    
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0]) # Use all reversal patterns by default
    
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
class StrategyOrderBlock:
    """Represents an Order Block zone used in strategy logic"""
    high: float
    low: float
    time: datetime
    index: int
    bias: str               # 'BULLISH' or 'BEARISH'
    structure_type: str     # 'CHoCH' or 'BOS'
    break_index: int
    is_mitigated: bool = False
    has_liquidity: bool = False
    liquidity_confirmed_index: int = -1
    entry_triggered: bool = False
    last_touch_index: int = -1


@dataclass
class Signal(BaseSignal):
    """
    Represents a trade signal from strategy.
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


# ==================== TIMEFRAME UTILITIES ====================

def _parse_timeframe_minutes(tf: str) -> int:
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


# ==================== STRATEGY CLASS ====================

class Strategy(BaseStrategy):
    """
    SMC Strategy - generates trade signals based on:
    - CHoCH/BOS (from src/indicators)
    - Order Blocks (from src/indicators, filtered by ob_body_ratio)
    - Equal Highs/Lows (from src/indicators)
    """
    
    def __init__(self, config: Config):
        super().__init__(config)

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        ts = pd.Timestamp(value)
        if ts is pd.NaT:
            return None
        parsed = ts.to_pydatetime()
        if isinstance(parsed, datetime):
            return parsed
        return None

    def _deduplicate_signals(self, signals: List[Signal]) -> List[Signal]:
        seen_keys = set()
        unique_signals: List[Signal] = []
        for signal in signals:
            key = (signal.entry_time, signal.trade_type, signal.signal_timeframe)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_signals.append(signal)
        return unique_signals

    def _find_precise_entry_1m(
        self,
        df_entry: pd.DataFrame,
        signal_candle_close_time: datetime,
        signal_tf_minutes: int,
        ob: 'StrategyOrderBlock',
        max_bars_to_search: int = 60,
    ) -> Optional[Tuple[datetime, float]]:
        """
        Find the precise entry point using 1m candles AFTER the signal candle closes.
        
        CRITICAL: This method prevents look-ahead bias by only searching for entries
        AFTER the signal timeframe candle has closed. We cannot know about a signal
        until the signal candle closes.
        
        Args:
            df_entry: DataFrame with 1m candles (must have Time, High, Low, Close columns)
            signal_candle_close_time: The CLOSE time of the signal candle (not open time!)
                For a 5m candle that opens at 08:30, the close time is 08:35
            signal_tf_minutes: Signal timeframe in minutes (e.g., 5 for 5m)
            ob: The Order Block being tested
            max_bars_to_search: Maximum number of 1m bars to search after signal close
        
        Returns:
            Tuple of (entry_time, entry_price) for the first 1m candle that touches OB
            None if no valid entry found within max_bars_to_search
        
        Example:
            Signal candle (5m) opens at 08:30, closes at 08:35
            signal_candle_close_time = 08:35
            
            We search 1m candles starting from 08:35:
            - 08:35 Close = 10.1 -> not in OB range -> skip
            - 08:36 Close = 9.95 -> in OB range -> RETURN (08:36, 9.95)
            
            We do NOT look at 08:31, 08:32, etc. because those are BEFORE
            the signal candle closed - that would be look-ahead bias!
        """
        if df_entry is None or df_entry.empty:
            return None
        
        # Ensure Time column is datetime
        df_entry = df_entry.copy()
        df_entry['Time'] = pd.to_datetime(df_entry['Time'])
        
        # Search window: from signal_candle_close_time onwards
        # We only look at candles AFTER the signal candle closes
        window_start = signal_candle_close_time
        window_end = signal_candle_close_time + timedelta(minutes=max_bars_to_search)
        
        # Filter 1m candles within the search window (AFTER signal close)
        mask = (df_entry['Time'] >= window_start) & (df_entry['Time'] <= window_end)
        candles_in_window = df_entry[mask].sort_values('Time')
        
        if candles_in_window.empty:
            return None
        
        # Iterate through 1m candles in chronological order
        for _, candle in candles_in_window.iterrows():
            close_price = candle['Close']
            low_price = candle['Low']
            high_price = candle['High']
            candle_time = candle['Time']
            
            # Check if Close price is within OB zone (touches OB)
            # For BULLISH OB: price should touch the OB from above (coming down)
            # For BEARISH OB: price should touch the OB from below (coming up)
            ob_tolerance = (ob.high - ob.low) * self.config.touch_tolerance_pct
            
            if ob.bias == 'BULLISH':
                # For long entry: price touches bullish OB zone
                # Close should be within or touch the OB zone
                if low_price <= (ob.high + ob_tolerance) and high_price >= (ob.low - ob_tolerance):
                    # Entry at the Close price of this 1m candle
                    entry_price = close_price
                    # Ensure entry is within OB overlap
                    overlap_low = max(ob.low, low_price)
                    overlap_high = min(ob.high, high_price)
                    if overlap_low <= overlap_high:
                        # Clamp entry price to OB zone
                        entry_price = min(max(close_price, ob.low), ob.high)
                        entry_time = candle_time.to_pydatetime() if hasattr(candle_time, 'to_pydatetime') else candle_time
                        return (entry_time, entry_price)
            else:
                # For short entry: price touches bearish OB zone
                if high_price >= (ob.low - ob_tolerance) and low_price <= (ob.high + ob_tolerance):
                    entry_price = close_price
                    overlap_low = max(ob.low, low_price)
                    overlap_high = min(ob.high, high_price)
                    if overlap_low <= overlap_high:
                        entry_price = min(max(close_price, ob.low), ob.high)
                        entry_time = candle_time.to_pydatetime() if hasattr(candle_time, 'to_pydatetime') else candle_time
                        return (entry_time, entry_price)
        
        return None
    
    def generate_signals(self, df: pd.DataFrame, symbol: str, df_entry: pd.DataFrame | None = None) -> List[Signal]:
        """
        Generate trade signals based on SMC concepts.
        
        MULTI-TIMEFRAME MODE (when df_entry is provided):
        - Uses df (signal timeframe, e.g., 5m) for indicator calculation
        - Uses df_entry (entry timeframe, e.g., 1m) for precise entry detection
        - This prevents repainting by finding the FIRST 1m candle that touches OB
        
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
        signals: List[Signal] = []
        
        # Determine if we're in multi-timeframe mode
        use_multi_tf = df_entry is not None and not df_entry.empty
        signal_tf_minutes = _parse_timeframe_minutes(self.config.signal_timeframe) if use_multi_tf else 0
        
        highs = df['High'].values.astype(np.float64)
        lows = df['Low'].values.astype(np.float64)
        closes = df['Close'].values.astype(np.float64)
        opens = df['Open'].values.astype(np.float64)
        times = df['Time'].values
        n = len(df)

        if n == 0:
            return []
        
        # ========== 1. PRE-COMPUTE INDICATORS ==========
        mitigation_mode = 'CLOSE' if self.config.ob_mitigation == 'close' else 'HIGHLOW'

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
                index=ob.index,
                bias=ob.bias,
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
                if self._ob_is_mitigated(ob, highs[i], lows[i], closes[i]):
                    continue

                touches_ob = self._price_touches_ob(
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
                        # MULTI-TIMEFRAME MODE: Find precise entry from 1m candles
                        if use_multi_tf:
                            # CRITICAL: Calculate signal candle CLOSE time
                            # current_time is the candle OPEN time (e.g., 08:30 for 5m candle)
                            # The candle CLOSES at open_time + signal_tf_minutes (e.g., 08:35)
                            # We can only know about the signal AFTER the candle closes
                            signal_candle_close_time = current_time + timedelta(minutes=signal_tf_minutes)
                            
                            precise_entry = self._find_precise_entry_1m(
                                df_entry=df_entry,
                                signal_candle_close_time=signal_candle_close_time,
                                signal_tf_minutes=signal_tf_minutes,
                                ob=ob,
                            )
                            if precise_entry:
                                entry_time_1m, entry_price_1m = precise_entry
                                signal = self._create_signal_with_price(
                                    symbol=symbol,
                                    ob=ob,
                                    entry_time=entry_time_1m,
                                    entry_price=entry_price_1m,
                                )
                                if signal:
                                    signals.append(signal)
                                    ob.entry_triggered = True
                        else:
                            # SINGLE-TIMEFRAME MODE: Use signal candle for entry (legacy)
                            signal = self._create_signal(
                                symbol=symbol,
                                ob=ob,
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
        signals = self._deduplicate_signals(signals)

        return signals

    # ==================== HELPER METHODS ====================

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

    def _price_touches_ob(self, ob: StrategyOrderBlock, high: float, low: float,
                          tolerance_pct: float = 0.0) -> bool:
        """Check if current candle's price range touches the OB zone (with optional tolerance)."""
        ob_range = max(0.0, ob.high - ob.low)
        tolerance = ob_range * max(0.0, tolerance_pct)
        return low <= (ob.high + tolerance) and high >= (ob.low - tolerance)
    
    def _ob_is_mitigated(self, ob: StrategyOrderBlock, high: float, low: float, close: float) -> bool:
        """Check if OB is fully mitigated."""
        if self.config.ob_mitigation == "close":
            if ob.bias == 'BULLISH':
                return close < ob.low
            else:
                return close > ob.high
        else:
            if ob.bias == 'BULLISH':
                return low < ob.low
            else:
                return high > ob.high

    def _select_entry_price(self, ob: StrategyOrderBlock, current_high: float,
                            current_low: float, previous_close: float) -> Optional[float]:
        """
        Select entry inside the traded OB overlap and prioritize price nearest
        to previous candle close.
        """
        overlap_low = max(ob.low, current_low)
        overlap_high = min(ob.high, current_high)

        if overlap_low > overlap_high:
            return None

        reference_price = previous_close
        if not np.isfinite(reference_price):
            reference_price = (overlap_low + overlap_high) / 2.0

        return min(max(reference_price, overlap_low), overlap_high)
    
    def _create_signal(self, symbol: str, ob: StrategyOrderBlock, entry_time: datetime,
                       current_high: float, current_low: float, current_close: float,
                       previous_close: float) -> Optional[Signal]:
        """
        Create a trade signal with SL and TP.
        
        LONG:
        - Entry: Candle close (if entry_on_candle_close=True) or OB overlap price
        - SL: Below OB low with buffer
        - TP: Entry + (Entry - SL) * RR
        
        SHORT:
        - Entry: Candle close (if entry_on_candle_close=True) or OB overlap price
        - SL: Above OB high with buffer
        - TP: Entry - (SL - Entry) * RR
        """
        ob_range = ob.high - ob.low
        buffer = ob_range * self.config.sl_buffer_pct

        if self.config.entry_on_candle_close:
            entry = current_close if np.isfinite(current_close) else None
        else:
            entry = self._select_entry_price(
                ob=ob,
                current_high=current_high,
                current_low=current_low,
                previous_close=previous_close,
            )
        if entry is None:
            return None
        
        if ob.bias == 'BULLISH':
            trade_type = 'LONG'
            
            base_sl = ob.low - buffer
            base_risk = entry - base_sl
            stop_loss = entry - (base_risk * self.config.sl_multiplier)
            
            if stop_loss > current_low:
                stop_loss = current_low - buffer
            
            actual_risk = entry - stop_loss
            take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
            
        else:
            trade_type = 'SHORT'
            
            base_sl = ob.high + buffer
            base_risk = base_sl - entry
            stop_loss = entry + (base_risk * self.config.sl_multiplier)
            
            if stop_loss < current_high:
                stop_loss = current_high + buffer
            
            actual_risk = stop_loss - entry
            take_profit = entry - (actual_risk * self.config.risk_reward_ratio)
        
        if trade_type == 'LONG' and stop_loss >= entry:
            return None
        if trade_type == 'SHORT' and stop_loss <= entry:
            return None
        
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
        
        return Signal(
            symbol=symbol,
            trade_type=trade_type,
            entry_time=entry_time,
            entry_price=round_price(entry),
            stop_loss=round_price(stop_loss),
            take_profit=round_price(take_profit),
            signal_timeframe=self.config.signal_timeframe,
        )

    def _create_signal_with_price(
        self, 
        symbol: str, 
        ob: StrategyOrderBlock, 
        entry_time: datetime,
        entry_price: float
    ) -> Optional[Signal]:
        """
        Create a trade signal with a specific entry price (for multi-timeframe mode).
        
        This method is used when we have a precise entry price from 1m candles,
        so we don't need to calculate entry from the signal timeframe candle.
        
        Args:
            symbol: Trading symbol
            ob: Order Block that triggered the signal
            entry_time: Precise entry time from 1m candle
            entry_price: Precise entry price (Close of 1m candle)
        
        Returns:
            Signal with SL and TP calculated based on OB and entry price
        """
        ob_range = ob.high - ob.low
        buffer = ob_range * self.config.sl_buffer_pct
        entry = entry_price
        
        if ob.bias == 'BULLISH':
            trade_type = 'LONG'
            
            base_sl = ob.low - buffer
            base_risk = entry - base_sl
            stop_loss = entry - (base_risk * self.config.sl_multiplier)
            
            # For multi-TF mode, we use OB low as reference instead of candle low
            if stop_loss > ob.low:
                stop_loss = ob.low - buffer
            
            actual_risk = entry - stop_loss
            take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
            
        else:
            trade_type = 'SHORT'
            
            base_sl = ob.high + buffer
            base_risk = base_sl - entry
            stop_loss = entry + (base_risk * self.config.sl_multiplier)
            
            # For multi-TF mode, we use OB high as reference instead of candle high
            if stop_loss < ob.high:
                stop_loss = ob.high + buffer
            
            actual_risk = stop_loss - entry
            take_profit = entry - (actual_risk * self.config.risk_reward_ratio)
        
        if trade_type == 'LONG' and stop_loss >= entry:
            return None
        if trade_type == 'SHORT' and stop_loss <= entry:
            return None
        
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
        
        return Signal(
            symbol=symbol,
            trade_type=trade_type,
            entry_time=entry_time,
            entry_price=round_price(entry),
            stop_loss=round_price(stop_loss),
            take_profit=round_price(take_profit),
            signal_timeframe=self.config.signal_timeframe,
        )
