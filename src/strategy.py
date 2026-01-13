"""
RSI Overbought/Oversold Trading Strategy
========================================
Simple RSI-based mean reversion strategy:
- RSI > 70: SHORT (overbought)
- RSI < 30: LONG (oversold)
- Fixed SL: 2% from entry price
- RR ratio: 2 (TP = 4% from entry price)

INTERFACE CONTRACT (must export for main.py):
- Config: dataclass with strategy configuration
- Signal: dataclass representing a trade signal
- Strategy: class with generate_signals(df, symbol) -> List[Signal] method
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Tuple
from dataclasses import dataclass


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "RSI Overbought/Oversold"


# ==================== CONFIGURATION ====================
@dataclass
class Config:
    """
    RSI Trading strategy configuration.
    """
    # === SYSTEM SETTINGS (set by main.py) ===
    data_folder: str = "src/data"
    output_folder: str = "src/output"
    
    # === STRATEGY SETTINGS ===
    # RSI settings
    rsi_period: int = 14
    rsi_overbought: float = 70.0  # RSI > 70 -> SHORT
    rsi_oversold: float = 30.0    # RSI < 30 -> LONG
    
    # Risk management
    stop_loss_pct: float = 2.0    # SL = 2% from entry
    risk_reward_ratio: float = 2.0  # RR = 2, so TP = 4%
    risk_per_trade: float = 10.0  # Risk amount in USD per trade
    
    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "rsi_period": self.rsi_period,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "stop_loss_pct": self.stop_loss_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
        }


# ==================== DATA CLASSES ====================
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


# ==================== INDICATORS ====================
def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI)"""
    close = df['Close']
    delta = close.diff()
    
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    
    return pd.Series(rsi).fillna(50)  # Fill NaN with neutral value


# ==================== STRATEGY ====================
class Strategy:
    """
    RSI Strategy - generates trade signals only.
    Does NOT calculate exit or results.
    """
    def __init__(self, config: Config):
        self.config = config
    
    def calculate_sl_tp(self, entry_price: float, trade_type: str) -> Tuple[float, float]:
        """
        Calculate Stop Loss and Take Profit
        - SL: 2% from entry
        - TP: 4% from entry (RR = 2)
        """
        sl_pct = self.config.stop_loss_pct / 100
        tp_pct = sl_pct * self.config.risk_reward_ratio
        
        if trade_type == 'LONG':
            stop_loss = entry_price * (1 - sl_pct)
            take_profit = entry_price * (1 + tp_pct)
        else:  # SHORT
            stop_loss = entry_price * (1 + sl_pct)
            take_profit = entry_price * (1 - tp_pct)
        
        return stop_loss, take_profit
    
    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """
        Generate trade signals based on RSI.
        Returns list of Signal objects (entry info only).
        """
        df = df.copy()
        
        # Calculate RSI
        df['RSI'] = calculate_rsi(df, self.config.rsi_period)
        
        # Skip first N rows to allow RSI to stabilize
        start_idx = self.config.rsi_period + 1
        
        signals = []
        in_position = False  # Track if we're in a position (to avoid overlapping signals)
        current_signal = None
        
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            time = row['Time'] if isinstance(row['Time'], datetime) else pd.to_datetime(row['Time'])
            rsi = row['RSI']
            
            # If in position, check if SL/TP would be hit to exit
            if in_position and current_signal is not None:
                high = row['High']
                low = row['Low']
                
                if current_signal.trade_type == 'LONG':
                    if low <= current_signal.stop_loss or high >= current_signal.take_profit:
                        in_position = False
                        current_signal = None
                else:  # SHORT
                    if high >= current_signal.stop_loss or low <= current_signal.take_profit:
                        in_position = False
                        current_signal = None
            
            # Look for new signals only if not in position
            if not in_position:
                entry_price = row['Close']
                
                # RSI < 30 -> LONG (oversold)
                if rsi < self.config.rsi_oversold:
                    sl, tp = self.calculate_sl_tp(entry_price, 'LONG')
                    
                    signal = Signal(
                        symbol=symbol,
                        trade_type='LONG',
                        entry_time=time,
                        entry_price=entry_price,
                        stop_loss=sl,
                        take_profit=tp
                    )
                    signals.append(signal)
                    in_position = True
                    current_signal = signal
                
                # RSI > 70 -> SHORT (overbought)
                elif rsi > self.config.rsi_overbought:
                    sl, tp = self.calculate_sl_tp(entry_price, 'SHORT')
                    
                    signal = Signal(
                        symbol=symbol,
                        trade_type='SHORT',
                        entry_time=time,
                        entry_price=entry_price,
                        stop_loss=sl,
                        take_profit=tp
                    )
                    signals.append(signal)
                    in_position = True
                    current_signal = signal
        
        return signals
