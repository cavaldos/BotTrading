# Indicator implementation rules (avoid known bugs)

When adding or porting a new indicator (from Pine or written from scratch), **you must** follow the rules below to avoid logic errors and time misalignment with the chart.

---

## 1. Timestamps — mandatory

### Rule

- **Every time** sent to the chart (breakTime, pivotTime, startTime, endTime, …) **must** come from the **same `Time` column of the candle DataFrame** and use the **same conversion** as candle export.

### How to do it

1. The indicator’s `to_json` function **must** accept an optional `times` argument (e.g. `times: np.ndarray = None`).
2. When `times` is provided, use the same formula as `export_candles`:
   ```python
   break_ts = int(pd.Timestamp(times[break_index]).timestamp())
   pivot_ts = int(pd.Timestamp(times[pivot_index]).timestamp())
   ```
3. In `main.py`, when calling `to_json` **always** pass `times=df['Time'].values`:
   ```python
   results['indicator_name'] = indicator_to_json(events, times=df['Time'].values)
   ```

### What goes wrong if you don’t

- Lines/zones are **shifted by one or more bars** on the time axis (time misalignment).

---

## 2. Pivot / leg (when using SMC-style logic like BOS/CHoCH)

### Rule

- **Leg:** Initialize `leg = BEARISH_LEG` (0); **do not** use -1 or “uninitialized”.
- **Update pivot:** Only when **leg changes** from the previous bar: `if leg != prev_leg:` (equivalent to `ta.change(currentLeg) != 0` in Pine).
- **Pivot bar:** When processing bar `i`, the pivot is at bar `k = i - size`; use `highs[k]`/`lows[k]` and compare with `max(highs[k+1..i])` / `min(lows[k+1..i])`.

### How to do it

- `ta.highest(size)` → `max(highs[k+1], ..., highs[i])` (exactly `size` bars, **including** current bar).
- `ta.lowest(size)` → `min(lows[k+1], ..., lows[i])`.
- After computing the new leg, set `prev_leg = leg` (before assigning the new value to `leg`), then assign `leg`; use `leg != prev_leg` to decide whether to update the pivot.

### What goes wrong if you don’t

- First pivot missing or pivots on wrong bars vs Pine; BOS/CHoCH appear in wrong places.

---

## 3. BOS vs CHoCH — when to emit events

### Rule

- **BOS:** Emit only when the break is **in the same direction** as the trend (or trend is 0):  
  bullish break when `trend_bias == BULLISH or trend_bias == NEUTRAL`, bearish break when `trend_bias == BEARISH or trend_bias == NEUTRAL`.
- **CHoCH:** Emit only when the break is **against** the trend (reversal):  
  bullish break when `trend_bias == BEARISH`, bearish break when `trend_bias == BULLISH`.
- **Always** update `trend_bias` (BULLISH/BEARISH) **after** each crossover/crossunder handling, whether or not you emitted an event.

### What goes wrong if you don’t

- BOS/CHoCH mixed up (continuation vs reversal); event count and positions don’t match Pine.

---

## 4. Swing and Internal (two streams)

### Rule

- If Pine has **two streams** (swing + internal): in Python run the detect function **twice** (two JIT calls) with two different `size` values; **do not** share state (pivot, trend, crossed) between streams.
- Merge results (append) then **sort by `break_index`** (or equivalent time field) before returning.

### What goes wrong if you don’t

- Results differ from Pine when both Swing and Internal are enabled.

---

## 5. Crossover / Crossunder

### Rule

- Use the **exact** conditions:
  - **Crossover(close, level):** `prev_close <= level and close_val > level`.
  - **Crossunder(close, level):** `prev_close >= level and close_val < level`.
- **prev_close** = close of the previous bar (saved from the previous loop iteration); **close_val** = close of the current bar.

### What goes wrong if you don’t

- Events fire on the wrong bar or don’t fire when Pine shows a signal.

---

## 6. JSON export for the chart

### Rule

- For indicators that draw **lines** or **zones** over time: the object sent to the chart **must** include `pivotTime`/`breakTime` or `startTime`/`endTime` (names depend on frontend) and **level**/price when needed.
- If the frontend uses `lineStyle`: also send `lineStyle` (e.g. `'solid'`, `'dashed'`) in the JSON so the chart draws the correct line style.

---

## 7. Quick checklist when adding a new indicator

Before considering an indicator “done”, verify:

| # | Item | ✓ |
|---|------|---|
| 1 | `to_json(..., times=df['Time'].values)` and use `pd.Timestamp(times[idx]).timestamp()` for every time sent to the chart | |
| 2 | If using leg/pivot: `leg = 0` initially; update pivot only when `leg != prev_leg` | |
| 3 | If BOS: emit only when trend is same direction (or 0); if CHoCH: emit only when trend is opposite direction | |
| 4 | After every break (crossover/crossunder), update `trend_bias` | |
| 5 | If Swing + Internal: run 2 independent streams, merge then sort by break_index | |
| 6 | Crossover/Crossunder use correct `prev_close` and current bar `close` | |
| 7 | In `main.py` always pass `times=df['Time'].values` when calling that indicator’s `to_json` | |

---

## 8. Related docs

- **Pine → Python port details:** `docs/strategy/pine_to_python_indicators.md`
- **Reference code:** `src/indicators/BOS.py`, `src/indicators/CHOCH.py`

When implementing a new indicator, use this file as the standard and checklist, and refer to BOS/CHoCH to avoid repeating the same bugs.
