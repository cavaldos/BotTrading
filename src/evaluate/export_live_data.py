"""
Export the live bot's data for the web chart (Live tab).

For every candle file the bot keeps in res/logs/data it writes to chart/public/data/live/:

  SYMBOL_1m.json   entry-timeframe candles, the bot's trades of every signal timeframe,
                   SMC indicators
  SYMBOL_2m.json   one file per signal timeframe of strategy.yaml: candles resampled the
  SYMBOL_3m.json   way the bot does, that timeframe's trades and the SMC indicators on
  ...              closed candles with the strategy settings (what the bot looks at)
  symbols.json     the files with `updatedAt`, last price and change; the chart polls it
                   every few seconds and reloads the file on screen when it changed

Trades come from the bot's output CSVs (res/logs/output/SYMBOL_TF_trades.csv). Each trade
carries what the bot did with it (`bot`), read from sent_orders.json / skipped_orders.json:
PENDING (waiting for approval), OPEN (order sent), EXPIRED, REJECTED, FAILED, NOT_FILLED,
SKIPPED (with the reason), ...

Runs as a loop (start.sh starts it) and only re-exports files whose inputs changed:
    python -m src.evaluate.export_live_data              # loop, checks every 2 s
    python -m src.evaluate.export_live_data --once       # one export, then exit
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.config import load_live_config, load_strategy_config
from src.indicators.export import (
    ALL_INDICATORS,
    datetime_to_timestamp,
    export_candles,
    export_volume,
    run_indicators,
    strategy_indicator_params,
)
from src.core.timeframe import convert_timeframe


# ==================== CONFIGURATION ====================

_CONFIG = load_live_config(include_strategy=False)
_STRATEGY = load_strategy_config()

LIVE_DATA_DIR = Path(_CONFIG.get("data", {}).get("folder", "res/logs/data"))
LIVE_TRADES_DIR = Path(_CONFIG.get("output", {}).get("folder", "res/logs/output"))
SENT_ORDERS_FILE = Path(_CONFIG.get("output", {}).get("log_file", "res/logs/sent_orders.json"))
SKIPPED_ORDERS_FILE = Path("res/logs/skipped_orders.json")  # same file as src/live/core/send.py
OUTPUT_DIR = Path("chart/public/data/live")

POLL_SECONDS = 2.0
MIN_INDICATOR_BARS = 30  # fewer closed candles than this: no SMC indicators

UP = "#26a69a"
DOWN = "#ef5350"
ACCENT = "#2962ff"
MUTED = "#787b86"
WARN = "#ff9800"

# What the bot did with a trade (sent_orders.json `status`): label shown on the chart, colour
BOT_STATUS = {
    "PENDING": ("Waiting approval", ACCENT),
    "OPEN": ("Sent", ACCENT),
    "PASSED": ("Sent", ACCENT),
    "WIN": ("Sent · win", UP),
    "LOSS": ("Sent · loss", DOWN),
    "EXPIRED": ("Expired", MUTED),
    "REJECTED": ("Rejected", MUTED),
    "CANCELLED": ("Cancelled", MUTED),
    "FAILED": ("Failed", DOWN),
    "NOT_FILLED": ("Not filled", WARN),
}
FILL_LABELS = {"FILLED": ("Filled", UP), "PARTIAL": ("Partly filled", WARN)}


def _tf_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _tf_minutes(tf: str) -> int:
    tf = str(tf).strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"Invalid timeframe: {tf}")


# Same timeframe logic as src/live/run.py
ENTRY_TF = str(_STRATEGY.get("entry_timeframe", "") or "").strip()
SIGNAL_TFS = _tf_list(_STRATEGY.get("signal_timeframe", _STRATEGY.get("indicator_timeframe", [])))
STRATEGY_TF_MODE = bool(ENTRY_TF and SIGNAL_TFS)
INDICATOR_PARAMS = strategy_indicator_params(_STRATEGY)


# ==================== PLANNING ====================

def _trades_file(symbol: str, tf: str) -> Path:
    return LIVE_TRADES_DIR / f"{symbol}_{tf}_trades.csv"


def plan_views() -> list[dict]:
    """One chart file per (symbol, timeframe): the bot's candle files plus each signal timeframe."""
    if not LIVE_DATA_DIR.exists():
        return []
    views: list[dict] = []
    for data_file in sorted(LIVE_DATA_DIR.glob("*.csv")):
        symbol, _, tf = data_file.stem.rpartition("_")
        if not symbol or not tf:
            continue
        try:
            source_minutes = _tf_minutes(tf)
        except ValueError:
            continue
        base = {"symbol": symbol, "data_file": data_file, "source_tf": tf}
        if STRATEGY_TF_MODE and tf == ENTRY_TF:
            # Entry timeframe: every signal timeframe's trades on one chart
            views.append({**base, "id": f"{symbol}_{tf}", "tf": tf, "resample": False,
                          "trade_files": [_trades_file(symbol, s) for s in SIGNAL_TFS],
                          "trades_from": SIGNAL_TFS, "tag_trades": True})
            for signal_tf in SIGNAL_TFS:
                if signal_tf == tf:
                    continue
                try:
                    minutes = _tf_minutes(signal_tf)
                except ValueError:
                    continue
                if minutes < source_minutes or minutes % source_minutes:
                    continue
                views.append({**base, "id": f"{symbol}_{signal_tf}", "tf": signal_tf, "resample": True,
                              "trade_files": [_trades_file(symbol, signal_tf)]})
        else:
            views.append({**base, "id": f"{symbol}_{tf}", "tf": tf, "resample": False,
                          "trade_files": [_trades_file(symbol, tf)]})
    return views


# ==================== INPUTS ====================

def _signature(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_bot_orders() -> dict[str, dict]:
    """sent_orders.json + skipped_orders.json by order key (SYMBOL|TYPE|ENTRY_TIME|TF)."""
    orders: dict[str, dict] = {}

    skipped = _read_json(SKIPPED_ORDERS_FILE)
    if isinstance(skipped, dict):
        skipped = skipped.get("skipped_orders", [])
    for item in skipped if isinstance(skipped, list) else []:
        if isinstance(item, dict) and item.get("key"):
            orders[item["key"]] = {
                "status": "SKIPPED",
                "label": "Skipped",
                "reason": str(item.get("reason") or ""),
                "decidedAt": item.get("skipped_at"),
            }

    sent = _read_json(SENT_ORDERS_FILE)
    sent = sent.get("sent_orders", []) if isinstance(sent, dict) else []
    for item in sent:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        status = str(item.get("status") or "OPEN").upper()
        label, color = BOT_STATUS.get(status, (status.title(), MUTED))
        fill = str(item.get("fill_status") or "").upper()
        if status == "OPEN" and fill in FILL_LABELS:
            label, color = FILL_LABELS[fill]
        orders[item["key"]] = {
            "status": status,
            "label": label,
            "color": color,
            "fill": fill or None,
            "sentAt": item.get("sent_at"),
            "decidedAt": item.get("decided_at") or item.get("approved_at"),
            "orderId": item.get("order_id"),
            "risk": item.get("risk_per_trade"),
        }
    return orders


def load_candles(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    df["Time"] = pd.to_datetime(df["Time"])
    df = df.sort_values("Time").reset_index(drop=True)
    if df.empty or df[["Open", "High", "Low", "Close"]].iloc[-1].isna().any():
        raise ValueError(f"{path.name} is empty or still being written")
    return df


def load_trades(files: list[Path]) -> pd.DataFrame:
    frames = []
    for path in files:
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype={"Entry_Time": str, "Signal_Timeframe": str})
        df.columns = df.columns.str.strip()
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    trades = pd.concat(frames, ignore_index=True)
    trades["_entry"] = pd.to_datetime(trades["Entry_Time"])
    sort_cols = ["_entry", "Signal_Timeframe"] if "Signal_Timeframe" in trades.columns else ["_entry"]
    return trades.sort_values(sort_cols, kind="stable").reset_index(drop=True)


# ==================== CONVERSION ====================

def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _snap(ts: int, candle_times: list[int]) -> int:
    """Time of the candle that contains `ts`."""
    i = bisect_right(candle_times, ts) - 1
    return candle_times[i] if i >= 0 else candle_times[0]


def export_trades(trades_df: pd.DataFrame, candle_times: list[int], bot_orders: dict[str, dict],
                  tag_trades: bool) -> tuple[list[dict], list[dict]]:
    """Trades and their markers (entry arrow, exit dot, bot decision) in chart format."""
    trades: list[dict] = []
    markers: list[dict] = []
    if trades_df.empty or not candle_times:
        return trades, markers

    for idx, row in enumerate(trades_df.to_dict("records")):
        trade_type = _text(row.get("Type")).upper()
        entry_price = _num(row.get("Entry_Price"))
        stop_loss = _num(row.get("Stop_Loss"))
        if trade_type not in ("LONG", "SHORT") or entry_price is None or stop_loss is None:
            continue
        take_profit = _num(row.get("Take_Profit"))
        if take_profit is None:
            take_profit = entry_price
        is_long = trade_type == "LONG"
        signal_tf = _text(row.get("Signal_Timeframe"))
        entry_raw = datetime_to_timestamp(pd.Timestamp(row["_entry"]))
        entry_time = _snap(entry_raw, candle_times)

        result = _text(row.get("Result")).title()
        exit_text = _text(row.get("Exit_Time"))
        exit_price = _num(row.get("Exit_Price"))
        is_open = result not in ("Win", "Loss") or not exit_text or exit_price is None
        if is_open:
            # Still running: draw the box up to the last candle
            exit_raw, exit_time, exit_price, result = None, candle_times[-1], None, "Pending"
        else:
            exit_raw = datetime_to_timestamp(pd.Timestamp(exit_text))
            exit_time = _snap(exit_raw, candle_times)

        key = f"{_text(row.get('Symbol'))}|{trade_type}|{_text(row.get('Entry_Time'))}"
        bot = bot_orders.get(f"{key}|{signal_tf}") if signal_tf else None
        bot = bot or bot_orders.get(key)

        zone = None
        zone_high, zone_low = _num(row.get("Zone_High")), _num(row.get("Zone_Low"))
        if zone_high is not None and zone_low is not None:
            zone = {"high": zone_high, "low": zone_low, "type": _text(row.get("Zone_Type")),
                    "time": _text(row.get("Zone_Time"))}

        trade = {
            "id": len(trades),
            "type": trade_type,
            "timeframe": signal_tf or None,
            "entryTime": entry_time,
            "entryTimeRaw": entry_raw,
            "exitTime": exit_time,
            "exitTimeRaw": exit_raw,
            "entryPrice": entry_price,
            "exitPrice": exit_price,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "status": "open" if is_open else "closed",
            "result": result,
            "pnlR": _num(row.get("PnL_R")),
            "zone": zone,
            "bot": bot,
        }
        if tag_trades and signal_tf:
            trade["tag"] = signal_tf
        trades.append(trade)

        n = len(trades)
        markers.append({
            "time": entry_time,
            "position": "belowBar" if is_long else "aboveBar",
            "color": UP if is_long else DOWN,
            "shape": "arrowUp" if is_long else "arrowDown",
            "text": f"{'L' if is_long else 'S'}{n}",
            "size": 2,
        })
        if not is_open:
            win = result == "Win"
            markers.append({
                "time": exit_time,
                "position": "aboveBar" if win else "belowBar",
                "color": UP if win else DOWN,
                "shape": "circle",
                "text": "W" if win else "X",
                "size": 1,
            })
        # Orders the bot actually sent (or asked approval for); skipped ones stay unlabeled
        if bot and bot["status"] != "SKIPPED":
            markers.append({
                "time": entry_time,
                "position": "belowBar" if is_long else "aboveBar",
                "color": bot.get("color") or ACCENT,
                "shape": "square",
                "text": f"Bot: {bot['label']}",
                "size": 1,
            })
    return trades, markers


def summarize(trades: list[dict]) -> dict:
    wins = sum(1 for t in trades if t["result"] == "Win")
    losses = sum(1 for t in trades if t["result"] == "Loss")
    closed = wins + losses
    return {
        "total": len(trades),
        "wins": wins,
        "losses": losses,
        "open": len(trades) - closed,
        "winRate": round(wins / closed * 100, 1) if closed else 0,
    }


def closed_candles(df: pd.DataFrame, tf: str, source_df: pd.DataFrame, source_tf: str) -> pd.DataFrame:
    """Drop the last resampled candle while its source candles are not all in (like the bot)."""
    covered_until = source_df["Time"].iloc[-1] + pd.Timedelta(minutes=_tf_minutes(source_tf))
    return df[df["Time"] + pd.Timedelta(minutes=_tf_minutes(tf)) <= covered_until].reset_index(drop=True)


def _clean(value: Any) -> Any:
    """NaN / inf -> None (browsers cannot parse NaN in JSON)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write next to the target, then rename: the chart never reads a half-written file."""
    try:
        text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except ValueError:
        text = json.dumps(_clean(payload), separators=(",", ":"), allow_nan=False)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def export_view(view: dict, bot_orders: dict[str, dict]) -> dict:
    """Write one chart file; returns its symbols.json entry. Raises when an input is unreadable."""
    source_df = load_candles(view["data_file"])
    if view["resample"]:
        df = convert_timeframe(source_df, view["tf"]).reset_index(drop=True)
        indicator_df = closed_candles(df, view["tf"], source_df, view["source_tf"])
    else:
        df = source_df
        indicator_df = source_df
    if df.empty:
        raise ValueError(f"no candles for {view['id']}")

    candles = export_candles(df)
    candle_times = [c["time"] for c in candles]
    trades, markers = export_trades(load_trades(view["trade_files"]), candle_times, bot_orders,
                                    view.get("tag_trades", False))
    if len(indicator_df) >= MIN_INDICATOR_BARS:
        indicators = run_indicators(indicator_df, **INDICATOR_PARAMS)
    else:
        indicators = {key: [] for key in ALL_INDICATORS}

    summary = summarize(trades)
    now = time.time()
    payload = {
        "candles": candles,
        "volume": export_volume(df),
        "trades": trades,
        "markers": markers,
        "summary": summary,
        **indicators,
        "metadata": {
            "symbol": view["symbol"],
            "timeframe": view["tf"],
            "id": view["id"],
            "dataPoints": len(df),
            "source": f"{view['data_file'].name}{' -> ' + view['tf'] if view['resample'] else ''}",
            "tradesFrom": view.get("trades_from") or [view["tf"]],
            "indicatorBars": len(indicator_df),
            "exportedAt": datetime.now().isoformat(timespec="seconds"),
            "isLive": True,
        },
    }
    write_json_atomic(OUTPUT_DIR / f"{view['id']}.json", payload)

    first_open, last_close = candles[0]["open"], candles[-1]["close"]
    entry = {
        "id": view["id"],
        "symbol": view["symbol"],
        "timeframe": view["tf"],
        "dataPoints": len(df),
        "trades": summary["total"],
        "winRate": summary["winRate"],
        "updatedAt": round(now, 3),
        "lastPrice": last_close,
        "changePct": round((last_close - first_open) / first_open * 100, 4) if first_open else 0,
        "indicators": {key: len(items) for key, items in indicators.items()},
    }
    if view.get("trades_from"):
        # Holds the trades of the other files too: the chart does not count them twice
        entry["tradesFrom"] = view["trades_from"]
    return entry


def write_symbols(entries: dict[str, dict]) -> None:
    symbols = sorted(entries.values(), key=lambda e: (e["symbol"], _tf_minutes(e["timeframe"])))
    write_json_atomic(OUTPUT_DIR / "symbols.json", {
        "symbols": symbols,
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
        "isLive": True,
    })


# ==================== RUNNING ====================

class LiveExporter:
    """Re-exports a chart file only when its candle file, trades files or the bot logs changed."""

    def __init__(self) -> None:
        self.signatures: dict[str, tuple] = {}
        self.entries: dict[str, dict] = {}
        self.errors: dict[str, str] = {}

    def run_once(self) -> tuple[int, list[str]]:
        """Returns (files written, error messages)."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        views = plan_views()
        if not views:
            return 0, []  # bot just started and cleared its data: keep the last files on screen

        shared = (_signature(SENT_ORDERS_FILE), _signature(SKIPPED_ORDERS_FILE))
        bot_orders: Optional[dict[str, dict]] = None
        written, errors = 0, []
        for view in views:
            sig = (tuple(_signature(p) for p in [view["data_file"], *view["trade_files"]]), shared)
            if self.signatures.get(view["id"]) == sig:
                continue
            if bot_orders is None:
                bot_orders = load_bot_orders()
            try:
                self.entries[view["id"]] = export_view(view, bot_orders)
            except Exception as exc:  # file being written by the bot: try again next time
                message = f"{view['id']}: {exc}"
                if self.errors.get(view["id"]) != message:
                    errors.append(message)
                self.errors[view["id"]] = message
                continue
            self.errors.pop(view["id"], None)
            self.signatures[view["id"]] = sig
            written += 1

        ids = {v["id"] for v in views}
        dropped = [i for i in self.entries if i not in ids]
        for i in dropped:
            self.entries.pop(i, None)
            self.signatures.pop(i, None)
        if (written or dropped) and self.entries:
            write_symbols(self.entries)
        return written, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Export live bot data for the web chart")
    parser.add_argument("--once", action="store_true", help="export once and exit")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS, help="seconds between checks (loop mode)")
    args = parser.parse_args()

    mode = f"timeframes {ENTRY_TF} + signals {','.join(SIGNAL_TFS)}" if STRATEGY_TF_MODE else "data file timeframes"
    print(f"Live chart export: {LIVE_DATA_DIR} + {LIVE_TRADES_DIR} -> {OUTPUT_DIR} ({mode})", flush=True)
    exporter = LiveExporter()
    try:
        while True:
            started = time.time()
            written, errors = exporter.run_once()
            stamp = datetime.now().strftime("%H:%M:%S")
            for message in errors:
                print(f"[{stamp}] Not exported (will retry): {message}", flush=True)
            if written:
                print(f"[{stamp}] Exported {written} file(s) in {time.time() - started:.1f}s", flush=True)
            if args.once:
                break
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        print("Export stopped.")


if __name__ == "__main__":
    main()
