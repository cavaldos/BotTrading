"""
Smart Money Concepts (SMC) Trading Strategy
============================================
Strategy based on institutional trading concepts:
- Market Structure: Swing Points, BOS, CHoCH
- Order Blocks (OB)
- Fair Value Gaps (FVG)
- Premium/Discount Zones
- Equal Highs/Lows
- Strong/Weak Levels

Entry Conditions:
- LONG: Bullish trend/CHoCH + Discount Zone + Near Bullish OB/FVG
- SHORT: Bearish trend/CHoCH + Premium Zone + Near Bearish OB/FVG
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Tuple, List, Optional
from dataclasses import dataclass, field
from enum import Enum


# ==================== ENUMS ====================
class Bias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SignalType(Enum):
    BOS = "BOS"
    CHOCH = "CHoCH"
    NONE = "NONE"


# ==================== CONFIGURATION ====================
@dataclass
class Config:
    """SMC Trading strategy configuration"""
    # Market Structure settings
    swing_length: int = 15           # Bars for swing point detection
    internal_length: int = 5         # Bars for internal structure
    
    # Order Block settings
    ob_filter: str = "ATR"           # "ATR" or "CMR" (Cumulative Mean Range)
    ob_mitigation: str = "HighLow"   # "Close" or "HighLow"
    max_order_blocks: int = 20       # Max OBs to track
    choch_only: bool = True          # Only trade OBs from CHoCH signals (improves win rate)
    
    # Fair Value Gap settings
    fvg_auto_threshold: bool = True  # Auto calculate FVG threshold
    fvg_threshold_multiplier: float = 2.0  # Threshold = avg_delta * multiplier
    
    # Equal Highs/Lows settings
    eqhl_length: int = 3             # Confirmation bars
    eqhl_threshold: float = 0.1      # Threshold as % of ATR (0.1 = 10%)
    
    # Premium/Discount Zone settings
    premium_threshold: float = 0.7   # Above 70% = Premium
    discount_threshold: float = 0.3  # Below 30% = Discount
    
    # ATR settings
    atr_period: int = 14
    atr_multiplier_sl: float = 1.5   # SL = ATR * multiplier
    
    # Risk/Reward settings
    risk_reward_ratio: float = 2.0   # TP = SL * RR
    risk_per_trade: float = 10.0     # USD per trade
    
    # Entry settings - minimum conditions required
    min_entry_conditions: int = 3    # Minimum conditions to enter trade
    
    # Data settings
    data_folder: str = "src/data"
    output_folder: str = "output"


# ==================== DATA CLASSES ====================
@dataclass
class SwingPoint:
    """Represents a swing high or swing low"""
    price: float
    bar_index: int
    bar_time: datetime
    swing_type: str          # 'HIGH' or 'LOW'
    pattern: str = ""        # 'HH', 'HL', 'LH', 'LL'
    crossed: bool = False
    is_strong: bool = False  # Strong or Weak level


@dataclass
class OrderBlock:
    """Represents an Order Block zone"""
    high: float
    low: float
    bar_index: int
    bar_time: datetime
    bias: Bias
    mitigated: bool = False
    mitigation_time: Optional[datetime] = None
    touched: bool = False  # Flag to track if OB has been touched for entry
    signal_type: str = "BOS"  # "BOS" or "CHOCH" - source of OB creation


@dataclass
class FairValueGap:
    """Represents a Fair Value Gap"""
    top: float
    bottom: float
    bar_index: int
    bar_time: datetime
    bias: Bias
    filled: bool = False
    fill_time: Optional[datetime] = None


@dataclass
class EqualLevel:
    """Represents Equal Highs or Equal Lows"""
    price: float
    bar_index: int
    bar_time: datetime
    level_type: str  # 'EQH' or 'EQL'
    swept: bool = False


@dataclass
class Trade:
    """Represents a single trade"""
    symbol: str
    trade_id: int
    entry_time: datetime
    entry_price: float
    trade_type: str          # 'LONG' or 'SHORT'
    stop_loss: float
    take_profit: float
    entry_reason: str = ""   # Entry signal description
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    result: Optional[str] = None  # 'WIN', 'LOSS', 'OPEN'
    pnl: float = 0.0
    risk_reward: float = 0.0
    status: str = 'OPEN'     # 'OPEN', 'CLOSED'


@dataclass
class SMCState:
    """Tracks the current SMC state for backtesting"""
    swing_trend: Bias = Bias.NEUTRAL
    internal_trend: Bias = Bias.NEUTRAL
    
    # Swing points
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows: List[SwingPoint] = field(default_factory=list)
    
    # Current swing levels
    current_swing_high: Optional[SwingPoint] = None
    current_swing_low: Optional[SwingPoint] = None
    
    # Order blocks
    bullish_obs: List[OrderBlock] = field(default_factory=list)
    bearish_obs: List[OrderBlock] = field(default_factory=list)
    
    # Fair value gaps
    bullish_fvgs: List[FairValueGap] = field(default_factory=list)
    bearish_fvgs: List[FairValueGap] = field(default_factory=list)
    
    # Equal levels
    equal_highs: List[EqualLevel] = field(default_factory=list)
    equal_lows: List[EqualLevel] = field(default_factory=list)
    
    # Premium/Discount
    range_high: float = 0.0
    range_low: float = 0.0
    equilibrium: float = 0.0
    
    # Signals
    last_bos_choch: SignalType = SignalType.NONE
    last_signal_bias: Bias = Bias.NEUTRAL


# ==================== INDICATORS ====================
def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr: pd.Series = true_range.ewm(span=period, adjust=False).mean()  # type: ignore
    
    return atr


def calculate_cumulative_mean_range(df: pd.DataFrame) -> pd.Series:
    """Calculate Cumulative Mean Range for volatility filtering"""
    tr = df['High'] - df['Low']
    cumulative_tr = tr.cumsum()
    bar_count = pd.Series(range(1, len(df) + 1), index=df.index)
    result = cumulative_tr / bar_count
    return pd.Series(result, index=df.index)


# ==================== SWING POINT DETECTION ====================
def detect_swing_high(df: pd.DataFrame, index: int, length: int) -> bool:
    """Detect if bar at index is a swing high"""
    if index < length or index >= len(df) - length:
        return False
    
    current_high = df['High'].iloc[index]
    
    # Check left side
    for i in range(index - length, index):
        if df['High'].iloc[i] >= current_high:
            return False
    
    # Check right side
    for i in range(index + 1, index + length + 1):
        if i >= len(df):
            return False
        if df['High'].iloc[i] >= current_high:
            return False
    
    return True


def detect_swing_low(df: pd.DataFrame, index: int, length: int) -> bool:
    """Detect if bar at index is a swing low"""
    if index < length or index >= len(df) - length:
        return False
    
    current_low = df['Low'].iloc[index]
    
    # Check left side
    for i in range(index - length, index):
        if df['Low'].iloc[i] <= current_low:
            return False
    
    # Check right side
    for i in range(index + 1, index + length + 1):
        if i >= len(df):
            return False
        if df['Low'].iloc[i] <= current_low:
            return False
    
    return True


def classify_swing_pattern(current_price: float, previous_price: float, 
                          swing_type: str) -> str:
    """Classify swing pattern as HH, HL, LH, LL"""
    if swing_type == 'HIGH':
        return 'HH' if current_price > previous_price else 'LH'
    else:  # LOW
        return 'HL' if current_price > previous_price else 'LL'


def update_swing_points(df: pd.DataFrame, state: SMCState, index: int, 
                        config: Config) -> Tuple[bool, bool, SignalType, Bias]:
    """
    Update swing points and detect BOS/CHoCH
    Returns: (new_swing_high, new_swing_low, signal_type, signal_bias)
    """
    new_swing_high = False
    new_swing_low = False
    signal_type = SignalType.NONE
    signal_bias = Bias.NEUTRAL
    
    row = df.iloc[index]
    bar_time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
    
    # Check for swing high (with confirmation delay)
    check_idx = index - config.swing_length
    if check_idx > 0 and detect_swing_high(df, check_idx, config.swing_length):
        swing_price = df['High'].iloc[check_idx]
        swing_time = df['Time'].iloc[check_idx]
        if not isinstance(swing_time, datetime):
            swing_time = pd.to_datetime(swing_time)
        
        # Determine pattern
        pattern = ""
        if state.current_swing_high:
            pattern = classify_swing_pattern(swing_price, 
                                            state.current_swing_high.price, 'HIGH')
        
        # Determine if strong or weak (based on current trend)
        is_strong = (state.swing_trend == Bias.BEARISH)
        
        new_swing = SwingPoint(
            price=swing_price,
            bar_index=check_idx,
            bar_time=swing_time,
            swing_type='HIGH',
            pattern=pattern,
            is_strong=is_strong
        )
        
        state.swing_highs.append(new_swing)
        state.current_swing_high = new_swing
        new_swing_high = True
        
        # Update range high
        if swing_price > state.range_high or state.range_high == 0:
            state.range_high = swing_price
    
    # Check for swing low (with confirmation delay)
    if check_idx > 0 and detect_swing_low(df, check_idx, config.swing_length):
        swing_price = df['Low'].iloc[check_idx]
        swing_time = df['Time'].iloc[check_idx]
        if not isinstance(swing_time, datetime):
            swing_time = pd.to_datetime(swing_time)
        
        # Determine pattern
        pattern = ""
        if state.current_swing_low:
            pattern = classify_swing_pattern(swing_price,
                                            state.current_swing_low.price, 'LOW')
        
        # Determine if strong or weak
        is_strong = (state.swing_trend == Bias.BULLISH)
        
        new_swing = SwingPoint(
            price=swing_price,
            bar_index=check_idx,
            bar_time=swing_time,
            swing_type='LOW',
            pattern=pattern,
            is_strong=is_strong
        )
        
        state.swing_lows.append(new_swing)
        state.current_swing_low = new_swing
        new_swing_low = True
        
        # Update range low
        if swing_price < state.range_low or state.range_low == 0:
            state.range_low = swing_price
    
    # Check for BOS/CHoCH
    close = row['Close']
    
    # Check bullish break (close above swing high)
    if state.current_swing_high and not state.current_swing_high.crossed:
        if close > state.current_swing_high.price:
            state.current_swing_high.crossed = True
            
            if state.swing_trend == Bias.BEARISH:
                # CHoCH - trend reversal
                signal_type = SignalType.CHOCH
                signal_bias = Bias.BULLISH
                state.swing_trend = Bias.BULLISH
            elif state.swing_trend == Bias.NEUTRAL:
                # First break - set initial trend
                signal_type = SignalType.BOS
                signal_bias = Bias.BULLISH
                state.swing_trend = Bias.BULLISH
            else:
                # BOS - trend continuation
                signal_type = SignalType.BOS
                signal_bias = Bias.BULLISH
            
            state.last_bos_choch = signal_type
            state.last_signal_bias = signal_bias
    
    # Check bearish break (close below swing low)
    if state.current_swing_low and not state.current_swing_low.crossed:
        if close < state.current_swing_low.price:
            state.current_swing_low.crossed = True
            
            if state.swing_trend == Bias.BULLISH:
                # CHoCH - trend reversal
                signal_type = SignalType.CHOCH
                signal_bias = Bias.BEARISH
                state.swing_trend = Bias.BEARISH
            elif state.swing_trend == Bias.NEUTRAL:
                # First break - set initial trend
                signal_type = SignalType.BOS
                signal_bias = Bias.BEARISH
                state.swing_trend = Bias.BEARISH
            else:
                # BOS - trend continuation
                signal_type = SignalType.BOS
                signal_bias = Bias.BEARISH
            
            state.last_bos_choch = signal_type
            state.last_signal_bias = signal_bias
    
    # Update equilibrium
    if state.range_high > 0 and state.range_low > 0:
        state.equilibrium = (state.range_high + state.range_low) / 2
    
    return new_swing_high, new_swing_low, signal_type, signal_bias


# ==================== ORDER BLOCK DETECTION ====================
def detect_order_block(df: pd.DataFrame, state: SMCState, index: int,
                      signal_type: SignalType, signal_bias: Bias,
                      config: Config, atr: float, volatility: float) -> Optional[OrderBlock]:
    """
    Detect Order Block after BOS/CHoCH signal
    OB is the last candle before the impulsive move
    """
    if signal_type == SignalType.NONE:
        return None
    
    # Look back to find the OB candle
    lookback = min(config.swing_length, index)
    
    if signal_bias == Bias.BULLISH:
        # Find the lowest low bar in the lookback range (bullish OB)
        min_low = float('inf')
        ob_idx = -1
        
        for i in range(index - lookback, index):
            if i < 0:
                continue
            
            # Apply volatility filter
            bar_range = df['High'].iloc[i] - df['Low'].iloc[i]
            is_high_volatility = bar_range >= 2 * volatility
            
            if is_high_volatility:
                # Invert for high volatility bars
                parsed_low = df['High'].iloc[i]
            else:
                parsed_low = df['Low'].iloc[i]
            
            if parsed_low < min_low:
                min_low = parsed_low
                ob_idx = i
        
        if ob_idx >= 0:
            bar_time = df['Time'].iloc[ob_idx]
            if not isinstance(bar_time, datetime):
                bar_time = pd.to_datetime(bar_time)
            
            return OrderBlock(
                high=df['High'].iloc[ob_idx],
                low=df['Low'].iloc[ob_idx],
                bar_index=ob_idx,
                bar_time=bar_time,
                bias=Bias.BULLISH,
                signal_type=signal_type.value  # Store BOS or CHoCH
            )
    
    else:  # BEARISH
        # Find the highest high bar in the lookback range (bearish OB)
        max_high = float('-inf')
        ob_idx = -1
        
        for i in range(index - lookback, index):
            if i < 0:
                continue
            
            # Apply volatility filter
            bar_range = df['High'].iloc[i] - df['Low'].iloc[i]
            is_high_volatility = bar_range >= 2 * volatility
            
            if is_high_volatility:
                # Invert for high volatility bars
                parsed_high = df['Low'].iloc[i]
            else:
                parsed_high = df['High'].iloc[i]
            
            if parsed_high > max_high:
                max_high = parsed_high
                ob_idx = i
        
        if ob_idx >= 0:
            bar_time = df['Time'].iloc[ob_idx]
            if not isinstance(bar_time, datetime):
                bar_time = pd.to_datetime(bar_time)
            
            return OrderBlock(
                high=df['High'].iloc[ob_idx],
                low=df['Low'].iloc[ob_idx],
                bar_index=ob_idx,
                bar_time=bar_time,
                bias=Bias.BEARISH,
                signal_type=signal_type.value  # Store BOS or CHoCH
            )
    
    return None


def check_ob_mitigation(state: SMCState, row: pd.Series, config: Config) -> None:
    """Check and update Order Block mitigation status"""
    raw_time = row['Time']
    if isinstance(raw_time, datetime):
        bar_time: datetime = raw_time
    else:
        bar_time = datetime.fromisoformat(str(raw_time))
    
    if config.ob_mitigation == "Close":
        mitigation_high = row['Close']
        mitigation_low = row['Close']
    else:  # HighLow
        mitigation_high = row['High']
        mitigation_low = row['Low']
    
    # Check bullish OBs
    for ob in state.bullish_obs:
        if not ob.mitigated and mitigation_low < ob.low:
            ob.mitigated = True
            ob.mitigation_time = bar_time
    
    # Check bearish OBs
    for ob in state.bearish_obs:
        if not ob.mitigated and mitigation_high > ob.high:
            ob.mitigated = True
            ob.mitigation_time = bar_time


# ==================== FAIR VALUE GAP DETECTION ====================
def detect_fvg(df: pd.DataFrame, state: SMCState, index: int,
              config: Config, avg_delta: float) -> Optional[FairValueGap]:
    """
    Detect Fair Value Gap (3-candle pattern)
    Bullish FVG: current_low > high[2] with bullish impulse
    Bearish FVG: current_high < low[2] with bearish impulse
    """
    if index < 2:
        return None
    
    current_low = df['Low'].iloc[index]
    current_high = df['High'].iloc[index]
    prev_close = df['Close'].iloc[index - 1]
    prev_open = df['Open'].iloc[index - 1]
    two_bars_high = df['High'].iloc[index - 2]
    two_bars_low = df['Low'].iloc[index - 2]
    
    bar_time = df['Time'].iloc[index]
    if not isinstance(bar_time, datetime):
        bar_time = pd.to_datetime(bar_time)
    
    # Calculate bar delta percentage
    if prev_open != 0:
        bar_delta_pct = (prev_close - prev_open) / prev_open * 100
    else:
        bar_delta_pct = 0
    
    # Calculate threshold
    if config.fvg_auto_threshold:
        threshold = abs(avg_delta) * config.fvg_threshold_multiplier
    else:
        threshold = 0.1  # Default 0.1%
    
    # Bullish FVG
    if current_low > two_bars_high and prev_close > two_bars_high:
        if bar_delta_pct > threshold:
            return FairValueGap(
                top=current_low,
                bottom=two_bars_high,
                bar_index=index,
                bar_time=bar_time,
                bias=Bias.BULLISH
            )
    
    # Bearish FVG
    if current_high < two_bars_low and prev_close < two_bars_low:
        if -bar_delta_pct > threshold:
            return FairValueGap(
                top=two_bars_low,
                bottom=current_high,
                bar_index=index,
                bar_time=bar_time,
                bias=Bias.BEARISH
            )
    
    return None


def check_fvg_fill(state: SMCState, row: pd.Series) -> None:
    """Check and update FVG fill status"""
    raw_time = row['Time']
    if isinstance(raw_time, datetime):
        bar_time: datetime = raw_time
    else:
        bar_time = datetime.fromisoformat(str(raw_time))
    
    # Check bullish FVGs
    for fvg in state.bullish_fvgs:
        if not fvg.filled and row['Low'] < fvg.bottom:
            fvg.filled = True
            fvg.fill_time = bar_time
    
    # Check bearish FVGs
    for fvg in state.bearish_fvgs:
        if not fvg.filled and row['High'] > fvg.top:
            fvg.filled = True
            fvg.fill_time = bar_time


# ==================== EQUAL HIGHS/LOWS DETECTION ====================
def detect_equal_highs_lows(state: SMCState, config: Config, atr: float) -> None:
    """Detect Equal Highs and Equal Lows patterns"""
    threshold = atr * config.eqhl_threshold
    
    # Check for Equal Highs
    if len(state.swing_highs) >= 2:
        prev_high = state.swing_highs[-2]
        curr_high = state.swing_highs[-1]
        
        if abs(prev_high.price - curr_high.price) < threshold:
            avg_price = (prev_high.price + curr_high.price) / 2
            eq_level = EqualLevel(
                price=avg_price,
                bar_index=curr_high.bar_index,
                bar_time=curr_high.bar_time,
                level_type='EQH'
            )
            state.equal_highs.append(eq_level)
    
    # Check for Equal Lows
    if len(state.swing_lows) >= 2:
        prev_low = state.swing_lows[-2]
        curr_low = state.swing_lows[-1]
        
        if abs(prev_low.price - curr_low.price) < threshold:
            avg_price = (prev_low.price + curr_low.price) / 2
            eq_level = EqualLevel(
                price=avg_price,
                bar_index=curr_low.bar_index,
                bar_time=curr_low.bar_time,
                level_type='EQL'
            )
            state.equal_lows.append(eq_level)


def check_equal_levels_sweep(state: SMCState, row: pd.Series) -> None:
    """Check if equal levels have been swept (liquidity taken)"""
    # Check Equal Highs sweep
    for eq in state.equal_highs:
        if not eq.swept and row['High'] > eq.price:
            eq.swept = True
    
    # Check Equal Lows sweep
    for eq in state.equal_lows:
        if not eq.swept and row['Low'] < eq.price:
            eq.swept = True


# ==================== PREMIUM/DISCOUNT ZONE ====================
def get_price_zone(price: float, state: SMCState, config: Config) -> str:
    """
    Determine if price is in Premium, Discount, or Equilibrium zone
    Returns: 'PREMIUM', 'DISCOUNT', or 'EQUILIBRIUM'
    """
    if state.range_high == 0 or state.range_low == 0:
        return 'EQUILIBRIUM'
    
    if state.range_high == state.range_low:
        return 'EQUILIBRIUM'
    
    # Calculate position in range (0 to 1)
    position = (price - state.range_low) / (state.range_high - state.range_low)
    
    if position >= config.premium_threshold:
        return 'PREMIUM'
    elif position <= config.discount_threshold:
        return 'DISCOUNT'
    else:
        return 'EQUILIBRIUM'


# ==================== SIGNAL DETECTION ====================
def check_near_order_block(price: float, obs: List[OrderBlock], 
                          atr: float, bias: Bias) -> Tuple[bool, Optional[OrderBlock]]:
    """Check if price is near an unfilled Order Block"""
    for ob in obs:
        if ob.mitigated:
            continue
        
        if ob.bias != bias:
            continue
        
        # Check if price is within OB zone or close to it
        if bias == Bias.BULLISH:
            # For bullish OB, price should be at or near the OB low
            if ob.low - atr <= price <= ob.high + atr * 0.5:
                return True, ob
        else:
            # For bearish OB, price should be at or near the OB high
            if ob.low - atr * 0.5 <= price <= ob.high + atr:
                return True, ob
    
    return False, None


def check_near_fvg(price: float, fvgs: List[FairValueGap],
                  atr: float, bias: Bias) -> Tuple[bool, Optional[FairValueGap]]:
    """Check if price is near an unfilled FVG"""
    for fvg in fvgs:
        if fvg.filled:
            continue
        
        if fvg.bias != bias:
            continue
        
        # Check if price is within FVG zone
        if fvg.bottom - atr * 0.3 <= price <= fvg.top + atr * 0.3:
            return True, fvg
    
    return False, None


def check_near_strong_level(price: float, state: SMCState, 
                           atr: float, trade_type: str) -> bool:
    """Check if price is near a strong support/resistance level"""
    if trade_type == 'LONG':
        # Check strong lows
        for swing in state.swing_lows[-5:]:  # Check last 5 swing lows
            if swing.is_strong and abs(price - swing.price) < atr:
                return True
    else:
        # Check strong highs
        for swing in state.swing_highs[-5:]:  # Check last 5 swing highs
            if swing.is_strong and abs(price - swing.price) < atr:
                return True
    
    return False


def detect_smc_signals(df: pd.DataFrame, state: SMCState, index: int,
                      config: Config, atr: float) -> Tuple[bool, bool, str, Optional[OrderBlock]]:
    """
    Detect SMC-based entry signals - OPTIMIZED OB TOUCH STRATEGY
    
    Entry Logic:
    1. Must have trend confirmation (Bullish/Bearish trend via BOS/CHoCH)
    2. Price touches OB zone (wick enters the OB zone)
    3. Only trade first touch of each OB
    4. Bullish OB should be in Discount zone, Bearish OB in Premium zone
    5. If choch_only=True, only trade OBs created from CHoCH signals (50% win rate)
    
    Returns: (signal_buy, signal_sell, entry_reason, touched_ob)
    """
    row = df.iloc[index]
    high = row['High']
    low = row['Low']
    close = row['Close']
    
    signal_buy = False
    signal_sell = False
    entry_reason = ""
    touched_ob: Optional[OrderBlock] = None
    
    # Check trend conditions
    has_bullish_trend = state.swing_trend == Bias.BULLISH
    has_bearish_trend = state.swing_trend == Bias.BEARISH
    
    # Get price zone for filtering
    price_zone = get_price_zone(close, state, config)
    
    # ========== CHECK BULLISH OB TOUCH (for LONG entry) ==========
    # Only trade Bullish OB when price is in Discount or Equilibrium zone
    if has_bullish_trend and price_zone in ['DISCOUNT', 'EQUILIBRIUM']:
        for ob in state.bullish_obs:
            # Skip if already touched or mitigated
            if ob.touched or ob.mitigated:
                continue
            
            # CHoCH only filter - skip OBs from BOS signals
            if config.choch_only and ob.signal_type != "CHoCH":
                continue
            
            # Check if price enters OB zone
            # For Bullish OB: price pulls back DOWN into the OB zone
            price_enters_ob = (low <= ob.high and low >= ob.low) or \
                             (high >= ob.low and low <= ob.high)
            
            if price_enters_ob:
                signal_buy = True
                zone_info = "Discount" if price_zone == 'DISCOUNT' else "EQ"
                signal_source = "CHoCH" if ob.signal_type == "CHoCH" else "BOS"
                entry_reason = f"Bullish Trend + {signal_source} OB Touch ({zone_info})"
                touched_ob = ob
                ob.touched = True  # Mark as touched
                break  # Only take first valid OB
    
    # ========== CHECK BEARISH OB TOUCH (for SHORT entry) ==========
    # Only trade Bearish OB when price is in Premium or Equilibrium zone
    if has_bearish_trend and not signal_buy and price_zone in ['PREMIUM', 'EQUILIBRIUM']:
        for ob in state.bearish_obs:
            # Skip if already touched or mitigated
            if ob.touched or ob.mitigated:
                continue
            
            # CHoCH only filter - skip OBs from BOS signals
            if config.choch_only and ob.signal_type != "CHoCH":
                continue
            
            # Check if price enters OB zone
            # For Bearish OB: price pulls back UP into the OB zone
            price_enters_ob = (high >= ob.low and high <= ob.high) or \
                             (high >= ob.low and low <= ob.high)
            
            if price_enters_ob:
                signal_sell = True
                zone_info = "Premium" if price_zone == 'PREMIUM' else "EQ"
                signal_source = "CHoCH" if ob.signal_type == "CHoCH" else "BOS"
                entry_reason = f"Bearish Trend + {signal_source} OB Touch ({zone_info})"
                touched_ob = ob
                ob.touched = True  # Mark as touched
                break  # Only take first valid OB
    
    return signal_buy, signal_sell, entry_reason, touched_ob


# ==================== BACKTESTER ====================
class Backtester:
    def __init__(self, config: Config):
        self.config = config
        self.trades: List[Trade] = []
        self.trade_counter = 0
    
    def calculate_sl_tp_from_ob(self, entry_price: float, ob: OrderBlock, 
                                trade_type: str) -> Tuple[float, float]:
        """
        Calculate SL and TP based on Order Block
        - SL: Below OB low (Long) or Above OB high (Short)
        - TP: RR 1:2 from entry
        """
        sl_buffer = 0.001  # 0.1% buffer for SL
        
        if trade_type == 'LONG':
            # SL below OB low
            stop_loss = ob.low * (1 - sl_buffer)
            sl_distance = entry_price - stop_loss
            # TP = 2x SL distance
            take_profit = entry_price + (sl_distance * self.config.risk_reward_ratio)
        else:  # SHORT
            # SL above OB high
            stop_loss = ob.high * (1 + sl_buffer)
            sl_distance = stop_loss - entry_price
            # TP = 2x SL distance
            take_profit = entry_price - (sl_distance * self.config.risk_reward_ratio)
        
        return stop_loss, take_profit
        
    def calculate_sl_tp(self, entry_price: float, atr: float, 
                       trade_type: str, ob: Optional[OrderBlock] = None) -> Tuple[float, float]:
        """Calculate Stop Loss and Take Profit"""
        # Default ATR-based SL
        sl_distance = atr * self.config.atr_multiplier_sl
        
        # If we have an OB, use its level for SL
        if ob is not None:
            if trade_type == 'LONG':
                ob_sl_distance = entry_price - ob.low
                sl_distance = max(sl_distance, ob_sl_distance * 1.1)  # Add 10% buffer
            else:
                ob_sl_distance = ob.high - entry_price
                sl_distance = max(sl_distance, ob_sl_distance * 1.1)
        
        tp_distance = sl_distance * self.config.risk_reward_ratio
        
        if trade_type == 'LONG':
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SHORT
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
            
        return stop_loss, take_profit
    
    def check_exit(self, trade: Trade, row: pd.Series) -> Tuple[bool, Optional[str], Optional[float]]:
        """Check if trade should be closed based on SL/TP"""
        high = row['High']
        low = row['Low']
        
        if trade.trade_type == 'LONG':
            # Check SL first (worst case)
            if low <= trade.stop_loss:
                return True, 'LOSS', trade.stop_loss
            # Check TP
            if high >= trade.take_profit:
                return True, 'WIN', trade.take_profit
        else:  # SHORT
            # Check SL first (worst case)
            if high >= trade.stop_loss:
                return True, 'LOSS', trade.stop_loss
            # Check TP
            if low <= trade.take_profit:
                return True, 'WIN', trade.take_profit
                
        return False, None, None
    
    def run(self, df: pd.DataFrame, symbol: str) -> List[Trade]:
        """Run backtest on given data"""
        # Calculate indicators
        df = df.copy()
        df['ATR'] = calculate_atr(df, self.config.atr_period)
        
        # Calculate volatility measure
        if self.config.ob_filter == "ATR":
            df['Volatility'] = df['ATR']
        else:
            df['Volatility'] = calculate_cumulative_mean_range(df)
        
        # Calculate bar delta for FVG threshold
        df['Delta_Pct'] = (df['Close'] - df['Open']) / df['Open'].replace(0, np.nan) * 100
        df['Avg_Delta'] = df['Delta_Pct'].abs().expanding().mean()
        
        # Initialize SMC state
        state = SMCState()
        
        # Skip first N rows to allow indicators to stabilize
        start_idx = self.config.swing_length * 2
        
        open_trade: Optional[Trade] = None
        trades = []
        
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
            atr = row['ATR']
            volatility = row['Volatility']
            avg_delta = row['Avg_Delta'] if not pd.isna(row['Avg_Delta']) else 0.1
            
            # Update SMC structures
            # 1. Update swing points and detect BOS/CHoCH
            new_sh, new_sl, signal_type, signal_bias = update_swing_points(
                df, state, i, self.config
            )
            
            # 2. Detect Order Block on BOS/CHoCH
            if signal_type != SignalType.NONE:
                new_ob = detect_order_block(df, state, i, signal_type, 
                                           signal_bias, self.config, atr, volatility)
                if new_ob:
                    if new_ob.bias == Bias.BULLISH:
                        state.bullish_obs.append(new_ob)
                        # Limit stored OBs
                        if len(state.bullish_obs) > self.config.max_order_blocks:
                            state.bullish_obs = state.bullish_obs[-self.config.max_order_blocks:]
                    else:
                        state.bearish_obs.append(new_ob)
                        if len(state.bearish_obs) > self.config.max_order_blocks:
                            state.bearish_obs = state.bearish_obs[-self.config.max_order_blocks:]
            
            # 3. Detect FVG
            new_fvg = detect_fvg(df, state, i, self.config, avg_delta)
            if new_fvg:
                if new_fvg.bias == Bias.BULLISH:
                    state.bullish_fvgs.append(new_fvg)
                else:
                    state.bearish_fvgs.append(new_fvg)
            
            # 4. Detect Equal Highs/Lows
            if new_sh or new_sl:
                detect_equal_highs_lows(state, self.config, atr)
            
            # 5. Update mitigations and fills
            check_ob_mitigation(state, row, self.config)
            check_fvg_fill(state, row)
            check_equal_levels_sweep(state, row)
            
            # Check if we have an open trade
            if open_trade is not None:
                should_close, result, exit_price = self.check_exit(open_trade, row)
                
                if should_close:
                    open_trade.exit_time = time
                    open_trade.exit_price = exit_price
                    open_trade.result = result
                    open_trade.status = 'CLOSED'
                    
                    # PnL based on risk amount
                    if result == 'WIN':
                        open_trade.pnl = self.config.risk_per_trade * self.config.risk_reward_ratio
                        open_trade.risk_reward = self.config.risk_reward_ratio
                    else:
                        open_trade.pnl = -self.config.risk_per_trade
                        open_trade.risk_reward = -1.0
                    
                    trades.append(open_trade)
                    open_trade = None
            
            # Look for new signals only if no open trade
            if open_trade is None:
                signal_buy, signal_sell, entry_reason, touched_ob = detect_smc_signals(
                    df, state, i, self.config, atr
                )
                
                if signal_buy and touched_ob:
                    self.trade_counter += 1
                    entry_price = row['Close']
                    
                    # SL = below OB low, TP = RR 1:2
                    sl, tp = self.calculate_sl_tp_from_ob(entry_price, touched_ob, 'LONG')
                    
                    open_trade = Trade(
                        symbol=symbol,
                        trade_id=self.trade_counter,
                        entry_time=time,
                        entry_price=entry_price,
                        trade_type='LONG',
                        stop_loss=sl,
                        take_profit=tp,
                        entry_reason=entry_reason
                    )
                    
                elif signal_sell and touched_ob:
                    self.trade_counter += 1
                    entry_price = row['Close']
                    
                    # SL = above OB high, TP = RR 1:2
                    sl, tp = self.calculate_sl_tp_from_ob(entry_price, touched_ob, 'SHORT')
                    
                    open_trade = Trade(
                        symbol=symbol,
                        trade_id=self.trade_counter,
                        entry_time=time,
                        entry_price=entry_price,
                        trade_type='SHORT',
                        stop_loss=sl,
                        take_profit=tp,
                        entry_reason=entry_reason
                    )
        
        # Close any remaining open trade at last price
        if open_trade is not None:
            last_row = df.iloc[-1]
            raw_time = last_row['Time']
            if isinstance(raw_time, datetime):
                open_trade.exit_time = raw_time
            else:
                open_trade.exit_time = datetime.fromisoformat(str(raw_time))
            open_trade.exit_price = float(last_row['Close'])
            open_trade.status = 'CLOSED'
            
            exit_price = open_trade.exit_price
            entry_price = open_trade.entry_price
            sl_distance = abs(entry_price - open_trade.stop_loss)
            
            if open_trade.trade_type == 'LONG':
                pnl = exit_price - entry_price
            else:
                pnl = entry_price - exit_price
                
            if pnl > 0:
                open_trade.result = 'WIN'
                open_trade.pnl = self.config.risk_per_trade * (pnl / sl_distance) if sl_distance > 0 else 0
            else:
                open_trade.result = 'LOSS'
                open_trade.pnl = -self.config.risk_per_trade * abs(pnl / sl_distance) if sl_distance > 0 else 0
            
            open_trade.risk_reward = open_trade.pnl / self.config.risk_per_trade if self.config.risk_per_trade > 0 else 0
            trades.append(open_trade)
        
        self.trades.extend(trades)
        return trades
