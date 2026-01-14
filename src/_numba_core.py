"""
Numba JIT-compiled core functions for SMC Strategy
===================================================
High-performance implementations of critical loops using Numba.

All functions use numpy arrays with primitive types for maximum JIT efficiency.
"""

import numpy as np
from numba import njit, prange
from numba.typed import List as NumbaList


# ==================== SWING POINT DETECTION ====================

@njit(cache=True)
def detect_swing_highs_jit(highs: np.ndarray, length: int) -> np.ndarray:
    """
    Detect swing high indices using fractal logic.
    Returns array of indices where swing highs occur.
    
    A swing high at index i is confirmed when highs[i] > all highs in [i-length, i+length]
    """
    n = len(highs)
    if n < length * 2 + 1:
        return np.array([], dtype=np.int64)
    
    # Pre-allocate maximum possible size
    result = np.empty(n, dtype=np.int64)
    count = 0
    
    for i in range(length, n - length):
        is_swing_high = True
        for j in range(i - length, i + length + 1):
            if j != i and highs[j] >= highs[i]:
                is_swing_high = False
                break
        
        if is_swing_high:
            result[count] = i
            count += 1
    
    return result[:count].copy()


@njit(cache=True)
def detect_swing_lows_jit(lows: np.ndarray, length: int) -> np.ndarray:
    """
    Detect swing low indices using fractal logic.
    Returns array of indices where swing lows occur.
    """
    n = len(lows)
    if n < length * 2 + 1:
        return np.array([], dtype=np.int64)
    
    result = np.empty(n, dtype=np.int64)
    count = 0
    
    for i in range(length, n - length):
        is_swing_low = True
        for j in range(i - length, i + length + 1):
            if j != i and lows[j] <= lows[i]:
                is_swing_low = False
                break
        
        if is_swing_low:
            result[count] = i
            count += 1
    
    return result[:count].copy()


# ==================== INCREMENTAL SWING DETECTION ====================

@njit(cache=True)
def check_swing_high_at_bar(highs: np.ndarray, check_idx: int, length: int) -> bool:
    """
    Check if bar at check_idx is a swing high.
    Called when current_idx = check_idx + length (confirmation bar).
    """
    if check_idx < length:
        return False
    if check_idx + length >= len(highs):
        return False
    
    for j in range(check_idx - length, check_idx + length + 1):
        if j != check_idx and highs[j] >= highs[check_idx]:
            return False
    return True


@njit(cache=True)
def check_swing_low_at_bar(lows: np.ndarray, check_idx: int, length: int) -> bool:
    """
    Check if bar at check_idx is a swing low.
    """
    if check_idx < length:
        return False
    if check_idx + length >= len(lows):
        return False
    
    for j in range(check_idx - length, check_idx + length + 1):
        if j != check_idx and lows[j] <= lows[check_idx]:
            return False
    return True


# ==================== STRUCTURE DETECTION (BOS/CHoCH) ====================

@njit(cache=True)
def detect_structures_incremental_jit(
    closes: np.ndarray,
    swing_high_indices: np.ndarray,
    swing_high_prices: np.ndarray,
    swing_low_indices: np.ndarray,
    swing_low_prices: np.ndarray
) -> tuple:
    """
    Detect BOS and CHoCH structures incrementally.
    
    Returns:
        - structure_types: 0 = BOS, 1 = CHoCH
        - structure_biases: 0 = BEARISH, 1 = BULLISH
        - break_indices: Index where break occurred
        - pivot_indices: Index of the swing point that was broken
        - count: Number of structures detected
    """
    n = len(closes)
    max_structures = n  # Maximum possible structures
    
    # Pre-allocate arrays
    structure_types = np.empty(max_structures, dtype=np.int8)
    structure_biases = np.empty(max_structures, dtype=np.int8)
    break_indices = np.empty(max_structures, dtype=np.int64)
    pivot_indices = np.empty(max_structures, dtype=np.int64)
    count = 0
    
    if len(swing_high_indices) < 1 or len(swing_low_indices) < 1:
        return structure_types[:0], structure_biases[:0], break_indices[:0], pivot_indices[:0], 0
    
    # Initial trend
    if swing_high_indices[0] > swing_low_indices[0]:
        current_trend = 1  # BULLISH
    else:
        current_trend = 0  # BEARISH
    
    # Track current unbroken swing high/low
    high_idx = 0
    low_idx = 0
    last_broken_high_idx = -1
    last_broken_low_idx = -1
    
    # Start from after first swings
    start_idx = max(swing_high_indices[0], swing_low_indices[0]) + 1
    
    for i in range(start_idx, n):
        close = closes[i]
        
        # Update swing high index if we passed new confirmed swings
        while high_idx < len(swing_high_indices) - 1 and swing_high_indices[high_idx + 1] < i:
            high_idx += 1
        
        while low_idx < len(swing_low_indices) - 1 and swing_low_indices[low_idx + 1] < i:
            low_idx += 1
        
        current_sh_idx = swing_high_indices[high_idx]
        current_sh_price = swing_high_prices[high_idx]
        current_sl_idx = swing_low_indices[low_idx]
        current_sl_price = swing_low_prices[low_idx]
        
        # Check for break of swing high
        if current_sh_idx > last_broken_high_idx and close > current_sh_price:
            if current_trend == 1:  # BULLISH
                struct_type = 0  # BOS
            else:
                struct_type = 1  # CHoCH
            
            structure_types[count] = struct_type
            structure_biases[count] = 1  # BULLISH
            break_indices[count] = i
            pivot_indices[count] = current_sh_idx
            count += 1
            
            current_trend = 1  # BULLISH
            last_broken_high_idx = current_sh_idx
        
        # Check for break of swing low
        if current_sl_idx > last_broken_low_idx and close < current_sl_price:
            if current_trend == 0:  # BEARISH
                struct_type = 0  # BOS
            else:
                struct_type = 1  # CHoCH
            
            structure_types[count] = struct_type
            structure_biases[count] = 0  # BEARISH
            break_indices[count] = i
            pivot_indices[count] = current_sl_idx
            count += 1
            
            current_trend = 0  # BEARISH
            last_broken_low_idx = current_sl_idx
    
    return (structure_types[:count].copy(), 
            structure_biases[:count].copy(), 
            break_indices[:count].copy(), 
            pivot_indices[:count].copy(), 
            count)


# ==================== ORDER BLOCK DETECTION ====================

@njit(cache=True)
def find_order_block_jit(
    opens: np.ndarray,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    break_idx: int,
    pivot_idx: int,
    bias: int  # 0 = BEARISH, 1 = BULLISH
) -> tuple:
    """
    Find order block for a structure break.
    
    Returns:
        (ob_idx, ob_high, ob_low, found)
    """
    if bias == 1:  # BULLISH - look for bearish candle
        for i in range(break_idx - 1, pivot_idx - 1, -1):
            if i < 0:
                break
            if closes[i] < opens[i]:  # Bearish candle
                return i, highs[i], lows[i], True
    else:  # BEARISH - look for bullish candle
        for i in range(break_idx - 1, pivot_idx - 1, -1):
            if i < 0:
                break
            if closes[i] > opens[i]:  # Bullish candle
                return i, highs[i], lows[i], True
    
    return -1, 0.0, 0.0, False


# ==================== FVG DETECTION ====================

@njit(cache=True)
def detect_fvg_jit(
    highs: np.ndarray,
    lows: np.ndarray,
    atr: np.ndarray,
    use_filter: bool = True
) -> tuple:
    """
    Detect Fair Value Gaps.
    
    Returns:
        - fvg_indices: Index where FVG was detected
        - fvg_tops: Top of FVG
        - fvg_bottoms: Bottom of FVG  
        - fvg_biases: 0 = BEARISH, 1 = BULLISH
        - count: Number of FVGs
    """
    n = len(highs)
    max_fvgs = n
    
    fvg_indices = np.empty(max_fvgs, dtype=np.int64)
    fvg_tops = np.empty(max_fvgs, dtype=np.float64)
    fvg_bottoms = np.empty(max_fvgs, dtype=np.float64)
    fvg_biases = np.empty(max_fvgs, dtype=np.int8)
    count = 0
    
    for i in range(2, n):
        # Bullish FVG: current low > high 2 bars ago
        if lows[i] > highs[i - 2]:
            gap_size = lows[i] - highs[i - 2]
            
            if use_filter:
                atr_value = atr[i] if i < len(atr) else atr[-1]
                if gap_size < 0.1 * atr_value:
                    continue
            
            fvg_indices[count] = i
            fvg_tops[count] = lows[i]
            fvg_bottoms[count] = highs[i - 2]
            fvg_biases[count] = 1  # BULLISH
            count += 1
        
        # Bearish FVG: current high < low 2 bars ago
        if highs[i] < lows[i - 2]:
            gap_size = lows[i - 2] - highs[i]
            
            if use_filter:
                atr_value = atr[i] if i < len(atr) else atr[-1]
                if gap_size < 0.1 * atr_value:
                    continue
            
            fvg_indices[count] = i
            fvg_tops[count] = lows[i - 2]
            fvg_bottoms[count] = highs[i]
            fvg_biases[count] = 0  # BEARISH
            count += 1
    
    return (fvg_indices[:count].copy(),
            fvg_tops[:count].copy(),
            fvg_bottoms[:count].copy(),
            fvg_biases[:count].copy(),
            count)


# ==================== EQH/EQL DETECTION ====================

@njit(cache=True)
def detect_eqhl_jit(
    swing_indices: np.ndarray,
    swing_prices: np.ndarray,
    atr: np.ndarray,
    threshold: float,
    is_high: bool  # True for EQH, False for EQL
) -> tuple:
    """
    Detect Equal Highs or Equal Lows.
    
    Returns:
        - eqhl_indices: Index where EQH/EQL was detected (second pivot)
        - eqhl_levels: Average price level
        - first_pivot_indices: Index of first pivot
        - count: Number detected
    """
    n = len(swing_indices)
    max_eqhl = n * n // 2  # Maximum pairs
    
    eqhl_indices = np.empty(max_eqhl, dtype=np.int64)
    eqhl_levels = np.empty(max_eqhl, dtype=np.float64)
    first_pivot_indices = np.empty(max_eqhl, dtype=np.int64)
    count = 0
    
    for i in range(n):
        for j in range(i + 1, n):
            idx1 = swing_indices[i]
            idx2 = swing_indices[j]
            price1 = swing_prices[i]
            price2 = swing_prices[j]
            
            # Get ATR at second swing
            atr_idx = min(idx2, len(atr) - 1)
            atr_value = atr[atr_idx]
            
            if abs(price1 - price2) < threshold * atr_value:
                eqhl_indices[count] = idx2
                eqhl_levels[count] = (price1 + price2) / 2
                first_pivot_indices[count] = idx1
                count += 1
    
    return (eqhl_indices[:count].copy(),
            eqhl_levels[:count].copy(),
            first_pivot_indices[:count].copy(),
            count)


# ==================== ATR CALCULATION ====================

@njit(cache=True)
def calculate_atr_jit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
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
    # First period-1 values use available data
    cumsum = 0.0
    for i in range(n):
        cumsum += tr[i]
        if i < period:
            atr[i] = cumsum / (i + 1)
        else:
            cumsum -= tr[i - period]
            atr[i] = cumsum / period
    
    return atr


# ==================== MAIN SIGNAL GENERATION ====================

@njit(cache=True)
def check_fvg_for_ob_jit(
    ob_creation_idx: int,
    ob_bias: int,
    fvg_indices: np.ndarray,
    fvg_biases: np.ndarray,
    fvg_count: int
) -> bool:
    """
    Check if there's a matching FVG for an order block.
    """
    for i in range(fvg_count):
        if fvg_biases[i] != ob_bias:
            continue
        if fvg_indices[i] >= ob_creation_idx and fvg_indices[i] <= ob_creation_idx + 10:
            return True
    return False


@njit(cache=True)
def check_liquidity_for_ob_jit(
    ob_creation_idx: int,
    ob_bias: int,
    current_idx: int,
    eqh_indices: np.ndarray,
    eqh_count: int,
    eql_indices: np.ndarray,
    eql_count: int
) -> bool:
    """
    Check if EQH/EQL appeared after OB creation.
    For Bullish OB: Need EQH
    For Bearish OB: Need EQL
    """
    if ob_bias == 1:  # BULLISH - need EQH
        for i in range(eqh_count):
            if eqh_indices[i] > ob_creation_idx and eqh_indices[i] <= current_idx:
                return True
    else:  # BEARISH - need EQL
        for i in range(eql_count):
            if eql_indices[i] > ob_creation_idx and eql_indices[i] <= current_idx:
                return True
    return False


@njit(cache=True)
def generate_signals_core_jit(
    times_int: np.ndarray,  # int64 timestamps
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atr: np.ndarray,
    swing_length: int,
    eqhl_length: int,
    eqhl_threshold: float,
    fvg_atr_filter: bool,
    sl_buffer_pct: float,
    risk_reward_ratio: float
) -> tuple:
    """
    Core signal generation - fully JIT compiled.
    
    Returns signal arrays:
        - signal_times: int64 timestamps
        - signal_types: 0 = SHORT, 1 = LONG
        - entry_prices, stop_losses, take_profits
        - count
    """
    n = len(closes)
    max_signals = n // 10  # Rough estimate
    
    # Output arrays
    signal_times = np.empty(max_signals, dtype=np.int64)
    signal_types = np.empty(max_signals, dtype=np.int8)
    entry_prices = np.empty(max_signals, dtype=np.float64)
    stop_losses = np.empty(max_signals, dtype=np.float64)
    take_profits = np.empty(max_signals, dtype=np.float64)
    signal_count = 0
    
    # Confirmed swing points (dynamic arrays)
    max_swings = n // swing_length + 1
    confirmed_sh_indices = np.empty(max_swings, dtype=np.int64)
    confirmed_sh_prices = np.empty(max_swings, dtype=np.float64)
    sh_count = 0
    
    confirmed_sl_indices = np.empty(max_swings, dtype=np.int64)
    confirmed_sl_prices = np.empty(max_swings, dtype=np.float64)
    sl_count = 0
    
    # Internal swings for EQH/EQL
    max_internal = n // eqhl_length + 1
    internal_h_indices = np.empty(max_internal, dtype=np.int64)
    internal_h_prices = np.empty(max_internal, dtype=np.float64)
    ih_count = 0
    
    internal_l_indices = np.empty(max_internal, dtype=np.int64)
    internal_l_prices = np.empty(max_internal, dtype=np.float64)
    il_count = 0
    
    # EQH/EQL storage
    max_eqhl = max_internal * 2
    eqh_indices = np.empty(max_eqhl, dtype=np.int64)
    eqh_count = 0
    eql_indices = np.empty(max_eqhl, dtype=np.int64)
    eql_count = 0
    
    # FVG storage
    max_fvg = n // 3 + 1
    fvg_indices = np.empty(max_fvg, dtype=np.int64)
    fvg_biases = np.empty(max_fvg, dtype=np.int8)
    fvg_count = 0
    
    # Active Order Blocks (fixed max size)
    max_obs = 100
    ob_highs = np.empty(max_obs, dtype=np.float64)
    ob_lows = np.empty(max_obs, dtype=np.float64)
    ob_indices = np.empty(max_obs, dtype=np.int64)
    ob_biases = np.empty(max_obs, dtype=np.int8)
    ob_creation_indices = np.empty(max_obs, dtype=np.int64)
    ob_has_fvg = np.zeros(max_obs, dtype=np.bool_)
    ob_has_liquidity = np.zeros(max_obs, dtype=np.bool_)
    ob_entry_triggered = np.zeros(max_obs, dtype=np.bool_)
    ob_active = np.zeros(max_obs, dtype=np.bool_)
    ob_count = 0
    
    # Track trend and broken levels
    current_trend = -1  # -1 = undefined, 0 = BEARISH, 1 = BULLISH
    last_broken_high_idx = -1
    last_broken_low_idx = -1
    
    # Process each bar
    for current_idx in range(n):
        current_time = times_int[current_idx]
        
        # ========== 1. Check for newly confirmed swing HIGH ==========
        check_idx = current_idx - swing_length
        if check_idx >= swing_length:
            is_swing_high = True
            for j in range(check_idx - swing_length, check_idx + swing_length + 1):
                if j != check_idx and j >= 0 and j < n and highs[j] >= highs[check_idx]:
                    is_swing_high = False
                    break
            
            if is_swing_high and sh_count < max_swings:
                confirmed_sh_indices[sh_count] = check_idx
                confirmed_sh_prices[sh_count] = highs[check_idx]
                sh_count += 1
        
        # ========== 2. Check for newly confirmed swing LOW ==========
        if check_idx >= swing_length:
            is_swing_low = True
            for j in range(check_idx - swing_length, check_idx + swing_length + 1):
                if j != check_idx and j >= 0 and j < n and lows[j] <= lows[check_idx]:
                    is_swing_low = False
                    break
            
            if is_swing_low and sl_count < max_swings:
                confirmed_sl_indices[sl_count] = check_idx
                confirmed_sl_prices[sl_count] = lows[check_idx]
                sl_count += 1
        
        # ========== 3. Initialize trend ==========
        if current_trend == -1 and sh_count > 0 and sl_count > 0:
            if confirmed_sh_indices[0] > confirmed_sl_indices[0]:
                current_trend = 1  # BULLISH
            else:
                current_trend = 0  # BEARISH
        
        # ========== 4. Detect structure breaks and create OBs ==========
        if current_trend >= 0 and sh_count > 0 and sl_count > 0:
            # Find most recent unbroken swing high
            current_sh_idx_ptr = -1
            for i in range(sh_count - 1, -1, -1):
                if confirmed_sh_indices[i] > last_broken_high_idx:
                    current_sh_idx_ptr = i
                    break
            
            # Find most recent unbroken swing low
            current_sl_idx_ptr = -1
            for i in range(sl_count - 1, -1, -1):
                if confirmed_sl_indices[i] > last_broken_low_idx:
                    current_sl_idx_ptr = i
                    break
            
            # Check break of swing high
            if current_sh_idx_ptr >= 0:
                sh_idx = confirmed_sh_indices[current_sh_idx_ptr]
                sh_price = confirmed_sh_prices[current_sh_idx_ptr]
                
                if closes[current_idx] > sh_price:
                    is_choch = (current_trend == 0)  # CHoCH if was bearish
                    current_trend = 1  # BULLISH
                    last_broken_high_idx = sh_idx
                    
                    # Create OB for CHoCH only
                    if is_choch and ob_count < max_obs:
                        # Find last bearish candle
                        for i in range(current_idx - 1, sh_idx - 1, -1):
                            if i >= 0 and closes[i] < opens[i]:
                                ob_highs[ob_count] = highs[i]
                                ob_lows[ob_count] = lows[i]
                                ob_indices[ob_count] = i
                                ob_biases[ob_count] = 1  # BULLISH
                                ob_creation_indices[ob_count] = current_idx
                                ob_has_fvg[ob_count] = check_fvg_for_ob_jit(
                                    current_idx, 1, fvg_indices[:fvg_count], 
                                    fvg_biases[:fvg_count], fvg_count
                                )
                                ob_has_liquidity[ob_count] = False
                                ob_entry_triggered[ob_count] = False
                                ob_active[ob_count] = True
                                ob_count += 1
                                break
            
            # Check break of swing low
            if current_sl_idx_ptr >= 0:
                sl_idx = confirmed_sl_indices[current_sl_idx_ptr]
                sl_price = confirmed_sl_prices[current_sl_idx_ptr]
                
                if closes[current_idx] < sl_price:
                    is_choch = (current_trend == 1)  # CHoCH if was bullish
                    current_trend = 0  # BEARISH
                    last_broken_low_idx = sl_idx
                    
                    # Create OB for CHoCH only
                    if is_choch and ob_count < max_obs:
                        # Find last bullish candle
                        for i in range(current_idx - 1, sl_idx - 1, -1):
                            if i >= 0 and closes[i] > opens[i]:
                                ob_highs[ob_count] = highs[i]
                                ob_lows[ob_count] = lows[i]
                                ob_indices[ob_count] = i
                                ob_biases[ob_count] = 0  # BEARISH
                                ob_creation_indices[ob_count] = current_idx
                                ob_has_fvg[ob_count] = check_fvg_for_ob_jit(
                                    current_idx, 0, fvg_indices[:fvg_count], 
                                    fvg_biases[:fvg_count], fvg_count
                                )
                                ob_has_liquidity[ob_count] = False
                                ob_entry_triggered[ob_count] = False
                                ob_active[ob_count] = True
                                ob_count += 1
                                break
        
        # ========== 5. Detect FVG at current bar ==========
        if current_idx >= 2 and fvg_count < max_fvg:
            # Bullish FVG
            if lows[current_idx] > highs[current_idx - 2]:
                gap_size = lows[current_idx] - highs[current_idx - 2]
                atr_value = atr[current_idx]
                
                if not fvg_atr_filter or gap_size >= 0.1 * atr_value:
                    fvg_indices[fvg_count] = current_idx
                    fvg_biases[fvg_count] = 1  # BULLISH
                    fvg_count += 1
                    
                    # Update has_fvg for active OBs
                    for k in range(ob_count):
                        if ob_active[k] and not ob_has_fvg[k] and ob_biases[k] == 1:
                            ob_has_fvg[k] = check_fvg_for_ob_jit(
                                ob_creation_indices[k], 1, 
                                fvg_indices[:fvg_count], fvg_biases[:fvg_count], fvg_count
                            )
            
            # Bearish FVG
            if highs[current_idx] < lows[current_idx - 2]:
                gap_size = lows[current_idx - 2] - highs[current_idx]
                atr_value = atr[current_idx]
                
                if not fvg_atr_filter or gap_size >= 0.1 * atr_value:
                    fvg_indices[fvg_count] = current_idx
                    fvg_biases[fvg_count] = 0  # BEARISH
                    fvg_count += 1
                    
                    # Update has_fvg for active OBs
                    for k in range(ob_count):
                        if ob_active[k] and not ob_has_fvg[k] and ob_biases[k] == 0:
                            ob_has_fvg[k] = check_fvg_for_ob_jit(
                                ob_creation_indices[k], 0, 
                                fvg_indices[:fvg_count], fvg_biases[:fvg_count], fvg_count
                            )
        
        # ========== 6. Detect internal swings for EQH/EQL ==========
        internal_check_idx = current_idx - eqhl_length
        if internal_check_idx >= eqhl_length:
            # Check internal high
            is_internal_high = True
            for j in range(internal_check_idx - eqhl_length, internal_check_idx + eqhl_length + 1):
                if j != internal_check_idx and j >= 0 and j < n and highs[j] >= highs[internal_check_idx]:
                    is_internal_high = False
                    break
            
            if is_internal_high and ih_count < max_internal:
                # Check for EQH with previous internal highs
                atr_value = atr[internal_check_idx]
                for prev_i in range(ih_count):
                    if abs(highs[internal_check_idx] - internal_h_prices[prev_i]) < eqhl_threshold * atr_value:
                        if eqh_count < max_eqhl:
                            eqh_indices[eqh_count] = internal_check_idx
                            eqh_count += 1
                        break
                
                internal_h_indices[ih_count] = internal_check_idx
                internal_h_prices[ih_count] = highs[internal_check_idx]
                ih_count += 1
            
            # Check internal low
            is_internal_low = True
            for j in range(internal_check_idx - eqhl_length, internal_check_idx + eqhl_length + 1):
                if j != internal_check_idx and j >= 0 and j < n and lows[j] <= lows[internal_check_idx]:
                    is_internal_low = False
                    break
            
            if is_internal_low and il_count < max_internal:
                # Check for EQL with previous internal lows
                atr_value = atr[internal_check_idx]
                for prev_i in range(il_count):
                    if abs(lows[internal_check_idx] - internal_l_prices[prev_i]) < eqhl_threshold * atr_value:
                        if eql_count < max_eqhl:
                            eql_indices[eql_count] = internal_check_idx
                            eql_count += 1
                        break
                
                internal_l_indices[il_count] = internal_check_idx
                internal_l_prices[il_count] = lows[internal_check_idx]
                il_count += 1
        
        # ========== 7. Update OB liquidity status ==========
        for k in range(ob_count):
            if ob_active[k] and not ob_has_liquidity[k]:
                ob_has_liquidity[k] = check_liquidity_for_ob_jit(
                    ob_creation_indices[k], ob_biases[k], current_idx,
                    eqh_indices[:eqh_count], eqh_count,
                    eql_indices[:eql_count], eql_count
                )
        
        # ========== 8. Check for entry at current bar ==========
        for k in range(ob_count):
            if not ob_active[k]:
                continue
            
            ob_high = ob_highs[k]
            ob_low = ob_lows[k]
            ob_bias = ob_biases[k]
            
            # Check if entry already triggered
            if ob_entry_triggered[k]:
                # Still check mitigation
                if ob_bias == 1:  # BULLISH OB
                    if lows[current_idx] < ob_low:
                        ob_active[k] = False
                else:  # BEARISH OB
                    if highs[current_idx] > ob_high:
                        ob_active[k] = False
                continue
            
            # Check if price touches OB zone
            price_touches = (lows[current_idx] <= ob_high and highs[current_idx] >= ob_low)
            
            if price_touches and ob_has_fvg[k] and ob_has_liquidity[k]:
                # Generate signal
                ob_range = ob_high - ob_low
                buffer = ob_range * sl_buffer_pct
                
                if ob_bias == 1:  # BULLISH -> LONG
                    entry = min(ob_high, closes[current_idx])
                    stop_loss = ob_low - buffer
                    risk = entry - stop_loss
                    take_profit = entry + (risk * risk_reward_ratio)
                    
                    if stop_loss < entry and signal_count < max_signals:
                        signal_times[signal_count] = current_time
                        signal_types[signal_count] = 1  # LONG
                        entry_prices[signal_count] = entry
                        stop_losses[signal_count] = stop_loss
                        take_profits[signal_count] = take_profit
                        signal_count += 1
                        ob_entry_triggered[k] = True
                
                else:  # BEARISH -> SHORT
                    entry = max(ob_low, closes[current_idx])
                    stop_loss = ob_high + buffer
                    risk = stop_loss - entry
                    take_profit = entry - (risk * risk_reward_ratio)
                    
                    if stop_loss > entry and signal_count < max_signals:
                        signal_times[signal_count] = current_time
                        signal_types[signal_count] = 0  # SHORT
                        entry_prices[signal_count] = entry
                        stop_losses[signal_count] = stop_loss
                        take_profits[signal_count] = take_profit
                        signal_count += 1
                        ob_entry_triggered[k] = True
            
            # Check if OB is fully mitigated
            if ob_bias == 1:  # BULLISH OB
                if lows[current_idx] < ob_low:
                    ob_active[k] = False
            else:  # BEARISH OB
                if highs[current_idx] > ob_high:
                    ob_active[k] = False
    
    return (signal_times[:signal_count].copy(),
            signal_types[:signal_count].copy(),
            entry_prices[:signal_count].copy(),
            stop_losses[:signal_count].copy(),
            take_profits[:signal_count].copy(),
            signal_count)


# ==================== TRADE SIMULATION ====================

@njit(cache=True)
def simulate_trades_jit(
    signal_times: np.ndarray,
    signal_types: np.ndarray,
    entry_prices: np.ndarray,
    stop_losses: np.ndarray,
    take_profits: np.ndarray,
    data_times: np.ndarray,
    data_highs: np.ndarray,
    data_lows: np.ndarray,
    data_closes: np.ndarray
) -> tuple:
    """
    Simulate trade exits - JIT optimized.
    
    Returns:
        - exit_times: int64 timestamps
        - exit_prices: float64
        - results: 0 = LOSS, 1 = WIN, 2 = OPEN
    """
    n_signals = len(signal_times)
    n_data = len(data_times)
    
    exit_times = np.empty(n_signals, dtype=np.int64)
    exit_prices = np.empty(n_signals, dtype=np.float64)
    results = np.empty(n_signals, dtype=np.int8)
    
    for i in range(n_signals):
        signal_time = signal_times[i]
        signal_type = signal_types[i]
        sl = stop_losses[i]
        tp = take_profits[i]
        
        # Binary search for entry index
        entry_idx = -1
        left, right = 0, n_data - 1
        while left <= right:
            mid = (left + right) // 2
            if data_times[mid] >= signal_time:
                entry_idx = mid
                right = mid - 1
            else:
                left = mid + 1
        
        if entry_idx < 0:
            exit_times[i] = data_times[-1]
            exit_prices[i] = data_closes[-1]
            results[i] = 2  # OPEN
            continue
        
        # Look for exit
        found_exit = False
        for j in range(entry_idx + 1, n_data):
            high = data_highs[j]
            low = data_lows[j]
            
            if signal_type == 1:  # LONG
                # Check SL first
                if low <= sl:
                    exit_times[i] = data_times[j]
                    exit_prices[i] = sl
                    results[i] = 0  # LOSS
                    found_exit = True
                    break
                # Check TP
                if high >= tp:
                    exit_times[i] = data_times[j]
                    exit_prices[i] = tp
                    results[i] = 1  # WIN
                    found_exit = True
                    break
            else:  # SHORT
                # Check SL first
                if high >= sl:
                    exit_times[i] = data_times[j]
                    exit_prices[i] = sl
                    results[i] = 0  # LOSS
                    found_exit = True
                    break
                # Check TP
                if low <= tp:
                    exit_times[i] = data_times[j]
                    exit_prices[i] = tp
                    results[i] = 1  # WIN
                    found_exit = True
                    break
        
        if not found_exit:
            exit_times[i] = data_times[-1]
            exit_prices[i] = data_closes[-1]
            results[i] = 2  # OPEN
    
    return exit_times, exit_prices, results
