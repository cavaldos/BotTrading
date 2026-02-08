# Indicator Implementation Guide

## Overview

This document provides a comprehensive guide for implementing new indicators in the project. It consolidates lessons learned from previous implementations (BOS, CHoCH) and establishes mandatory rules to prevent recurring bugs — particularly time misalignment and logic errors when porting from Pine Script to Python.

## Why This Guide Exists

During early development, several indicators were ported from Pine Script. Some AI models (and developers) copied the BOS/CHoCH logic verbatim into unrelated indicators — carrying over SMC-specific concepts (pivot/leg, trend_bias, crossover/crossunder) into indicators that have completely different logic. This created two categories of problems:

- Indicators contaminated with irrelevant logic — e.g., an RSI-based indicator unnecessarily using leg, prev_leg, trend_bias because the model copied from BOS.py as a template.
- Actual shared rules being ignored — e.g., timestamp conversion (which applies to ALL indicators) was skipped because it was buried among SMC-specific rules.

This guide separates what is universal (applies to every indicator) from what is specific (applies only to SMC-style structure indicators).

---

## Part 1 — Universal Rules (ALL indicators)

These rules apply to every indicator, regardless of type.

### 1.1 Timestamp Handling

Every timestamp sent to the chart must come from the candle DataFrame's Time column and use the same conversion as export_candles.

```python
# In the indicator's to_json function:
def to_json(events, times: np.ndarray = None):
    for event in events:
        event['time'] = int(pd.Timestamp(times[event['index']]).timestamp())
    return events

# In main.py — ALWAYS pass times:
results['my_indicator'] = my_indicator_to_json(events, times=df['Time'].values)
```

**If violated:** Lines/zones shift by one or more bars on the chart.

### 1.2 JSON Export Structure

Every indicator that draws on the chart must include:

| Field | Required | Description |
|-------|----------|-------------|
| Time fields (startTime, endTime, breakTime, etc.) | ✅ Always | Unix timestamp from candle Time column |
| Price/level fields (level, high, low, etc.) | ✅ When applicable | The price value to draw at |
| lineStyle | When applicable | 'solid', 'dashed', etc. |
| type / direction | When applicable | Label for frontend rendering |

### 1.3 Function Signature Convention

```python
# Every indicator's to_json MUST accept times
def to_json(results, times: np.ndarray = None) -> list[dict]:
    ...
```

### 1.4 Checklist (Universal)

| # | Item |
|---|------|
| 1 | to_json accepts times parameter |
| 2 | All timestamps use pd.Timestamp(times[idx]).timestamp() |
| 3 | main.py passes times=df['Time'].values |
| 4 | JSON output includes all fields the frontend expects |

---

## Part 2 — SMC Structure Indicators Only (BOS, CHoCH, and similar)

These rules apply only to indicators that detect market structure breaks using swing pivots — such as BOS (Break of Structure), CHoCH (Change of Character), or any new indicator that identifies structure shifts via pivot highs/lows.

⚠️ **Do NOT apply these rules to non-SMC indicators** (e.g., RSI, MACD, Bollinger Bands, Volume Profile, etc.). If your indicator does not use pivots, legs, or structure breaks, skip this entire section.

### 2.1 Pivot / Leg Initialization

```python
BEARISH_LEG = 0
BULLISH_LEG = 1

leg = BEARISH_LEG       # Always start at 0, never -1
prev_leg = leg
```

Update the pivot only when the leg changes:

```python
if leg != prev_leg:
    # Update pivot point
    pivot_high = highs[k]   # k = i - size
    pivot_low = lows[k]
```

Pine equivalents:
- `ta.highest(size)` → `max(highs[k+1], ..., highs[i])` — exactly size bars including current
- `ta.lowest(size)` → `min(lows[k+1], ..., lows[i])`

### 2.2 BOS vs CHoCH Classification

| Event | Condition | Meaning |
|-------|-----------|---------|
| BOS | Break is in the same direction as current trend (or trend is neutral) | Continuation |
| CHoCH | Break is against the current trend | Reversal |

```python
# Bullish break
if trend_bias == BULLISH or trend_bias == NEUTRAL:
    emit_bos(direction='bullish')
elif trend_bias == BEARISH:
    emit_choch(direction='bullish')

# Bearish break
if trend_bias == BEARISH or trend_bias == NEUTRAL:
    emit_bos(direction='bearish')
elif trend_bias == BULLISH:
    emit_choch(direction='bearish')

# ALWAYS update trend_bias after handling the break
trend_bias = BULLISH  # or BEARISH based on the break direction
```

### 2.3 Crossover / Crossunder

Use the exact Pine-equivalent conditions:

```python
# Crossover: price crosses ABOVE level
if prev_close <= level and close_val > level:
    ...

# Crossunder: price crosses BELOW level
if prev_close >= level and close_val < level:
    ...
```

- `prev_close` = close of the previous bar (saved from prior iteration)
- `close_val` = close of the current bar

### 2.4 Swing + Internal (Dual Stream)

When Pine has two streams (Swing structure + Internal structure):

1. Run the detection function twice with different size parameters
2. Each stream has its own independent state (pivot, trend, crossed) — never shared
3. Merge results and sort by break_index before returning

```python
swing_events = detect(highs, lows, closes, size=swing_size)
internal_events = detect(highs, lows, closes, size=internal_size)

all_events = swing_events + internal_events
all_events.sort(key=lambda e: e['break_index'])
```

### 2.5 Checklist (SMC-specific)

| # | Item |
|---|------|
| 1 | leg initialized to 0 (BEARISH_LEG), not -1 |
| 2 | Pivot updates only when leg != prev_leg |
| 3 | BOS emitted for same-direction breaks; CHoCH for opposite-direction |
| 4 | trend_bias updated after every break, even if no event emitted |
| 5 | Crossover uses prev_close <= level and close > level |
| 6 | Crossunder uses prev_close >= level and close < level |
| 7 | Dual streams (Swing + Internal) use independent state |

---

## Part 3 — Common Mistakes When Using AI Models

When asking an AI model to implement a new indicator, the following mistakes have been observed:

### ❌ Mistake 1: Copying BOS/CHoCH as a template for everything

**Symptom:** A volume-based indicator contains leg, prev_leg, trend_bias, crossover logic that makes no sense for volume analysis.

**Fix:** Only reference BOS/CHoCH when the new indicator is also an SMC structure indicator. For other types, start from scratch or use a minimal template that only includes the universal rules (Part 1).

### ❌ Mistake 2: Skipping timestamp conversion

**Symptom:** The indicator works in backtesting but lines appear shifted on the live chart.

**Fix:** Always follow Section 1.1. This is non-negotiable for every indicator.

### ❌ Mistake 3: Sharing state between independent streams

**Symptom:** Enabling both Swing and Internal produces different results than enabling them separately.

**Fix:** Follow Section 2.4. Each stream is a completely independent computation.

### ❌ Mistake 4: Using < instead of <= in crossover

**Symptom:** One or two signals are missing compared to Pine Script output.

**Fix:** Follow Section 2.3. The = in <= / >= matters.

---

## Part 4 — How to Add a New Indicator (Step by Step)

### Step 1: Classify the indicator

- SMC structure indicator (detects pivots, structure breaks) → Use Part 1 + Part 2
- Everything else (oscillators, overlays, volume, etc.) → Use Part 1 only

### Step 2: Implement the core logic

- Port from Pine Script or write from scratch
- If porting from Pine, refer to `docs/strategy/pine_to_python_indicators.md` for translation patterns

### Step 3: Implement to_json

```python
def to_json(results, times: np.ndarray = None) -> list[dict]:
    output = []
    for r in results:
        entry = {
            'time': int(pd.Timestamp(times[r['index']]).timestamp()),
            'value': r['value'],
            # ... other fields the frontend needs
        }
        output.append(entry)
    return output
```

### Step 4: Register in main.py

```python
results['new_indicator'] = new_indicator_to_json(
    events,
    times=df['Time'].values
)
```

### Step 5: Verify with checklist

1. Run the universal checklist (Section 1.4)
2. If SMC-type, also run the SMC checklist (Section 2.5)
3. Compare output with Pine Script (if porting) on the same dataset

---

## Reference Files

| Resource | Path |
|----------|------|
| Pine → Python porting guide | `docs/strategy/pine_to_python_indicators.md` |
| BOS implementation (reference) | `src/indicators/BOS.py` |
| CHoCH implementation (reference) | `src/indicators/CHOCH.py` |
| This guide | `docs/strategy/indicator_implementation_guide.md` |
