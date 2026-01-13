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
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Tuple, Optional
from dataclasses import dataclass, field


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
    
    # === FVG ===
    fvg_atr_filter: bool = True     # Filter small FVGs using ATR
    
    # === RISK MANAGEMENT ===
    sl_buffer_pct: float = 0.1      # Buffer SL (% of OB range)
    risk_reward_ratio: float = 2.0  # Fixed RR = 1:2
    risk_per_trade: float = 10.0    # USD
    
    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "swing_length": self.swing_length,
            "internal_length": self.internal_length,
            "eqhl_length": self.eqhl_length,
            "eqhl_threshold": self.eqhl_threshold,
            "sl_buffer_pct": self.sl_buffer_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
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


# ==================== INDICATOR FUNCTIONS ====================

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR)"""
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    close = df['Close'].astype(float)
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    
    tr_df = pd.DataFrame({'tr1': tr1, 'tr2': tr2, 'tr3': tr3})
    tr = tr_df.max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    # Fill NaN values with tr values
    atr_result = pd.Series(np.where(pd.isna(atr), tr, atr), index=df.index)
    return atr_result


def detect_swing_points(df: pd.DataFrame, length: int) -> List[SwingPoint]:
    """
    Detect swing high/low points using fractal logic.
    
    A swing high is formed when High[length] is higher than all highs 
    in the surrounding window (length bars on each side).
    
    A swing low is formed when Low[length] is lower than all lows 
    in the surrounding window.
    """
    swing_points = []
    
    highs = df['High'].values
    lows = df['Low'].values
    times = df['Time'].values
    
    # Need at least length*2 + 1 bars
    if len(df) < length * 2 + 1:
        return swing_points
    
    # Track previous swing for labeling (HH, HL, LH, LL)
    last_swing_high = None
    last_swing_low = None
    
    for i in range(length, len(df) - length):
        # Check for swing high
        is_swing_high = True
        for j in range(i - length, i + length + 1):
            if j != i and highs[j] >= highs[i]:
                is_swing_high = False
                break
        
        if is_swing_high:
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
        
        # Check for swing low
        is_swing_low = True
        for j in range(i - length, i + length + 1):
            if j != i and lows[j] <= lows[i]:
                is_swing_low = False
                break
        
        if is_swing_low:
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
        
        Flow:
        1. Calculate ATR
        2. Detect swing points (for structure)
        3. Detect internal swing points (for EQH/EQL - faster detection)
        4. Detect structures (BOS/CHoCH)
        5. Detect order blocks (focus on CHoCH OBs)
        6. Detect FVGs
        7. Detect EQH/EQL from internal swings
        8. For each bar, check entry conditions
        """
        df = df.copy()
        signals = []
        
        # 1. Calculate ATR
        atr = calculate_atr(df, period=14)
        
        # 2. Detect swing points for structure
        swing_points = detect_swing_points(df, self.config.swing_length)
        
        if len(swing_points) < 4:
            return signals
        
        # 3. Detect internal swing points for EQH/EQL (faster detection)
        internal_swings = detect_swing_points(df, self.config.eqhl_length)
        
        # 4. Detect structures (BOS/CHoCH)
        structures = detect_structure(df, swing_points)
        
        # 5. Detect order blocks
        order_blocks = detect_order_blocks(df, structures)
        
        # 6. Detect FVGs
        fvgs = detect_fvg(df, atr, self.config.fvg_atr_filter)
        
        # 7. Detect EQH/EQL from internal swings (more sensitive)
        equal_hls = detect_equal_hl(df, internal_swings, self.config.eqhl_threshold, atr)
        
        # Filter only CHoCH order blocks
        choch_obs = [ob for ob in order_blocks if ob.is_choch_ob]
        
        # 8. Process each bar for entry conditions
        times = df['Time'].values
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        
        # Track active OBs (not yet mitigated and entry not triggered)
        active_obs = []
        
        for i in range(len(df)):
            current_time = pd.to_datetime(times[i])
            
            # Add new CHoCH OBs that start at or before this bar
            for ob in choch_obs:
                if ob.structure is not None and ob.structure.break_index == i and ob not in active_obs:
                    # Check if FVG appeared at or after OB creation
                    ob.has_fvg = self._check_fvg_for_ob(ob, fvgs)
                    active_obs.append(ob)
            
            # Update OB liquidity status (check for EQH/EQL after OB creation)
            for ob in active_obs:
                if not ob.has_liquidity:
                    ob.has_liquidity = self._check_liquidity_for_ob(ob, equal_hls, i)
            
            # Check for mitigation and entry
            obs_to_remove = []
            
            for ob in active_obs:
                # Skip if entry already triggered
                if ob.entry_triggered:
                    continue
                
                # Check if price touches OB zone (entry condition)
                if self._price_touches_ob(ob, highs[i], lows[i]):
                    # Check all conditions for entry
                    if ob.has_fvg and ob.has_liquidity:
                        # Generate signal
                        signal = self._create_signal(
                            symbol=symbol,
                            ob=ob,
                            entry_time=current_time,
                            entry_price=closes[i],
                            current_high=highs[i],
                            current_low=lows[i]
                        )
                        
                        if signal:
                            signals.append(signal)
                            ob.entry_triggered = True
                
                # Check if OB is fully mitigated (price went through completely)
                if self._ob_is_mitigated(ob, highs[i], lows[i], closes[i]):
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
            # SL below OB with buffer
            stop_loss = ob.low - buffer
            # TP based on RR
            risk = entry - stop_loss
            take_profit = entry + (risk * self.config.risk_reward_ratio)
            
        else:  # BEARISH
            # SHORT trade
            trade_type = 'SHORT'
            # Entry at OB bottom (more conservative) or current close
            entry = max(ob.low, entry_price)
            # SL above OB with buffer
            stop_loss = ob.high + buffer
            # TP based on RR
            risk = stop_loss - entry
            take_profit = entry - (risk * self.config.risk_reward_ratio)
        
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
