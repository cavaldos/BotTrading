# SMC Strategy (Current Python Implementation)

## 1) Purpose and Scope

This document is a detailed technical specification of the **current live strategy logic** implemented in `src/strategy/smc_strategy.py`.

It is written to help you:
- Reuse the strategy later.
- Re-implement the same behavior in another codebase or language.
- Understand exactly why a signal is or is not generated.

This specification describes the Python implementation only (not old Pine variants).

---

## 2) Source of Truth

- Strategy file: `src/strategy/smc_strategy.py`
- Reversal pattern engine: `src/indicators/reversal_patterns.py`
- Signal simulation and reporting: `src/backtest/statistics.py`
- Live execution wrapper: `src/live/core/execute.py`
- Live scheduling loop: `src/live/run.py`

---

## 3) High-Level Trading Idea

The strategy is a causal SMC engine based on:
- Market structure events (CHoCH and BOS)
- Order Blocks (OB)
- Liquidity events (EQH/EQL)
- Reversal confirmation (candlestick patterns and optional simple reversal)

Core flow:
1. Detect structure and OB events on historical bars.
2. Activate each event only when it becomes known in time.
3. Wait for OB retest.
4. Validate optional liquidity and reversal conditions.
5. Create LONG/SHORT signal with deterministic SL/TP formulas.

---

## 4) Data Contracts

### 4.1 Config dataclass

`Config` controls behavior and risk. Main groups:

1) Structure
- `swing_length`
- `internal_length`

2) Liquidity detection
- `eqhl_length`
- `eqhl_threshold`

3) Order block behavior
- `ob_mitigation` in `{ "close", "high_low" }`
- `ob_body_ratio` (minimum body/range for OB origin candle)

4) Entry filters
- `require_liquidity` (if True, EQH/EQL after break is required)
- `require_reversal_pattern` (if True, reversal check is required)
- `enable_simple_reversal` (fallback reversal check)
- `simple_reversal_wick_ratio`
- `touch_tolerance_pct`
- `touch_confirmation_bars`

5) Risk
- `risk_reward_ratio`
- `sl_buffer_pct`
- `sl_multiplier`
- `min_sl_pct`
- `risk_per_trade` (position sizing metadata)

6) Pattern selection
- `reversal_pattern_ids` (`[0]` means use all patterns)

Note: `fvg_atr_filter` is kept for compatibility but is not used in current entry logic.

### 4.2 Runtime OB object (`StrategyOrderBlock`)

Each active OB stores:
- zone: `high`, `low`
- metadata: `time`, `index`, `bias`, `structure_type`, `break_index`
- state flags: `is_mitigated`, `has_liquidity`, `entry_triggered`
- retest memory: `last_touch_index`

### 4.3 Output signal (`Signal`)

Each signal contains:
- `symbol`
- `trade_type` (`LONG` or `SHORT`)
- `entry_time`
- `entry_price`
- `stop_loss`
- `take_profit`

---

## 5) Indicator Dependencies and Usage

The strategy consumes these indicator outputs:

1) `detect_choch(...)` -> CHoCH events
2) `detect_bos(...)` -> BOS events
3) `detect_order_blocks(...)` -> candidate OB zones with `break_index`
4) `detect_eqh_eql(...)` -> equal highs/lows with `confirmed_index`

Important implementation rule:
- Indicators are precomputed on the dataset, but each event is only used at its activation bar to avoid look-ahead.

---

## 6) Full Signal Generation Algorithm

## Step A: Precompute arrays

From the input DataFrame (`Open, High, Low, Close, Time`):
- Convert OHLC arrays to `float64`
- Keep `Time` array for signal timestamps

## Step B: Precompute indicator events

Run CHoCH, BOS, OB, and EQH/EQL detection once.

## Step C: Build event queues

### C1) OB queue by `break_index`

For each detected OB:
1. Apply trending candle filter on OB origin candle:

```text
body = abs(close - open)
range = high - low
keep OB if (body / range) > ob_body_ratio
```

2. Classify OB as `CHoCH` or `BOS` by matching `(break_index, bias)` with CHoCH/BOS events.
3. Create `StrategyOrderBlock` and push into `obs_by_break[break_index]`.

### C2) Liquidity queue by `confirmed_index`

Group EQH/EQL events into `eqhl_by_confirm[confirmed_index]`.

## Step D: Causal bar-by-bar loop

Iterate bars `i = 0..n-1`.

For each bar `i`:

1) Activate OBs
- Add all `obs_by_break[i]` into `active_obs`.

2) Update liquidity flags
- Process each `eqhl_by_confirm[i]`.
- For each active OB not yet liquid:
  - Ignore liquidity events with `confirmed_index <= ob.break_index`.
  - If OB is bullish, accept `EQH`.
  - If OB is bearish, accept `EQL`.
  - Set `ob.has_liquidity = True`.

3) Process each active OB

For each OB in `active_obs`:

a) Mitigation check (invalidates OB)
- `ob_mitigation == "close"`:
  - Bullish OB mitigated if `close < ob.low`
  - Bearish OB mitigated if `close > ob.high`
- `ob_mitigation == "high_low"`:
  - Bullish OB mitigated if `low < ob.low`
  - Bearish OB mitigated if `high > ob.high`

If mitigated: remove OB from active list.

b) Touch check with tolerance

```text
ob_range   = max(0, ob.high - ob.low)
tolerance  = ob_range * max(0, touch_tolerance_pct)
touches_ob = (low <= ob.high + tolerance) and (high >= ob.low - tolerance)
```

If touched: `ob.last_touch_index = i`.

c) Touch confirmation window

```text
touch_window = max(0, touch_confirmation_bars)
touch_recent = (last_touch_index >= 0) and ((i - last_touch_index) <= touch_window)

if touch_window > 0:
    entry_window_active = touch_recent
else:
    entry_window_active = touches_ob
```

d) Entry gates (only if not already triggered)

- `liquidity_ok = ob.has_liquidity OR (not require_liquidity)`

- Reversal checks:
  - `pattern_ok`: strict candlestick logic from `reversal_patterns.py`
  - `simple_reversal_ok`: optional lightweight rejection/displacement logic

Simple reversal logic:

- Bullish OB:
  - candle must close bullish (`close > open`)
  - and (`lower_wick/range >= simple_reversal_wick_ratio` OR `close > prev_close`)

- Bearish OB:
  - candle must close bearish (`close < open`)
  - and (`upper_wick/range >= simple_reversal_wick_ratio` OR `close < prev_close`)

- Combine:

```text
if require_reversal_pattern:
    reversal_ok = pattern_ok OR simple_reversal_ok
else:
    reversal_ok = True
```

If `liquidity_ok AND reversal_ok`, create signal and mark `ob.entry_triggered = True`.

## Step E: Deduplicate signals

After loop, deduplicate by key:

```text
(entry_time, trade_type)
```

Keep first occurrence only.

---

## 7) Exact SL/TP and Entry Formulas

Let:
- `ob_range = ob.high - ob.low`
- `buffer = ob_range * sl_buffer_pct`

### 7.1 LONG signal

1) Entry

```text
body_low = min(current_open, current_close)
entry    = min(ob.high, body_low)
```

2) Base stop and expansion

```text
base_sl   = ob.low - buffer
base_risk = entry - base_sl
stop_loss = entry - (base_risk * sl_multiplier)
```

3) Safety extension if SL is too tight versus current candle low

```text
if stop_loss > current_low:
    stop_loss = current_low - buffer
```

4) TP by RR

```text
actual_risk = entry - stop_loss
take_profit = entry + (actual_risk * risk_reward_ratio)
```

### 7.2 SHORT signal

1) Entry

```text
body_high = max(current_open, current_close)
entry     = max(ob.low, body_high)
```

2) Base stop and expansion

```text
base_sl   = ob.high + buffer
base_risk = base_sl - entry
stop_loss = entry + (base_risk * sl_multiplier)
```

3) Safety extension if SL is too tight versus current candle high

```text
if stop_loss < current_high:
    stop_loss = current_high + buffer
```

4) TP by RR

```text
actual_risk = stop_loss - entry
take_profit = entry - (actual_risk * risk_reward_ratio)
```

### 7.3 Validity guards and minimum SL distance

- Reject LONG if `stop_loss >= entry`
- Reject SHORT if `stop_loss <= entry`

Then enforce minimum stop distance:

```text
sl_pct = abs(entry - stop_loss) / entry * 100
if sl_pct < min_sl_pct:
    min_sl_distance = entry * min_sl_pct / 100
    if LONG:  stop_loss = entry - min_sl_distance
    if SHORT: stop_loss = entry + min_sl_distance

    actual_risk = abs(entry - stop_loss)
    TP recomputed using risk_reward_ratio
```

Finally round values via `round_price(...)`.

---

## 8) Causality and Anti Look-Ahead Design

The strategy is designed to be causal:
- OBs are only activated at `break_index`.
- EQH/EQL are only applied at `confirmed_index` (not at raw pivot bar).
- OB mitigation is evaluated using only current bar prices in the forward loop.

Live execution safety layer:
- `execute.py` drops an incomplete last candle.
- `run.py` can run in wait-for-close mode to evaluate only closed candles.

---

## 9) Reversal Pattern Engine Notes

Strict patterns are delegated to Numba JIT functions in `src/utils/reversal_patterns.py`.

Pattern behavior:
- `reversal_pattern_ids = [0]` means "all supported patterns".
- Any other ID list limits detection to selected pattern families.

The strategy now supports a hybrid gate:
- strict pattern OR simple reversal, controlled by config.

---

## 10) Practical Configuration Profiles

### Profile A: Strict (higher precision, fewer trades)

```yaml
require_liquidity: true
require_reversal_pattern: true
enable_simple_reversal: false
touch_tolerance_pct: 0.0
touch_confirmation_bars: 0
ob_body_ratio: 0.35
```

### Profile B: Balanced (captures cleaner missed retests)

```yaml
require_liquidity: false
require_reversal_pattern: true
enable_simple_reversal: true
simple_reversal_wick_ratio: 0.20
touch_tolerance_pct: 0.05
touch_confirmation_bars: 2
ob_body_ratio: 0.30
```

---

## 11) Re-Implementation Blueprint (Language Agnostic)

If you re-implement this strategy elsewhere, follow this exact order:

1. Load OHLCV data and convert to numeric arrays.
2. Precompute CHoCH, BOS, OB, EQH/EQL events.
3. Filter OB by body/range threshold.
4. Build event queues by `break_index` and `confirmed_index`.
5. Iterate bars forward in time:
   - Activate OBs.
   - Update OB liquidity flags.
   - Invalidate mitigated OBs.
   - Track touches and touch windows.
   - Evaluate entry gates (liquidity + reversal).
   - Create signal and mark OB triggered.
6. Deduplicate by `(entry_time, trade_type)`.
7. Simulate exits in a separate module (if needed) starting from next bar.

---

## 12) Known Constraints

1. This strategy is OB-centric. If OB detection is too strict, entries drop sharply.
2. Reversal pattern strictness can miss visually good setups; `enable_simple_reversal` is the intended fallback.
3. Removing `require_liquidity` increases coverage but can also increase false positives.
4. Touch tolerance and touch window improve practical capture but must be tuned by timeframe and symbol volatility.

---

## 13) Minimal Pseudocode

```text
signals = []
active_obs = []

precompute choch, bos, ob, eqhl
build obs_by_break and eqhl_by_confirm

for each bar i:
    activate obs_by_break[i]

    for each eqhl event at i:
        update ob.has_liquidity for active OBs (directional and post-break only)

    next_active = []
    for each ob in active_obs:
        if mitigated(ob, bar i):
            continue

        touched = touches_ob_with_tolerance(ob, bar i)
        if touched:
            ob.last_touch_index = i

        if entry_window_active(ob, i):
            liquidity_ok = ob.has_liquidity or !require_liquidity
            pattern_ok = strict_pattern(ob.bias, i)
            simple_ok = enable_simple_reversal and simple_reversal(ob.bias, i)
            reversal_ok = (pattern_ok or simple_ok) if require_reversal_pattern else True

            if !ob.entry_triggered and liquidity_ok and reversal_ok:
                signal = create_signal(ob, bar i)
                if signal is valid:
                    signals.append(signal)
                    ob.entry_triggered = True

        next_active.append(ob)

    active_obs = next_active

signals = dedup_by(entry_time, trade_type)
return signals
```

---

## 14) Final Notes

This document reflects the current behavior in `src/strategy/smc_strategy.py` and related execution wrappers at the time of writing.

If you change config defaults or gating logic later, update this file in the same commit so documentation always matches runtime behavior.
