"""
SMC Strategy - Structure + Order Block + Liquidity
==================================================
Smart Money Concepts trading strategy based on:
- Market structure events (CHoCH and BOS)
- Order Blocks (OB), classified by the structure break that activated them
- Equal Highs/Lows (EQH/EQL) as post-break liquidity confirmation
- Optional FVG / IFVG confluence
- Optional reversal-pattern and ADX filters
- Optional CHoCH safeguard that blocks entries when the actual entry candle is
  against the trade and the selected same-bias gap confluence is IFVG

Actual entry flow implemented by this module:
---------------------------------------------
LONG setup:
1. Detect bullish OBs from structure breaks and classify them as CHoCH or BOS.
2. Activate the OB when its break_index is reached.
3. Optionally require a later EQH confirmation after the OB break
   (require_liquidity=True).
4. Wait for price to trade into the OB entry level (entry_ob_level, 0.5 = 50%).
   Entry can happen on the touch bar or within touch_confirmation_bars after
   the most recent qualifying touch.
5. Optionally require matching bullish FVG/IFVG confluence.
6. Optionally require a bullish reversal candlestick pattern (at least
   min_reversal_pattern_matches families) or a simpler rejection/displacement
   reversal.
7. Optionally require a valid opposite-side liquidity destination before entry.
8. Optionally reject the setup if it is CHoCH-based, the actual entry candle is
   bearish against the intended LONG, and the selected bullish gap confluence is
   IFVG (block_choch_against_entry_mode="ifvg").
9. Enter LONG at the CLOSE of the confirmation candle (the reversal pattern is
   only known once that candle closes, which is also when live sends a market
   order):
   - single-timeframe mode: the signal candle must trade through the entry
     level and pass the reversal filter
   - multi-timeframe mode: the first entry-timeframe candle after the signal
     bar closes that trades through the entry level and passes the reversal
     filter. The search stops as soon as an entry-TF candle mitigates the OB.
   Signal entry_time is the OPEN time of the confirmation candle; the trade
   simulator starts checking SL/TP from the next candle.
10. SL is placed below the OB with buffer / multiplier logic and minimum-SL
    enforcement inherited from BaseStrategy.
11. TP is configurable: fixed RR, opposite OB, opposite swing, or opposite HTF
     liquidity, with optional fallback to fixed RR.

SHORT setup: opposite of the above.

Important behavior notes:
- The strategy can trade both CHoCH-based and BOS-based OBs; CHoCH is not a
  mandatory prerequisite for every trade.
- When the CHoCH safeguard is enabled, it only blocks CHoCH setups whose real
  entry candle is against the trade and whose selected same-bias gap is IFVG;
  BOS setups and CHoCH setups without that combination are still allowed.
- Same-bias OBs that overlap and form within a small break-index distance can
  be merged into a single union zone, activated on the latest break.
- Liquidity confirmation is tracked causally after the OB break; indicator
  precomputed mitigation flags are ignored and mitigation is evaluated bar by
  bar in the strategy loop.
- In multi-timeframe mode, structure is detected on the signal timeframe while
  entry timing is refined on the entry timeframe.

Plug-in contract (see src/strategy/__init__.py), exported by src/strategy/smc/__init__.py:
- Config: dataclass with strategy configuration (BaseConfig subclass)
- Strategy: generate_signals(df, symbol, df_entry=None) -> List[Signal] (BaseStrategy subclass)
- STRATEGY_NAME: display name

Uses indicator modules from src/indicators for OB, CHoCH, BOS, EQH/EQL,
swings, and optional FVG/IFVG confluence detection.
"""

import warnings
from datetime import datetime, timedelta
from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Tuple, Union, cast

import numpy as np
import pandas as pd
from numba import njit

from src.strategy.base import (
    BaseConfig,
    BaseSignal,
    BaseStrategy,
    BaseZone,
    TradePlan,
    parse_timeframe_minutes,
)
from src.indicators.reversal_patterns import count_reversal_patterns_jit

from src.indicators.CHOCH import detect_choch, CHoCH
from src.indicators.BOS import detect_bos, BOS
from src.indicators.order_block import detect_order_blocks as detect_order_blocks_indicator
from src.indicators.order_block import OrderBlock as IndicatorOrderBlock
from src.indicators.eqh_eql import detect_eqh_eql, EqualHL as IndicatorEqualHL
from src.indicators.FVG import detect_fvg, FairValueGap
from src.indicators.ifvg import detect_ifvg, InverseFairValueGap
from src.indicators.swing import detect_swings, Swing as IndicatorSwing
from src.indicators.adx import compute_adx_jit
from src.core.timeframe import convert_timeframe, normalize_timeframes
from src.strategy.smc.key_levels import (
    LEVEL_SOURCES,
    KeyLevelParams,
    KeyLevels,
    build_key_levels,
    pick_stop,
    pick_target,
)


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "SMC"


def classify_candle_color(open_price: float, close_price: float) -> str:
    if not np.isfinite(open_price) or not np.isfinite(close_price):
        return ""
    if close_price > open_price:
        return "GREEN"
    if close_price < open_price:
        return "RED"
    return "DOJI"


def candle_relation_to_trade(trade_type: str, candle_color: str) -> str:
    if candle_color == "DOJI" or not candle_color:
        return "NEUTRAL"
    if trade_type == "LONG":
        return "WITH_TRADE" if candle_color == "GREEN" else "AGAINST_TRADE"
    if trade_type == "SHORT":
        return "WITH_TRADE" if candle_color == "RED" else "AGAINST_TRADE"
    return "UNKNOWN"


# ==================== CONFIGURATION ====================
@dataclass
class Config(BaseConfig):
    """
    SMC Trading strategy configuration.

    Inherits generic fields from BaseConfig:
    - data_folder, output_folder
    - signal_timeframe, entry_timeframe, slice_window
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
    max_active_obs_per_bias: int = 10  # Keep only the most recent bullish/bearish OBs
    merge_overlapping_obs: bool = True  # Merge same-bias OBs that overlap and form close together
    ob_merge_max_break_gap: int = 5     # Max break_index distance allowed when merging overlapping OBs
    entry_ob_level: float = 0.5     # Level the confirmation candle must trade through (0.0=low, 0.5=midpoint, 1.0=high)

    # === ENTRY FILTERS ===
    require_liquidity: bool = True          # Require EQH/EQL liquidity event after OB break
    require_opposite_liquidity_target: bool = True  # Require opposite-side target: OB or EQH/EQL or FVG/IFVG
    require_reversal_pattern: bool = True   # Require reversal pattern before entry
    enable_simple_reversal: bool = True     # Allow simpler rejection/displacement reversal
    simple_reversal_wick_ratio: float = 0.25  # Min wick/range ratio for simple reversal
    block_choch_against_entry_mode: str = "off"  # off | ifvg

    # === IMBALANCE FILTER ===
    gap_filter_mode: str = "none"   # none | fvg | ifvg | both
    fvg_atr_filter: bool = True     # Legacy name; toggles FVG/IFVG auto-threshold filter

    # === REVERSAL PATTERNS ===
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])
    min_reversal_pattern_matches: int = 1   # Pattern families that must match on the same candle
    doji_body_ratio: float = 0.1            # Doji: body <= ratio * range
    tweezer_tolerance_ratio: float = 0.1    # Tweezer: |low1-low2| <= ratio * avg candle range

    # === HIGHER-TIMEFRAME TREND FILTER ===
    # "" disables it. Otherwise LONG needs close > EMA and SHORT needs close < EMA
    # on the last CLOSED candle of this timeframe at the moment of entry.
    trend_filter_timeframe: str = ""
    trend_filter_ema_length: int = 50

    # === ADX STRENGTH FILTER ===
    enable_adx_filter: bool = False         # When True, require ADX strength confirmation
    adx_length: int = 14                    # ADX period on signal timeframe
    adx_threshold: float = 20.0             # Minimum ADX to allow entries

    # === FLEXIBLE TAKE PROFIT ===
    # fixed_rr: TP from risk_reward_ratio only
    # opposite_ob: TP from opposite active OB only
    # opposite_swing: TP from opposite confirmed swing only
    # opposite_ob_or_swing: combine both target sources
    # htf_liquidity: TP from opposite liquidity only (OB, EQH/EQL, FVG/IFVG)
    # htf_liquidity_or_rr: same as htf_liquidity, fallback to fixed RR if no target
    # htf_level: TP at a key level of level_timeframe (skip the trade if none)
    # htf_level_or_rr: same as htf_level, fallback to fixed RR if no level
    tp_mode: str = "opposite_ob_or_swing"
    # skip: reject trade when no structural target exists
    # fixed_rr: fallback to classic RR TP when no structural target exists
    tp_no_target_policy: str = "skip"
    # Swing length used to build opposite swing targets
    tp_swing_length: int = 5
    # Extra risk multiplier per 1R above the configured base RR.
    # effective_risk = risk_per_trade * (1 + factor * max(0, flex_rr - base_rr))
    flex_tp_risk_boost_factor: float = 0.0

    # === SL / TP AT HIGHER-TIMEFRAME KEY LEVELS ===
    # [] = off. e.g. ["15m", "10m"]: SL / TP go to the swing highs/lows and key
    # zones (EQH/EQL, OB, FVG) of these timeframes that are known and not yet
    # traded through at entry. The first timeframe in the list that has a
    # usable level wins (15m, else 10m); SL and TP are picked separately.
    level_timeframe: Union[str, List[str]] = field(default_factory=list)
    level_sources: List[str] = field(default_factory=lambda: list(LEVEL_SOURCES))  # swing | eqhl | ob | fvg
    level_swing_length: int = 5          # candles on each side of a swing high/low
    level_lookback_bars: int = 200       # only levels formed in the last N level candles (0 = all)
    # zone: SL from the entry OB (as before)
    # level: SL beyond the nearest key level, skip the trade if there is none
    # level_or_zone: key level, else the entry OB SL
    sl_mode: str = "zone"
    level_sl_buffer_pct: float = 0.05    # SL this % beyond the level
    max_sl_pct: float = 0.0              # a level SL wider than this % does not count (0 = no limit)
    level_tp_buffer_pct: float = 0.0     # TP this % before the level
    level_tp_min_rr: float = 1.0         # TP level must give at least this RR (0 = nearest level)

    def __post_init__(self) -> None:
        pattern_ids = list(self.reversal_pattern_ids or [])
        using_default_patterns = len(pattern_ids) == 1 and pattern_ids[0] == 0
        if not self.require_reversal_pattern and not using_default_patterns:
            warnings.warn(
                "require_reversal_pattern=False disables reversal filtering, so reversal_pattern_ids and simple reversal settings will not affect entries until require_reversal_pattern=True.",
                stacklevel=3,
            )

    def report_label(self) -> str:
        return str(self.reversal_pattern_ids)

    def get_strategy_info(self) -> dict:
        """Return strategy configuration for reporting"""
        return {
            "name": STRATEGY_NAME,
            "signal_timeframe": self.signal_timeframe,
            "entry_timeframe": self.entry_timeframe,
            "slice_window": self.slice_window,
            "swing_length": self.swing_length,
            "internal_length": self.internal_length,
            "eqhl_length": self.eqhl_length,
            "eqhl_threshold": self.eqhl_threshold,
            "ob_detection": "LuxAlgo extreme candle + trending candle filter",
            "ob_body_ratio": self.ob_body_ratio,
            "max_active_obs_per_bias": self.max_active_obs_per_bias,
            "merge_overlapping_obs": self.merge_overlapping_obs,
            "ob_merge_max_break_gap": self.ob_merge_max_break_gap,
            "entry_ob_level": self.entry_ob_level,
            "require_liquidity": self.require_liquidity,
            "require_opposite_liquidity_target": self.require_opposite_liquidity_target,
            "require_reversal_pattern": self.require_reversal_pattern,
            "enable_simple_reversal": self.enable_simple_reversal,
            "simple_reversal_wick_ratio": self.simple_reversal_wick_ratio,
            "block_choch_against_entry_mode": self.block_choch_against_entry_mode,
            "gap_filter_mode": self.gap_filter_mode,
            "fvg_atr_filter": self.fvg_atr_filter,
            "touch_tolerance_pct": self.touch_tolerance_pct,
            "touch_confirmation_bars": self.touch_confirmation_bars,
            "entry_on_candle_close": self.entry_on_candle_close,
            "sl_buffer_pct": self.sl_buffer_pct,
            "sl_multiplier": self.sl_multiplier,
            "min_sl_pct": self.min_sl_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_per_trade": self.risk_per_trade,
            "reversal_pattern_ids": self.reversal_pattern_ids,
            "min_reversal_pattern_matches": self.min_reversal_pattern_matches,
            "doji_body_ratio": self.doji_body_ratio,
            "tweezer_tolerance_ratio": self.tweezer_tolerance_ratio,
            "tp_mode": self.tp_mode,
            "tp_no_target_policy": self.tp_no_target_policy,
            "tp_swing_length": self.tp_swing_length,
            "flex_tp_risk_boost_factor": self.flex_tp_risk_boost_factor,
            "trend_filter_timeframe": self.trend_filter_timeframe,
            "trend_filter_ema_length": self.trend_filter_ema_length,
            "enable_adx_filter": self.enable_adx_filter,
            "adx_length": self.adx_length,
            "adx_threshold": self.adx_threshold,
            "level_timeframe": self.level_timeframe,
            "level_sources": self.level_sources,
            "level_swing_length": self.level_swing_length,
            "level_lookback_bars": self.level_lookback_bars,
            "sl_mode": self.sl_mode,
            "level_sl_buffer_pct": self.level_sl_buffer_pct,
            "max_sl_pct": self.max_sl_pct,
            "level_tp_buffer_pct": self.level_tp_buffer_pct,
            "level_tp_min_rr": self.level_tp_min_rr,
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
class StrategyGapZone(BaseZone):
    """Represents an active FVG/IFVG zone used as entry confluence."""
    index: int = 0
    gap_type: str = 'FVG'            # 'FVG' or 'IFVG'
    source_index: int = -1


@dataclass
class Signal(BaseSignal):
    """
    Represents a trade signal from SMC strategy.
    Only contains entry information - no exit data.
    """
    pass


@dataclass
class ReversalDecision:
    passed: bool
    source: str


@dataclass
class PreciseEntryResult:
    entry_time: datetime
    entry_price: float
    entry_index: int
    reversal_source: str


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
        self._tp_context: dict[str, Any] | None = None
        self._key_levels: List[KeyLevels] = []

    def generate_signals(self, df: pd.DataFrame, symbol: str, df_entry: pd.DataFrame | None = None) -> List[Signal]:
        return self._generate_signals_core(df=df, symbol=symbol, df_entry=df_entry)

    def _get_slice_window(self) -> int:
        try:
            return max(0, int(self.config.slice_window))
        except (TypeError, ValueError):
            return 0

    def _generate_signals_core(
        self,
        *,
        df: pd.DataFrame,
        symbol: str,
        df_entry: pd.DataFrame | None = None,
    ) -> List[Signal]:
        """
        Generate trade signals based on SMC concepts.

        MULTI-TIMEFRAME MODE (when df_entry is provided):
        - Uses df (signal timeframe, e.g., 5m) for indicator calculation
        - Uses df_entry (entry timeframe, e.g., 1m) for precise entry detection
        - This prevents repainting by finding the FIRST entry-TF candle that touches OB
          and passes the reversal filter on the entry timeframe candle itself

        SINGLE-TIMEFRAME MODE (when df_entry is None):
        - Uses df for both indicator calculation and entry
        - This is the legacy behavior

        SLICE-WINDOW MODE (when slice_window > 0):
        - Indicators/events are precomputed once on the full signal timeframe
        - The causal runtime loop only keeps active state from the last N signal bars
        - This avoids replaying the whole strategy for every rolling window

        EVENT-TIME GATED: Pre-computes indicators, but consumes them causally.
        Each event (OB, EQH/EQL) is only activated at the bar it becomes known.
        Mitigation is computed bar-by-bar — never uses indicator's pre-computed is_mitigated.

        NO LOOK-AHEAD BIAS:
        - OB activated at break_index (structure break bar)
        - FVG/IFVG activated at their detection bar
        - EQH/EQL activated at confirmed_index (confirmation bar, not pivot bar)
        - OB mitigation checked at each bar using current price only
        - FVG/IFVG validity checked at each bar using current price only
        - has_liquidity updated incrementally as events arrive
        - CHoCH is not required during retrace
        """
        df = df.copy()
        signals: List[BaseSignal] = []
        self._tp_context = None

        # Determine if we're in multi-timeframe mode
        use_multi_tf = df_entry is not None and not df_entry.empty
        signal_tf_minutes = parse_timeframe_minutes(self.config.signal_timeframe) if use_multi_tf else 0

        highs = df['High'].values.astype(np.float64)
        lows = df['Low'].values.astype(np.float64)
        closes = df['Close'].values.astype(np.float64)
        opens = df['Open'].values.astype(np.float64)
        times = df['Time'].to_numpy(dtype='datetime64[ns]')
        n = len(df)

        if n == 0:
            return []

        slice_window = self._get_slice_window()

        entry_times = np.empty(0, dtype='datetime64[ns]')
        entry_opens = np.empty(0, dtype=np.float64)
        entry_highs = np.empty(0, dtype=np.float64)
        entry_lows = np.empty(0, dtype=np.float64)
        entry_closes = np.empty(0, dtype=np.float64)
        if use_multi_tf and df_entry is not None:
            entry_df = df_entry.copy()
            entry_df['Time'] = pd.to_datetime(entry_df['Time'])
            entry_df = entry_df.sort_values('Time').reset_index(drop=True)
            entry_times = entry_df['Time'].to_numpy(dtype='datetime64[ns]')
            entry_opens = entry_df['Open'].to_numpy(dtype=np.float64)
            entry_highs = entry_df['High'].to_numpy(dtype=np.float64)
            entry_lows = entry_df['Low'].to_numpy(dtype=np.float64)
            entry_closes = entry_df['Close'].to_numpy(dtype=np.float64)

        trend_filter = self._build_trend_filter(entry_df if use_multi_tf and df_entry is not None else df)
        self._key_levels = self._build_key_levels(entry_df if use_multi_tf and df_entry is not None else df)
        entry_tf_minutes = max(1, parse_timeframe_minutes(self.config.entry_timeframe))
        single_tf_minutes = max(1, parse_timeframe_minutes(self.config.signal_timeframe))

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

        choch_lookup: dict[tuple[int, str], CHoCH] = {}
        for choch in choch_list:
            choch_lookup.setdefault((int(choch.break_index), choch.bias), choch)

        bos_lookup: dict[tuple[int, str], BOS] = {}
        for bos in bos_list:
            bos_lookup.setdefault((int(bos.break_index), bos.bias), bos)

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

        gap_filter_mode = self._normalize_gap_filter_mode()
        fvg_list: List[FairValueGap] = []
        ifvg_list: List[InverseFairValueGap] = []
        if gap_filter_mode in {"fvg", "both"}:
            fvg_list = detect_fvg(df, auto_threshold=self.config.fvg_atr_filter)
        if gap_filter_mode in {"ifvg", "both"}:
            ifvg_list = detect_ifvg(df, auto_threshold=self.config.fvg_atr_filter)

        uses_swing_targets = self._normalize_tp_mode() in {"opposite_swing", "opposite_ob_or_swing"}
        swings_by_confirm: dict[int, List[IndicatorSwing]] = {}
        if uses_swing_targets:
            tp_swing_length = max(1, int(self.config.tp_swing_length))
            swing_events = detect_swings(df, length=tp_swing_length)
            for swing in swing_events:
                confirm_index = int(swing.index + tp_swing_length)
                if 0 <= confirm_index < n:
                    swings_by_confirm.setdefault(confirm_index, []).append(swing)

        # ADX strength filter: pre-compute ADX on signal timeframe
        adx_values: np.ndarray | None = None
        if self.config.enable_adx_filter:
            adx_values, _, _ = compute_adx_jit(
                highs,
                lows,
                closes,
                max(1, int(self.config.adx_length)),
            )

        # ========== 2. BUILD EVENT QUEUES (indexed by activation bar) ==========

        # OBs: filter by body_ratio, optionally merge overlapping zones,
        # then group by break_index (activation bar)
        # IGNORE indicator's is_mitigated - we compute mitigation ourselves bar-by-bar
        strategy_obs: List[StrategyOrderBlock] = []
        for ob in ob_list:
            if not _check_trending_candle_jit(opens, closes, highs, lows,
                                              ob.index, self.config.ob_body_ratio):
                continue

            structure_type = self._classify_ob_structure(ob, choch_lookup, bos_lookup)

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

            strategy_obs.append(sob)

        strategy_obs = self._merge_overlapping_order_blocks(strategy_obs)

        obs_by_break: dict[int, List[StrategyOrderBlock]] = {}
        for ob in strategy_obs:
            obs_by_break.setdefault(ob.break_index, []).append(ob)

        # EQH/EQL: group by confirmed_index (confirmation bar, NOT pivot bar)
        eqhl_by_confirm: dict[int, List[IndicatorEqualHL]] = {}
        for eqhl in eqhl_list:
            ci = eqhl.confirmed_index
            eqhl_by_confirm.setdefault(ci, []).append(eqhl)

        gap_zones_by_index: dict[int, List[StrategyGapZone]] = {}
        for fvg in fvg_list:
            gap_zone = self._create_gap_zone(
                top=fvg.top,
                bottom=fvg.bottom,
                bias=fvg.bias,
                time=fvg.time,
                index=int(fvg.index),
                gap_type='FVG',
            )
            gap_zones_by_index.setdefault(gap_zone.index, []).append(gap_zone)

        for ifvg in ifvg_list:
            gap_zone = self._create_gap_zone(
                top=ifvg.top,
                bottom=ifvg.bottom,
                bias=ifvg.bias,
                time=ifvg.time,
                index=int(ifvg.index),
                gap_type='IFVG',
                source_index=int(ifvg.source_index),
            )
            gap_zones_by_index.setdefault(gap_zone.index, []).append(gap_zone)

        # ========== 3. CAUSAL BAR-BY-BAR LOOP ==========
        active_obs: List[StrategyOrderBlock] = []
        active_gap_zones: List[StrategyGapZone] = []
        confirmed_swing_highs: List[Tuple[int, float]] = []
        confirmed_swing_lows: List[Tuple[int, float]] = []
        confirmed_eqh_levels: List[Tuple[int, float]] = []
        confirmed_eql_levels: List[Tuple[int, float]] = []

        for i in range(n):
            current_time = self._parse_datetime(times[i])
            if current_time is None:
                continue

            slice_start_index = max(0, i - slice_window + 1) if slice_window > 0 else 0

            # 3a. Activate OBs whose structure break happens at this bar
            for ob in obs_by_break.get(i, []):
                active_obs.append(ob)

            active_obs = self._prune_active_obs(
                active_obs,
                min_break_index=slice_start_index if slice_window > 0 else None,
            )

            # 3b. Activate FVG/IFVG zones that become known at this bar
            for gap_zone in gap_zones_by_index.get(i, []):
                active_gap_zones.append(gap_zone)

            if slice_window > 0:
                active_gap_zones = [
                    gap_zone for gap_zone in active_gap_zones
                    if gap_zone.index >= slice_start_index
                ]

            # 3c. Process EQH/EQL events confirmed at this bar → update has_liquidity
            for eqhl in eqhl_by_confirm.get(i, []):
                if eqhl.type == 'EQH':
                    confirmed_eqh_levels.append((i, float(eqhl.level)))
                elif eqhl.type == 'EQL':
                    confirmed_eql_levels.append((i, float(eqhl.level)))

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

            # 3c.1 Activate confirmed swings for TP targets (causal, no look-ahead)
            for swing in swings_by_confirm.get(i, []):
                if swing.is_high:
                    confirmed_swing_highs.append((i, float(swing.price)))
                else:
                    confirmed_swing_lows.append((i, float(swing.price)))

            if slice_window > 0:
                confirmed_swing_highs = [
                    item for item in confirmed_swing_highs if item[0] >= slice_start_index
                ]
                confirmed_swing_lows = [
                    item for item in confirmed_swing_lows if item[0] >= slice_start_index
                ]
                confirmed_eqh_levels = [
                    item for item in confirmed_eqh_levels if item[0] >= slice_start_index
                ]
                confirmed_eql_levels = [
                    item for item in confirmed_eql_levels if item[0] >= slice_start_index
                ]

            remaining_gap_zones = []
            for gap_zone in active_gap_zones:
                if self._zone_is_mitigated(gap_zone, highs[i], lows[i], closes[i]):
                    continue
                remaining_gap_zones.append(gap_zone)
            active_gap_zones = remaining_gap_zones

            tp_context: dict[str, Any] = {
                "current_index": i,
                "current_high": float(highs[i]),
                "current_low": float(lows[i]),
                "current_close": float(closes[i]),
                "active_obs": active_obs,
                "active_gap_zones": active_gap_zones,
                "confirmed_swing_high_levels": [price for _, price in confirmed_swing_highs],
                "confirmed_swing_low_levels": [price for _, price in confirmed_swing_lows],
                "confirmed_eqh_levels": confirmed_eqh_levels,
                "confirmed_eql_levels": confirmed_eql_levels,
            }
            self._tp_context = tp_context

            # 3d. Check mitigation & entry for each active OB
            remaining_obs = []
            for ob in active_obs:
                # Use base class zone mitigation check
                if self._zone_is_mitigated(ob, highs[i], lows[i], closes[i]):
                    ob.is_mitigated = True
                    continue  # Skip OB entirely - do not process entry

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
                    current_adx: float | None = None
                    adx_threshold: float | None = None

                    # ADX filter: require minimum trend strength on signal timeframe
                    if self.config.enable_adx_filter:
                        if adx_values is None:
                            remaining_obs.append(ob)
                            continue
                        current_adx = float(adx_values[i])
                        adx_threshold = max(0.0, float(self.config.adx_threshold))
                        if (not np.isfinite(current_adx)) or current_adx < adx_threshold:
                            remaining_obs.append(ob)
                            continue

                    liquidity_ok = (
                        (not self.config.require_liquidity)
                        or (
                            ob.has_liquidity
                            and ob.liquidity_confirmed_index >= 0
                            and ob.liquidity_confirmed_index < i
                        )
                    )

                    gap_ok = self._passes_gap_filter(ob, active_gap_zones)

                    if liquidity_ok and gap_ok:
                        # MULTI-TIMEFRAME MODE: Find precise entry from entry-TF candles
                        if use_multi_tf and df_entry is not None:
                            signal_candle_close_time = current_time + timedelta(minutes=signal_tf_minutes)

                            precise_entry = self._find_precise_entry_with_reversal(
                                entry_times=entry_times,
                                entry_opens=entry_opens,
                                entry_highs=entry_highs,
                                entry_lows=entry_lows,
                                entry_closes=entry_closes,
                                signal_candle_close_time=signal_candle_close_time,
                                zone=ob,
                            )
                            if precise_entry:
                                trade_type = 'LONG' if ob.bias == 'BULLISH' else 'SHORT'
                                decision_time = precise_entry.entry_time + timedelta(minutes=entry_tf_minutes)
                                tp_context["decision_time"] = decision_time
                                if not self._passes_trend_filter(trend_filter, trade_type, decision_time):
                                    remaining_obs.append(ob)
                                    continue
                                opposite_liquidity_ok = self._has_opposite_liquidity_target(
                                    entry=float(precise_entry.entry_price),
                                    trade_type=trade_type,
                                    current_index=i,
                                    current_high=float(highs[i]),
                                    current_low=float(lows[i]),
                                    current_close=float(closes[i]),
                                    active_obs=active_obs,
                                    active_gap_zones=active_gap_zones,
                                    confirmed_eqh_levels=confirmed_eqh_levels,
                                    confirmed_eql_levels=confirmed_eql_levels,
                                )
                                if opposite_liquidity_ok:
                                    # Use base class signal creation (multi-TF)
                                    signal = self._create_signal_from_entry(
                                        symbol=symbol,
                                        zone=ob,
                                        entry_time=precise_entry.entry_time,
                                        entry_price=precise_entry.entry_price,
                                    )
                                    if signal:
                                        if self._should_block_choch_against_entry(
                                            signal=signal,
                                            ob=ob,
                                            entry_candle_open=float(entry_opens[precise_entry.entry_index]),
                                            entry_candle_close=float(entry_closes[precise_entry.entry_index]),
                                            active_gap_zones=active_gap_zones,
                                        ):
                                            remaining_obs.append(ob)
                                            continue
                                        signals.append(signal)
                                        ob.entry_triggered = True
                        else:
                            reversal_decision = self._evaluate_reversal_filter(opens, closes, highs, lows, i, ob.bias)
                            if not reversal_decision.passed:
                                remaining_obs.append(ob)
                                continue

                            # SINGLE-TIMEFRAME MODE: the candle must trade through the
                            # entry level; fill at its close (pattern is known only then).
                            entry_level = self._get_ob_midpoint(ob)
                            if not (np.isfinite(entry_level) and lows[i] <= entry_level <= highs[i]):
                                remaining_obs.append(ob)
                                continue
                            trade_type = 'LONG' if ob.bias == 'BULLISH' else 'SHORT'
                            decision_time = current_time + timedelta(minutes=single_tf_minutes)
                            tp_context["decision_time"] = decision_time
                            if not self._passes_trend_filter(trend_filter, trade_type, decision_time):
                                remaining_obs.append(ob)
                                continue
                            signal = self._create_signal_from_entry(
                                symbol=symbol,
                                zone=ob,
                                entry_time=current_time,
                                entry_price=float(closes[i]),
                            )
                            if signal:
                                if self._should_block_choch_against_entry(
                                    signal=signal,
                                    ob=ob,
                                    entry_candle_open=float(opens[i]),
                                    entry_candle_close=float(closes[i]),
                                    active_gap_zones=active_gap_zones,
                                ):
                                    remaining_obs.append(ob)
                                    continue
                                opposite_liquidity_ok = self._has_opposite_liquidity_target(
                                    entry=float(signal.entry_price),
                                    trade_type=signal.trade_type,
                                    current_index=i,
                                    current_high=float(highs[i]),
                                    current_low=float(lows[i]),
                                    current_close=float(closes[i]),
                                    active_obs=active_obs,
                                    active_gap_zones=active_gap_zones,
                                    confirmed_eqh_levels=confirmed_eqh_levels,
                                    confirmed_eql_levels=confirmed_eql_levels,
                                )
                                if opposite_liquidity_ok:
                                    signals.append(signal)
                                    ob.entry_triggered = True

                remaining_obs.append(ob)

            active_obs = remaining_obs

        # ========== 4. DEDUPLICATE SIGNALS ==========
        deduped = self._deduplicate_signals(cast(List[BaseSignal], signals))
        self._tp_context = None

        return cast(List[Signal], deduped)

    # ==================== SMC-SPECIFIC HELPER METHODS ====================

    def _build_trend_filter(self, source_df: pd.DataFrame) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """
        Build (close_times, closes, ema) on trend_filter_timeframe from the finest
        data available. Returns None when the filter is disabled.
        """
        tf = str(self.config.trend_filter_timeframe or "").strip()
        if not tf or source_df is None or source_df.empty:
            return None
        ohlcv = source_df[['Time', 'Open', 'High', 'Low', 'Close']].copy()
        ohlcv['Volume'] = source_df['Volume'] if 'Volume' in source_df.columns else 0.0
        htf = convert_timeframe(ohlcv, tf)
        length = max(1, int(self.config.trend_filter_ema_length))
        closes = htf['Close'].to_numpy(dtype=np.float64)
        ema = pd.Series(closes).ewm(span=length, adjust=False).mean().to_numpy()
        ema[: length - 1] = np.nan  # not enough history yet
        close_times = htf['Time'].to_numpy(dtype='datetime64[ns]') + np.timedelta64(parse_timeframe_minutes(tf), 'm')
        return close_times, closes, ema

    def _passes_trend_filter(
        self,
        trend_filter: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]],
        trade_type: str,
        decision_time: datetime,
    ) -> bool:
        """Use only the last higher-timeframe candle closed at decision_time (no look-ahead)."""
        if trend_filter is None:
            return True
        close_times, closes, ema = trend_filter
        idx = int(np.searchsorted(close_times, np.datetime64(decision_time, 'ns'), side='right')) - 1
        if idx < 0 or not np.isfinite(ema[idx]):
            return False
        if trade_type == 'LONG':
            return bool(closes[idx] > ema[idx])
        return bool(closes[idx] < ema[idx])

    # ==================== HIGHER-TIMEFRAME KEY LEVELS (SL / TP) ====================

    def _normalize_sl_mode(self) -> str:
        mode = str(self.config.sl_mode or "zone").strip().lower()
        return mode if mode in {"zone", "level", "level_or_zone"} else "zone"

    def _level_timeframes(self) -> List[str]:
        return normalize_timeframes(self.config.level_timeframe or [])

    def _uses_level_targets(self) -> bool:
        return self._normalize_tp_mode() in {"htf_level", "htf_level_or_rr"}

    def _build_key_levels(self, source_df: pd.DataFrame) -> List[KeyLevels]:
        """Key levels of each level_timeframe, in priority order, when the SL or TP uses them."""
        if self._normalize_sl_mode() == "zone" and not self._uses_level_targets():
            return []
        if source_df is None or source_df.empty:
            return []
        params = KeyLevelParams(
            sources=tuple(str(item).strip().lower() for item in (self.config.level_sources or [])),
            swing_length=max(1, int(self.config.level_swing_length)),
            ob_swing_length=max(1, int(self.config.swing_length)),
            ob_internal_length=max(1, int(self.config.internal_length)),
            ob_body_ratio=float(self.config.ob_body_ratio),
            fvg_auto_threshold=bool(self.config.fvg_atr_filter),
            eqhl_length=max(1, int(self.config.eqhl_length)),
            eqhl_threshold=float(self.config.eqhl_threshold),
        )
        levels: List[KeyLevels] = []
        for tf in self._level_timeframes():
            item = build_key_levels(source_df, tf, params)
            if item is not None:
                levels.append(item)
        return levels

    def _decision_ns(self) -> Optional[int]:
        decision_time = (self._tp_context or {}).get("decision_time")
        if decision_time is None:
            return None
        return int(pd.Timestamp(decision_time).value)

    def _level_stop(self, entry: float, trade_type: str) -> Optional[Tuple[float, str]]:
        """(SL, source) from the first level timeframe that has a usable level."""
        decision_ns = self._decision_ns()
        if decision_ns is None:
            return None
        for levels in self._key_levels:
            found = pick_stop(
                levels,
                entry=entry,
                trade_type=trade_type,
                decision_ns=decision_ns,
                buffer_pct=float(self.config.level_sl_buffer_pct),
                min_sl_pct=float(self.config.min_sl_pct),
                max_sl_pct=float(self.config.max_sl_pct),
                lookback_bars=int(self.config.level_lookback_bars),
            )
            if found is not None:
                return found
        return None

    def _level_target(self, entry: float, stop_loss: float, trade_type: str) -> Optional[Tuple[float, float, str]]:
        """(TP, RR, source) from the first level timeframe that has a usable level."""
        decision_ns = self._decision_ns()
        if decision_ns is None:
            return None
        for levels in self._key_levels:
            found = pick_target(
                levels,
                entry=entry,
                stop_loss=stop_loss,
                trade_type=trade_type,
                decision_ns=decision_ns,
                buffer_pct=float(self.config.level_tp_buffer_pct),
                min_rr=float(self.config.level_tp_min_rr),
                lookback_bars=int(self.config.level_lookback_bars),
            )
            if found is not None:
                return found
        return None

    def _plan_with_stop(self, entry: float, trade_type: str, stop_loss: float, sl_source: str) -> Optional[TradePlan]:
        """Plan with this SL and the fixed-RR TP."""
        if (trade_type == "LONG" and stop_loss >= entry) or (trade_type == "SHORT" and stop_loss <= entry):
            return None
        risk = abs(entry - stop_loss)
        rr = float(self.config.risk_reward_ratio)
        take_profit = entry + risk * rr if trade_type == "LONG" else entry - risk * rr
        return TradePlan(
            stop_loss=stop_loss,
            take_profit=take_profit,
            rr_ratio=self._compute_rr_ratio(entry, stop_loss, take_profit),
            base_risk_per_trade=self.config.risk_per_trade,
            risk_per_trade=self.config.risk_per_trade,
            sl_source=sl_source,
        )

    def _classify_ob_structure(
        self,
        ob: IndicatorOrderBlock,
        choch_lookup: dict[tuple[int, str], CHoCH],
        bos_lookup: dict[tuple[int, str], BOS],
    ) -> str:
        key = (int(ob.break_index), ob.bias)
        choch = choch_lookup.get(key)
        if choch is not None:
            return 'CHoCH'

        bos = bos_lookup.get(key)
        if bos is not None:
            return 'BOS'

        return 'BOS'

    def _create_gap_zone(
        self,
        *,
        top: float,
        bottom: float,
        bias: str,
        time: datetime,
        index: int,
        gap_type: str,
        source_index: int = -1,
    ) -> StrategyGapZone:
        return StrategyGapZone(
            high=max(float(top), float(bottom)),
            low=min(float(top), float(bottom)),
            time=time,
            bias=bias,
            index=index,
            gap_type=gap_type,
            source_index=source_index,
        )

    def _normalize_gap_filter_mode(self) -> str:
        mode = str(self.config.gap_filter_mode).strip().lower()
        aliases = {
            "off": "none",
            "disabled": "none",
            "either": "both",
            "any": "both",
            "fvg_or_ifvg": "both",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"none", "fvg", "ifvg", "both"}:
            return "none"
        return mode

    def _normalize_choch_against_entry_mode(self) -> str:
        mode = str(self.config.block_choch_against_entry_mode).strip().lower()
        aliases = {
            "none": "off",
            "disabled": "off",
            "false": "off",
            "true": "ifvg",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"off", "ifvg"}:
            return "off"
        return mode

    def _should_block_choch_against_entry(
        self,
        *,
        signal: BaseSignal,
        ob: StrategyOrderBlock,
        entry_candle_open: float,
        entry_candle_close: float,
        active_gap_zones: List[StrategyGapZone],
    ) -> bool:
        mode = self._normalize_choch_against_entry_mode()
        if mode == "off":
            return False
        if ob.structure_type != "CHoCH":
            return False

        entry_candle_color = classify_candle_color(entry_candle_open, entry_candle_close)
        entry_relation = candle_relation_to_trade(signal.trade_type, entry_candle_color)
        if entry_relation != "AGAINST_TRADE":
            return False

        if mode == "ifvg":
            primary_gap_zone = self._select_primary_gap_zone(ob, active_gap_zones)
            return primary_gap_zone is not None and primary_gap_zone.gap_type == "IFVG"

        return False

    def _merge_overlapping_order_blocks(
        self,
        order_blocks: List[StrategyOrderBlock],
    ) -> List[StrategyOrderBlock]:
        if not self.config.merge_overlapping_obs or len(order_blocks) <= 1:
            return sorted(
                order_blocks,
                key=lambda ob: (ob.break_index, ob.index, ob.low, ob.high),
            )

        merged_obs: List[StrategyOrderBlock] = []
        for bias in ("BULLISH", "BEARISH"):
            bias_obs = [ob for ob in order_blocks if ob.bias == bias]
            if not bias_obs:
                continue

            bias_obs.sort(key=lambda ob: (ob.break_index, ob.index, ob.low, ob.high))
            clusters: List[StrategyOrderBlock] = []

            for ob in bias_obs:
                clusters.append(ob)
                while len(clusters) >= 2 and self._should_merge_order_blocks(clusters[-2], clusters[-1]):
                    right = clusters.pop()
                    left = clusters.pop()
                    clusters.append(self._combine_order_blocks(left, right))

            merged_obs.extend(clusters)

        merged_obs.sort(key=lambda ob: (ob.break_index, ob.index, ob.low, ob.high))
        return merged_obs

    def _should_merge_order_blocks(
        self,
        left: StrategyOrderBlock,
        right: StrategyOrderBlock,
    ) -> bool:
        if left.bias != right.bias:
            return False

        max_break_gap = max(0, int(self.config.ob_merge_max_break_gap))
        if abs(int(right.break_index) - int(left.break_index)) > max_break_gap:
            return False

        return max(left.low, right.low) <= min(left.high, right.high)

    def _combine_order_blocks(
        self,
        left: StrategyOrderBlock,
        right: StrategyOrderBlock,
    ) -> StrategyOrderBlock:
        latest = right if right.break_index >= left.break_index else left
        structure_types = {left.structure_type, right.structure_type}
        structure_type = 'CHoCH' if 'CHoCH' in structure_types else latest.structure_type

        return StrategyOrderBlock(
            high=max(float(left.high), float(right.high)),
            low=min(float(left.low), float(right.low)),
            time=latest.time,
            bias=latest.bias,
            index=int(latest.index),
            structure_type=structure_type,
            break_index=max(int(left.break_index), int(right.break_index)),
            is_mitigated=False,
            has_liquidity=False,
            liquidity_confirmed_index=-1,
            entry_triggered=False,
            last_touch_index=-1,
        )

    def _prune_active_obs(
        self,
        active_obs: List[StrategyOrderBlock],
        min_break_index: int | None = None,
    ) -> List[StrategyOrderBlock]:
        if min_break_index is not None:
            active_obs = [ob for ob in active_obs if ob.break_index >= min_break_index]

        limit = max(1, int(self.config.max_active_obs_per_bias))
        if len(active_obs) <= limit:
            return active_obs

        kept_obs: List[StrategyOrderBlock] = []
        kept_per_bias: dict[str, int] = {}

        for ob in reversed(active_obs):
            current_count = kept_per_bias.get(ob.bias, 0)
            if current_count >= limit:
                continue
            kept_per_bias[ob.bias] = current_count + 1
            kept_obs.append(ob)

        kept_obs.reverse()
        return kept_obs

    def _get_ob_midpoint(self, zone: BaseZone) -> float:
        return float(zone.low + ((zone.high - zone.low) * self.config.entry_ob_level))

    def _passes_gap_filter(
        self,
        ob: StrategyOrderBlock,
        active_gap_zones: List[StrategyGapZone],
    ) -> bool:
        gap_filter_mode = self._normalize_gap_filter_mode()
        if gap_filter_mode == "none":
            return True

        required_gap_types = {
            "fvg": {"FVG"},
            "ifvg": {"IFVG"},
            "both": {"FVG", "IFVG"},
        }[gap_filter_mode]

        for gap_zone in active_gap_zones:
            if gap_zone.bias != ob.bias:
                continue
            if gap_zone.gap_type not in required_gap_types:
                continue
            return True

        return False

    def _select_primary_gap_zone(
        self,
        ob: StrategyOrderBlock,
        active_gap_zones: List[StrategyGapZone],
    ) -> Optional[StrategyGapZone]:
        same_bias_gap_zones = [gap_zone for gap_zone in active_gap_zones if gap_zone.bias == ob.bias]
        if not same_bias_gap_zones:
            return None

        gap_filter_mode = self._normalize_gap_filter_mode()
        if gap_filter_mode == "fvg":
            preferred_types = {"FVG"}
        elif gap_filter_mode == "ifvg":
            preferred_types = {"IFVG"}
        else:
            preferred_types = {"FVG", "IFVG"}

        preferred = [gap_zone for gap_zone in same_bias_gap_zones if gap_zone.gap_type in preferred_types]
        candidates = preferred or same_bias_gap_zones
        return max(candidates, key=lambda gap_zone: (gap_zone.index, gap_zone.source_index))

    def _collect_opposite_liquidity_levels(
        self,
        *,
        entry: float,
        trade_type: str,
        current_index: int,
        current_high: float,
        current_low: float,
        current_close: float,
        active_obs: List[StrategyOrderBlock],
        active_gap_zones: List[StrategyGapZone],
        confirmed_eqh_levels: List[Tuple[int, float]],
        confirmed_eql_levels: List[Tuple[int, float]],
    ) -> List[Tuple[float, int]]:
        """
        Collect opposite-side liquidity levels that can act as a destination.

        source_priority:
        - 0: opposite OB
        - 1: EQH/EQL
        - 2: FVG/IFVG
        """
        levels: List[Tuple[float, int]] = []

        if trade_type == "LONG":
            for target_ob in active_obs:
                if target_ob.bias != "BEARISH":
                    continue
                if target_ob.break_index > current_index:
                    continue
                if self._zone_is_mitigated(target_ob, current_high, current_low, current_close):
                    continue

                level = float(target_ob.low)
                if level <= entry:
                    continue
                # Must still be ahead of current bar (not already consumed)
                if level <= current_high:
                    continue
                levels.append((level, 0))

            for event_index, level in confirmed_eqh_levels:
                if event_index >= current_index:
                    continue
                if level <= entry:
                    continue
                if level <= current_high:
                    continue
                levels.append((float(level), 1))

            for gap_zone in active_gap_zones:
                if gap_zone.bias != "BEARISH":
                    continue
                level = float(gap_zone.low)
                if level <= entry:
                    continue
                if level <= current_high:
                    continue
                levels.append((level, 2))

            return levels

        for target_ob in active_obs:
            if target_ob.bias != "BULLISH":
                continue
            if target_ob.break_index > current_index:
                continue
            if self._zone_is_mitigated(target_ob, current_high, current_low, current_close):
                continue

            level = float(target_ob.high)
            if level >= entry:
                continue
            # Must still be ahead of current bar (not already consumed)
            if level >= current_low:
                continue
            levels.append((level, 0))

        for event_index, level in confirmed_eql_levels:
            if event_index >= current_index:
                continue
            if level >= entry:
                continue
            if level >= current_low:
                continue
            levels.append((float(level), 1))

        for gap_zone in active_gap_zones:
            if gap_zone.bias != "BULLISH":
                continue
            level = float(gap_zone.high)
            if level >= entry:
                continue
            if level >= current_low:
                continue
            levels.append((level, 2))

        return levels

    def _has_opposite_liquidity_target(
        self,
        *,
        entry: float,
        trade_type: str,
        current_index: int,
        current_high: float,
        current_low: float,
        current_close: float,
        active_obs: List[StrategyOrderBlock],
        active_gap_zones: List[StrategyGapZone],
        confirmed_eqh_levels: List[Tuple[int, float]],
        confirmed_eql_levels: List[Tuple[int, float]],
    ) -> bool:
        if not self.config.require_opposite_liquidity_target:
            return True

        candidates = self._collect_opposite_liquidity_levels(
            entry=entry,
            trade_type=trade_type,
            current_index=current_index,
            current_high=current_high,
            current_low=current_low,
            current_close=current_close,
            active_obs=active_obs,
            active_gap_zones=active_gap_zones,
            confirmed_eqh_levels=confirmed_eqh_levels,
            confirmed_eql_levels=confirmed_eql_levels,
        )
        return len(candidates) > 0

    def _is_reversal_candle(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                            lows: np.ndarray, current_idx: int, ob_bias: str) -> bool:
        """Check if at least min_reversal_pattern_matches pattern families match."""
        is_bullish = (ob_bias == 'BULLISH')
        pattern_ids = np.array(self.config.reversal_pattern_ids or [0], dtype=np.int64)
        min_matches = max(1, int(self.config.min_reversal_pattern_matches))
        matches = count_reversal_patterns_jit(
            opens, closes, highs, lows, current_idx, is_bullish, pattern_ids,
            float(self.config.doji_body_ratio), float(self.config.tweezer_tolerance_ratio),
        )
        return matches >= min_matches

    def _is_simple_reversal(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray,
                            lows: np.ndarray, current_idx: int, ob_bias: str) -> bool:
        """
        Lightweight reversal check to catch clean rejection setups that strict
        candlestick patterns can miss.

        Bullish: bullish close + (meaningful lower wick OR close above previous high)
        Bearish: bearish close + (meaningful upper wick OR close below previous low)

        Displacement used to be "close > previous close", which almost every
        green candle satisfies; it now requires closing beyond the previous
        candle's extreme.
        """
        if current_idx <= 0:
            return False

        o_curr = opens[current_idx]
        c_curr = closes[current_idx]
        h_curr = highs[current_idx]
        l_curr = lows[current_idx]
        h_prev = highs[current_idx - 1]
        l_prev = lows[current_idx - 1]

        candle_range = h_curr - l_curr
        if candle_range <= 0:
            return False

        wick_ratio = max(0.0, self.config.simple_reversal_wick_ratio)

        if ob_bias == 'BULLISH':
            lower_wick = min(o_curr, c_curr) - l_curr
            wick_ok = (lower_wick / candle_range) >= wick_ratio
            displacement_ok = c_curr > h_prev
            return c_curr > o_curr and (wick_ok or displacement_ok)

        upper_wick = h_curr - max(o_curr, c_curr)
        wick_ok = (upper_wick / candle_range) >= wick_ratio
        displacement_ok = c_curr < l_prev
        return c_curr < o_curr and (wick_ok or displacement_ok)

    def _passes_reversal_filter(
        self,
        opens: np.ndarray,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        current_idx: int,
        ob_bias: str,
    ) -> bool:
        return self._evaluate_reversal_filter(
            opens, closes, highs, lows, current_idx, ob_bias
        ).passed

    def _evaluate_reversal_filter(
        self,
        opens: np.ndarray,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        current_idx: int,
        ob_bias: str,
    ) -> ReversalDecision:
        if not self.config.require_reversal_pattern:
            return ReversalDecision(passed=True, source='DISABLED')

        pattern_ok = self._is_reversal_candle(opens, closes, highs, lows, current_idx, ob_bias)
        if pattern_ok:
            return ReversalDecision(passed=True, source='PATTERN')

        simple_reversal_ok = self.config.enable_simple_reversal and self._is_simple_reversal(
            opens, closes, highs, lows, current_idx, ob_bias
        )
        if simple_reversal_ok:
            return ReversalDecision(passed=True, source='SIMPLE')

        return ReversalDecision(passed=False, source='NONE')

    def _find_precise_entry_with_reversal(
        self,
        *,
        entry_times: np.ndarray,
        entry_opens: np.ndarray,
        entry_highs: np.ndarray,
        entry_lows: np.ndarray,
        entry_closes: np.ndarray,
        signal_candle_close_time: datetime,
        zone: BaseZone,
        max_bars_to_search: int = 60,
    ) -> Optional[PreciseEntryResult]:
        """
        Find the first entry-timeframe candle that trades through the OB entry
        level and passes the reversal filter on the entry timeframe itself.

        - Entry price is the CLOSE of that candle: the pattern is only confirmed
          when the candle closes (live sends a market order at that moment).
        - The search stops as soon as an entry-TF candle mitigates the OB, so a
          zone that was already broken can never produce an entry later on.
        """
        if len(entry_times) == 0:
            return None

        entry_tf_minutes = max(1, parse_timeframe_minutes(self.config.entry_timeframe))
        window_start = np.datetime64(signal_candle_close_time, 'ns')
        window_end = window_start + np.timedelta64(max_bars_to_search * entry_tf_minutes, 'm')

        start_idx = int(np.searchsorted(entry_times, window_start, side='left'))
        end_idx = int(np.searchsorted(entry_times, window_end, side='right'))
        zone_tolerance = (zone.high - zone.low) * self.config.touch_tolerance_pct
        entry_level = self._get_ob_midpoint(zone)
        if not np.isfinite(entry_level):
            return None

        for idx in range(start_idx, min(end_idx, len(entry_times))):
            low_price = entry_lows[idx]
            high_price = entry_highs[idx]

            if self._zone_is_mitigated(zone, high_price, low_price, entry_closes[idx]):
                return None

            touches_zone = low_price <= (zone.high + zone_tolerance) and high_price >= (zone.low - zone_tolerance)
            if not touches_zone:
                continue

            if not (low_price <= entry_level <= high_price):
                continue

            overlap_low = max(zone.low, low_price)
            overlap_high = min(zone.high, high_price)
            if overlap_low > overlap_high:
                continue

            reversal_decision = self._evaluate_reversal_filter(
                entry_opens,
                entry_closes,
                entry_highs,
                entry_lows,
                idx,
                zone.bias,
            )
            if not reversal_decision.passed:
                continue

            entry_time = self._parse_datetime(entry_times[idx])
            if entry_time is None:
                continue
            return PreciseEntryResult(
                entry_time=entry_time,
                entry_price=float(entry_closes[idx]),
                entry_index=idx,
                reversal_source=reversal_decision.source,
            )

        return None

    def _normalize_tp_mode(self) -> str:
        mode = str(self.config.tp_mode).strip().lower()
        valid_modes = {
            "fixed_rr",
            "opposite_ob",
            "opposite_swing",
            "opposite_ob_or_swing",
            "htf_liquidity",
            "htf_liquidity_or_rr",
            "htf_level",
            "htf_level_or_rr",
        }
        if mode not in valid_modes:
            return "opposite_ob_or_swing"
        return mode

    def _normalize_tp_no_target_policy(self) -> str:
        policy = str(self.config.tp_no_target_policy).strip().lower()
        if policy not in {"skip", "fixed_rr"}:
            return "skip"
        return policy

    def _get_flex_tp_risk_boost_factor(self) -> float:
        try:
            factor = float(self.config.flex_tp_risk_boost_factor)
        except (TypeError, ValueError):
            return 0.0
        if not np.isfinite(factor):
            return 0.0
        return max(0.0, factor)

    def _compute_effective_risk_per_trade(self, flex_rr_ratio: Optional[float]) -> tuple[float, float]:
        base_risk = max(0.0, float(self.config.risk_per_trade))
        if flex_rr_ratio is None or not np.isfinite(flex_rr_ratio):
            return (base_risk, 1.0)

        base_rr = max(0.0, float(self.config.risk_reward_ratio))
        extra_rr = max(0.0, float(flex_rr_ratio) - base_rr)
        factor = self._get_flex_tp_risk_boost_factor()
        risk_boost_multiplier = 1.0 + (extra_rr * factor)
        return (base_risk * risk_boost_multiplier, risk_boost_multiplier)

    def _collect_tp_candidates_from_opposite_ob(
        self,
        *,
        entry: float,
        risk: float,
        trade_type: str,
        current_index: int,
        current_high: float,
        current_low: float,
        current_close: float,
        active_obs: List[StrategyOrderBlock],
    ) -> List[Tuple[float, float, int]]:
        candidates: List[Tuple[float, float, int]] = []

        for target_ob in active_obs:
            if trade_type == "LONG" and target_ob.bias != "BEARISH":
                continue
            if trade_type == "SHORT" and target_ob.bias != "BULLISH":
                continue
            if target_ob.break_index > current_index:
                continue
            if self._zone_is_mitigated(target_ob, current_high, current_low, current_close):
                continue

            level = target_ob.low if trade_type == "LONG" else target_ob.high
            if trade_type == "LONG" and level <= entry:
                continue
            if trade_type == "SHORT" and level >= entry:
                continue

            rr = (level - entry) / risk if trade_type == "LONG" else (entry - level) / risk
            if rr > 0:
                # source_priority: 0 = opposite OB
                candidates.append((float(level), float(rr), 0))

        return candidates

    def _collect_tp_candidates_from_opposite_swing(
        self,
        *,
        entry: float,
        risk: float,
        trade_type: str,
        confirmed_swing_high_levels: List[float],
        confirmed_swing_low_levels: List[float],
    ) -> List[Tuple[float, float, int]]:
        candidates: List[Tuple[float, float, int]] = []

        if trade_type == "LONG":
            for level in confirmed_swing_high_levels:
                if level <= entry:
                    continue
                rr = (level - entry) / risk
                if rr > 0:
                    # source_priority: 1 = opposite swing
                    candidates.append((float(level), float(rr), 1))
        else:
            for level in confirmed_swing_low_levels:
                if level >= entry:
                    continue
                rr = (entry - level) / risk
                if rr > 0:
                    candidates.append((float(level), float(rr), 1))

        return candidates

    def _collect_tp_candidates_from_opposite_liquidity(
        self,
        *,
        entry: float,
        risk: float,
        trade_type: str,
        current_index: int,
        current_high: float,
        current_low: float,
        current_close: float,
        active_obs: List[StrategyOrderBlock],
        active_gap_zones: List[StrategyGapZone],
        confirmed_eqh_levels: List[Tuple[int, float]],
        confirmed_eql_levels: List[Tuple[int, float]],
    ) -> List[Tuple[float, float, int]]:
        candidates: List[Tuple[float, float, int]] = []
        levels = self._collect_opposite_liquidity_levels(
            entry=entry,
            trade_type=trade_type,
            current_index=current_index,
            current_high=current_high,
            current_low=current_low,
            current_close=current_close,
            active_obs=active_obs,
            active_gap_zones=active_gap_zones,
            confirmed_eqh_levels=confirmed_eqh_levels,
            confirmed_eql_levels=confirmed_eql_levels,
        )
        for level, source_priority in levels:
            rr = (level - entry) / risk if trade_type == "LONG" else (entry - level) / risk
            if rr > 0:
                candidates.append((float(level), float(rr), int(source_priority)))
        return candidates

    def _select_take_profit_from_structure(
        self,
        *,
        entry: float,
        stop_loss: float,
        trade_type: str,
    ) -> Optional[Tuple[float, float]]:
        """
        Select TP from market-structure targets.

        Rules:
        - Gather candidates based on tp_mode.
        - Prefer candidates with RR >= anchor (risk_reward_ratio), nearest anchor wins.
        - If no candidate reaches anchor, pick candidate with highest RR.
        """
        context = self._tp_context
        if not context:
            return None

        risk = abs(entry - stop_loss)
        if risk <= 0:
            return None

        current_index = int(context.get("current_index", -1))
        if current_index < 0:
            return None

        current_high = float(context.get("current_high", entry))
        current_low = float(context.get("current_low", entry))
        current_close = float(context.get("current_close", entry))

        active_obs = cast(List[StrategyOrderBlock], context.get("active_obs", []))
        active_gap_zones = cast(List[StrategyGapZone], context.get("active_gap_zones", []))
        confirmed_swing_high_levels = cast(List[float], context.get("confirmed_swing_high_levels", []))
        confirmed_swing_low_levels = cast(List[float], context.get("confirmed_swing_low_levels", []))
        confirmed_eqh_levels = cast(List[Tuple[int, float]], context.get("confirmed_eqh_levels", []))
        confirmed_eql_levels = cast(List[Tuple[int, float]], context.get("confirmed_eql_levels", []))

        tp_mode = self._normalize_tp_mode()
        candidates: List[Tuple[float, float, int]] = []

        if tp_mode in {"opposite_ob", "opposite_ob_or_swing"}:
            candidates.extend(
                self._collect_tp_candidates_from_opposite_ob(
                    entry=entry,
                    risk=risk,
                    trade_type=trade_type,
                    current_index=current_index,
                    current_high=current_high,
                    current_low=current_low,
                    current_close=current_close,
                    active_obs=active_obs,
                )
            )

        if tp_mode in {"opposite_swing", "opposite_ob_or_swing"}:
            candidates.extend(
                self._collect_tp_candidates_from_opposite_swing(
                    entry=entry,
                    risk=risk,
                    trade_type=trade_type,
                    confirmed_swing_high_levels=confirmed_swing_high_levels,
                    confirmed_swing_low_levels=confirmed_swing_low_levels,
                )
            )

        if tp_mode in {"htf_liquidity", "htf_liquidity_or_rr"}:
            candidates.extend(
                self._collect_tp_candidates_from_opposite_liquidity(
                    entry=entry,
                    risk=risk,
                    trade_type=trade_type,
                    current_index=current_index,
                    current_high=current_high,
                    current_low=current_low,
                    current_close=current_close,
                    active_obs=active_obs,
                    active_gap_zones=active_gap_zones,
                    confirmed_eqh_levels=confirmed_eqh_levels,
                    confirmed_eql_levels=confirmed_eql_levels,
                )
            )

        if not candidates:
            return None

        rr_anchor = max(0.1, float(self.config.risk_reward_ratio))
        above_anchor = [candidate for candidate in candidates if candidate[1] >= rr_anchor]

        if above_anchor:
            if trade_type == "LONG":
                best = min(
                    above_anchor,
                    key=lambda candidate: (abs(candidate[1] - rr_anchor), candidate[2], candidate[0]),
                )
            else:
                best = min(
                    above_anchor,
                    key=lambda candidate: (abs(candidate[1] - rr_anchor), candidate[2], -candidate[0]),
                )
            return (float(best[0]), float(best[1]))

        if trade_type == "LONG":
            best = max(candidates, key=lambda candidate: (candidate[1], -candidate[2], -candidate[0]))
        else:
            best = max(candidates, key=lambda candidate: (candidate[1], -candidate[2], candidate[0]))

        return (float(best[0]), float(best[1]))

    def _build_trade_plan(
        self,
        entry: float,
        zone: BaseZone,
        trade_type: str,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Optional[TradePlan]:
        """
        Compute SL/TP with flexible TP mode and per-signal risk sizing.

        SL is inherited from BaseStrategy (entry OB), or placed beyond the
        nearest key level of level_timeframe when sl_mode is level / level_or_zone.
        tp_mode htf_level / htf_level_or_rr: TP at the nearest key level with
        RR >= level_tp_min_rr (no cap).
        Other TP modes: TP execution is capped at base RR when structure TP
        extends beyond it. In that case, risk_per_trade is boosted from the
        selected structure RR.
        """
        base_plan = super()._build_trade_plan(
            entry=entry,
            zone=zone,
            trade_type=trade_type,
            candle_high=candle_high,
            candle_low=candle_low,
        )

        sl_mode = self._normalize_sl_mode()
        if sl_mode != "zone":
            level_stop = self._level_stop(entry, trade_type)
            if level_stop is not None:
                base_plan = self._plan_with_stop(entry, trade_type, level_stop[0], level_stop[1])
            elif sl_mode == "level":
                return None
            elif base_plan is not None:
                base_plan = replace(base_plan, sl_source="OB")
        if base_plan is None:
            return None

        tp_mode = self._normalize_tp_mode()
        if tp_mode == "fixed_rr":
            return base_plan

        if tp_mode in {"htf_level", "htf_level_or_rr"}:
            target = self._level_target(entry, base_plan.stop_loss, trade_type)
            if target is None:
                return replace(base_plan, tp_source="RR") if tp_mode == "htf_level_or_rr" else None
            take_profit, rr_ratio, tp_source = target
            return TradePlan(
                stop_loss=base_plan.stop_loss,
                take_profit=take_profit,
                rr_ratio=rr_ratio,
                flex_take_profit=take_profit,
                flex_rr_ratio=rr_ratio,
                base_risk_per_trade=base_plan.base_risk_per_trade,
                risk_per_trade=base_plan.risk_per_trade,
                sl_source=base_plan.sl_source,
                tp_source=tp_source,
            )

        structure_take_profit = self._select_take_profit_from_structure(
            entry=entry,
            stop_loss=base_plan.stop_loss,
            trade_type=trade_type,
        )
        if structure_take_profit is not None:
            structure_tp, flex_rr_ratio = structure_take_profit
            if trade_type == "LONG" and structure_tp > entry:
                execution_take_profit = structure_tp
                execution_rr_ratio = flex_rr_ratio
                tp_capped = False
                if base_plan.rr_ratio is not None and flex_rr_ratio > base_plan.rr_ratio:
                    execution_take_profit = base_plan.take_profit
                    execution_rr_ratio = base_plan.rr_ratio
                    tp_capped = True

                effective_risk_per_trade, risk_boost_multiplier = self._compute_effective_risk_per_trade(
                    flex_rr_ratio,
                )
                return TradePlan(
                    stop_loss=base_plan.stop_loss,
                    take_profit=execution_take_profit,
                    rr_ratio=execution_rr_ratio,
                    flex_take_profit=structure_tp,
                    flex_rr_ratio=flex_rr_ratio,
                    base_risk_per_trade=base_plan.base_risk_per_trade,
                    risk_per_trade=effective_risk_per_trade,
                    risk_boost_multiplier=risk_boost_multiplier,
                    tp_capped=tp_capped,
                    sl_source=base_plan.sl_source,
                )
            if trade_type == "SHORT" and structure_tp < entry:
                execution_take_profit = structure_tp
                execution_rr_ratio = flex_rr_ratio
                tp_capped = False
                if base_plan.rr_ratio is not None and flex_rr_ratio > base_plan.rr_ratio:
                    execution_take_profit = base_plan.take_profit
                    execution_rr_ratio = base_plan.rr_ratio
                    tp_capped = True

                effective_risk_per_trade, risk_boost_multiplier = self._compute_effective_risk_per_trade(
                    flex_rr_ratio,
                )
                return TradePlan(
                    stop_loss=base_plan.stop_loss,
                    take_profit=execution_take_profit,
                    rr_ratio=execution_rr_ratio,
                    flex_take_profit=structure_tp,
                    flex_rr_ratio=flex_rr_ratio,
                    base_risk_per_trade=base_plan.base_risk_per_trade,
                    risk_per_trade=effective_risk_per_trade,
                    risk_boost_multiplier=risk_boost_multiplier,
                    tp_capped=tp_capped,
                    sl_source=base_plan.sl_source,
                )

        if tp_mode == "htf_liquidity_or_rr":
            return base_plan

        if self._normalize_tp_no_target_policy() == "fixed_rr":
            return base_plan
        return None

    def _compute_sl_tp(
        self,
        entry: float,
        zone: BaseZone,
        trade_type: str,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Optional[Tuple[float, float]]:
        trade_plan = self._build_trade_plan(
            entry=entry,
            zone=zone,
            trade_type=trade_type,
            candle_high=candle_high,
            candle_low=candle_low,
        )
        if trade_plan is None:
            return None
        return (trade_plan.stop_loss, trade_plan.take_profit)
