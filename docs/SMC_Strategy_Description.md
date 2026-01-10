# Smart Money Concepts (SMC) Trading Strategy - Technical Documentation

## Overview

This document describes the **Smart Money Concepts (SMC)** trading strategy implemented in PineScript. SMC is a technical analysis methodology that focuses on tracking institutional trader behavior (banks, hedge funds, market makers) through market structure analysis.

---

## 1. Market Structure

### 1.1 Swing Structure (Higher Timeframe)

| Term | Definition | Calculation |
|------|------------|-------------|
| **Swing High** | Confirmed peak when price creates highest high within N bars | `high[size] > ta.highest(size)` |
| **Swing Low** | Confirmed trough when price creates lowest low within N bars | `low[size] < ta.lowest(size)` |
| **Default Length** | 50 bars | Configurable via `swingsLengthInput` |

**Swing Patterns:**

| Pattern | Name | Meaning | Trend Indication |
|---------|------|---------|------------------|
| **HH** | Higher High | Current high > Previous high | Bullish continuation |
| **HL** | Higher Low | Current low > Previous low | Bullish continuation |
| **LH** | Lower High | Current high < Previous high | Bearish continuation |
| **LL** | Lower Low | Current low < Previous low | Bearish continuation |

### 1.2 Internal Structure (Lower Timeframe)

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Pivot Length** | 5 bars | More sensitive detection |
| **Purpose** | Early trend change detection | Identifies minor structure shifts |
| **Confluence Filter** | Optional | Filters based on candle body vs wick ratio |

---

## 2. Structure Signals

### 2.1 BOS (Break of Structure)

| Aspect | Description |
|--------|-------------|
| **Definition** | Price breaks swing point IN THE SAME DIRECTION as current trend |
| **Bullish BOS** | In uptrend: `close` crosses above previous swing high |
| **Bearish BOS** | In downtrend: `close` crosses below previous swing low |
| **Significance** | CONFIRMS trend continuation |

### 2.2 CHoCH (Change of Character)

| Aspect | Description |
|--------|-------------|
| **Definition** | Price breaks swing point OPPOSITE to current trend direction |
| **Bullish CHoCH** | In downtrend: `close` crosses above swing high → Reversal UP |
| **Bearish CHoCH** | In uptrend: `close` crosses below swing low → Reversal DOWN |
| **Significance** | SIGNALS potential trend reversal |

### Detection Logic:

```
IF close CROSSES_OVER swing_high AND swing_high NOT previously crossed:
    IF current_trend == BEARISH → Signal = "CHoCH" (Reversal)
    ELSE → Signal = "BOS" (Continuation)

IF close CROSSES_UNDER swing_low AND swing_low NOT previously crossed:
    IF current_trend == BULLISH → Signal = "CHoCH" (Reversal)
    ELSE → Signal = "BOS" (Continuation)
```

---

## 3. Order Blocks (OB)

### 3.1 Definition

Order Blocks represent the **last candle before an impulsive price move**, indicating zones where institutional orders were accumulated.

### 3.2 Order Block Types

| Type | Trigger | Identification |
|------|---------|----------------|
| **Bullish OB** | Bullish BOS or CHoCH | Bar with lowest `parsed_low` in pivot range |
| **Bearish OB** | Bearish BOS or CHoCH | Bar with highest `parsed_high` in pivot range |

### 3.3 Volatility Filter

| Filter Method | Formula | Purpose |
|---------------|---------|---------|
| **ATR** | `ta.atr(200)` | Standard volatility measure |
| **Cumulative Mean Range** | `ta.cum(ta.tr) / bar_index` | Better for limited data |
| **High Volatility Bar** | `(high - low) >= 2 * volatility_measure` | Inverts parsed high/low |

```
IF high_volatility_bar:
    parsed_high = low (inverted)
    parsed_low = high (inverted)
```

### 3.4 Order Block Mitigation

| OB Type | Mitigation Condition | Result |
|---------|---------------------|--------|
| **Bullish OB** | `mitigation_source < OB.low` | OB removed (filled) |
| **Bearish OB** | `mitigation_source > OB.high` | OB removed (filled) |

**Mitigation Source Options:**
- `Close` - More conservative
- `High/Low` - More aggressive

---

## 4. Fair Value Gaps (FVG)

### 4.1 Definition

FVG is a price gap between 3 consecutive candles where the middle candle moves impulsively, creating an imbalance in price structure.

### 4.2 FVG Detection

| Type | Conditions | Gap Zone |
|------|------------|----------|
| **Bullish FVG** | `current_low > high[2]` AND `close[1] > high[2]` AND `bar_delta% > threshold` | From `high[2]` to `low[0]` |
| **Bearish FVG** | `current_high < low[2]` AND `close[1] < low[2]` AND `-bar_delta% > threshold` | From `low[2]` to `high[0]` |

### 4.3 Auto Threshold Calculation

```
bar_delta_percent = (close[1] - open[1]) / (open[1] * 100)
threshold = cumulative_average(|bar_delta_percent|) * 2
```

### 4.4 FVG Mitigation

| FVG Type | Fill Condition |
|----------|----------------|
| **Bullish FVG** | `low < FVG.bottom` |
| **Bearish FVG** | `high > FVG.top` |

---

## 5. Equal Highs/Lows (EQH/EQL)

### 5.1 Definition

Price levels where multiple swing highs or lows form at **nearly identical prices**, often representing liquidity pools.

### 5.2 Detection Formula

| Type | Formula | Default Threshold |
|------|---------|-------------------|
| **Equal High (EQH)** | `|prev_swing_high - curr_swing_high| < threshold * ATR` | 0.1 (10% of ATR) |
| **Equal Low (EQL)** | `|prev_swing_low - curr_swing_low| < threshold * ATR` | 0.1 (10% of ATR) |

### 5.3 Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `equalHighsLowsLengthInput` | 3 | Bars for confirmation |
| `equalHighsLowsThresholdInput` | 0.1 | Sensitivity (0-0.5 range) |

---

## 6. Premium/Discount Zones

### 6.1 Zone Definitions

| Zone | Price Range | Trading Implication |
|------|-------------|---------------------|
| **Premium** | `range_high` to `(0.95 * high + 0.05 * low)` | Sell zone (overvalued) |
| **Equilibrium** | `(0.525 * high + 0.475 * low)` to `(0.475 * high + 0.525 * low)` | Fair value (50% level) |
| **Discount** | `(0.05 * high + 0.95 * low)` to `range_low` | Buy zone (undervalued) |

### 6.2 Calculation Base

```
range_high = trailing_swing_high (updated on each new high)
range_low = trailing_swing_low (updated on each new low)
```

---

## 7. Strong/Weak Highs & Lows

### 7.1 Classification Logic

| Level Type | Condition | Interpretation |
|------------|-----------|----------------|
| **Strong High** | `swing_trend == BEARISH` | Difficult to break (protected) |
| **Weak High** | `swing_trend == BULLISH` | Likely to be broken (target) |
| **Strong Low** | `swing_trend == BULLISH` | Difficult to break (protected) |
| **Weak Low** | `swing_trend == BEARISH` | Likely to be broken (target) |

---

## 8. Multi-Timeframe Levels

| Timeframe | Label | Description |
|-----------|-------|-------------|
| **Daily (D)** | PDH/PDL | Previous Day High/Low |
| **Weekly (W)** | PWH/PWL | Previous Week High/Low |
| **Monthly (M)** | PMH/PML | Previous Month High/Low |

---

## 9. Trading Logic Summary

### 9.1 Long Setup Conditions

| # | Condition | Description |
|---|-----------|-------------|
| 1 | `swing_trend == BULLISH` OR `CHoCH_bullish` | Uptrend or bullish reversal |
| 2 | `price_in_discount_zone` | Price at lower range |
| 3 | `price_near_bullish_OB` | Near unfilled bullish order block |
| 4 | `bullish_FVG_unfilled` | Bullish imbalance exists |
| 5 | `near_strong_low` | Protected support level |

### 9.2 Short Setup Conditions

| # | Condition | Description |
|---|-----------|-------------|
| 1 | `swing_trend == BEARISH` OR `CHoCH_bearish` | Downtrend or bearish reversal |
| 2 | `price_in_premium_zone` | Price at upper range |
| 3 | `price_near_bearish_OB` | Near unfilled bearish order block |
| 4 | `bearish_FVG_unfilled` | Bearish imbalance exists |
| 5 | `near_strong_high` | Protected resistance level |

---

## 10. Configuration Parameters

### 10.1 Structure Settings

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `swingsLengthInput` | 50 | 10+ | Bars for swing point detection |
| `internalStructureSize` | 5 | Fixed | Bars for internal structure |
| `internalFilterConfluenceInput` | false | bool | Filter by candle body/wick ratio |

### 10.2 Order Block Settings

| Parameter | Default | Options | Description |
|-----------|---------|---------|-------------|
| `orderBlockFilterInput` | ATR | ATR, Cumulative Mean Range | Volatility filter method |
| `orderBlockMitigationInput` | High/Low | Close, High/Low | Mitigation detection source |
| `internalOrderBlocksSizeInput` | 5 | 1-20 | Max internal OBs displayed |
| `swingOrderBlocksSizeInput` | 5 | 1-20 | Max swing OBs displayed |

### 10.3 Equal Highs/Lows Settings

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `equalHighsLowsLengthInput` | 3 | 1+ | Confirmation bars |
| `equalHighsLowsThresholdInput` | 0.1 | 0-0.5 | Detection sensitivity |

### 10.4 Fair Value Gap Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fairValueGapsThresholdInput` | true | Auto threshold enabled |
| `fairValueGapsTimeframeInput` | Current | FVG detection timeframe |
| `fairValueGapsExtendInput` | 1 | Bars to extend FVG display |

---

## 11. Alert Conditions

### 11.1 Structure Alerts

| Alert Name | Trigger Condition |
|------------|-------------------|
| `Internal Bullish BOS` | Internal structure bullish break of structure |
| `Internal Bearish BOS` | Internal structure bearish break of structure |
| `Internal Bullish CHoCH` | Internal structure bullish change of character |
| `Internal Bearish CHoCH` | Internal structure bearish change of character |
| `Bullish BOS` | Swing structure bullish break of structure |
| `Bearish BOS` | Swing structure bearish break of structure |
| `Bullish CHoCH` | Swing structure bullish change of character |
| `Bearish CHoCH` | Swing structure bearish change of character |

### 11.2 Order Block Alerts

| Alert Name | Trigger Condition |
|------------|-------------------|
| `Bullish Internal OB Breakout` | Price mitigates bullish internal order block |
| `Bearish Internal OB Breakout` | Price mitigates bearish internal order block |
| `Bullish Swing OB Breakout` | Price mitigates bullish swing order block |
| `Bearish Swing OB Breakout` | Price mitigates bearish swing order block |

### 11.3 Other Alerts

| Alert Name | Trigger Condition |
|------------|-------------------|
| `Equal Highs` | Equal highs pattern detected |
| `Equal Lows` | Equal lows pattern detected |
| `Bullish FVG` | Bullish fair value gap formed |
| `Bearish FVG` | Bearish fair value gap formed |

---

## 12. Visual Display Options

| Feature | Options | Default |
|---------|---------|---------|
| **Mode** | Historical, Present | Historical |
| **Style** | Colored, Monochrome | Colored |
| **Color Candles** | true/false | false |
| **Label Sizes** | Tiny, Small, Normal | Varies |
| **Line Styles** | Solid, Dashed, Dotted | Varies |

---

## 13. Data Structures (UDT)

### 13.1 Core Types

| Type | Fields | Purpose |
|------|--------|---------|
| `pivot` | currentLevel, lastLevel, crossed, barTime, barIndex | Stores swing point data |
| `trend` | bias (BULLISH/BEARISH) | Tracks current trend direction |
| `orderBlock` | barHigh, barLow, barTime, bias | Stores order block data |
| `fairValueGap` | top, bottom, bias, topBox, bottomBox | Stores FVG data |
| `trailingExtremes` | top, bottom, barTime, barIndex, lastTopTime, lastBottomTime | Tracks running highs/lows |
| `alerts` | Multiple boolean fields | Stores alert states |

---

## 14. Implementation Notes

1. **Execution Order**: Internal structure is calculated with 5-bar lookback, swing structure with configurable lookback (default 50)

2. **Real-time Updates**: Order blocks and FVGs are continuously monitored for mitigation

3. **Memory Management**: Arrays store historical data; order blocks limited to 100 entries

4. **Multi-timeframe**: HTF levels only displayed when chart timeframe is lower than requested level

---

## License

This strategy is based on LuxAlgo's Smart Money Concepts indicator, licensed under Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0).
