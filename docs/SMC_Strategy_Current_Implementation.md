# SMC Strategy (Current Python Implementation)

## 1) Purpose and Scope

Technical specification of the **current strategy logic** in `src/strategy/smc/strategy.py`.

Use it to:
- Understand exactly why a signal is or is not generated.
- Re-implement the same behavior in another codebase or language.

Python implementation only (not old Pine variants). Last verified against the code on 2026-10-23.

---

## 2) Source of Truth

| Part | File |
|---|---|
| Strategy | `src/strategy/smc/strategy.py` |
| Shared SL/TP, mitigation, touch, dedup | `src/strategy/base.py` |
| Reversal pattern engine | `src/indicators/reversal_patterns.py` |
| Config loading (unknown YAML keys are dropped silently) | `src/config/load_config.py`, `src/strategy/config_factory.py` |
| Trade simulation and reports | `src/backtest/statistics.py` |
| Live signal generation / scheduling | `src/live/core/execute.py`, `src/live/run.py` |
| Live order placement (market order) | `src/live/core/send.py`, `src/live/core/place_order.py` |

---

## 3) High-Level Idea

A causal SMC engine built on:
- Market structure events (CHoCH, BOS)
- Order Blocks (OB) activated by those breaks
- Liquidity (EQH/EQL) confirmed after the break
- Optional confluence/filters: FVG/IFVG, ADX, higher-timeframe EMA trend, opposite-side target
- Reversal confirmation on the candle that retests the OB

Core flow:
1. Detect structure, OB, EQH/EQL, FVG/IFVG, swings once on the signal timeframe.
2. Activate each event only at the bar where it becomes known.
3. Wait for the OB retest.
4. Check filters and the reversal candle.
5. Enter at the **close** of the confirmation candle; SL/TP from the OB and the TP mode.

---

## 4) Modes

| Mode | When | Indicators on | Confirmation candle |
|---|---|---|---|
| Multi-timeframe (normal) | `df_entry` given (signal TF ≠ entry TF) | signal TF (e.g. 2m/3m/5m) | entry TF (1m) candles **after** the signal bar closes |
| Single-timeframe | no `df_entry` | same TF | the signal bar itself |

`slice_window > 0` keeps only state from the last N signal bars (indicators are still computed once on all data).

---

## 5) Config (main groups)

1. Structure: `swing_length`, `internal_length`
2. Liquidity detection: `eqhl_length`, `eqhl_threshold`
3. Order block: `mitigation_mode` (`close` | `high_low`; old key `ob_mitigation` is aliased), `ob_body_ratio`, `max_active_obs_per_bias`, `merge_overlapping_obs`, `ob_merge_max_break_gap`, `entry_ob_level`
4. Entry filters: `require_liquidity`, `require_opposite_liquidity_target`, `gap_filter_mode` (`none|fvg|ifvg|both`), `fvg_atr_filter` (auto-threshold for FVG/IFVG detection), `enable_adx_filter` / `adx_length` / `adx_threshold`, `trend_filter_timeframe` / `trend_filter_ema_length`, `block_choch_against_entry_mode` (`off|ifvg`), `touch_tolerance_pct`, `touch_confirmation_bars`
5. Reversal: `require_reversal_pattern`, `reversal_pattern_ids`, `min_reversal_pattern_matches`, `doji_body_ratio`, `tweezer_tolerance_ratio`, `enable_simple_reversal`, `simple_reversal_wick_ratio`
6. Risk: `risk_reward_ratio`, `sl_buffer_pct`, `sl_multiplier`, `min_sl_pct`, `risk_per_trade`, `fee_per_trade_r` (report only)
7. Take profit: `tp_mode`, `tp_no_target_policy`, `tp_swing_length`, `flex_tp_risk_boost_factor`

`entry_on_candle_close` exists in `BaseConfig` but SMC ignores it: entries always fill at the confirmation candle close.

---

## 6) Signal Generation Algorithm

### Step A: Precompute
- OHLC arrays (`float64`), times.
- `detect_choch`, `detect_bos`, `detect_order_blocks`, `detect_eqh_eql`, optional `detect_fvg` / `detect_ifvg`, optional `detect_swings` (TP), optional ADX, optional HTF EMA (built from the entry-TF data by resampling).

### Step B: Event queues
- **OB** (by `break_index`):
  1. Keep only if origin candle `abs(close-open)/(high-low) > ob_body_ratio`.
  2. Classify as `CHoCH` if `(break_index, bias)` matches a CHoCH, otherwise `BOS`.
  3. Optionally merge same-bias OBs that overlap and whose `break_index` differ by ≤ `ob_merge_max_break_gap` (union zone, activated on the latest break; CHoCH wins the label).
- **EQH/EQL** by `confirmed_index`.
- **FVG/IFVG** by detection index.
- **Swings** by `index + tp_swing_length` (confirmation bar).

### Step C: Bar-by-bar loop (signal TF bar `i`)
1. Activate OBs with `break_index == i`; keep at most `max_active_obs_per_bias` per bias (newest).
2. Activate gap zones for bar `i`; drop gap zones mitigated by bar `i`.
3. EQH/EQL confirmed at `i` → for active OBs without liquidity, with `confirmed_index > break_index`:
   - Bullish OB accepts **EQH**, bearish OB accepts **EQL**.
   - Store `liquidity_confirmed_index`.
4. Register confirmed swings / EQH / EQL levels for TP targets.
5. For each active OB:
   - **Mitigation** (removes the OB):
     - `close`: bullish if `close < ob.low`, bearish if `close > ob.high`
     - `high_low`: bullish if `low < ob.low`, bearish if `high > ob.high`
   - **Touch**: `low <= ob.high + tol and high >= ob.low - tol`, `tol = (ob.high-ob.low) * touch_tolerance_pct`. On touch, `last_touch_index = i`.
   - **Entry window**: `touches_ob` if `touch_confirmation_bars == 0`, otherwise `i - last_touch_index <= touch_confirmation_bars`.
   - If the window is active and the OB has not triggered yet, check in order:
     1. ADX ≥ `adx_threshold` (if enabled)
     2. `liquidity_ok`: not required, or `has_liquidity and liquidity_confirmed_index < i`
     3. `gap_ok`: an active same-bias FVG/IFVG of the selected type exists (if enabled)
     4. Find the confirmation candle (Step D)
     5. HTF trend filter at the decision time (if enabled)
     6. Opposite-side target exists (if required): opposite OB, EQH (LONG) / EQL (SHORT), or opposite FVG/IFVG beyond the entry and beyond the current bar
     7. Build the signal (Step E); reject if the CHoCH safeguard fires
   - On success: append the signal, set `entry_triggered = True`. An OB trades at most once.

### Step D: Confirmation candle

`entry_level = ob.low + (ob.high - ob.low) * entry_ob_level`

**Multi-timeframe**: scan entry-TF candles from the signal bar close for up to 60 candles:
1. If the candle **mitigates** the OB (same rule as above) → stop, no entry.
2. It must touch the zone (with tolerance), trade through `entry_level`, and overlap the zone.
3. It must pass the reversal filter (Section 8).
4. First match wins.

**Single-timeframe**: bar `i` must trade through `entry_level` and pass the reversal filter.

Entry price = **close of the confirmation candle**. `entry_time` = open time of that candle; the simulator checks SL/TP starting with the next candle. This matches live, which sends a market order once the candle has closed.

### Step E: HTF trend filter (optional)
- Resample the entry-TF data to `trend_filter_timeframe`, EMA(`trend_filter_ema_length`) on close.
- Decision time = close time of the confirmation candle. Use the last HTF candle whose close time ≤ decision time.
- LONG requires `close > EMA`, SHORT requires `close < EMA`. Not enough history → reject.

### Step F: CHoCH safeguard (optional, `block_choch_against_entry_mode: ifvg`)
Reject when the OB is CHoCH-based, the confirmation candle closes against the trade, and the newest same-bias gap zone is an IFVG. With the reversal filter on, the confirmation candle almost never closes against the trade, so this rarely fires.

### Step G: Deduplicate
Key `(entry_time, trade_type, signal_timeframe)`, keep the first.

---

## 7) SL / TP

Let `buffer = (ob.high - ob.low) * sl_buffer_pct`.

LONG:
```text
base_sl   = ob.low - buffer
stop_loss = entry - (entry - base_sl) * sl_multiplier
if stop_loss > ob.low: stop_loss = ob.low - buffer
take_profit (fixed) = entry + (entry - stop_loss) * risk_reward_ratio
```
SHORT: mirror with `ob.high`.

Guards:
- Reject LONG if `stop_loss >= entry`, SHORT if `stop_loss <= entry`.
- If `|entry - stop_loss| / entry * 100 < min_sl_pct`: move SL to exactly `min_sl_pct` away and recompute the fixed TP.

TP modes (`tp_mode`):
| Mode | Targets |
|---|---|
| `fixed_rr` | fixed TP only |
| `opposite_ob` | nearest-to-anchor opposite OB edge |
| `opposite_swing` | confirmed opposite swing |
| `opposite_ob_or_swing` | both |
| `htf_liquidity` | opposite OB, EQH/EQL, FVG/IFVG |
| `htf_liquidity_or_rr` | as above, fixed RR if none |

Selection: prefer targets with RR ≥ `risk_reward_ratio` (closest to it); otherwise the highest-RR target. If the structural RR is above the fixed RR, the **executed TP is capped at the fixed RR** and `risk_per_trade` is boosted by `1 + flex_tp_risk_boost_factor * (flex_rr - base_rr)`. No target → `tp_no_target_policy` (`skip` or `fixed_rr`).

Prices are rounded with `round_price(...)`.

---

## 8) Reversal Filter

```text
if not require_reversal_pattern: pass
elif count_reversal_patterns(candle) >= min_reversal_pattern_matches: pass   (source PATTERN)
elif enable_simple_reversal and simple_reversal(candle): pass                 (source SIMPLE)
else: fail
```

### 8.1 Pattern families (`reversal_pattern_ids`, `[0]` = all)

| ID | LONG | SHORT | Rule (summary) |
|---|---|---|---|
| 1 | doji | doji | body ≤ `doji_body_ratio` × range (default 0.1) |
| 2 | hammer | — | body < 30%, lower wick ≥ 60%, upper wick ≤ 10% |
| 3 | — | shooting star | body < 35%, upper wick ≥ 2×body and ≥ 50%, lower wick ≤ 10% |
| 4 | bullish engulfing | bearish engulfing | body engulfs previous opposite body |
| 6 | bullish harami | bearish harami | small body inside previous large opposite body |
| 7 | morning star | evening star | 3 candles, star body ≤ 30%, third closes past middle of first |
| 8 | tweezer bottom | tweezer top | opposite colors, lows/highs within `tweezer_tolerance_ratio` × avg range |
| 9 | three outside up | three outside down | engulfing + confirming close |
| 10 | bullish belt hold | bearish belt hold | opens at the extreme (≤ 5% wick), body ≥ 60% |

Hanging man (long lower wick for SHORT) and inverted hammer (long upper wick for LONG) are intentionally not used: at an OB those shapes show pressure against the trade.

Each family counts at most once per candle.

### 8.2 Simple reversal
- LONG: green candle and (`lower_wick / range >= simple_reversal_wick_ratio` **or** `close > previous high`)
- SHORT: red candle and (`upper_wick / range >= simple_reversal_wick_ratio` **or** `close < previous low`)

---

## 9) Causality

- OB activated at `break_index`; EQH/EQL at `confirmed_index`; swings at `index + length`; gaps at detection index.
- Liquidity must be confirmed on a bar **before** the entry bar.
- Multi-TF confirmation candles start after the signal bar closes.
- Entry fills at the confirmation candle close (the pattern is unknown before that).
- HTF trend uses only closed HTF candles.
- Live: `execute.py` drops still-forming candles on both entry TF and signal TF.

---

## 10) Backtest Notes

- `src/backtest/main.py` clears `output_folder` and appends a section to `report_folder/<backtest_results_file>.md`.
- Simulator: from the candle after `entry_time`, SL is checked before TP on each candle.
- `fee_per_trade_r` is a flat R cost. Real exchange fees in R depend on the SL width: `fee_R ≈ round_trip_fee_% / sl_%` (e.g. 0.11% taker round trip with a 0.18% SL ≈ 0.6R). Tight SLs make fees dominate.

---

## 11) Known Constraints

1. OB-centric: strict OB detection sharply reduces entries.
2. The multi-TF search reuses filter state (ADX, gap zones, liquidity) from the signal bar that started it for up to 60 entry-TF candles; only mitigation is re-checked per candle.
3. Removing `require_liquidity` adds many low-quality trades.
4. Touch tolerance / window must be tuned per timeframe and symbol volatility.
5. Unknown YAML keys are ignored silently; check spelling when adding config.

---

## 12) Minimal Pseudocode

```text
precompute choch, bos, ob, eqhl, gaps, swings, adx, htf_ema
build obs_by_break, eqhl_by_confirm, gaps_by_index, swings_by_confirm

for each signal bar i:
    activate obs/gaps/swings for i; prune
    for eqhl confirmed at i: set ob liquidity (bullish<-EQH, bearish<-EQL, post-break only)
    for ob in active_obs:
        if mitigated(ob, bar i): drop; continue
        update touch / entry window
        if window and not ob.entry_triggered
           and adx_ok and liquidity_ok(ob, i) and gap_ok(ob):
            candle = first entry-TF candle after bar i close that
                     (stops if it mitigates ob) trades through entry_level
                     and passes reversal filter          # single-TF: bar i itself
            if candle and trend_ok(candle close time) and opposite_target_exists(candle.close):
                signal = build(entry=candle.close, sl/tp from ob + tp_mode)
                if signal and not choch_safeguard(signal): emit; ob.entry_triggered = True
dedup by (entry_time, trade_type, signal_timeframe)
```

---

## 13) Final Notes

If you change defaults or gating logic, update this file in the same commit so the documentation keeps matching runtime behavior.
