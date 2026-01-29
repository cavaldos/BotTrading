"""
SMC Strategy - Order Block + CHoCH + Liquidity + FVG
=====================================================
Smart Money Concepts trading strategy based on:
- Market Structure (BOS/CHoCH)
- Order Blocks (OB) - specifically CHoCH Order Blocks
- Equal Highs/Lows (EQH/EQL) - Liquidity zones
- Fair Value Gaps (FVG)

Entry Logic:
-----------
LONG Setup:
1. CHoCH Bullish detected (trend reversal from bearish to bullish)
2. Identify Order Block that created the CHoCH
3. FVG Bullish must appear when/after OB is created
4. After CHoCH, EQH must form while waiting for price to return to OB
5. Price touches OB zone -> Entry LONG
6. SL: Below OB with buffer (avoid stop hunt)
7. TP: Entry + (Entry - SL) * RR (fixed RR = 2)

SHORT Setup: (opposite)
1. CHoCH Bearish detected
2. Identify Order Block that created the CHoCH
3. FVG Bearish must appear when/after OB is created
4. After CHoCH, EQL must form while waiting for price to return to OB
5. Price touches OB zone -> Entry SHORT
6. SL: Above OB with buffer
7. TP: Entry - (SL - Entry) * RR

INTERFACE CONTRACT (must export for main.py):
- Config: dataclass with strategy configuration
- Signal: dataclass representing a trade signal
- Strategy: class with generate_signals(df, symbol) -> List[Signal] method

PERFORMANCE: Uses Numba JIT for critical helper functions while preserving exact logic.
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass, field
from numba import njit
from src.utils.reversal_patterns import check_reversal_pattern_jit, check_pattern_by_ids_jit
from src.utils.rounding import round_price


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "SMC"


# ==================== CONFIGURATION ====================
@dataclass
class Config:
    """
    SMC Trading strategy configuration.
    """
    # === SYSTEM SETTINGS (set by main.py) ===
    data_folder: str = "src/data"
    output_folder: str = "src/output"
    risk_reward_ratio: float = 2.0  # Loaded from YAML config
    
    # === STRUCTURE DETECTION ===
    swing_length: int = 10          # Lookback for swing structure (adjusted for 15m/30m)
    internal_length: int = 3        # Lookback for internal structure
    
    # === EQH/EQL DETECTION ===
    eqhl_length: int = 3            # Smaller length for EQH/EQL detection (faster detection)
    eqhl_threshold: float = 0.3     # ATR multiplier for EQH/EQL sensitivity (increased)
    
    # === ORDER BLOCK ===
    ob_mitigation: str = "close" # "close" or "high_low"
    ob_body_ratio: float = 0.35      # Candle body size / candle range must be > 90% (trending candle filter)
    # Note: OB detection uses LuxAlgo extreme candle logic + trending candle filter
    
    # === FVG ===
    fvg_atr_filter: bool = True     # Filter small FVGs using ATR
    
    # === RISK MANAGEMENT ===
    sl_buffer_pct: float = 0.1      # Buffer SL (% of OB range)
    sl_multiplier: float = 1.05     # Hệ số mở rộng SL (1.0 = bình thường, 1.1 = mở rộng 10% tránh stop hunt)
    min_sl_pct: float = 0.05         # Minimum SL % - không đặt lệnh nếu SL < x% giá entry
    risk_per_trade: float = 10.0    # USD

    # === SIGNAL TIMING ===
    signal_delay_candles: int = 1   # Delay signal by N closed candles (anti-repainting)
    
    # === REVERSAL PATTERN FILTER ===
    # Mảng các ID cặp pattern muốn sử dụng
    # [0] = tất cả patterns (mặc định)
    # [1] = chỉ doji
    # [1, 2] = doji + hammer/hanging
    # Pattern Pair IDs:
    #   0 = all (tất cả patterns)
    #   1 = doji
    #   0 = all patterns
    #   1 = doji
    #   2 = hammer / hanging_man
    #   3 = inverted_hammer / shooting_star
    #   4 = bullish_engulfing / bearish_engulfing
    #   5 = piercing_line / dark_cloud_cover
    #   6 = bullish_harami / bearish_harami
    #   7 = morning_star / evening_star
    #   8 = tweezer_bottom / tweezer_top
    #   9 = three_outside_up / three_outside_down
    #   10 = bullish_belt_hold / bearish_belt_hold
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])
    
    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "swing_length": self.swing_length,
            "internal_length": self.internal_length,
            "eqhl_length": self.eqhl_length,
            "eqhl_threshold": self.eqhl_threshold,
            "ob_detection": "LuxAlgo extreme candle + trending candle filter",
            "ob_body_ratio": self.ob_body_ratio,
            "sl_buffer_pct": self.sl_buffer_pct,
            "sl_multiplier": self.sl_multiplier,
            "min_sl_pct": self.min_sl_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
            "signal_delay_candles": self.signal_delay_candles,
            "reversal_pattern_ids": self.reversal_pattern_ids,
        }


# ==================== DATA CLASSES ====================
@dataclass
class SwingPoint:
    """Represents a swing high or swing low point"""
    price: float
    time: datetime
    index: int
    type: str           # 'HH', 'HL', 'LH', 'LL'
    is_high: bool       # True for swing high, False for swing low


@dataclass
class Structure:
    """Represents a BOS or CHoCH event"""
    type: str           # 'BOS' or 'CHoCH'
    bias: str           # 'BULLISH' or 'BEARISH'
    level: float        # Price level that was broken
    break_time: datetime
    break_index: int
    pivot_time: datetime
    pivot_index: int


@dataclass
class OrderBlock:
    """Represents an Order Block zone (LuxAlgo-style detection)"""
    high: float
    low: float
    time: datetime
    index: int
    bias: str               # 'BULLISH' or 'BEARISH'
    structure_type: str     # 'CHoCH' or 'BOS' (type of structure break that created this OB)
    structure: Optional[Structure] = None  # Reference to CHoCH/BOS
    is_mitigated: bool = False
    has_fvg: bool = False       # True if FVG appeared with/after this OB
    has_liquidity: bool = False # True if EQH/EQL appeared after this OB
    entry_triggered: bool = False  # True if entry was already triggered


@dataclass
class EqualHL:
    """Represents Equal High or Equal Low (liquidity zone)"""
    type: str           # 'EQH' or 'EQL'
    level: float
    time: datetime
    index: int
    first_pivot_index: int
    second_pivot_index: int


@dataclass
class FairValueGap:
    """Represents a Fair Value Gap"""
    top: float
    bottom: float
    bias: str           # 'BULLISH' or 'BEARISH'
    time: datetime
    index: int
    is_filled: bool = False


@dataclass
class Signal:
    """
    Represents a trade signal from strategy.
    Only contains entry information - no exit data.
    """
    symbol: str
    trade_type: str           # 'LONG' or 'SHORT'
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float


# ==================== NUMBA JIT OPTIMIZED FUNCTIONS ====================

@njit(cache=True)
def _calculate_atr_jit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """
    Calculate Average True Range (ATR) - JIT optimized.
    """
    n = len(highs)
    tr = np.empty(n, dtype=np.float64)
    atr = np.empty(n, dtype=np.float64)
    
    # Calculate True Range
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr[i] = max(tr1, max(tr2, tr3))
    
    # Calculate ATR using rolling mean
    cumsum = 0.0
    for i in range(n):
        cumsum += tr[i]
        if i < period:
            atr[i] = cumsum / (i + 1)
        else:
            cumsum -= tr[i - period]
            atr[i] = cumsum / period
    
    return atr


@njit(cache=True)
def _check_swing_high_jit(highs: np.ndarray, i: int, length: int) -> bool:
    """Check if index i is a swing high - JIT optimized."""
    n = len(highs)
    if i < length or i >= n - length:
        return False
    
    for j in range(i - length, i + length + 1):
        if j != i and highs[j] >= highs[i]:
            return False
    return True


@njit(cache=True)
def _check_swing_high_confirmed_jit(highs: np.ndarray, check_idx: int, length: int, current_idx: int) -> bool:
    """
    Check if check_idx is a swing high using ONLY data available at current_idx.
    NO LOOK-AHEAD: Once confirmed, this swing point will NEVER change.
    
    A swing high at check_idx is confirmed when:
    - We have 'length' bars BEFORE check_idx
    - We have 'length' bars AFTER check_idx (but not beyond current_idx)
    - It's the highest in that window
    """
    # Need at least 'length' bars before
    if check_idx < length:
        return False
    
    # Need at least 'length' bars after, but not beyond current_idx
    end_idx = check_idx + length
    if end_idx > current_idx:
        return False  # Not enough bars after to confirm
    
    # Check if it's the highest in the window [check_idx - length, check_idx + length]
    for j in range(check_idx - length, end_idx + 1):
        if j != check_idx and highs[j] >= highs[check_idx]:
            return False
    
    return True


@njit(cache=True)
def _check_swing_low_confirmed_jit(lows: np.ndarray, check_idx: int, length: int, current_idx: int) -> bool:
    """
    Check if check_idx is a swing low using ONLY data available at current_idx.
    NO LOOK-AHEAD: Once confirmed, this swing point will NEVER change.
    """
    if check_idx < length:
        return False
    
    end_idx = check_idx + length
    if end_idx > current_idx:
        return False
    
    for j in range(check_idx - length, end_idx + 1):
        if j != check_idx and lows[j] <= lows[check_idx]:
            return False
    
    return True


@njit(cache=True)
def _check_swing_low_jit(lows: np.ndarray, i: int, length: int) -> bool:
    """Check if index i is a swing low - JIT optimized."""
    n = len(lows)
    if i < length or i >= n - length:
        return False
    
    for j in range(i - length, i + length + 1):
        if j != i and lows[j] <= lows[i]:
            return False
    return True


@njit(cache=True)
def _precompute_all_swings_jit(highs: np.ndarray, lows: np.ndarray, length: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Pre-compute ALL swing high/low indices at once - much faster than checking each bar.
    
    Returns:
        - swing_high_indices: Array of indices where swing highs occur
        - swing_low_indices: Array of indices where swing lows occur
    """
    n = len(highs)
    # Pre-allocate max possible size
    sh_indices = np.empty(n, dtype=np.int64)
    sl_indices = np.empty(n, dtype=np.int64)
    sh_count = 0
    sl_count = 0
    
    for i in range(length, n - length):
        # Check swing high
        is_sh = True
        for j in range(i - length, i + length + 1):
            if j != i and highs[j] >= highs[i]:
                is_sh = False
                break
        if is_sh:
            sh_indices[sh_count] = i
            sh_count += 1
        
        # Check swing low
        is_sl = True
        for j in range(i - length, i + length + 1):
            if j != i and lows[j] <= lows[i]:
                is_sl = False
                break
        if is_sl:
            sl_indices[sl_count] = i
            sl_count += 1
    
    return sh_indices[:sh_count].copy(), sl_indices[:sl_count].copy()


@njit(cache=True)
def _precompute_parsed_hl_jit(highs: np.ndarray, lows: np.ndarray, atr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Pre-compute parsed high/low for high volatility bars (LuxAlgo logic).
    
    If candle range >= 2 * ATR, swap high/low:
    - parsedHigh = low (for high volatility bar)
    - parsedLow = high (for high volatility bar)
    
    This helps identify the "extreme" point of momentum candles.
    """
    n = len(highs)
    parsed_highs = np.empty(n, dtype=np.float64)
    parsed_lows = np.empty(n, dtype=np.float64)
    
    for i in range(n):
        candle_range = highs[i] - lows[i]
        if candle_range >= 2.0 * atr[i]:
            # High volatility bar - swap
            parsed_highs[i] = lows[i]
            parsed_lows[i] = highs[i]
        else:
            parsed_highs[i] = highs[i]
            parsed_lows[i] = lows[i]
    
    return parsed_highs, parsed_lows


@njit(cache=True)
def _check_trending_candle_jit(opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, 
                               lows: np.ndarray, candle_idx: int, body_ratio_threshold: float) -> bool:
    """
    Check if a candle is a trending candle.
    
    Trending candle: Body size / Candle range > body_ratio_threshold (e.g., 0.6 = 60%)
    
    Body = abs(close - open)
    Range = high - low
    
    Args:
        opens, closes, highs, lows: OHLC arrays
        candle_idx: Index of candle to check
        body_ratio_threshold: Minimum body ratio (e.g., 0.6 for 60%)
    
    Returns:
        True if body_ratio > threshold, False otherwise
    """
    body = abs(closes[candle_idx] - opens[candle_idx])
    candle_range = highs[candle_idx] - lows[candle_idx]
    
    if candle_range == 0:
        return False
    
    body_ratio = body / candle_range
    return body_ratio > body_ratio_threshold


@njit(cache=True)
def _find_ob_index_jit(parsed_highs: np.ndarray, parsed_lows: np.ndarray, 
                       start_idx: int, end_idx: int, is_bullish: bool,
                       opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                       body_ratio_threshold: float) -> int:
    """
    Find Order Block candle index using LuxAlgo logic + trending candle filter.
    
    For Bullish OB: Find candle with LOWEST parsed_low in range (must be trending candle)
    For Bearish OB: Find candle with HIGHEST parsed_high in range (must be trending candle)
    
    Trending candle: Body size > body_ratio_threshold * candle range
    
    Args:
        parsed_highs: Pre-computed parsed highs array
        parsed_lows: Pre-computed parsed lows array
        start_idx: Start of search range (pivot index)
        end_idx: End of search range (break index)
        is_bullish: True for Bullish OB, False for Bearish OB
        opens, closes, highs, lows: OHLC arrays for body ratio check
        body_ratio_threshold: Minimum body ratio (e.g., 0.6 for 60%)
    
    Returns:
        Index of the OB candle, or -1 if invalid range
    """
    if start_idx >= end_idx or start_idx < 0:
        return -1
    
    if is_bullish:
        best_idx = -1
        best_val = np.inf
        for i in range(start_idx, end_idx):
            if _check_trending_candle_jit(opens, closes, highs, lows, i, body_ratio_threshold):
                if parsed_lows[i] < best_val:
                    best_val = parsed_lows[i]
                    best_idx = i
        return best_idx
    else:
        best_idx = -1
        best_val = -np.inf
        for i in range(start_idx, end_idx):
            if _check_trending_candle_jit(opens, closes, highs, lows, i, body_ratio_threshold):
                if parsed_highs[i] > best_val:
                    best_val = parsed_highs[i]
                    best_idx = i
        return best_idx


@njit(cache=True)
def _precompute_fvg_jit(highs: np.ndarray, lows: np.ndarray, atr: np.ndarray, use_filter: bool) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Pre-compute ALL FVGs at once.
    
    Returns:
        - fvg_indices, fvg_tops, fvg_bottoms, fvg_biases (1=BULLISH, 0=BEARISH)
    """
    n = len(highs)
    max_fvg = n
    
    fvg_indices = np.empty(max_fvg, dtype=np.int64)
    fvg_tops = np.empty(max_fvg, dtype=np.float64)
    fvg_bottoms = np.empty(max_fvg, dtype=np.float64)
    fvg_biases = np.empty(max_fvg, dtype=np.int8)
    count = 0
    
    for i in range(2, n):
        # Bullish FVG
        if lows[i] > highs[i - 2]:
            gap_size = lows[i] - highs[i - 2]
            if not use_filter or gap_size >= 0.1 * atr[i]:
                fvg_indices[count] = i
                fvg_tops[count] = lows[i]
                fvg_bottoms[count] = highs[i - 2]
                fvg_biases[count] = 1  # BULLISH
                count += 1
        
        # Bearish FVG
        if highs[i] < lows[i - 2]:
            gap_size = lows[i - 2] - highs[i]
            if not use_filter or gap_size >= 0.1 * atr[i]:
                fvg_indices[count] = i
                fvg_tops[count] = lows[i - 2]
                fvg_bottoms[count] = highs[i]
                fvg_biases[count] = 0  # BEARISH
                count += 1
    
    return fvg_indices[:count].copy(), fvg_tops[:count].copy(), fvg_bottoms[:count].copy(), fvg_biases[:count].copy()


# ==================== INDICATOR FUNCTIONS ====================

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR) - optimized with Numba"""
    high = df['High'].values.astype(np.float64)
    low = df['Low'].values.astype(np.float64)
    close = df['Close'].values.astype(np.float64)
    
    atr_values = _calculate_atr_jit(high, low, close, period)
    return pd.Series(atr_values, index=df.index)


def detect_swing_points(df: pd.DataFrame, length: int) -> List[SwingPoint]:
    """
    Detect swing high/low points using fractal logic.
    
    A swing high is formed when High[length] is higher than all highs 
    in the surrounding window (length bars on each side).
    
    A swing low is formed when Low[length] is lower than all lows 
    in the surrounding window.
    """
    swing_points = []
    
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    times = df['Time'].values
    
    # Need at least length*2 + 1 bars
    if len(df) < length * 2 + 1:
        return swing_points
    
    # Track previous swing for labeling (HH, HL, LH, LL)
    last_swing_high = None
    last_swing_low = None
    
    for i in range(length, len(df) - length):
        # Check for swing high using JIT function
        if _check_swing_high_jit(highs, i, length):
            # Determine type (HH or LH)
            if last_swing_high is None:
                swing_type = 'HH'
            elif highs[i] > last_swing_high.price:
                swing_type = 'HH'
            else:
                swing_type = 'LH'
            
            swing = SwingPoint(
                price=highs[i],
                time=pd.to_datetime(times[i]),
                index=i,
                type=swing_type,
                is_high=True
            )
            swing_points.append(swing)
            last_swing_high = swing
        
        # Check for swing low using JIT function
        if _check_swing_low_jit(lows, i, length):
            # Determine type (LL or HL)
            if last_swing_low is None:
                swing_type = 'LL'
            elif lows[i] < last_swing_low.price:
                swing_type = 'LL'
            else:
                swing_type = 'HL'
            
            swing = SwingPoint(
                price=lows[i],
                time=pd.to_datetime(times[i]),
                index=i,
                type=swing_type,
                is_high=False
            )
            swing_points.append(swing)
            last_swing_low = swing
    
    # Sort by index
    swing_points.sort(key=lambda x: x.index)
    return swing_points


def detect_structure(df: pd.DataFrame, swing_points: List[SwingPoint]) -> List[Structure]:
    """
    Detect BOS (Break of Structure) and CHoCH (Change of Character).
    
    - BOS: Price breaks structure in the direction of current trend
    - CHoCH: Price breaks structure against current trend (reversal)
    
    Logic:
    - In uptrend (BULLISH): breaking above swing high = BOS, breaking below swing low = CHoCH
    - In downtrend (BEARISH): breaking below swing low = BOS, breaking above swing high = CHoCH
    """
    structures = []
    
    if len(swing_points) < 2:
        return structures
    
    closes = df['Close'].values
    times = df['Time'].values
    
    # Get separate lists of swing highs and lows
    swing_highs = [sp for sp in swing_points if sp.is_high]
    swing_lows = [sp for sp in swing_points if not sp.is_high]
    
    if len(swing_highs) < 1 or len(swing_lows) < 1:
        return structures
    
    # Track current unbroken swing high and low
    current_swing_high = swing_highs[0]
    current_swing_low = swing_lows[0]
    high_idx = 0
    low_idx = 0
    
    # Determine initial trend
    if current_swing_high.index > current_swing_low.index:
        current_trend = 'BULLISH'
    else:
        current_trend = 'BEARISH'
    
    # Track last broken levels to avoid duplicate structures
    last_broken_high_index = -1
    last_broken_low_index = -1
    
    # Process each bar to check for structure breaks
    start_idx = max(current_swing_high.index, current_swing_low.index) + 1
    
    for i in range(start_idx, len(df)):
        close = closes[i]
        
        # Update swing points that we've passed
        while high_idx < len(swing_highs) - 1 and swing_highs[high_idx + 1].index < i:
            high_idx += 1
            current_swing_high = swing_highs[high_idx]
        
        while low_idx < len(swing_lows) - 1 and swing_lows[low_idx + 1].index < i:
            low_idx += 1
            current_swing_low = swing_lows[low_idx]
        
        # Check for break of swing high (only if not already broken)
        if current_swing_high.index > last_broken_high_index and close > current_swing_high.price:
            if current_trend == 'BULLISH':
                structure_type = 'BOS'
            else:
                structure_type = 'CHoCH'
            
            structure = Structure(
                type=structure_type,
                bias='BULLISH',
                level=current_swing_high.price,
                break_time=pd.to_datetime(times[i]),
                break_index=i,
                pivot_time=current_swing_high.time,
                pivot_index=current_swing_high.index
            )
            structures.append(structure)
            
            current_trend = 'BULLISH'
            last_broken_high_index = current_swing_high.index
            
            # Move to next swing high
            if high_idx < len(swing_highs) - 1:
                high_idx += 1
                current_swing_high = swing_highs[high_idx]
        
        # Check for break of swing low (only if not already broken)
        if current_swing_low.index > last_broken_low_index and close < current_swing_low.price:
            if current_trend == 'BEARISH':
                structure_type = 'BOS'
            else:
                structure_type = 'CHoCH'
            
            structure = Structure(
                type=structure_type,
                bias='BEARISH',
                level=current_swing_low.price,
                break_time=pd.to_datetime(times[i]),
                break_index=i,
                pivot_time=current_swing_low.time,
                pivot_index=current_swing_low.index
            )
            structures.append(structure)
            
            current_trend = 'BEARISH'
            last_broken_low_index = current_swing_low.index
            
            # Move to next swing low
            if low_idx < len(swing_lows) - 1:
                low_idx += 1
                current_swing_low = swing_lows[low_idx]
    
    return structures


def detect_order_blocks(df: pd.DataFrame, structures: List[Structure], atr: pd.Series, body_ratio_threshold: float = 0.6) -> List[OrderBlock]:
    """
    Detect Order Blocks from structure breaks using LuxAlgo logic + trending candle filter.
    
    LuxAlgo Order Block Detection:
    - For Bullish OB: Find candle with LOWEST parsed_low in range from pivot to break
    - For Bearish OB: Find candle with HIGHEST parsed_high in range from pivot to break
    - High volatility bars (range >= 2*ATR) have their high/low swapped
    
    Trending Candle Filter:
    - Candle body size > body_ratio_threshold * candle range (e.g., 0.6 = 60%)
    - Body = abs(close - open)
    - Range = high - low
    
    Creates OB for both CHoCH and BOS structure breaks.
    """
    order_blocks = []
    
    highs = df['High'].values.astype(np.float64)
    lows = df['Low'].values.astype(np.float64)
    opens = df['Open'].values.astype(np.float64)
    closes = df['Close'].values.astype(np.float64)
    times = df['Time'].values
    atr_values = atr.values.astype(np.float64)
    
    # Pre-compute parsed highs/lows for high volatility filter
    parsed_highs, parsed_lows = _precompute_parsed_hl_jit(highs, lows, atr_values)
    
    for structure in structures:
        # Find OB using LuxAlgo logic + trending candle filter
        ob_index = _find_ob_index_jit(
            parsed_highs, 
            parsed_lows,
            structure.pivot_index,
            structure.break_index,
            is_bullish=(structure.bias == 'BULLISH'),
            opens=opens,
            closes=closes,
            highs=highs,
            lows=lows,
            body_ratio_threshold=body_ratio_threshold
        )
        
        if ob_index >= 0:
            ob_high = max(parsed_highs[ob_index], parsed_lows[ob_index])
            ob_low = min(parsed_highs[ob_index], parsed_lows[ob_index])
            ob = OrderBlock(
                high=ob_high,
                low=ob_low,
                time=pd.to_datetime(times[ob_index]),
                index=ob_index,
                bias=structure.bias,
                structure_type=structure.type,  # 'CHoCH' or 'BOS'
                structure=structure
            )
            order_blocks.append(ob)
    
    return order_blocks


def detect_equal_hl(df: pd.DataFrame, swing_points: List[SwingPoint], 
                    threshold: float, atr: pd.Series) -> List[EqualHL]:
    """
    Detect Equal Highs (EQH) and Equal Lows (EQL).
    
    EQH: Two swing highs with prices within threshold * ATR of each other
    EQL: Two swing lows with prices within threshold * ATR of each other
    """
    equal_hls = []
    
    swing_highs = [sp for sp in swing_points if sp.is_high]
    swing_lows = [sp for sp in swing_points if not sp.is_high]
    
    # Detect EQH
    for i in range(len(swing_highs)):
        for j in range(i + 1, len(swing_highs)):
            sp1 = swing_highs[i]
            sp2 = swing_highs[j]
            
            # Get ATR at the second swing point
            atr_value = atr.iloc[sp2.index] if sp2.index < len(atr) else atr.iloc[-1]
            
            # Check if prices are close enough
            if abs(sp1.price - sp2.price) < threshold * atr_value:
                eqh = EqualHL(
                    type='EQH',
                    level=(sp1.price + sp2.price) / 2,
                    time=sp2.time,
                    index=sp2.index,
                    first_pivot_index=sp1.index,
                    second_pivot_index=sp2.index
                )
                equal_hls.append(eqh)
    
    # Detect EQL
    for i in range(len(swing_lows)):
        for j in range(i + 1, len(swing_lows)):
            sp1 = swing_lows[i]
            sp2 = swing_lows[j]
            
            # Get ATR at the second swing point
            atr_value = atr.iloc[sp2.index] if sp2.index < len(atr) else atr.iloc[-1]
            
            # Check if prices are close enough
            if abs(sp1.price - sp2.price) < threshold * atr_value:
                eql = EqualHL(
                    type='EQL',
                    level=(sp1.price + sp2.price) / 2,
                    time=sp2.time,
                    index=sp2.index,
                    first_pivot_index=sp1.index,
                    second_pivot_index=sp2.index
                )
                equal_hls.append(eql)
    
    return equal_hls


def detect_fvg(df: pd.DataFrame, atr: pd.Series, use_filter: bool = True) -> List[FairValueGap]:
    """
    Detect Fair Value Gaps (FVG).
    
    Bullish FVG: Low[current] > High[2 bars ago] (gap up)
    Bearish FVG: High[current] < Low[2 bars ago] (gap down)
    """
    fvgs = []
    
    highs = df['High'].values
    lows = df['Low'].values
    times = df['Time'].values
    
    for i in range(2, len(df)):
        # Bullish FVG: current low > high 2 bars ago
        if lows[i] > highs[i - 2]:
            gap_size = lows[i] - highs[i - 2]
            
            # Filter small gaps if enabled
            if use_filter:
                atr_value = atr.iloc[i] if i < len(atr) else atr.iloc[-1]
                if gap_size < 0.1 * atr_value:
                    continue
            
            fvg = FairValueGap(
                top=lows[i],
                bottom=highs[i - 2],
                bias='BULLISH',
                time=pd.to_datetime(times[i]),
                index=i
            )
            fvgs.append(fvg)
        
        # Bearish FVG: current high < low 2 bars ago
        if highs[i] < lows[i - 2]:
            gap_size = lows[i - 2] - highs[i]
            
            # Filter small gaps if enabled
            if use_filter:
                atr_value = atr.iloc[i] if i < len(atr) else atr.iloc[-1]
                if gap_size < 0.1 * atr_value:
                    continue
            
            fvg = FairValueGap(
                top=lows[i - 2],
                bottom=highs[i],
                bias='BEARISH',
                time=pd.to_datetime(times[i]),
                index=i
            )
            fvgs.append(fvg)
    
    return fvgs


# ==================== STRATEGY CLASS ====================

class Strategy:
    """
    SMC Strategy - generates trade signals based on:
    - CHoCH Order Blocks
    - Fair Value Gaps
    - Equal Highs/Lows (Liquidity)
    """
    
    def __init__(self, config: Config):
        self.config = config
    
    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """
        Generate trade signals based on SMC concepts.
        
        OPTIMIZED VERSION: Pre-computes swing points and FVGs using JIT,
        then uses efficient set lookups in the main loop.
        
        CRITICAL: Detect patterns incrementally to avoid look-ahead bias.
        A swing point at bar i is only confirmed after we have swing_length bars after it.
        This means signals use only information available at the current bar.
        """
        df = df.copy()
        signals = []

        if self.config.signal_delay_candles < 0:
            raise ValueError("signal_delay_candles must be >= 0")
        
        # Calculate ATR for entire dataset (uses past data only - no look-ahead)
        atr = calculate_atr(df, period=14)
        atr_values = atr.values  # Convert to numpy for faster access
        
        # Arrays for efficient access
        times = df['Time'].values
        highs = df['High'].values.astype(np.float64)
        lows = df['Low'].values.astype(np.float64)
        closes = df['Close'].values.astype(np.float64)
        opens = df['Open'].values.astype(np.float64)
        n = len(df)
        
        # ========== INCREMENTAL SWING DETECTION (NO LOOK-AHEAD) ==========
        # Instead of pre-computing all swing points, we detect them incrementally.
        # A swing point is only confirmed when we have enough bars AFTER it.
        # Once confirmed, it will NEVER change - eliminating look-ahead bias.
        #
        # Key insight: A swing at bar X is confirmed at bar X + swing_length
        # because we need swing_length bars after X to verify it's the highest/lowest
        
        # Track which indices have been checked (to avoid re-checking)
        checked_swing_high_indices = set()
        checked_swing_low_indices = set()
        checked_internal_high_indices = set()
        checked_internal_low_indices = set()
        
        # ========== PRE-COMPUTE ALL FVGS ==========
        fvg_indices_arr, fvg_tops_arr, fvg_bottoms_arr, fvg_biases_arr = _precompute_fvg_jit(
            highs, lows, atr_values, self.config.fvg_atr_filter
        )
        # Create dict for fast FVG lookup by index: {index: (top, bottom, bias)}
        fvg_by_index = {}
        for i in range(len(fvg_indices_arr)):
            idx = fvg_indices_arr[i]
            bias = 'BULLISH' if fvg_biases_arr[i] == 1 else 'BEARISH'
            fvg_by_index[idx] = (fvg_tops_arr[i], fvg_bottoms_arr[i], bias)
        
        # ========== PRE-COMPUTE PARSED HIGHS/LOWS (LuxAlgo High Volatility Filter) ==========
        parsed_highs, parsed_lows = _precompute_parsed_hl_jit(highs, lows, atr_values)
        
        # ========== MAIN LOOP VARIABLES ==========
        confirmed_swing_highs = []  # List of SwingPoint
        confirmed_swing_lows = []   # List of SwingPoint
        confirmed_internal_highs = []
        confirmed_internal_lows = []
        
        # For O(1) EQH/EQL lookup: dict[price_rounded] = SwingPoint
        internal_highs_by_price = {}  # Cache để tìm EQH nhanh
        internal_lows_by_price = {}   # Cache để tìm EQL nhanh
        
        last_swing_high = None
        last_swing_low = None
        
        all_fvgs = []
        all_equal_hls = []
        active_obs = []
        
        current_trend = None
        last_broken_high_index = -1
        last_broken_low_index = -1
        
        # ========== MAIN LOOP ==========
        for current_idx in range(n):
            if current_idx <= self.config.signal_delay_candles:
                continue
            current_time = pd.to_datetime(times[current_idx])
            
            # 1. Check if a swing HIGH gets confirmed at this bar
            # A swing at bar X is confirmed at bar X + swing_length (when we have enough bars after)
            swing_high_idx = current_idx - self.config.swing_length
            if swing_high_idx >= self.config.swing_length and swing_high_idx not in checked_swing_high_indices:
                checked_swing_high_indices.add(swing_high_idx)
                
                # Use incremental check - only uses data up to current_idx
                if _check_swing_high_confirmed_jit(highs, swing_high_idx, self.config.swing_length, current_idx):
                    # Determine type (HH or LH)
                    if last_swing_high is None:
                        swing_type = 'HH'
                    elif highs[swing_high_idx] > last_swing_high.price:
                        swing_type = 'HH'
                    else:
                        swing_type = 'LH'
                    
                    swing = SwingPoint(
                        price=highs[swing_high_idx],
                        time=pd.to_datetime(times[swing_high_idx]),
                        index=swing_high_idx,
                        type=swing_type,
                        is_high=True
                    )
                    confirmed_swing_highs.append(swing)
                    last_swing_high = swing
            
            # 2. Check if a swing LOW gets confirmed at this bar
            swing_low_idx = current_idx - self.config.swing_length
            if swing_low_idx >= self.config.swing_length and swing_low_idx not in checked_swing_low_indices:
                checked_swing_low_indices.add(swing_low_idx)
                
                # Use incremental check - only uses data up to current_idx
                if _check_swing_low_confirmed_jit(lows, swing_low_idx, self.config.swing_length, current_idx):
                    # Determine type (LL or HL)
                    if last_swing_low is None:
                        swing_type = 'LL'
                    elif lows[swing_low_idx] < last_swing_low.price:
                        swing_type = 'LL'
                    else:
                        swing_type = 'HL'
                    
                    swing = SwingPoint(
                        price=lows[swing_low_idx],
                        time=pd.to_datetime(times[swing_low_idx]),
                        index=swing_low_idx,
                        type=swing_type,
                        is_high=False
                    )
                    confirmed_swing_lows.append(swing)
                    last_swing_low = swing
            
            # 3. Detect structure breaks at CURRENT bar
            if current_trend is None and confirmed_swing_highs and confirmed_swing_lows:
                if confirmed_swing_highs[0].index > confirmed_swing_lows[0].index:
                    current_trend = 'BULLISH'
                else:
                    current_trend = 'BEARISH'
            
            if current_trend is not None and confirmed_swing_highs and confirmed_swing_lows:
                # Get most recent unbroken swing high and low
                current_swing_high = None
                for sh in reversed(confirmed_swing_highs):
                    if sh.index > last_broken_high_index:
                        current_swing_high = sh
                        break
                
                current_swing_low = None
                for sl in reversed(confirmed_swing_lows):
                    if sl.index > last_broken_low_index:
                        current_swing_low = sl
                        break
                
                # Check for break of swing high
                if current_swing_high and closes[current_idx] > current_swing_high.price:
                    if current_trend == 'BULLISH':
                        structure_type = 'BOS'
                    else:
                        structure_type = 'CHoCH'
                    
                    structure = Structure(
                        type=structure_type,
                        bias='BULLISH',
                        level=current_swing_high.price,
                        break_time=current_time,
                        break_index=current_idx,
                        pivot_time=current_swing_high.time,
                        pivot_index=current_swing_high.index
                    )
                    current_trend = 'BULLISH'
                    last_broken_high_index = current_swing_high.index
                    
                    # Create order block for CHoCH and BOS (LuxAlgo logic + trending candle filter)
                    # Find extreme candle in range using parsed highs/lows + body ratio check
                    ob_index = _find_ob_index_jit(
                        parsed_highs, parsed_lows,
                        structure.pivot_index, current_idx,
                        is_bullish=True,
                        opens=opens,
                        closes=closes,
                        highs=highs,
                        lows=lows,
                        body_ratio_threshold=self.config.ob_body_ratio
                    )
                    
                    if ob_index >= 0:
                        ob_high = max(parsed_highs[ob_index], parsed_lows[ob_index])
                        ob_low = min(parsed_highs[ob_index], parsed_lows[ob_index])
                        ob = OrderBlock(
                            high=ob_high,
                            low=ob_low,
                            time=pd.to_datetime(times[ob_index]),
                            index=ob_index,
                            bias='BULLISH',
                            structure_type=structure_type,
                            structure=structure
                        )
                        ob.has_fvg = self._check_fvg_for_ob(ob, all_fvgs)
                        active_obs.append(ob)
                
                # Check for break of swing low
                if current_swing_low and closes[current_idx] < current_swing_low.price:
                    if current_trend == 'BEARISH':
                        structure_type = 'BOS'
                    else:
                        structure_type = 'CHoCH'
                    
                    structure = Structure(
                        type=structure_type,
                        bias='BEARISH',
                        level=current_swing_low.price,
                        break_time=current_time,
                        break_index=current_idx,
                        pivot_time=current_swing_low.time,
                        pivot_index=current_swing_low.index
                    )
                    current_trend = 'BEARISH'
                    last_broken_low_index = current_swing_low.index
                    
                    # Create order block for CHoCH and BOS (LuxAlgo logic + trending candle filter)
                    # Find extreme candle in range using parsed highs/lows + body ratio check
                    ob_index = _find_ob_index_jit(
                        parsed_highs, parsed_lows,
                        structure.pivot_index, current_idx,
                        is_bullish=False,
                        opens=opens,
                        closes=closes,
                        highs=highs,
                        lows=lows,
                        body_ratio_threshold=self.config.ob_body_ratio
                    )
                    
                    if ob_index >= 0:
                        ob_high = max(parsed_highs[ob_index], parsed_lows[ob_index])
                        ob_low = min(parsed_highs[ob_index], parsed_lows[ob_index])
                        ob = OrderBlock(
                            high=ob_high,
                            low=ob_low,
                            time=pd.to_datetime(times[ob_index]),
                            index=ob_index,
                            bias='BEARISH',
                            structure_type=structure_type,
                            structure=structure
                        )
                        ob.has_fvg = self._check_fvg_for_ob(ob, all_fvgs)
                        active_obs.append(ob)
            
            # 4. Detect FVG at current bar using pre-computed data
            if current_idx in fvg_by_index:
                top, bottom, bias = fvg_by_index[current_idx]
                fvg = FairValueGap(
                    top=top,
                    bottom=bottom,
                    bias=bias,
                    time=current_time,
                    index=current_idx
                )
                all_fvgs.append(fvg)
                
                # Update has_fvg for active OBs
                for ob in active_obs:
                    if not ob.has_fvg and ob.bias == bias:
                        ob.has_fvg = self._check_fvg_for_ob(ob, all_fvgs)
            
            # 5. Detect internal swings for EQH/EQL (incremental, no look-ahead)
            internal_high_idx = current_idx - self.config.eqhl_length
            if internal_high_idx >= self.config.eqhl_length and internal_high_idx not in checked_internal_high_indices:
                checked_internal_high_indices.add(internal_high_idx)
                
                # Use incremental check for internal swings
                if _check_swing_high_confirmed_jit(highs, internal_high_idx, self.config.eqhl_length, current_idx):
                    # Check for EQH with previous internal highs
                    for prev_ih in confirmed_internal_highs:
                        if abs(highs[internal_high_idx] - prev_ih.price) < self.config.eqhl_threshold * atr_values[internal_high_idx]:
                            eqh = EqualHL(
                                type='EQH',
                                level=(highs[internal_high_idx] + prev_ih.price) / 2,
                                time=pd.to_datetime(times[internal_high_idx]),
                                index=internal_high_idx,
                                first_pivot_index=prev_ih.index,
                                second_pivot_index=internal_high_idx
                            )
                            all_equal_hls.append(eqh)
                            break
                    
                    confirmed_internal_highs.append(SwingPoint(
                        price=highs[internal_high_idx],
                        time=pd.to_datetime(times[internal_high_idx]),
                        index=internal_high_idx,
                        type='H',
                        is_high=True
                    ))
            
            internal_low_idx = current_idx - self.config.eqhl_length
            if internal_low_idx >= self.config.eqhl_length and internal_low_idx not in checked_internal_low_indices:
                checked_internal_low_indices.add(internal_low_idx)
                
                # Use incremental check for internal swings
                if _check_swing_low_confirmed_jit(lows, internal_low_idx, self.config.eqhl_length, current_idx):
                    # Check for EQL with previous internal lows
                    for prev_il in confirmed_internal_lows:
                        if abs(lows[internal_low_idx] - prev_il.price) < self.config.eqhl_threshold * atr_values[internal_low_idx]:
                            eql = EqualHL(
                                type='EQL',
                                level=(lows[internal_low_idx] + prev_il.price) / 2,
                                time=pd.to_datetime(times[internal_low_idx]),
                                index=internal_low_idx,
                                first_pivot_index=prev_il.index,
                                second_pivot_index=internal_low_idx
                            )
                            all_equal_hls.append(eql)
                            break
                    
                    confirmed_internal_lows.append(SwingPoint(
                        price=lows[internal_low_idx],
                        time=pd.to_datetime(times[internal_low_idx]),
                        index=internal_low_idx,
                        type='L',
                        is_high=False
                    ))
            
            # 6. Update OB liquidity status
            for ob in active_obs:
                if not ob.has_liquidity:
                    ob.has_liquidity = self._check_liquidity_for_ob(ob, all_equal_hls, current_idx)
            
            # 7. Get nearest swing high and swing low (actual fractal points, not just max/min)

            
            # 8. Check for entry at CURRENT bar
            # Use list comprehension instead of remove() for O(n) performance
            remaining_obs = []
            
            eval_idx = current_idx - self.config.signal_delay_candles
            eval_high = highs[eval_idx]
            eval_low = lows[eval_idx]
            eval_close = closes[eval_idx]
            eval_open = opens[eval_idx]
            eval_time = pd.to_datetime(times[eval_idx])

            for ob in active_obs:
                is_mitigated = self._ob_is_mitigated(ob, eval_high, eval_low, eval_close)
                
                if is_mitigated:
                    ob.is_mitigated = True
                    continue  # Skip to next OB, don't add to remaining_obs
                
                # Only check entry if not yet triggered
                if not ob.entry_triggered and self._price_touches_ob(ob, eval_high, eval_low):
                    # Check if current candle is a reversal pattern
                    is_reversal = self._is_reversal_candle(
                        opens,
                        closes,
                        highs,
                        lows,
                        eval_idx,
                        ob.bias,
                        is_closed_candle=True
                    )
                    
                    if ob.has_fvg and ob.has_liquidity and is_reversal:
                        signal = self._create_signal(
                            symbol=symbol,
                            ob=ob,
                            entry_time=eval_time,
                            entry_price=eval_close,
                            current_open=eval_open,
                            current_high=eval_high,
                            current_low=eval_low
                        )
                        
                        if signal:
                            signals.append(signal)
                            ob.entry_triggered = True
                
                remaining_obs.append(ob)
            
            # Replace active_obs with filtered list
            active_obs = remaining_obs
        
        return signals
    
    def _check_fvg_for_ob(self, ob: OrderBlock, fvgs: List[FairValueGap]) -> bool:
        """
        Check if there's a matching FVG at or after OB creation.
        FVG must be same bias as OB and appear within reasonable range.
        """
        ob_creation_index = ob.structure.break_index if ob.structure else ob.index
        
        for fvg in fvgs:
            # FVG must be same bias
            if fvg.bias != ob.bias:
                continue
            
            # FVG must appear at or after OB creation (within 10 bars)
            if fvg.index >= ob_creation_index and fvg.index <= ob_creation_index + 10:
                return True
        
        return False
    
    def _check_liquidity_for_ob(self, ob: OrderBlock, equal_hls: List[EqualHL], 
                                 current_index: int) -> bool:
        """
        Check if EQH/EQL appeared after OB creation.
        
        For Bullish OB: Need EQH (equal highs) to form after CHoCH
        For Bearish OB: Need EQL (equal lows) to form after CHoCH
        """
        ob_creation_index = ob.structure.break_index if ob.structure else ob.index
        
        for eqhl in equal_hls:
            # Check timing: EQH/EQL must be after OB creation and at or before current bar
            if eqhl.index <= ob_creation_index or eqhl.index > current_index:
                continue
            
            # For Bullish OB, need EQH
            if ob.bias == 'BULLISH' and eqhl.type == 'EQH':
                return True
            
            # For Bearish OB, need EQL
            if ob.bias == 'BEARISH' and eqhl.type == 'EQL':
                return True
        
        return False
    
    def _is_reversal_candle(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, current_idx: int, ob_bias: str,
                           is_closed_candle: bool = True) -> bool:
        """
        Check if current candle is a reversal pattern (bullish or bearish based on ob_bias).
        
        Uses JIT-compiled function from reversal_patterns module for performance.
        """
        if not is_closed_candle:
            return False
        is_bullish = (ob_bias == 'BULLISH')
        
        # Chuyển list thành numpy array cho JIT function
        pattern_ids = np.array(self.config.reversal_pattern_ids, dtype=np.int64)
        
        # Nếu chỉ có [0] thì dùng function check tất cả (nhanh hơn)
        if len(pattern_ids) == 1 and pattern_ids[0] == 0:
            return check_reversal_pattern_jit(opens, closes, highs, lows, current_idx, is_bullish)
        
        # Ngược lại, check theo danh sách IDs
        return check_pattern_by_ids_jit(opens, closes, highs, lows, current_idx, is_bullish, pattern_ids)
    


    
    def _price_touches_ob(self, ob: OrderBlock, high: float, low: float) -> bool:
        """
        Check if current candle's price range touches the OB zone.
        """
        # Price touches OB if candle low <= OB high AND candle high >= OB low
        return low <= ob.high and high >= ob.low
    
    def _ob_is_mitigated(self, ob: OrderBlock, high: float, low: float, close: float) -> bool:
        """
        Check if OB is fully mitigated (price went through completely).
        """
        if self.config.ob_mitigation == "close":
            if ob.bias == 'BULLISH':
                return close < ob.low
            else:
                return close > ob.high
        else:  # high_low
            if ob.bias == 'BULLISH':
                return low < ob.low
            else:
                return high > ob.high
    
    def _create_signal(self, symbol: str, ob: OrderBlock, entry_time: datetime,
                       entry_price: float, current_open: float,
                       current_high: float, current_low: float) -> Optional[Signal]:
        """
        Create a trade signal with SL and TP.
        
        LONG:
        - Entry: When price touches OB zone
        - SL: Below OB low with buffer
        - If SL is shorter than nearest swing low, use swing low as SL
        - TP: Entry + (Entry - SL) * RR
        
        SHORT:
        - Entry: When price touches OB zone
        - SL: Above OB high with buffer
        - If SL is shorter than nearest swing high, use swing high as SL
        - TP: Entry - (SL - Entry) * RR
        """
        ob_range = ob.high - ob.low
        buffer = ob_range * self.config.sl_buffer_pct
        
        if ob.bias == 'BULLISH':
            # LONG trade
            trade_type = 'LONG'
            # Entry tại đáy body nến (tránh râu nến)
            body_low = min(current_open, entry_price)
            entry = min(ob.high, body_low)
            
            # SL below OB with buffer, then apply multiplier
            base_sl = ob.low - buffer
            base_risk = entry - base_sl
            stop_loss = entry - (base_risk * self.config.sl_multiplier)
            
            # Nếu SL nằm trong râu nến entry, kéo SL xuống dưới low của nến
            if stop_loss > current_low:
                stop_loss = current_low - buffer
            
            # TP based on actual risk to maintain RR ratio
            actual_risk = entry - stop_loss
            
            take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
            
        else:  # BEARISH
            # SHORT trade
            trade_type = 'SHORT'
            # Entry tại đỉnh body nến (tránh râu nến)
            body_high = max(current_open, entry_price)
            entry = max(ob.low, body_high)
            
            # SL above OB with buffer, then apply multiplier
            base_sl = ob.high + buffer
            base_risk = base_sl - entry
            stop_loss = entry + (base_risk * self.config.sl_multiplier)
            
            # Nếu SL nằm trong râu nến entry, kéo SL lên trên high của nến
            if stop_loss < current_high:
                stop_loss = current_high + buffer
            
            # TP based on actual risk to maintain RR ratio
            actual_risk = stop_loss - entry
            
            take_profit = entry - (actual_risk * self.config.risk_reward_ratio)
        
        # Validate signal
        if trade_type == 'LONG' and stop_loss >= entry:
            return None
        if trade_type == 'SHORT' and stop_loss <= entry:
            return None
        
        # Check minimum SL percentage - adjust SL if too small
        sl_pct = abs(entry - stop_loss) / entry * 100
        if sl_pct < self.config.min_sl_pct:
            # Adjust SL to meet minimum requirement
            min_sl_distance = entry * self.config.min_sl_pct / 100
            if trade_type == 'LONG':
                stop_loss = entry - min_sl_distance
            else:
                stop_loss = entry + min_sl_distance
            # Recalculate TP with new SL
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
            take_profit=round_price(take_profit)
        )
    