"""
Verify Orders - replay test against look-ahead bias
===================================================

How it works
------------
1. Backtest: run the normal backtest (strategy + TradeSimulator, same as
   src/backtest/main.py) on a slice of 1m data -> "backtest orders".
2. Ticks: every 1m candle becomes N random prices inside [Low, High].
   First tick = Open, last tick = Close, High and Low sit at random places in
   between, so candles rebuilt from the ticks equal the original candles.
3. Replay: the ticks are played back like a live market. Each time a 1m candle
   closes, the strategy is called with ONLY the candles closed at that moment
   (the forming signal-TF candle is dropped, like live). An order is placed
   only if its entry candle is the candle that just closed.
4. Fill / exit: placed orders fill at the close tick, then exit on the first
   later tick that touches SL or TP.
5. Compare replay orders with backtest orders. All match -> no look-ahead.

Statuses
--------
MATCH        same order, same entry/SL/TP, same result
AMBIGUOUS    same order, result differs only because the exit 1m candle touched
             both SL and TP (backtest assumes SL first, here the ticks decide)
PRICE_DIFF   same order but entry/SL/TP differ -> prices used future data
RESULT_DIFF  same order and prices, result differs on a normal candle
MISSING      backtest order not placed on time in replay -> backtest used future
             data to create it (Late_Min = minutes until it first appeared)
EXTRA        placed in replay but not in backtest -> future data removed it
             (live would have traded it)

Signals depend only on closed candles. Rebuilt candles are checked to equal the
original ones for every seed, so the walk-forward (the slow part) runs once and
only fills/exits are re-run per seed.

Usage
-----
    # menu: pick a coin, then a timeframe (default folder res/verify: data/SYMBOL_1m.csv + res/SYMBOL_TF_trades.csv)
    python -m src.evaluate.verify_orders
    # no menu
    python -m src.evaluate.verify_orders --dir res/verify --symbol BTCUSDT --tf 5m
    python -m src.evaluate.verify_orders --dir res/verify --all
    # or run the backtest itself on a data slice, then check it
    python -m src.evaluate.verify_orders --symbol BTCUSDT --tf 5m
    python -m src.evaluate.verify_orders --symbol BTCUSDT --tf 5m --candles 0 --seeds 5
    python -m src.evaluate.verify_orders --data res/data --start "2026-10-01" --end "2026-10-10"
    python -m src.evaluate.verify_orders --symbol BTCUSDT --window 5000   # see only the last 5000 1m candles, like live
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numba import njit
from tqdm import tqdm

from src.backtest.statistics import Trade, TradeSimulator
from src.config import load_backtest_config, load_strategy_config
from src.config.load_config import load_yaml_config
from src.strategy import BaseSignal as Signal, build_strategy_config, create_strategy, parse_timeframe_minutes
from src.core.timeframe import convert_timeframe


DEFAULT_DATA_FOLDER = "res/data"
DEFAULT_REPORT_FOLDER = "res/report"
REPORT_NAME = "verify_orders"
DEFAULT_VERIFY_DIR = "res/verify"  # data/ + res/ ; used when no data source is given
LIVE_MAX_ORDER_AGE_MINUTES = 10  # src/live/core/send.py MAX_ORDER_AGE_MINUTES

FAIL_STATUSES = ("PRICE_DIFF", "RESULT_DIFF", "MISSING", "EXTRA")
ALL_STATUSES = ("MATCH", "AMBIGUOUS") + FAIL_STATUSES
RESULT_NAMES = {0: "LOSS", 1: "WIN", 2: "OPEN"}

# (trade_type, entry_time in ns, signal_timeframe)
SignalKey = tuple[str, int, str]


# ==============================================================================
# DATA
# ==============================================================================

def load_candles(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, skipinitialspace=True)
    df.columns = df.columns.str.strip()
    df["Time"] = pd.to_datetime(df["Time"])
    df = df.sort_values("Time").drop_duplicates("Time").reset_index(drop=True)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = df[col].astype(np.float64)
    return df[["Time", "Open", "High", "Low", "Close", "Volume"]]


def select_slice(
    df: pd.DataFrame,
    *,
    candles: int,
    start: str | None,
    end: str | None,
    timeframes_minutes: list[int],
    entry_minutes: int,
    align_end: bool = True,
) -> pd.DataFrame:
    """Pick the test range, then trim the end so the last candle closes every signal-TF bucket."""
    if start:
        df = df[df["Time"] >= pd.Timestamp(start)]
    if end:
        df = df[df["Time"] <= pd.Timestamp(end)]
    if not start and not end and candles > 0:
        df = df.tail(candles)
    df = df.reset_index(drop=True)
    if df.empty or not align_end:
        return df

    # A partial last bucket would be used by the backtest but dropped by the
    # replay (it is still forming), so end the slice on a bucket boundary.
    bucket = math.lcm(*timeframes_minutes)
    if 1440 % bucket == 0:
        close_minute = df["Time"].dt.hour * 60 + df["Time"].dt.minute + entry_minutes
        aligned = np.flatnonzero((close_minute % bucket == 0).to_numpy())
        if len(aligned):
            df = df.iloc[: aligned[-1] + 1].reset_index(drop=True)
    return df


def generate_ticks(df: pd.DataFrame, n_ticks: int, rng: np.random.Generator) -> np.ndarray:
    """N random prices per candle: Open first, Close last, High and Low somewhere in between."""
    if n_ticks < 4:
        raise ValueError("ticks must be >= 4 (Open, High, Low, Close)")
    o = df["Open"].to_numpy()
    h = df["High"].to_numpy()
    l = df["Low"].to_numpy()
    c = df["Close"].to_numpy()
    n = len(df)

    ticks = l[:, None] + rng.random((n, n_ticks)) * (h - l)[:, None]
    np.clip(ticks, l[:, None], h[:, None], out=ticks)

    rows = np.arange(n)
    inner = n_ticks - 2  # positions 1 .. n_ticks-2
    pos_high = rng.integers(1, n_ticks - 1, size=n)
    pos_low = 1 + (pos_high - 1 + rng.integers(1, inner, size=n)) % inner
    ticks[rows, pos_high] = h
    ticks[rows, pos_low] = l
    ticks[:, 0] = o
    ticks[:, -1] = c
    return ticks


def candles_from_ticks(ticks: np.ndarray, df: pd.DataFrame) -> pd.DataFrame:
    """Rebuild 1m candles from ticks (Volume is not simulated, copied as is)."""
    return pd.DataFrame({
        "Time": df["Time"].to_numpy(),
        "Open": ticks[:, 0],
        "High": ticks.max(axis=1),
        "Low": ticks.min(axis=1),
        "Close": ticks[:, -1],
        "Volume": df["Volume"].to_numpy(),
    })


def count_candle_mismatches(rebuilt: pd.DataFrame, original: pd.DataFrame) -> int:
    cols = ["Open", "High", "Low", "Close"]
    return int((rebuilt[cols].to_numpy() != original[cols].to_numpy()).any(axis=1).sum())


def drop_forming_candles(df: pd.DataFrame, tf_minutes: int, now: pd.Timestamp) -> pd.DataFrame:
    """Keep only candles closed at `now` (same rule as live _drop_incomplete_candle)."""
    if df.empty:
        return df
    closed = (df["Time"] + pd.Timedelta(minutes=tf_minutes)) <= now
    if bool(closed.all()):
        return df
    return df.loc[closed].reset_index(drop=True)


# ==============================================================================
# STRATEGY HELPERS
# ==============================================================================

def build_config(strategy_cfg: dict[str, Any], data_folder: str, signal_tf: str, entry_tf: str):
    return build_strategy_config(
        data_folder=data_folder,
        output_folder="res/output",
        strategy_config=strategy_cfg,
        overrides={"signal_timeframe": signal_tf, "entry_timeframe": entry_tf},
    )


def signal_key(signal: Signal | Trade) -> SignalKey:
    return (str(signal.trade_type), int(pd.Timestamp(signal.entry_time).value), str(signal.signal_timeframe))


def signal_prices(signal: Signal | Trade) -> tuple[float, float, float]:
    return (float(signal.entry_price), float(signal.stop_loss), float(signal.take_profit))


def prices_equal(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    return all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in zip(a, b))


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    signal_tf: str,
    entry_tf: str,
    strategy_cfg: dict[str, Any],
    data_folder: str,
) -> list[Trade]:
    """Same pipeline as src/backtest/main.py for one symbol / signal TF."""
    cfg = build_config(strategy_cfg, data_folder, signal_tf, entry_tf)
    strategy = create_strategy(cfg)
    if signal_tf != entry_tf:
        signals = strategy.generate_signals(convert_timeframe(df, signal_tf), symbol, df_entry=df)
    else:
        signals = strategy.generate_signals(df, symbol)
    return TradeSimulator(cfg).simulate(signals, df)


def load_orders(path: Path) -> list[Trade]:
    """Read a trades CSV written by generate_trade_orders (backtest or live output)."""
    trades: list[Trade] = []
    for row in pd.read_csv(path).to_dict("records"):
        entry = float(row["Entry_Price"])
        stop_loss = float(row["Stop_Loss"])
        take_profit = float(row["Take_Profit"])
        risk = abs(entry - stop_loss)
        exit_time = row.get("Exit_Time")
        trades.append(Trade(
            symbol=str(row["Symbol"]).strip(),
            trade_type=str(row["Type"]).strip().upper(),
            entry_time=pd.Timestamp(row["Entry_Time"]).to_pydatetime(),
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal_timeframe=str(row["Signal_Timeframe"]).strip(),
            # CSV RR is rounded to 2 decimals: recompute from prices
            rr_ratio=abs(take_profit - entry) / risk if risk > 0 else None,
            exit_time=pd.Timestamp(exit_time).to_pydatetime() if pd.notna(exit_time) else None,
            result=str(row.get("Result", "Open")).strip().upper(),
        ))
    return trades


def find_order_files(orders_path: Path) -> list[tuple[str, str, Path]]:
    """`SYMBOL_TF_trades.csv` file or folder -> [(symbol, tf, path)]."""
    files = [orders_path] if orders_path.is_file() else sorted(orders_path.glob("*_trades.csv"))
    found = []
    for path in files:
        parts = path.name[: -len("_trades.csv")].rsplit("_", 1) if path.name.endswith("_trades.csv") else []
        if len(parts) != 2:
            raise SystemExit(f"Tên file lệnh phải có dạng SYMBOL_TF_trades.csv: {path}")
        found.append((parts[0], parts[1], path))
    return found


# ==============================================================================
# WALK-FORWARD REPLAY (runs in worker processes)
# ==============================================================================

_WORKER: dict[str, Any] = {}


def _init_worker(
    df1m: pd.DataFrame,
    symbol: str,
    signal_tfs: list[str],
    entry_tf: str,
    strategy_cfg: dict[str, Any],
    data_folder: str,
    window: int,
) -> None:
    _WORKER.clear()
    _WORKER.update(
        df1m=df1m,
        times_ns=df1m["Time"].to_numpy(dtype="datetime64[ns]").astype(np.int64),
        symbol=symbol,
        entry_tf=entry_tf,
        entry_minutes=parse_timeframe_minutes(entry_tf),
        window=window,
        strategies={tf: create_strategy(build_config(strategy_cfg, data_folder, tf, entry_tf)) for tf in signal_tfs},
    )


def _signals_at_step(step: int, signal_tf: str) -> list[Signal]:
    """What the strategy outputs right after 1m candle `step` closes, seeing only closed candles."""
    df1m: pd.DataFrame = _WORKER["df1m"]
    window: int = _WORKER["window"]
    entry_tf: str = _WORKER["entry_tf"]
    strategy: Strategy = _WORKER["strategies"][signal_tf]
    symbol: str = _WORKER["symbol"]

    lo = 0 if window <= 0 else max(0, step + 1 - window)
    df_entry = df1m.iloc[lo : step + 1]
    if lo > 0:
        df_entry = df_entry.reset_index(drop=True)
    if signal_tf == entry_tf:
        return list(strategy.generate_signals(df_entry, symbol))

    now = pd.Timestamp(df1m["Time"].iat[step]) + pd.Timedelta(minutes=_WORKER["entry_minutes"])
    signal_df = drop_forming_candles(convert_timeframe(df_entry, signal_tf), parse_timeframe_minutes(signal_tf), now)
    if signal_df.empty:
        return []
    return list(strategy.generate_signals(signal_df, symbol, df_entry=df_entry))


def _run_chunk(signal_tf: str, start: int, stop: int):
    """
    Replay steps [start, stop).
    Returns orders placed on time, the first step each signal was seen, and errors.
    """
    times_ns: np.ndarray = _WORKER["times_ns"]
    on_time: dict[SignalKey, Signal] = {}
    first_seen: dict[SignalKey, tuple[int, tuple[float, float, float]]] = {}
    errors: list[tuple[int, str]] = []

    for step in range(start, stop):
        try:
            signals = _signals_at_step(step, signal_tf)
        except Exception as exc:  # report, do not stop the whole replay
            errors.append((step, f"{type(exc).__name__}: {exc}"))
            continue
        step_ns = times_ns[step]
        for signal in signals:
            key = signal_key(signal)
            if key not in first_seen:
                first_seen[key] = (step, signal_prices(signal))
            if key[1] == step_ns and key not in on_time:
                on_time[key] = signal

    return signal_tf, on_time, first_seen, errors, stop - start


def run_walk_forward(
    df1m: pd.DataFrame,
    symbol: str,
    signal_tfs: list[str],
    entry_tf: str,
    strategy_cfg: dict[str, Any],
    data_folder: str,
    window: int,
    workers: int,
) -> dict[str, dict[str, Any]]:
    n = len(df1m)
    chunk = max(25, math.ceil(n / max(1, workers * 32)))  # small chunks = smoother progress bar
    tasks = [(tf, s, min(n, s + chunk)) for tf in signal_tfs for s in range(0, n, chunk)]
    # Later steps see more history and cost more: start them first.
    tasks.sort(key=lambda t: -t[1])

    out = {tf: {"on_time": {}, "first_seen": {}, "errors": []} for tf in signal_tfs}

    def merge(result) -> int:
        tf, on_time, first_seen, errors, steps = result
        bucket = out[tf]
        bucket["on_time"].update(on_time)
        seen = bucket["first_seen"]
        for key, value in first_seen.items():
            if key not in seen or value[0] < seen[key][0]:
                seen[key] = value
        bucket["errors"].extend(errors)
        return steps

    init_args = (df1m, symbol, signal_tfs, entry_tf, strategy_cfg, data_folder, window)
    with tqdm(total=n * len(signal_tfs), desc=f"  Replay {symbol}", unit="step", smoothing=0.05) as bar:
        if workers <= 1:
            _init_worker(*init_args)
            for task in tasks:
                bar.update(merge(_run_chunk(*task)))
        else:
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init_args) as pool:
                futures = [pool.submit(_run_chunk, *task) for task in tasks]
                try:
                    for future in as_completed(futures):
                        bar.update(merge(future.result()))
                except KeyboardInterrupt:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
    return out


# ==============================================================================
# FILL / EXIT ON TICKS
# ==============================================================================

@njit(cache=True)
def _exit_on_ticks_jit(ticks: np.ndarray, start_candle: int, is_long: bool, stop_loss: float, take_profit: float):
    """First tick after the entry candle that touches SL or TP. result: 0=LOSS, 1=WIN, 2=OPEN."""
    n_candles, n_ticks = ticks.shape
    for c in range(start_candle, n_candles):
        for j in range(n_ticks):
            price = ticks[c, j]
            if is_long:
                if price <= stop_loss:
                    return c, stop_loss, 0
                if price >= take_profit:
                    return c, take_profit, 1
            else:
                if price >= stop_loss:
                    return c, stop_loss, 0
                if price <= take_profit:
                    return c, take_profit, 1
    return n_candles - 1, ticks[n_candles - 1, n_ticks - 1], 2


def simulate_on_ticks(
    on_time: dict[SignalKey, Signal],
    ticks: np.ndarray,
    times_ns: np.ndarray,
) -> dict[SignalKey, tuple[str, int]]:
    """Fill each placed order at its entry candle's close tick, exit tick by tick -> (result, exit candle)."""
    results: dict[SignalKey, tuple[str, int]] = {}
    for key, signal in on_time.items():
        entry_idx = int(np.searchsorted(times_ns, key[1]))
        exit_idx, _, code = _exit_on_ticks_jit(
            ticks, entry_idx + 1, signal.trade_type == "LONG", float(signal.stop_loss), float(signal.take_profit)
        )
        results[key] = (RESULT_NAMES[int(code)], int(exit_idx))
    return results


# ==============================================================================
# COMPARE
# ==============================================================================

def _ns_to_time(ns: int) -> pd.Timestamp:
    return pd.Timestamp(ns)


def _exit_index(trade: Trade, times_ns: np.ndarray) -> int:
    if trade.exit_time is None:
        return len(times_ns) - 1
    exit_ts = pd.Timestamp(trade.exit_time)
    if exit_ts.tzinfo is not None:
        exit_ts = exit_ts.tz_convert("UTC").tz_localize(None)
    return int(np.searchsorted(times_ns, exit_ts.value))


def _is_ambiguous(trade: Trade, idx: int, highs: np.ndarray, lows: np.ndarray) -> bool:
    """Exit candle touched both SL and TP: order inside the candle is unknown."""
    if idx >= len(highs):
        return False
    if trade.trade_type == "LONG":
        return bool(lows[idx] <= trade.stop_loss and highs[idx] >= trade.take_profit)
    return bool(highs[idx] >= trade.stop_loss and lows[idx] <= trade.take_profit)


def _trade_r(result: str, rr: float | None) -> float | None:
    if result == "WIN":
        return float(rr) if rr is not None else None
    if result == "LOSS":
        return -1.0
    return None


def compare_orders(
    *,
    backtest_trades: list[Trade],
    replay: dict[str, Any],
    seed_results: dict[int, dict[SignalKey, tuple[str, int]]],
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    times_ns = df["Time"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()

    bt = {signal_key(t): t for t in backtest_trades}
    on_time: dict[SignalKey, Signal] = replay["on_time"]
    first_seen = replay["first_seen"]
    seeds = list(seed_results)

    rows: list[dict[str, Any]] = []
    for key in sorted(set(bt) | set(on_time), key=lambda k: (k[1], k[0], k[2])):
        trade = bt.get(key)
        signal = on_time.get(key)
        row: dict[str, Any] = {
            "Status": "",
            "Type": key[0],
            "Signal_TF": key[2],
            "Entry_Time": _ns_to_time(key[1]),
            "Late_Min": None,
            "BT_Entry": None, "BT_SL": None, "BT_TP": None, "BT_RR": None,
            "BT_Result": None, "BT_Exit_Time": None, "BT_Ambiguous": None,
            "RP_Entry": None, "RP_SL": None, "RP_TP": None,
            "RP_Results": None, "RP_Exit_Time": None,
            "Note": "",
        }

        bt_exit_idx = -1
        if trade is not None:
            bt_exit_idx = _exit_index(trade, times_ns)
            row.update(
                BT_Entry=trade.entry_price, BT_SL=trade.stop_loss, BT_TP=trade.take_profit,
                BT_RR=round(trade.rr_ratio, 3) if trade.rr_ratio is not None else None,
                BT_Result=trade.result,
                BT_Exit_Time=_ns_to_time(int(times_ns[min(bt_exit_idx, len(times_ns) - 1)])),
                BT_Ambiguous=_is_ambiguous(trade, bt_exit_idx, highs, lows) if trade.result != "OPEN" else False,
            )
        if signal is not None:
            row.update(RP_Entry=signal.entry_price, RP_SL=signal.stop_loss, RP_TP=signal.take_profit)
            per_seed = [seed_results[s][key] for s in seeds]
            row["RP_Results"] = "|".join(result for result, _ in per_seed)
            row["RP_Exit_Time"] = "|".join(str(_ns_to_time(int(times_ns[idx]))) for _, idx in per_seed)

        if trade is not None and signal is not None:
            bt_prices = signal_prices(trade)
            rp_prices = signal_prices(signal)
            if not prices_equal(bt_prices, rp_prices):
                row["Status"] = "PRICE_DIFF"
                diffs = [
                    f"{name} {b:.10g} -> {r:.10g}"
                    for name, b, r in zip(("Entry", "SL", "TP"), bt_prices, rp_prices)
                    if not math.isclose(b, r, rel_tol=1e-9, abs_tol=1e-12)
                ]
                row["Note"] = "backtest -> replay: " + ", ".join(diffs)
            else:
                statuses = []
                for s in seeds:
                    result, exit_idx = seed_results[s][key]
                    if result == trade.result and exit_idx == bt_exit_idx:
                        statuses.append("MATCH")
                    elif exit_idx == bt_exit_idx and row["BT_Ambiguous"]:
                        statuses.append("AMBIGUOUS")
                    else:
                        statuses.append("RESULT_DIFF")
                if "RESULT_DIFF" in statuses:
                    row["Status"] = "RESULT_DIFF"
                elif "AMBIGUOUS" in statuses:
                    row["Status"] = "AMBIGUOUS"
                    row["Note"] = "nến thoát lệnh chạm cả SL và TP"
                else:
                    row["Status"] = "MATCH"
        elif trade is not None:
            row["Status"] = "MISSING"
            seen = first_seen.get(key)
            if seen is None:
                row["Note"] = "không bao giờ xuất hiện trong replay"
            else:
                late = int((times_ns[seen[0]] - key[1]) // 60_000_000_000)
                row["Late_Min"] = late
                note = f"xuất hiện trễ {late} phút"
                if late <= LIVE_MAX_ORDER_AGE_MINUTES:
                    note += " (live vẫn gửi nhưng trễ, giá đã khác)"
                if not prices_equal(seen[1], signal_prices(trade)):
                    note += f"; lúc xuất hiện Entry/SL/TP = {seen[1][0]:.10g}/{seen[1][1]:.10g}/{seen[1][2]:.10g}"
                row["Note"] = note
        else:
            row["Status"] = "EXTRA"
            row["Note"] = "live vào lệnh này nhưng backtest không có"
        rows.append(row)
    return rows


def summarize(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    signal_tf: str,
    backtest_trades: list[Trade],
    replay: dict[str, Any],
    seed_results: dict[int, dict[SignalKey, tuple[str, int]]],
) -> dict[str, Any]:
    counts = {status: 0 for status in ALL_STATUSES}
    for row in rows:
        counts[row["Status"]] += 1

    def stats(results: list[tuple[str, float | None]]) -> tuple[int, float, float]:
        closed = [(res, rr) for res, rr in results if res in ("WIN", "LOSS")]
        wins = sum(1 for res, _ in closed if res == "WIN")
        total_r = sum(r for r in (_trade_r(res, rr) for res, rr in closed) if r is not None)
        wr = wins / len(closed) * 100 if closed else 0.0
        return len(closed), wr, total_r

    bt_stats = stats([(t.result, t.rr_ratio) for t in backtest_trades])
    # Same RR rule as TradeSimulator: signal.rr_ratio, else from prices
    rr_by_key: dict[SignalKey, float | None] = {}
    for key, signal in replay["on_time"].items():
        rr = signal.rr_ratio
        if rr is None:
            risk = abs(signal.entry_price - signal.stop_loss)
            rr = abs(signal.take_profit - signal.entry_price) / risk if risk > 0 else None
        rr_by_key[key] = rr
    seed_stats = {
        seed: stats([(res, rr_by_key[key]) for key, (res, _) in results.items()])
        for seed, results in seed_results.items()
    }

    errors = replay["errors"]
    return {
        "symbol": symbol,
        "signal_tf": signal_tf,
        "backtest_orders": len(backtest_trades),
        "replay_orders": len(replay["on_time"]),
        "counts": counts,
        "passed": all(counts[s] == 0 for s in FAIL_STATUSES) and not errors,
        "bt_stats": bt_stats,
        "seed_stats": seed_stats,
        "errors": errors,
    }


# ==============================================================================
# REPORT
# ==============================================================================

def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def write_report(
    path: Path,
    *,
    run_info: dict[str, Any],
    summaries: list[dict[str, Any]],
    rows_by_run: dict[tuple[str, str], list[dict[str, Any]]],
    csv_paths: dict[tuple[str, str], Path],
) -> None:
    lines: list[str] = []
    lines.append("# Verify orders (replay chống look-ahead)")
    lines.append("")
    lines.append(f"- Thời điểm chạy: {run_info['run_time']}")
    lines.append(f"- Dữ liệu: `{run_info['data_folder']}`")
    lines.append(f"- Lệnh gốc để đối chiếu: {run_info['baseline']}")
    lines.append(f"- Ticks mỗi nến 1m: {run_info['ticks']} | Seeds: {', '.join(map(str, run_info['seeds']))}")
    window = run_info["window"]
    lines.append(
        "- Dữ liệu strategy thấy mỗi bước: "
        + ("toàn bộ lịch sử tới thời điểm đó" if window <= 0 else f"{window} nến 1m gần nhất (giống live)")
    )
    lines.append(f"- Thời gian chạy: {run_info['elapsed']:.0f}s")
    lines.append("")

    for symbol, info in run_info["symbols"].items():
        lines.append(
            f"- {symbol}: {info['candles']} nến 1m, {info['start']} → {info['end']}; "
            f"nến dựng lại từ tick khớp nến gốc: {info['rebuild_ok']}"
        )
    lines.append("")

    lines.append("## Kết quả")
    lines.append("")
    header = "| Cặp | TF | Lệnh gốc | Replay | MATCH | AMBIGUOUS | PRICE_DIFF | RESULT_DIFF | MISSING | EXTRA | Kết luận |"
    lines.append(header)
    lines.append("|" + "---|" * (header.count("|") - 1))
    for s in summaries:
        c = s["counts"]
        verdict = "✅ CHÍNH XÁC" if s["passed"] else "❌ CÓ LỆCH"
        lines.append(
            f"| {s['symbol']} | {s['signal_tf']} | {s['backtest_orders']} | {s['replay_orders']} | "
            f"{c['MATCH']} | {c['AMBIGUOUS']} | {c['PRICE_DIFF']} | {c['RESULT_DIFF']} | "
            f"{c['MISSING']} | {c['EXTRA']} | {verdict} |"
        )
    lines.append("")

    lines.append("## Win rate lệnh gốc vs replay")
    lines.append("")
    lines.append("| Cặp | TF | Lệnh gốc (lệnh đóng / WR / R) | " + " | ".join(f"Seed {seed}" for seed in run_info["seeds"]) + " |")
    lines.append("|---|---|---|" + "---|" * len(run_info["seeds"]))
    for s in summaries:
        n, wr, r = s["bt_stats"]
        cells = [f"{sn} / {swr:.1f}% / {sr:+.2f}R" for sn, swr, sr in (s["seed_stats"][seed] for seed in run_info["seeds"])]
        lines.append(f"| {s['symbol']} | {s['signal_tf']} | {n} / {wr:.1f}% / {r:+.2f}R | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("R gross (chưa trừ phí). Replay vào lệnh ở giá đóng nến 1m, thoát ở đúng giá SL/TP (không tính trượt giá).")
    lines.append("")

    lines.append("## Ý nghĩa trạng thái")
    lines.append("")
    lines.append("- **MATCH**: trùng lệnh, trùng Entry/SL/TP, trùng kết quả.")
    lines.append("- **AMBIGUOUS**: trùng lệnh, kết quả khác chỉ vì nến 1m thoát lệnh chạm cả SL và TP "
                 "(backtest luôn tính SL trước, replay do thứ tự tick quyết định). Không phải look-ahead.")
    lines.append("- **PRICE_DIFF**: trùng lệnh nhưng Entry/SL/TP khác → giá tính bằng dữ liệu tương lai.")
    lines.append("- **RESULT_DIFF**: trùng lệnh và giá, kết quả khác ở nến bình thường → lỗi phần mô phỏng thoát lệnh.")
    lines.append("- **MISSING**: backtest có, replay không vào đúng lúc → backtest dùng dữ liệu tương lai để tạo lệnh. "
                 "`Late_Min` = số phút sau mới xuất hiện.")
    lines.append("- **EXTRA**: replay có, backtest không → dữ liệu tương lai đã xoá lệnh này (live vẫn vào).")
    lines.append("")

    for s in summaries:
        key = (s["symbol"], s["signal_tf"])
        rows = rows_by_run[key]
        bad = [r for r in rows if r["Status"] not in ("MATCH",)]
        lines.append(f"## {s['symbol']} {s['signal_tf']} — chi tiết lệnh lệch")
        lines.append("")
        lines.append(f"CSV đầy đủ: `{csv_paths[key]}`")
        lines.append("")
        if s["errors"]:
            step, msg = s["errors"][0]
            lines.append(f"⚠️ Strategy lỗi ở {len(s['errors'])} bước (bước đầu: {step}): `{msg}`")
            lines.append("")
        if not bad:
            lines.append("Không có lệnh lệch.")
            lines.append("")
            continue
        lines.append("| Trạng thái | Type | Entry time | Gốc Entry/SL/TP → KQ | Replay Entry/SL/TP → KQ | Ghi chú |")
        lines.append("|---|---|---|---|---|---|")
        for r in bad[:50]:
            bt = (f"{r['BT_Entry']:.10g}/{r['BT_SL']:.10g}/{r['BT_TP']:.10g} → {r['BT_Result']}"
                  if r["BT_Entry"] is not None else "-")
            rp = (f"{r['RP_Entry']:.10g}/{r['RP_SL']:.10g}/{r['RP_TP']:.10g} → {str(r['RP_Results']).replace('|', ', ')}"
                  if r["RP_Entry"] is not None else "-")
            lines.append(f"| {r['Status']} | {r['Type']} | {r['Entry_Time']} | {bt} | {rp} | {r['Note']} |")
        if len(bad) > 50:
            lines.append(f"| ... | | | | | còn {len(bad) - 50} lệnh, xem CSV |")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ==============================================================================
# MAIN
# ==============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay 1m candles as random ticks and verify backtest orders (look-ahead check).")
    parser.add_argument("--symbol", nargs="+", default=None, help="e.g. BTCUSDT (default: every *_1m.csv in the data folder)")
    parser.add_argument("--tf", nargs="+", default=None, help="signal timeframes, e.g. 5m (default: strategy.yaml signal_timeframe)")
    parser.add_argument("--dir", default=None, help="folder with data/ (1m CSV) and res/ (*_trades.csv to check); report goes to <dir>/report (default: res/verify)")
    parser.add_argument("--all", action="store_true", help="no menu: check every symbol / timeframe")
    parser.add_argument("--orders", default=None, help="trades CSV or folder of SYMBOL_TF_trades.csv to check (instead of running the backtest)")
    parser.add_argument("--data", default=None, help="data folder (default: backtest.yaml folder_path)")
    parser.add_argument("--candles", type=int, default=20000, help="last N 1m candles to test, 0 = whole file (default 20000)")
    parser.add_argument("--start", default=None, help="test start time, e.g. 2026-10-01 (overrides --candles)")
    parser.add_argument("--end", default=None, help="test end time (overrides --candles)")
    parser.add_argument("--ticks", type=int, default=20, help="random prices per 1m candle (default 20, min 4)")
    parser.add_argument("--seeds", type=int, default=3, help="number of random tick sets (default 3)")
    parser.add_argument("--seed", type=int, default=42, help="first random seed (default 42)")
    parser.add_argument("--window", type=int, default=0, help="1m candles the strategy sees each step, 0 = all history (default); 5000 = like live")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel processes")
    parser.add_argument("--strategy-config", default=None, help="path to strategy YAML")
    parser.add_argument("--out", default=None, help="report folder (default: backtest.yaml report_folder)")
    return parser.parse_args()


def resolve_csv(data_dir: Path, symbol: str, entry_tf: str) -> Path | None:
    for name in (symbol, symbol.upper(), f"{symbol.upper()}USDT"):
        path = data_dir / f"{name}_{entry_tf}.csv"
        if path.exists():
            return path
    return None


def build_catalog(
    data_dir: Path,
    entry_tf: str,
    orders_path: Path | None,
    strategy_tfs: list[str],
) -> dict[str, tuple[Path, dict[str, Path | None]]]:
    """
    symbol -> (1m CSV, {TF: orders CSV or None}).
    TFs that have an orders file come first; strategy TFs without one run the backtest.
    """
    orders: dict[str, dict[str, Path]] = {}
    if orders_path is not None and orders_path.exists():
        for symbol, tf, path in find_order_files(orders_path):
            orders.setdefault(symbol.upper(), {})[tf] = path

    catalog: dict[str, tuple[Path, dict[str, Path | None]]] = {}
    for csv_path in sorted(data_dir.glob(f"*_{entry_tf}.csv")):
        symbol = csv_path.name.rsplit("_", 1)[0]
        with_orders = orders.pop(symbol.upper(), {})
        tfs: dict[str, Path | None] = dict(sorted(with_orders.items(), key=lambda kv: parse_timeframe_minutes(kv[0])))
        for tf in strategy_tfs:
            tfs.setdefault(tf, None)
        catalog[symbol] = (csv_path, tfs)

    for symbol, tf_paths in orders.items():
        for path in tf_paths.values():
            print(f"⚠️ Skipped {path}: no {symbol}_{entry_tf}.csv in {data_dir}")
    return catalog


def choose(title: str, options: list[tuple[str, str]]) -> str:
    """Numbered menu: type a number or a name (prefix ok); Enter = first option; q = quit."""
    print(f"\n{title}")
    width = max(len(key) for key, _ in options)
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}. {key.ljust(width)}  {label}")
    while True:
        try:
            raw = input(f"Select (1-{len(options)}, Enter = 1, q = quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
        if raw.lower() in ("q", "quit", "exit"):
            raise SystemExit(0)
        if not raw:
            return options[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        matches = [key for key, _ in options if key.lower() == raw.lower()]
        matches = matches or [key for key, _ in options if key.lower().startswith(raw.lower())]
        if len(matches) == 1:
            return matches[0]
        print("  Invalid choice, try again.")


def choose_job(
    catalog: dict[str, tuple[Path, dict[str, Path | None]]],
    data_dir: Path,
    ask_symbol: bool = True,
) -> tuple[str, str]:
    if ask_symbol:
        symbol_options = []
        for symbol, (_, tfs) in catalog.items():
            with_orders = [tf for tf, path in tfs.items() if path is not None]
            label = f"orders file: {', '.join(with_orders)}" if with_orders else "no orders file"
            symbol_options.append((symbol, label))
        symbol = choose(f"Select coin (data: {data_dir}):", symbol_options)
    else:
        symbol = next(iter(catalog))

    tf_options = []
    for tf, path in catalog[symbol][1].items():
        if path is not None:
            count = len(pd.read_csv(path, usecols=["Entry_Time"]))
            tf_options.append((tf, f"check against {path} ({count} orders)"))
        else:
            tf_options.append((tf, "no orders file: run the backtest on this data, then check it"))
    if not tf_options:
        raise SystemExit(f"{symbol}: no timeframe to check")
    tf = choose(f"Select timeframe for {symbol}:", tf_options)
    return symbol, tf


def main() -> None:
    args = parse_args()
    started = time.time()

    backtest_cfg = load_backtest_config(include_strategy=False)
    strategy_cfg = load_strategy_config()
    if args.strategy_config:
        strategy_cfg.update(load_yaml_config(config_path=args.strategy_config, default_name="strategy.yaml"))

    # No data source given: use res/verify (data/ + res/) when it exists
    if not (args.dir or args.data or args.orders):
        project_root = Path(__file__).resolve().parents[2]
        for candidate in (Path(DEFAULT_VERIFY_DIR), project_root / DEFAULT_VERIFY_DIR):
            if candidate.is_dir():
                args.dir = str(candidate)
                break
    verify_dir = Path(args.dir) if args.dir else None
    data_folder = str(
        args.data or (verify_dir / "data" if verify_dir else None) or backtest_cfg.get("folder_path") or DEFAULT_DATA_FOLDER
    ).strip()
    orders_arg = args.orders or (str(verify_dir / "res") if verify_dir else None)
    report_dir = Path(
        args.out or (verify_dir / "report" if verify_dir else None) or backtest_cfg.get("report_folder") or DEFAULT_REPORT_FOLDER
    )
    entry_tf = str(strategy_cfg.get("entry_timeframe", "1m")).strip() or "1m"
    strategy_tfs = [str(tf).strip() for tf in (strategy_cfg.get("signal_timeframe") or ["5m"]) if str(tf).strip()]
    entry_minutes = parse_timeframe_minutes(entry_tf)
    seeds = [args.seed + i for i in range(max(1, args.seeds))]
    data_dir = Path(data_folder)

    catalog = build_catalog(data_dir, entry_tf, Path(orders_arg) if orders_arg else None, strategy_tfs + list(args.tf or []))
    if not catalog:
        raise SystemExit(f"Không có file *_{entry_tf}.csv trong {data_dir}")

    if args.symbol:
        wanted = {s.upper() for s in args.symbol} | {f"{s.upper()}USDT" for s in args.symbol}
        catalog = {sym: entry for sym, entry in catalog.items() if sym.upper() in wanted}
        if not catalog:
            raise SystemExit(f"Không tìm thấy {', '.join(args.symbol)} trong {data_dir}")
    if args.tf:
        catalog = {
            sym: (csv_path, {tf: path for tf, path in tfs.items() if tf in args.tf})
            for sym, (csv_path, tfs) in catalog.items()
        }

    # symbol -> (1m CSV, {signal TF: orders CSV to check, or None = run the backtest})
    jobs: dict[str, tuple[Path, dict[str, Path | None]]] = {}
    use_menu = sys.stdin.isatty() and not args.all and not (args.symbol and args.tf)
    if use_menu:
        # One symbol + one timeframe, picked from the menu
        sym, tf = choose_job(catalog, data_dir, ask_symbol=not (args.symbol and len(catalog) == 1))
        jobs[sym] = (catalog[sym][0], {tf: catalog[sym][1][tf]})
    else:
        has_orders = any(path is not None for _, tfs in catalog.values() for path in tfs.values())
        for sym, (csv_path, tfs) in catalog.items():
            if args.tf:
                picked = dict(tfs)
            elif has_orders:
                picked = {tf: path for tf, path in tfs.items() if path is not None}
            else:
                picked = {tf: None for tf in strategy_tfs}
            if picked:
                jobs[sym] = (csv_path, picked)
    if not jobs:
        raise SystemExit("Không có gì để kiểm tra")

    used_orders = [path for _, tfs in jobs.values() for path in tfs.values() if path is not None]
    baseline_label = (
        ", ".join(f"`{path}`" for path in used_orders)
        if used_orders else "chạy backtest trên cùng đoạn dữ liệu (như src/backtest/main.py)"
    )

    print("\n" + "=" * 64)
    print("VERIFY ORDERS — replay tick ngẫu nhiên để bắt look-ahead")
    print("=" * 64)
    print(f"  Data: {data_folder} | Entry TF: {entry_tf}")
    print(f"  Lệnh gốc: {baseline_label}")
    print(f"  Ticks/nến: {args.ticks} | Seeds: {seeds} | Window: {'toàn bộ lịch sử' if args.window <= 0 else args.window}")
    print(f"  Workers: {args.workers}")

    summaries: list[dict[str, Any]] = []
    rows_by_run: dict[tuple[str, str], list[dict[str, Any]]] = {}
    csv_paths: dict[tuple[str, str], Path] = {}
    symbols_info: dict[str, dict[str, Any]] = {}

    for symbol, (csv_file, tf_orders) in jobs.items():
        signal_tfs = list(tf_orders)
        # Checking a given orders file: use the data file exactly as that backtest saw it
        orders_mode = any(path is not None for path in tf_orders.values())
        df = select_slice(
            load_candles(csv_file),
            candles=0 if orders_mode else args.candles,
            start=args.start,
            end=args.end,
            timeframes_minutes=[parse_timeframe_minutes(tf) for tf in signal_tfs] + [entry_minutes],
            entry_minutes=entry_minutes,
            align_end=not orders_mode,
        )
        if len(df) < 10:
            print(f"\n{symbol}: không đủ dữ liệu trong khoảng đã chọn, bỏ qua")
            continue
        first_time, last_time = df["Time"].iat[0], df["Time"].iat[-1]
        print(f"\n{symbol}: {len(df)} nến {entry_tf} | {first_time} → {last_time}")

        # 1. Ticks for each seed + check rebuilt candles equal the original
        ticks_by_seed: dict[int, np.ndarray] = {}
        mismatched = 0
        for seed in seeds:
            ticks = generate_ticks(df, args.ticks, np.random.default_rng(seed))
            mismatched = max(mismatched, count_candle_mismatches(candles_from_ticks(ticks, df), df))
            ticks_by_seed[seed] = ticks
        rebuild_ok = "100%" if mismatched == 0 else f"lệch {mismatched} nến (dữ liệu O/C nằm ngoài H/L)"
        print(f"  Nến dựng lại từ tick khớp nến gốc: {rebuild_ok}")
        symbols_info[symbol] = {"candles": len(df), "start": first_time, "end": last_time, "rebuild_ok": rebuild_ok}
        df_replay = candles_from_ticks(ticks_by_seed[seeds[0]], df)

        # 2. Orders to check: given CSV, or the normal backtest on the same slice
        backtest_by_tf: dict[str, list[Trade]] = {}
        for tf, orders_path in tf_orders.items():
            if orders_path is None:
                backtest_by_tf[tf] = run_backtest(df, symbol, tf, entry_tf, strategy_cfg, data_folder)
                print(f"  [{tf}] Backtest: {len(backtest_by_tf[tf])} lệnh")
                continue
            orders = load_orders(orders_path)
            inside = [t for t in orders if first_time <= pd.Timestamp(t.entry_time) <= last_time]
            backtest_by_tf[tf] = inside
            skipped = len(orders) - len(inside)
            print(f"  [{tf}] Lệnh gốc: {len(inside)} lệnh từ {orders_path}"
                  + (f" (bỏ {skipped} lệnh nằm ngoài khoảng dữ liệu)" if skipped else ""))

        # 3. Walk-forward replay on candles rebuilt from ticks
        replay_by_tf = run_walk_forward(
            df_replay, symbol, signal_tfs, entry_tf, strategy_cfg, data_folder, args.window, args.workers
        )

        # 4. Fill/exit on ticks for every seed, then compare
        times_ns = df["Time"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
        for tf in signal_tfs:
            replay = replay_by_tf[tf]
            seed_results = {seed: simulate_on_ticks(replay["on_time"], ticks_by_seed[seed], times_ns) for seed in seeds}
            rows = compare_orders(backtest_trades=backtest_by_tf[tf], replay=replay, seed_results=seed_results, df=df)
            summary = summarize(
                rows, symbol=symbol, signal_tf=tf, backtest_trades=backtest_by_tf[tf],
                replay=replay, seed_results=seed_results,
            )
            summaries.append(summary)
            key = (symbol, tf)
            rows_by_run[key] = rows
            csv_paths[key] = report_dir / REPORT_NAME / f"{symbol}_{tf}.csv"
            write_csv(rows, csv_paths[key])

            c = summary["counts"]
            verdict = "CHÍNH XÁC" if summary["passed"] else "CÓ LỆCH"
            print(
                f"  [{tf}] Replay: {summary['replay_orders']} lệnh | MATCH {c['MATCH']} | AMBIGUOUS {c['AMBIGUOUS']} | "
                f"PRICE_DIFF {c['PRICE_DIFF']} | RESULT_DIFF {c['RESULT_DIFF']} | MISSING {c['MISSING']} | "
                f"EXTRA {c['EXTRA']} -> {verdict}"
            )
            if summary["errors"]:
                print(f"  [{tf}] ⚠️ strategy lỗi ở {len(summary['errors'])} bước: {summary['errors'][0][1]}")

    if not summaries:
        raise SystemExit("Không có gì để kiểm tra")

    report_path = report_dir / f"{REPORT_NAME}.md"
    write_report(
        report_path,
        run_info={
            "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_folder": data_folder,
            "baseline": baseline_label,
            "ticks": args.ticks,
            "seeds": seeds,
            "window": args.window,
            "elapsed": time.time() - started,
            "symbols": symbols_info,
        },
        summaries=summaries,
        rows_by_run=rows_by_run,
        csv_paths=csv_paths,
    )
    print("\n" + "=" * 64)
    print(f"Báo cáo: {report_path}")
    print(f"CSV từng lệnh: {report_dir / REPORT_NAME}/")
    print("=" * 64)


if __name__ == "__main__":
    main()
