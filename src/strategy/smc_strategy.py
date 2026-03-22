"""
SMC Strategy - Structure + Order Block + Liquidity
==================================================
Smart Money Concepts trading strategy based on:
- Market structure events (CHoCH and BOS)
- Order Blocks (OB), classified by the structure break that activated them
- Equal Highs/Lows (EQH/EQL) as post-break liquidity confirmation
- Optional FVG / IFVG confluence
- Optional reversal-pattern and ADX filters

Actual entry flow implemented by this module:
---------------------------------------------
LONG setup:
1. Detect bullish OBs from structure breaks and classify them as CHoCH or BOS.
2. Activate the OB when its break_index is reached.
3. Optionally require a later EQH confirmation after the OB break
   (require_liquidity=True).
4. Wait for price to touch the OB zone. Entry can happen on the touch bar or
   within touch_confirmation_bars after the most recent touch.
5. Optionally require matching bullish FVG/IFVG confluence.
6. Optionally require a bullish reversal candlestick pattern or a simpler
   rejection/displacement reversal.
7. Optionally require a valid opposite-side liquidity destination before entry.
8. Enter LONG:
   - single-timeframe mode: from the signal timeframe candle/zone overlap or
     candle close, depending on configuration
   - multi-timeframe mode: from the first entry-timeframe candle after the
     signal bar closes that both touches the zone and passes the reversal filter
9. SL is placed below the OB with buffer / multiplier logic and minimum-SL
   enforcement inherited from BaseStrategy.
10. TP is configurable: fixed RR, opposite OB, opposite swing, or opposite HTF
    liquidity, with optional fallback to fixed RR.

SHORT setup: opposite of the above.

Important behavior notes:
- The strategy can trade both CHoCH-based and BOS-based OBs; CHoCH is not a
  mandatory prerequisite for every trade.
- Liquidity confirmation is tracked causally after the OB break; indicator
  precomputed mitigation flags are ignored and mitigation is evaluated bar by
  bar in the strategy loop.
- In multi-timeframe mode, structure is detected on the signal timeframe while
  entry timing is refined on the entry timeframe.

INTERFACE CONTRACT (must export for main.py):
- Config: dataclass with strategy configuration
- Signal: dataclass representing a trade signal
- Strategy: class with generate_signals(df, symbol) -> List[Signal] method

Uses indicator modules from src/indicators for OB, CHoCH, BOS, EQH/EQL,
swings, and optional FVG/IFVG confluence detection.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, cast

import numpy as np
import pandas as pd
from numba import njit

from src.strategy.base_strategy import (
    BaseConfig,
    BaseSignal,
    BaseStrategy,
    BaseZone,
    parse_timeframe_minutes,
)
from src.indicators.reversal_patterns import check_reversal_pattern_jit, check_pattern_by_ids_jit

from src.indicators.CHOCH import detect_choch, CHoCH
from src.indicators.BOS import detect_bos, BOS
from src.indicators.order_block import detect_order_blocks as detect_order_blocks_indicator
from src.indicators.order_block import OrderBlock as IndicatorOrderBlock
from src.indicators.eqh_eql import detect_eqh_eql, EqualHL as IndicatorEqualHL
from src.indicators.FVG import detect_fvg, FairValueGap
from src.indicators.ifvg import detect_ifvg, InverseFairValueGap
from src.indicators.swing import detect_swings, Swing as IndicatorSwing
from src.indicators.adx import compute_adx_jit


# ==================== STRATEGY NAME ====================
STRATEGY_NAME = "SMC"


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

    # === ENTRY FILTERS ===
    require_liquidity: bool = True          # Require EQH/EQL liquidity event after OB break
    require_opposite_liquidity_target: bool = True  # Require opposite-side target: OB or EQH/EQL or FVG/IFVG
    require_reversal_pattern: bool = True   # Require reversal pattern before entry
    enable_simple_reversal: bool = True     # Allow simpler rejection/displacement reversal
    simple_reversal_wick_ratio: float = 0.25  # Min wick/range ratio for simple reversal

    # === IMBALANCE FILTER ===
    gap_filter_mode: str = "none"   # none | fvg | ifvg | both
    fvg_atr_filter: bool = True     # Legacy name; toggles FVG/IFVG auto-threshold filter

    # === REVERSAL PATTERNS ===
    reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])

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
    tp_mode: str = "opposite_ob_or_swing"
    # skip: reject trade when no structural target exists
    # fixed_rr: fallback to classic RR TP when no structural target exists
    tp_no_target_policy: str = "skip"
    # Swing length used to build opposite swing targets
    tp_swing_length: int = 5

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
            "require_liquidity": self.require_liquidity,
            "require_opposite_liquidity_target": self.require_opposite_liquidity_target,
            "require_reversal_pattern": self.require_reversal_pattern,
            "enable_simple_reversal": self.enable_simple_reversal,
            "simple_reversal_wick_ratio": self.simple_reversal_wick_ratio,
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
            "tp_mode": self.tp_mode,
            "tp_no_target_policy": self.tp_no_target_policy,
            "tp_swing_length": self.tp_swing_length,
            "enable_adx_filter": self.enable_adx_filter,
            "adx_length": self.adx_length,
            "adx_threshold": self.adx_threshold,
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
        times = df['Time'].values
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

            self._tp_context = {
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
                    # ADX filter: require minimum trend strength on signal timeframe
                    if self.config.enable_adx_filter:
                        if adx_values is None:
                            remaining_obs.append(ob)
                            continue
                        current_adx = adx_values[i]
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
                                entry_time_1m, entry_price_1m = precise_entry
                                trade_type = 'LONG' if ob.bias == 'BULLISH' else 'SHORT'
                                opposite_liquidity_ok = self._has_opposite_liquidity_target(
                                    entry=float(entry_price_1m),
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
                                        entry_time=entry_time_1m,
                                        entry_price=entry_price_1m,
                                    )
                                    if signal:
                                        signals.append(signal)
                                        ob.entry_triggered = True
                        else:
                            reversal_ok = self._passes_reversal_filter(opens, closes, highs, lows, i, ob.bias)
                            if not reversal_ok:
                                remaining_obs.append(ob)
                                continue

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

    def _passes_reversal_filter(
        self,
        opens: np.ndarray,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        current_idx: int,
        ob_bias: str,
    ) -> bool:
        if not self.config.require_reversal_pattern:
            return True

        pattern_ok = self._is_reversal_candle(opens, closes, highs, lows, current_idx, ob_bias)
        simple_reversal_ok = self.config.enable_simple_reversal and self._is_simple_reversal(
            opens, closes, highs, lows, current_idx, ob_bias
        )
        return pattern_ok or simple_reversal_ok

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
    ) -> Optional[Tuple[datetime, float]]:
        """
        Find the first entry-timeframe candle that both touches the zone and
        passes the reversal filter on the entry timeframe itself.
        """
        if len(entry_times) == 0:
            return None

        entry_tf_minutes = max(1, parse_timeframe_minutes(self.config.entry_timeframe))
        window_start = np.datetime64(signal_candle_close_time, 'ns')
        window_end = window_start + np.timedelta64(max_bars_to_search * entry_tf_minutes, 'm')

        start_idx = int(np.searchsorted(entry_times, window_start, side='left'))
        end_idx = int(np.searchsorted(entry_times, window_end, side='right'))
        zone_tolerance = (zone.high - zone.low) * self.config.touch_tolerance_pct

        for idx in range(start_idx, min(end_idx, len(entry_times))):
            low_price = entry_lows[idx]
            high_price = entry_highs[idx]
            close_price = entry_closes[idx]

            touches_zone = low_price <= (zone.high + zone_tolerance) and high_price >= (zone.low - zone_tolerance)
            if not touches_zone:
                continue

            overlap_low = max(zone.low, low_price)
            overlap_high = min(zone.high, high_price)
            if overlap_low > overlap_high:
                continue

            if not self._passes_reversal_filter(entry_opens, entry_closes, entry_highs, entry_lows, idx, zone.bias):
                continue

            entry_time = self._parse_datetime(entry_times[idx])
            if entry_time is None:
                continue
            entry_price = min(max(close_price, zone.low), zone.high)
            return (entry_time, entry_price)

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
        }
        if mode not in valid_modes:
            return "opposite_ob_or_swing"
        return mode

    def _normalize_tp_no_target_policy(self) -> str:
        policy = str(self.config.tp_no_target_policy).strip().lower()
        if policy not in {"skip", "fixed_rr"}:
            return "skip"
        return policy

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
    ) -> Optional[float]:
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
            return float(best[0])

        if trade_type == "LONG":
            best = max(candidates, key=lambda candidate: (candidate[1], -candidate[2], -candidate[0]))
        else:
            best = max(candidates, key=lambda candidate: (candidate[1], -candidate[2], candidate[0]))

        return float(best[0])

    def _compute_sl_tp(
        self,
        entry: float,
        zone: BaseZone,
        trade_type: str,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> Optional[Tuple[float, float]]:
        """
        Compute SL/TP with flexible TP mode.

        SL is inherited from BaseStrategy.
        TP is selected by:
        - fixed RR (tp_mode=fixed_rr), OR
        - structure target (opposite OB/swing), with optional RR fallback policy.
        """
        base_result = super()._compute_sl_tp(
            entry=entry,
            zone=zone,
            trade_type=trade_type,
            candle_high=candle_high,
            candle_low=candle_low,
        )
        if base_result is None:
            return None

        stop_loss, rr_take_profit = base_result
        tp_mode = self._normalize_tp_mode()
        if tp_mode == "fixed_rr":
            return (stop_loss, rr_take_profit)

        structure_take_profit = self._select_take_profit_from_structure(
            entry=entry,
            stop_loss=stop_loss,
            trade_type=trade_type,
        )
        if structure_take_profit is not None:
            if trade_type == "LONG" and structure_take_profit > entry:
                return (stop_loss, structure_take_profit)
            if trade_type == "SHORT" and structure_take_profit < entry:
                return (stop_loss, structure_take_profit)

        if tp_mode == "htf_liquidity_or_rr":
            return (stop_loss, rr_take_profit)

        if self._normalize_tp_no_target_policy() == "fixed_rr":
            return (stop_loss, rr_take_profit)
        return None
