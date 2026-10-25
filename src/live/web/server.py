"""
Trading API for the web chart, served by the live bot (stdlib HTTP server).

All /api routes except /api/health need `Authorization: Bearer <WEB_API_TOKEN>`.
The server listens on 127.0.0.1 by default; the chart's Vite server proxies /api
to it, so a public chart link still needs the token to see or place anything.

  GET  /api/health                       no auth, is the bot up
  GET  /api/me                           check the token
  GET  /api/config                       symbols, risk limits, approval timeout
  GET  /api/signals                      pending + recently decided bot signals
  GET  /api/signals/<id>/image           chart snapshot PNG of a signal
  POST /api/signals/<id>/approve|reject  decide a pending signal
  POST /api/orders/preview               size / checks for a manual order (nothing placed)
  POST /api/orders                       place a manual order ({..., confirm: true, clientId})
  GET  /api/orders                       recent manual orders
  POST /api/orders/<id>/cancel           cancel an open manual limit order
  GET  /api/events                       server-sent events: snapshot, signal, order
"""

from __future__ import annotations

import hmac
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from typing import Callable, Optional

from src.live.core.approval import ApprovalHub, Signal
from src.live.web.orders import ManualOrderService, OrderError


MAX_BODY = 64 * 1024
KEEPALIVE_SECONDS = 15


class WebApiServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        orders: ManualOrderService,
        hub: Optional[ApprovalHub] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        if not token or len(token) < 16:
            raise ValueError("WEB_API_TOKEN in .env must be set (16+ characters)")
        self.host = host
        self.port = int(port)
        self.token = token
        self.orders = orders
        self.hub = hub
        self.on_log = on_log or (lambda msg: None)
        self.on_error = on_error or (lambda msg: print(f"Web API error: {msg}"))
        self.stopping = threading.Event()
        self._subscribers: set[Queue] = set()
        self._sub_lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ---------------- lifecycle ----------------

    def start(self) -> "WebApiServer":
        handler = type("Handler", (_Handler,), {"app": self})
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="WebApiServer")
        self._thread.start()
        if self.hub is not None:
            self.hub.add_listener(self._on_hub_event)
        self.on_log(f"Web API on http://{self.host}:{self.port}")
        return self

    def stop(self) -> None:
        self.stopping.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    # ---------------- events ----------------

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def broadcast(self, event: str, data: dict) -> None:
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait((event, data))
            except Full:
                pass  # slow client: it gets a fresh snapshot when it reconnects

    def _on_hub_event(self, event: str, sig: Signal) -> None:
        self.broadcast("signal", {"event": event, "signal": sig.to_dict(), "serverTime": time.time()})

    def on_order_event(self, event: str, record: dict) -> None:
        self.broadcast("order", {"order": record, "serverTime": time.time()})

    def snapshot(self) -> dict:
        signals = self.hub.snapshot() if self.hub is not None else {"pending": [], "recent": []}
        return {
            "serverTime": time.time(),
            "approval": self.hub is not None,
            "timeoutMinutes": self.hub.timeout_minutes if self.hub is not None else None,
            "signals": signals,
            "orders": self.orders.recent(),
            "config": self.orders.config(),
        }

    def check_token(self, header: str) -> bool:
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.token.encode())


_SIGNAL_ACTION = re.compile(r"^/api/signals/([0-9a-f]{8,32})/(approve|reject)$")
_SIGNAL_IMAGE = re.compile(r"^/api/signals/([0-9a-f]{8,32})/image$")
_ORDER_CANCEL = re.compile(r"^/api/orders/([0-9a-f]{6,32})/cancel$")


class _Handler(BaseHTTPRequestHandler):
    app: WebApiServer
    server_version = "BotTradeAPI/1.0"

    def log_message(self, format, *args):  # noqa: A002 - keep the Rich dashboard clean
        pass

    # ---------------- helpers ----------------

    def _send_json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise OrderError("Request too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise OrderError("Invalid JSON")
        if not isinstance(data, dict):
            raise OrderError("Invalid JSON")
        return data

    def _authorized(self) -> bool:
        if self.app.check_token(self.headers.get("Authorization", "")):
            return True
        time.sleep(0.3)  # slow down token guessing
        self._send_json(401, {"error": "Unauthorized"})
        return False

    def _path(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    # ---------------- routes ----------------

    def do_GET(self):
        path = self._path()
        if path == "/api/health":
            return self._send_json(200, {"ok": True, "approval": self.app.hub is not None, "serverTime": time.time()})
        if not path.startswith("/api/"):
            return self._send_json(404, {"error": "Not found"})
        if not self._authorized():
            return None
        try:
            if path == "/api/me":
                return self._send_json(200, {"ok": True, "serverTime": time.time()})
            if path == "/api/config":
                return self._send_json(200, self.app.snapshot()["config"])
            if path == "/api/signals":
                return self._send_json(200, self.app.snapshot()["signals"])
            if path == "/api/orders":
                return self._send_json(200, self.app.orders.recent())
            if path == "/api/events":
                return self._stream_events()
            match = _SIGNAL_IMAGE.match(path)
            if match:
                return self._signal_image(match.group(1))
            return self._send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.app.on_error(f"GET {path}: {exc}")
            return self._send_json(500, {"error": str(exc)})

    def do_POST(self):
        path = self._path()
        if not path.startswith("/api/"):
            return self._send_json(404, {"error": "Not found"})
        if not self._authorized():
            return None
        try:
            body = self._read_json()
            if path == "/api/orders/preview":
                return self._send_json(200, self.app.orders.preview(body))
            if path == "/api/orders":
                result = self.app.orders.place(body)
                return self._send_json(200, result)
            match = _ORDER_CANCEL.match(path)
            if match:
                return self._send_json(200, self.app.orders.cancel(match.group(1)))
            match = _SIGNAL_ACTION.match(path)
            if match:
                return self._decide(match.group(1), match.group(2))
            return self._send_json(404, {"error": "Not found"})
        except OrderError as exc:
            return self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.app.on_error(f"POST {path}: {exc}")
            return self._send_json(500, {"error": str(exc)})

    def _decide(self, signal_id: str, action: str):
        hub = self.app.hub
        if hub is None:
            return self._send_json(400, {"error": "Approval is not enabled on the bot"})
        ok, message, sig = hub.decide(signal_id, action, via="web", by="web")
        return self._send_json(200, {"ok": ok, "message": message, "signal": sig.to_dict() if sig else None})

    def _signal_image(self, signal_id: str):
        sig = self.app.hub.get(signal_id) if self.app.hub is not None else None
        if sig is None or not sig.image:
            return self._send_json(404, {"error": "No image"})
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "private, max-age=600")
        self.send_header("Content-Length", str(len(sig.image)))
        self.end_headers()
        self.wfile.write(sig.image)

    def _stream_events(self):
        q = self.app.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._write_event("snapshot", self.app.snapshot())
            while not self.app.stopping.is_set():
                try:
                    event, data = q.get(timeout=KEEPALIVE_SECONDS)
                except Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._write_event(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.unsubscribe(q)

    def _write_event(self, event: str, data) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()
