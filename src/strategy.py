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
from reversal_patterns import check_reversal_pattern_jit, check_pattern_by_ids_jit


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "SMC - Order Block + CHoCH + Liquidity"


# ==================== CONFIGURATION ====================
@dataclass
class Config:
    """
    SMC Trading strategy configuration.
    """
    # === SYSTEM SETTINGS (set by main.py) ===
    data_folder: str = "src/data"
    output_folder: str = "src/output"
    
    # === STRUCTURE DETECTION ===
    swing_length: int = 10          # Lookback for swing structure (adjusted for 15m/30m)
    internal_length: int = 3        # Lookback for internal structure
    
    # === EQH/EQL DETECTION ===
    eqhl_length: int = 3            # Smaller length for EQH/EQL detection (faster detection)
    eqhl_threshold: float = 0.3     # ATR multiplier for EQH/EQL sensitivity (increased)
    
    # === ORDER BLOCK ===
    ob_mitigation: str = "high_low" # "close" or "high_low"
    ob_body_ratio: float = 0.5      # Minimum body/range ratio for OB (filter weak candles like doji)
    ob_body_ratio: float = 0.5      # Minimum body/range ratio for OB (filter weak candles like doji)
    
    # === FVG ===
    fvg_atr_filter: bool = True     # Filter small FVGs using ATR
    
    # === RISK MANAGEMENT ===
    sl_buffer_pct: float = 0.1      # Buffer SL (% of OB range)
    sl_multiplier: float = 1.05   # Hệ số mở rộng SL (1.0 = bình thường, 1.1 = mở rộng 10% tránh stop hunt)
    risk_reward_ratio: float = 2.0  # Fixed RR = 1:2
    risk_per_trade: float = 10.0    # USD
    
    # === REVERSAL PATTERN FILTER ===
    # Mảng các ID cặp pattern muốn sử dụng
    # [0] = tất cả patterns (mặc định)
    # [1] = chỉ doji
    # [1, 2] = doji + hammer/hanging
    # Pattern Pair IDs:
    #   0 = all (tất cả patterns)
    #   1 = doji
    #   2 = hammer / hanging_man
    #   3 = inverted_hammer / shooting_star
    #   4 = bullish_engulfing / bearish_engulfing
    #   5 = piercing_line / dark_cloud_cover
    #   6 = bullish_harami / bearish_harami
    #   7 = three_white_soldiers / three_black_crows
    #   8 = morning_star / evening_star
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [4,5])
    
    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "swing_length": self.swing_length,
            "internal_length": self.internal_length,
            "eqhl_length": self.eqhl_length,
            "eqhl_threshold": self.eqhl_threshold,
            "ob_body_ratio": self.ob_body_ratio,
            "sl_buffer_pct": self.sl_buffer_pct,
            "sl_multiplier": self.sl_multiplier,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
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
    """Represents an Order Block zone"""
    high: float
    low: float
    time: datetime
    index: int
    bias: str           # 'BULLISH' or 'BEARISH'
    is_choch_ob: bool   # True if this OB created a CHoCH
    body_ratio: float = 0.0     # Body to range ratio (for filtering weak candles)
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


def detect_order_blocks(df: pd.DataFrame, structures: List[Structure]) -> List[OrderBlock]:
    """
    Detect Order Blocks from structure breaks.
    
    Order Block is the last opposite candle before the structure break:
    - Bullish OB: Last bearish (red) candle before bullish break
    - Bearish OB: Last bullish (green) candle before bearish break
    """
    order_blocks = []
    
    opens = df['Open'].values
    closes = df['Close'].values
    highs = df['High'].values
    lows = df['Low'].values
    times = df['Time'].values
    
    for structure in structures:
        # Find the last opposite candle before the break
        ob_index = None
        
        if structure.bias == 'BULLISH':
            # Looking for last bearish candle before bullish break
            for i in range(structure.break_index - 1, structure.pivot_index - 1, -1):
                if i < 0:
                    break
                if closes[i] < opens[i]:  # Bearish candle
                    ob_index = i
                    break
        else:  # BEARISH
            # Looking for last bullish candle before bearish break
            for i in range(structure.break_index - 1, structure.pivot_index - 1, -1):
                if i < 0:
                    break
                if closes[i] > opens[i]:  # Bullish candle
                    ob_index = i
                    break
        
        if ob_index is not None:
            ob = OrderBlock(
                high=highs[ob_index],
                low=lows[ob_index],
                time=pd.to_datetime(times[ob_index]),
                index=ob_index,
                bias=structure.bias,
                is_choch_ob=(structure.type == 'CHoCH'),
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
        
        # ========== PRE-COMPUTE ALL SWING POINTS ==========
        # This is much faster than checking each bar individually
        swing_high_indices_arr, swing_low_indices_arr = _precompute_all_swings_jit(
            highs, lows, self.config.swing_length
        )
        internal_high_indices_arr, internal_low_indices_arr = _precompute_all_swings_jit(
            highs, lows, self.config.eqhl_length
        )
        
        # Convert to sets for O(1) lookup
        swing_high_indices_set = set(swing_high_indices_arr)
        swing_low_indices_set = set(swing_low_indices_arr)
        internal_high_indices_set = set(internal_high_indices_arr)
        internal_low_indices_set = set(internal_low_indices_arr)
        
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
        
        # ========== MAIN LOOP VARIABLES ==========
        confirmed_swing_highs = []  # List of SwingPoint
        confirmed_swing_lows = []   # List of SwingPoint
        confirmed_internal_highs = []
        confirmed_internal_lows = []
        
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
            current_time = pd.to_datetime(times[current_idx])
            
            # 1. Check if a swing HIGH gets confirmed at this bar
            swing_high_idx = current_idx - self.config.swing_length
            if swing_high_idx >= self.config.swing_length and swing_high_idx in swing_high_indices_set:
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
            if swing_low_idx >= self.config.swing_length and swing_low_idx in swing_low_indices_set:
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
                    
                    # Create order block for CHoCH
                    if structure_type == 'CHoCH':
                        for i in range(current_idx - 1, structure.pivot_index - 1, -1):
                            if i >= 0 and closes[i] < opens[i]:
                                # Check body ratio - filter weak candles (doji, spinning top)
                                candle_range = highs[i] - lows[i]
                                body = abs(closes[i] - opens[i])
                                body_ratio = body / candle_range if candle_range > 0 else 0
                                
                                if body_ratio >= self.config.ob_body_ratio:
                                    ob = OrderBlock(
                                        high=highs[i],
                                        low=lows[i],
                                        time=pd.to_datetime(times[i]),
                                        index=i,
                                        bias='BULLISH',
                                        is_choch_ob=True,
                                        body_ratio=body_ratio,
                                        structure=structure
                                    )
                                    ob.has_fvg = self._check_fvg_for_ob(ob, all_fvgs)
                                    active_obs.append(ob)
                                    break
                
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
                    
                    # Create order block for CHoCH
                    if structure_type == 'CHoCH':
                        for i in range(current_idx - 1, structure.pivot_index - 1, -1):
                            if i >= 0 and closes[i] > opens[i]:
                                # Check body ratio - filter weak candles (doji, spinning top)
                                candle_range = highs[i] - lows[i]
                                body = abs(closes[i] - opens[i])
                                body_ratio = body / candle_range if candle_range > 0 else 0
                                
                                if body_ratio >= self.config.ob_body_ratio:
                                    ob = OrderBlock(
                                        high=highs[i],
                                        low=lows[i],
                                        time=pd.to_datetime(times[i]),
                                        index=i,
                                        bias='BEARISH',
                                        is_choch_ob=True,
                                        body_ratio=body_ratio,
                                        structure=structure
                                    )
                                    ob.has_fvg = self._check_fvg_for_ob(ob, all_fvgs)
                                    active_obs.append(ob)
                                    break
            
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
            
            # 5. Detect internal swings for EQH/EQL
            internal_high_idx = current_idx - self.config.eqhl_length
            if internal_high_idx >= self.config.eqhl_length and internal_high_idx in internal_high_indices_set:
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
            if internal_low_idx >= self.config.eqhl_length and internal_low_idx in internal_low_indices_set:
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
            
            # 7. Check for entry at CURRENT bar
            obs_to_remove = []
            
            for ob in active_obs:
                if ob.entry_triggered:
                    if self._ob_is_mitigated(ob, highs[current_idx], lows[current_idx], closes[current_idx]):
                        ob.is_mitigated = True
                        obs_to_remove.append(ob)
                    continue
                
                if self._price_touches_ob(ob, highs[current_idx], lows[current_idx]):
                    # Check if current candle is a reversal pattern
                    is_reversal = self._is_reversal_candle(opens, closes, highs, lows, 
                                                           current_idx, ob.bias)
                    
                    if ob.has_fvg and ob.has_liquidity and is_reversal:
                        signal = self._create_signal(
                            symbol=symbol,
                            ob=ob,
                            entry_time=current_time,
                            entry_price=closes[current_idx],
                            current_high=highs[current_idx],
                            current_low=lows[current_idx]
                        )
                        
                        if signal:
                            signals.append(signal)
                            ob.entry_triggered = True
                
                if self._ob_is_mitigated(ob, highs[current_idx], lows[current_idx], closes[current_idx]):
                    ob.is_mitigated = True
                    obs_to_remove.append(ob)
            
            # Remove mitigated OBs
            for ob in obs_to_remove:
                if ob in active_obs:
                    active_obs.remove(ob)
        
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
                           lows: np.ndarray, current_idx: int, ob_bias: str) -> bool:
        """
        Check if current candle is a reversal pattern (bullish or bearish based on ob_bias).
        
        Uses JIT-compiled function from reversal_patterns module for performance.
        """
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
                       entry_price: float, current_high: float, 
                       current_low: float) -> Optional[Signal]:
        """
        Create a trade signal with SL and TP.
        
        LONG:
        - Entry: When price touches OB zone
        - SL: Below OB low with buffer
        - TP: Entry + (Entry - SL) * RR
        
        SHORT:
        - Entry: When price touches OB zone
        - SL: Above OB high with buffer
        - TP: Entry - (SL - Entry) * RR
        """
        ob_range = ob.high - ob.low
        buffer = ob_range * self.config.sl_buffer_pct
        
        if ob.bias == 'BULLISH':
            # LONG trade
            trade_type = 'LONG'
            # Entry at OB top (more conservative) or current close
            entry = min(ob.high, entry_price)
            # SL below OB with buffer, then apply multiplier
            base_sl = ob.low - buffer
            base_risk = entry - base_sl
            stop_loss = entry - (base_risk * self.config.sl_multiplier)
            # TP based on actual risk to maintain RR ratio
            actual_risk = entry - stop_loss
            take_profit = entry + (actual_risk * self.config.risk_reward_ratio)
            
        else:  # BEARISH
            # SHORT trade
            trade_type = 'SHORT'
            # Entry at OB bottom (more conservative) or current close
            entry = max(ob.low, entry_price)
            # SL above OB with buffer, then apply multiplier
            base_sl = ob.high + buffer
            base_risk = base_sl - entry
            stop_loss = entry + (base_risk * self.config.sl_multiplier)
            # TP based on actual risk to maintain RR ratio
            actual_risk = stop_loss - entry
            take_profit = entry - (actual_risk * self.config.risk_reward_ratio)
        
        # Validate signal
        if trade_type == 'LONG' and stop_loss >= entry:
            return None
        if trade_type == 'SHORT' and stop_loss <= entry:
            return None
        
        return Signal(
            symbol=symbol,
            trade_type=trade_type,
            entry_time=entry_time,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
