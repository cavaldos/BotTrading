"""
Trade chart snapshots for live notifications.

When a live order is sent, a job goes to `SnapshotWorker`. The worker:
  1. builds the chart payload (signal-TF candles + indicators + trade + entry zone)
  2. opens the frontend page `/#/snapshot` in headless Chromium (Playwright),
     injecting the payload as `window.__SNAPSHOT_DATA__`
  3. screenshots the chart and sends it to Discord with the entry message.
If anything fails, the text-only message is sent instead.

The frontend is served from the built `chart/dist` folder by a small local
HTTP server, or from `snapshot.frontend_url` (e.g. a running `npm run dev`).

Manual test (renders the latest strategy trade found in the data folder):
    python -m src.live.core.snapshot --data res/data --symbol BTCUSDT --tf 5m --out /tmp/snap.png [--send]
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional

import pandas as pd

from src.indicators.export import (
    datetime_to_timestamp,
    export_candles,
    export_volume,
    run_indicators,
    strategy_indicator_params,
)
from src.core.timeframe import convert_timeframe


PROJECT_ROOT = Path(__file__).resolve().parents[3]

SNAPSHOT_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "bars": 400,               # signal-TF candles sent to the chart
    "lookback_bars": 170,      # bars shown before entry (extended to include the entry zone)
    "right_padding_bars": 35,  # empty space after entry for the TP/SL box
    "max_visible_bars": 250,   # cap when widening the view to reach a far entry zone
    "width": 1400,
    "height": 800,
    "scale": 2,                # device pixel ratio of the screenshot
    "timeout": 20,             # seconds per render
    "port": 0,                 # 0 = pick a free port
    "dist_folder": "chart/dist",
    "frontend_url": "",        # e.g. "http://localhost:5173" to use a dev server instead of dist
    "indicators": ["orderBlocks", "bos", "choch", "eqhEql", "fvg", "ifvg", "swing"],
}

_TIME_KEYS = ("time", "startTime", "endTime", "breakTime", "pivotTime", "sourceTime")


def load_snapshot_config(live_config: dict | None) -> dict[str, Any]:
    raw = (live_config or {}).get("snapshot") or {}
    cfg = dict(SNAPSHOT_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


def _tf_minutes(tf: str) -> int:
    tf = str(tf).strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"Invalid timeframe: {tf}")


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    df["Time"] = pd.to_datetime(df["Time"])
    return df.sort_values("Time").reset_index(drop=True)


def _resolve_data_file(data_folder: str, symbol: str, entry_tf: str) -> Path:
    folder = Path(data_folder)
    candidates = [folder / f"{symbol}_{entry_tf}.csv", folder / f"{symbol}.csv"]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"No candle file for {symbol} ({entry_tf}) in {folder}")


def _item_last_time(item: dict) -> Optional[float]:
    times = [item.get(k) for k in _TIME_KEYS]
    times = [t for t in times if isinstance(t, (int, float)) and t > 0]
    return max(times) if times else None


def build_snapshot_payload(
    row: dict,
    data_folder: str,
    entry_timeframe: str,
    strategy_config: dict | None = None,
    snapshot_config: dict | None = None,
) -> dict:
    """Build the JSON the `/snapshot` page renders for one trade row (trades CSV columns)."""
    cfg = dict(SNAPSHOT_DEFAULTS)
    cfg.update(snapshot_config or {})

    symbol = str(row["Symbol"]).strip()
    trade_type = str(row["Type"]).strip().upper()
    entry_time = pd.Timestamp(str(row["Entry_Time"]).strip())
    entry_price = float(row["Entry_Price"])
    stop_loss = float(row["Stop_Loss"])
    take_profit = _float_or_none(row.get("Take_Profit"))
    if take_profit is None:
        take_profit = entry_price
    entry_tf = str(entry_timeframe or "1m").strip()
    signal_tf = str(row.get("Signal_Timeframe") or "").strip() or entry_tf

    entry_minutes = _tf_minutes(entry_tf)
    signal_minutes = _tf_minutes(signal_tf)
    signal_seconds = signal_minutes * 60

    # Only data the strategy could see at entry (the entry candle closes at entry_time + entry_tf).
    df_entry = _load_ohlcv(_resolve_data_file(data_folder, symbol, entry_tf))
    df_entry = df_entry[df_entry["Time"] <= entry_time].reset_index(drop=True)
    if df_entry.empty:
        raise ValueError(f"No candles up to entry time {entry_time} for {symbol}")

    signal_all = convert_timeframe(df_entry, signal_tf) if signal_tf != entry_tf else df_entry.copy()
    decision_time = entry_time + timedelta(minutes=entry_minutes)
    closed_mask = signal_all["Time"] + pd.Timedelta(minutes=signal_minutes) <= decision_time
    signal_closed = signal_all[closed_mask].reset_index(drop=True)
    if len(signal_closed) < 20:
        raise ValueError(f"Not enough {signal_tf} candles for {symbol} ({len(signal_closed)})")

    indicators = run_indicators(
        signal_closed,
        include=cfg.get("indicators") or None,
        **strategy_indicator_params(strategy_config),
    )

    # Display the last N candles, including the still-forming one that holds the entry.
    display = signal_all.tail(int(cfg["bars"])).reset_index(drop=True)
    candles = export_candles(display)
    volume = export_volume(display)
    window_start = candles[0]["time"]

    entry_ts = datetime_to_timestamp(entry_time)
    entry_bar_ts = entry_ts - (entry_ts % signal_seconds)

    zone = None
    zone_high = _float_or_none(row.get("Zone_High"))
    zone_low = _float_or_none(row.get("Zone_Low"))
    zone_time_raw = row.get("Zone_Time")
    if zone_high is not None and zone_low is not None and zone_time_raw not in (None, "") and not pd.isna(zone_time_raw):
        zone_ts = datetime_to_timestamp(pd.Timestamp(str(zone_time_raw)))
        zone_type = str(row.get("Zone_Type") or "OB").strip() or "OB"
        is_long = trade_type == "LONG"
        zone = {
            "startTime": zone_ts,
            "endTime": entry_bar_ts + signal_seconds,
            "high": zone_high,
            "low": zone_low,
            "bias": "BULLISH" if is_long else "BEARISH",
            "type": zone_type,
            "label": f"ENTRY {zone_type.replace('_', ' ')}",
            "color": "rgba(255, 193, 7, 0.16)",
            "borderColor": "rgba(255, 193, 7, 0.9)",
            "borderWidth": 2,
        }

    # Viewport: enough history to see the entry zone and structure, plus room on the right.
    lookback = int(cfg["lookback_bars"]) * signal_seconds
    view_from = entry_bar_ts - lookback
    if zone:
        view_from = min(view_from, zone["startTime"] - 10 * signal_seconds)
    max_visible = max(int(cfg["max_visible_bars"]), int(cfg["lookback_bars"]))
    view_from = max(view_from, entry_bar_ts - max_visible * signal_seconds, window_start)
    view_to = entry_bar_ts + int(cfg["right_padding_bars"]) * signal_seconds

    # Keep only indicators still alive inside the view (less clutter on the image).
    for key, items in indicators.items():
        indicators[key] = [
            item for item in items
            if (_item_last_time(item) or view_from) >= view_from
        ]

    visible = [c for c in candles if view_from <= c["time"] <= view_to]
    lows = [c["low"] for c in visible] + [stop_loss, take_profit, entry_price]
    highs = [c["high"] for c in visible] + [stop_loss, take_profit, entry_price]
    if zone:
        lows.append(zone["low"])
        highs.append(zone["high"])
    price_low, price_high = min(lows), max(highs)
    pad = max(price_high - price_low, abs(entry_price) * 0.002) * 0.06

    risk = abs(entry_price - stop_loss)
    rr = abs(take_profit - entry_price) / risk if risk > 0 else None

    return {
        "metadata": {
            "symbol": symbol,
            "signalTimeframe": signal_tf,
            "entryTimeframe": entry_tf,
            "createdAt": datetime.now().isoformat(timespec="seconds"),
        },
        "candles": candles,
        "volume": volume,
        "indicators": indicators,
        "visibility": {key: True for key in indicators},
        "zone": zone,
        "trade": {
            "id": 0,
            "type": trade_type,
            "entryTime": entry_ts,
            "entryBarTime": entry_bar_ts,
            # Open trade: project the TP/SL box to the right edge of the view.
            "exitTime": view_to - 2 * signal_seconds,
            "entryPrice": entry_price,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "rr": round(rr, 2) if rr is not None else None,
            "status": "open",
            "result": "Pending",
        },
        "view": {
            "from": view_from,
            "to": view_to,
            "priceLow": price_low - pad,
            "priceHigh": price_high + pad,
        },
    }


# ==================== STATIC FRONTEND SERVER ====================

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - keep the Rich dashboard clean
        pass


class StaticChartServer:
    """Serve the built frontend (chart/dist) on 127.0.0.1 in a daemon thread."""

    def __init__(self, dist_folder: str, port: int = 0):
        dist = Path(dist_folder)
        if not dist.is_absolute():
            dist = PROJECT_ROOT / dist
        if not (dist / "index.html").exists():
            raise FileNotFoundError(f"Frontend build not found at {dist} (run `npm run build` in chart/)")
        handler = functools.partial(_QuietHandler, directory=str(dist))
        self._server = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="SnapshotStaticServer")

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "StaticChartServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ==================== RENDERER ====================

class SnapshotRenderer:
    """Headless Chromium kept alive between renders. Use from a single thread only."""

    def __init__(self, page_url: str, cfg: dict):
        self.page_url = page_url
        self.cfg = cfg
        self._playwright = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        self.close()
        from playwright.sync_api import sync_playwright  # imported lazily: optional dependency

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self._browser

    def render(self, payload: dict) -> bytes:
        browser = self._ensure_browser()
        timeout_ms = int(float(self.cfg["timeout"]) * 1000)
        page = browser.new_page(
            viewport={"width": int(self.cfg["width"]), "height": int(self.cfg["height"])},
            device_scale_factor=float(self.cfg["scale"]),
        )
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.add_init_script(f"window.__SNAPSHOT_DATA__ = {json.dumps(payload)};")
            page.goto(self.page_url, wait_until="load", timeout=timeout_ms)
            try:
                page.wait_for_function("window.__SNAPSHOT_READY__ === true", timeout=timeout_ms)
            except Exception as exc:
                detail = f" (page errors: {'; '.join(errors[:3])})" if errors else ""
                raise RuntimeError(f"Snapshot page not ready{detail}") from exc
            return page.locator("#snapshot-root").screenshot(type="png", timeout=timeout_ms)
        finally:
            page.close()

    def close(self) -> None:
        for closer in (getattr(self._browser, "close", None), getattr(self._playwright, "stop", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass
        self._browser = None
        self._playwright = None


def resolve_page_url(cfg: dict) -> tuple[str, Optional[StaticChartServer]]:
    """Return the snapshot page URL, starting a static server for chart/dist when needed."""
    frontend_url = str(cfg.get("frontend_url") or "").strip()
    if frontend_url:
        return f"{frontend_url.rstrip('/')}/#/snapshot", None
    server = StaticChartServer(str(cfg["dist_folder"]), int(cfg["port"])).start()
    return f"{server.url}/#/snapshot", server


# ==================== WORKER ====================

class SnapshotWorker:
    """Background thread: render the trade chart and send the Discord entry message."""

    def __init__(
        self,
        snapshot_config: dict,
        data_folder: str,
        entry_timeframe: str,
        strategy_config: dict | None = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = snapshot_config
        self.data_folder = data_folder
        self.entry_timeframe = entry_timeframe
        self.strategy_config = strategy_config or {}
        self.on_error = on_error or (lambda msg: print(f"Snapshot error: {msg}"))
        self._queue: Queue = Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="SnapshotWorker")
        self._server: Optional[StaticChartServer] = None
        self._renderer: Optional[SnapshotRenderer] = None

    def start(self) -> "SnapshotWorker":
        page_url, self._server = resolve_page_url(self.cfg)
        self._renderer = SnapshotRenderer(page_url, self.cfg)
        self._thread.start()
        return self

    def submit(
        self,
        row: dict,
        message_kwargs: dict,
        sender: Optional[Callable[[dict, Optional[bytes]], None]] = None,
    ) -> bool:
        """Queue a trade for snapshot + send. False when the worker is not running.

        `sender(message_kwargs, image)` delivers the message; default: Discord entry message.
        """
        if not self._thread.is_alive() or self._stop.is_set():
            return False
        self._queue.put((dict(row), dict(message_kwargs), sender))
        return True

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._server is not None:
            self._server.stop()

    def _run(self) -> None:
        from src.live.core.telegram_approval import send_entry_telegram

        try:
            while not self._stop.is_set():
                try:
                    row, message_kwargs, sender = self._queue.get(timeout=0.5)
                except Empty:
                    continue
                if sender is None:
                    sender = send_entry_telegram
                image = None
                try:
                    payload = build_snapshot_payload(
                        row, self.data_folder, self.entry_timeframe, self.strategy_config, self.cfg,
                    )
                    image = self._renderer.render(payload)
                except Exception as exc:
                    self.on_error(f"{row.get('Symbol', '?')}: {exc}")
                    if self._renderer is not None:
                        self._renderer.close()  # restart the browser on the next job
                try:
                    sender(message_kwargs, image)
                except Exception as exc:
                    self.on_error(f"Send failed: {exc}")
        finally:
            if self._renderer is not None:
                self._renderer.close()


# ==================== MANUAL TEST ====================

def _latest_strategy_trade(data_folder: str, symbol: str, signal_tf: str, entry_tf: str, strategy_config: dict) -> dict:
    from src.strategy import build_strategy_config, create_strategy

    df_entry = _load_ohlcv(_resolve_data_file(data_folder, symbol, entry_tf))
    config = build_strategy_config(
        data_folder=data_folder,
        output_folder="res/logs/output",
        strategy_config=strategy_config,
        overrides={"signal_timeframe": signal_tf, "entry_timeframe": entry_tf},
    )
    strategy = create_strategy(config)
    if signal_tf != entry_tf:
        signals = strategy.generate_signals(convert_timeframe(df_entry, signal_tf), symbol, df_entry=df_entry)
    else:
        signals = strategy.generate_signals(df_entry, symbol)
    if not signals:
        raise SystemExit(f"No strategy signals for {symbol} {signal_tf}")
    s = signals[-1]
    return {
        "Symbol": s.symbol,
        "Signal_Timeframe": s.signal_timeframe or signal_tf,
        "Type": s.trade_type,
        "Entry_Time": pd.Timestamp(s.entry_time).strftime("%Y-%m-%d %H:%M:%S"),
        "Entry_Price": s.entry_price,
        "Stop_Loss": s.stop_loss,
        "Take_Profit": s.take_profit,
        "Zone_High": getattr(s, "zone_high", None),
        "Zone_Low": getattr(s, "zone_low", None),
        "Zone_Time": getattr(s, "zone_time", None),
        "Zone_Type": getattr(s, "zone_type", ""),
    }


def main() -> None:
    from src.config import load_live_config, load_strategy_config

    parser = argparse.ArgumentParser(description="Render a trade chart snapshot")
    parser.add_argument("--data", default=None, help="Candle folder (default: live data folder)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--tf", default=None, help="Signal timeframe (default: first strategy signal_timeframe)")
    parser.add_argument("--out", default="snapshot.png")
    parser.add_argument("--json", default=None, help="Also save the payload JSON here")
    parser.add_argument("--send", action="store_true", help="Send the image to Telegram")
    args = parser.parse_args()

    live_config = load_live_config(include_strategy=False)
    strategy_config = load_strategy_config()
    cfg = load_snapshot_config(live_config)
    data_folder = args.data or live_config.get("data", {}).get("folder", "res/logs/data")
    entry_tf = str(strategy_config.get("entry_timeframe") or "1m")
    signal_tfs = strategy_config.get("signal_timeframe") or ["5m"]
    signal_tf = args.tf or (signal_tfs[0] if isinstance(signal_tfs, list) else str(signal_tfs))

    row = _latest_strategy_trade(data_folder, args.symbol, signal_tf, entry_tf, strategy_config)
    print("Trade:", row)
    payload = build_snapshot_payload(row, data_folder, entry_tf, strategy_config, cfg)
    if args.json:
        Path(args.json).write_text(json.dumps(payload), encoding="utf-8")

    page_url, server = resolve_page_url(cfg)
    renderer = SnapshotRenderer(page_url, cfg)
    try:
        image = renderer.render(payload)
    finally:
        renderer.close()
        if server is not None:
            server.stop()
    Path(args.out).write_bytes(image)
    print(f"Saved {args.out} ({len(image) / 1024:.0f} KB)")

    if args.send:
        from src.live.core.telegram_approval import send_entry_telegram

        send_entry_telegram(dict(
            type=row["Type"],
            entry=float(row["Entry_Price"]),
            stoploss=float(row["Stop_Loss"]),
            takeprofit=float(row["Take_Profit"]),
            time=row["Entry_Time"],
            name_coin=row["Symbol"],
            result="SNAPSHOT TEST",
            signal_timeframe=row["Signal_Timeframe"],
        ), image)
        print("Sent to Telegram")


if __name__ == "__main__":
    main()
