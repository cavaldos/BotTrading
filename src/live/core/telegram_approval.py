"""
Telegram channel of the approval hub (fallback when the web chart is not reachable).

Each new signal is sent as chart image + entry text with two buttons:
  ✅ Đồng ý -> hub.decide(id, "approve")      ❌ Bỏ qua -> hub.decide(id, "reject")
When the signal is decided anywhere (web or Telegram) or expires, the message is
rewritten with the final status and the buttons are removed.

Only clicks coming from TELEGRAM_CHAT_ID are accepted (and, when
`approval.allowed_user_ids` is set, only from those Telegram user ids).
"""

from __future__ import annotations

import html
import json
import os
import threading
from typing import Any, Callable, Optional

import requests

from src.live.core.approval import ApprovalHub, Signal
from src.live.core.discord_notify import format_entry_message_telegram


APPROVAL_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "timeout_minutes": 5,
    "telegram": True,          # also send signals to Telegram (with buttons)
    "allowed_user_ids": [],
    "auto": False,             # start in auto mode (no approval); the web toggle overrides it
    "auto_file": "res/logs/approval_mode.json",  # auto mode set on the web, kept after a restart
}

POLL_SECONDS = 10
CONFLICT_RETRY_SECONDS = 30  # back off while another instance polls the same token
CAPTION_LIMIT = 1024       # Telegram limit for photo captions
MESSAGE_LIMIT = 4096       # Telegram limit for text messages

VIA_LABEL = {"web": "Web", "telegram": "Telegram", "auto": "Tự động"}


def load_approval_config(live_config: dict | None) -> dict[str, Any]:
    raw = (live_config or {}).get("approval") or {}
    cfg = dict(APPROVAL_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


def send_telegram_text(token: str, chat_id: str, text: str, timeout: float = 15) -> bool:
    """Send a plain text message (shown as preformatted). Returns False on any failure."""
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"<pre>{html.escape(text)[: MESSAGE_LIMIT - 20]}</pre>", "parse_mode": "HTML"},
            timeout=timeout,
        )
        return bool(response.json().get("ok"))
    except Exception:
        return False


def send_entry_telegram(message_kwargs: dict, image: bytes | None, token: str | None = None, chat_id: str | None = None) -> bool:
    """Entry message (chart image + text + order result) on Telegram, without approval buttons.

    Used when the bot trades without the approval hub. Token / chat id default to .env.
    The image is always sent when there is one (text goes in a second message if too long for a caption).
    """
    token = token if token is not None else os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    kwargs = {k: v for k, v in message_kwargs.items() if k != "result"}
    text = format_entry_message_telegram(**kwargs)
    result = str(message_kwargs.get("result") or "").replace("**", "").strip()
    if result:
        text += f"\n<pre>{html.escape(result)}</pre>"
    api = f"https://api.telegram.org/bot{token}"
    try:
        if image:
            caption = text if len(text) <= CAPTION_LIMIT else None
            data = {"chat_id": chat_id, **({"caption": caption, "parse_mode": "HTML"} if caption else {})}
            response = requests.post(
                f"{api}/sendPhoto", data=data, files={"photo": ("chart.png", image, "image/png")}, timeout=30,
            )
            sent_photo = bool(response.json().get("ok"))
            if sent_photo and caption:
                return True
        response = requests.post(
            f"{api}/sendMessage",
            json={"chat_id": chat_id, "text": text[:MESSAGE_LIMIT], "parse_mode": "HTML"},
            timeout=15,
        )
        return bool(response.json().get("ok"))
    except Exception:
        return False


def status_line(sig: Signal, timeout_minutes: float) -> str:
    if sig.decided_via == "telegram":
        who = f"{sig.decided_by} (Telegram)"
    else:
        who = VIA_LABEL.get(sig.decided_via, sig.decided_via)
    line = {
        "PENDING": f"⏳ Chờ duyệt (hết hạn sau {timeout_minutes:g} phút) — duyệt trên Web hoặc bấm nút",
        "PLACING": f"⏳ Đã duyệt bởi {who} — đang đặt lệnh...",
        "PLACED": f"✅ ĐÃ GỬI LỆNH (duyệt bởi {who}) — chờ sàn khớp",
        "FAILED": f"⚠️ ĐẶT LỆNH THẤT BẠI (duyệt bởi {who})",
        "REJECTED": f"❌ ĐÃ BỎ QUA bởi {who}",
        "EXPIRED": "⌛ HẾT HẠN — không đặt lệnh",
    }.get(sig.status, sig.status)
    if sig.edited and sig.status in ("PLACING", "PLACED", "FAILED"):
        line += f"\nĐã chỉnh trên web: {sig.order_type.upper()} @ {sig.entry:g} · SL {sig.stop_loss:g} · TP {sig.take_profit:g}"
    return line


class TelegramChannel:
    def __init__(
        self,
        token: str,
        chat_id: str,
        hub: ApprovalHub,
        allowed_user_ids: Optional[list] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID is not set in .env")
        self._api = f"https://api.telegram.org/bot{token}"
        self._token = token
        self.chat_id = str(chat_id).strip()
        self.hub = hub
        self.allowed_user_ids = {int(uid) for uid in (allowed_user_ids or [])}
        self.on_log = on_log or (lambda msg: None)
        self.on_error = on_error or (lambda msg: print(f"Telegram error: {msg}"))
        self._messages: dict[str, tuple[int, bool]] = {}   # signal id -> (message_id, has_photo)
        self._stop = threading.Event()
        self._offset: Optional[int] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="TelegramChannel")

    # ---------------- Telegram API ----------------

    def _call(self, method: str, http_timeout: float = 15, files: dict | None = None, **params) -> Any:
        url = f"{self._api}/{method}"
        params = {k: v for k, v in params.items() if v is not None}
        if files:
            data = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
            response = requests.post(url, data=data, files=files, timeout=http_timeout)
        else:
            response = requests.post(url, json=params, timeout=http_timeout)
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError(f"Telegram {method}: invalid response")
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method}: {payload.get('description', response.status_code)}")
        return payload.get("result")

    # ---------------- lifecycle ----------------

    def start(self) -> "TelegramChannel":
        self._call("getMe")  # fails fast on a bad token
        # Ignore button clicks made before this run (their signals are gone).
        updates = self._call("getUpdates", offset=-1, timeout=0)
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        self.hub.add_listener(self.on_event)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    # ---------------- outgoing ----------------

    def notify(self, text: str) -> None:
        """Plain information message (e.g. an order placed from the web)."""
        if not send_telegram_text(self._token, self.chat_id, text):
            self.on_error("notify failed")

    def on_event(self, event: str, sig: Signal) -> None:
        if event == "new":
            self._send_signal(sig)
        elif event == "update":
            self._update_signal(sig)

    def _text(self, sig: Signal) -> str:
        base = format_entry_message_telegram(**sig.message_kwargs)
        return f"{base}\n\n<b>{html.escape(status_line(sig, self.hub.timeout_minutes))}</b>"

    def _send_signal(self, sig: Signal) -> None:
        markup = {"inline_keyboard": [[
            {"text": "✅ Đồng ý", "callback_data": f"ok:{sig.id}"},
            {"text": "❌ Bỏ qua", "callback_data": f"no:{sig.id}"},
        ]]}
        text = self._text(sig)
        message = None
        has_photo = False
        if sig.image and len(text) <= CAPTION_LIMIT:
            try:
                message = self._call(
                    "sendPhoto",
                    http_timeout=30,
                    files={"photo": ("chart.png", sig.image, "image/png")},
                    chat_id=self.chat_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                has_photo = True
            except Exception as exc:
                self.on_error(f"{sig.label}: send photo failed, sending text only ({exc})")
        elif sig.image:
            # Text too long for a caption: image first, then the text with the buttons
            try:
                self._call("sendPhoto", http_timeout=30, files={"photo": ("chart.png", sig.image, "image/png")},
                           chat_id=self.chat_id)
            except Exception as exc:
                self.on_error(f"{sig.label}: send photo failed ({exc})")
        if message is None:
            try:
                message = self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML", reply_markup=markup)
            except Exception as exc:
                self.on_error(f"{sig.label}: send failed ({exc})")
                return
        self._messages[sig.id] = (int(message["message_id"]), has_photo)
        # Decided before the message went out (e.g. on the web): show the final state now.
        if sig.status != "PENDING":
            self._update_signal(sig)

    def _update_signal(self, sig: Signal) -> None:
        sent = self._messages.get(sig.id)
        if sent is None:
            return
        message_id, has_photo = sent
        params = dict(chat_id=self.chat_id, message_id=message_id, parse_mode="HTML", reply_markup={"inline_keyboard": []})
        text = self._text(sig)
        try:
            if has_photo:
                self._call("editMessageCaption", caption=text, **params)
            else:
                self._call("editMessageText", text=text, **params)
        except Exception as exc:
            if "not modified" not in str(exc):
                self.on_error(f"{sig.label}: edit message failed ({exc})")
        if sig.status in ("PLACED", "FAILED") and sig.result_text:
            try:
                self._call(
                    "sendMessage",
                    chat_id=self.chat_id,
                    text=f"<pre>{html.escape(sig.result_text)[: MESSAGE_LIMIT - 20]}</pre>",
                    parse_mode="HTML",
                    reply_to_message_id=message_id,
                )
            except Exception as exc:
                self.on_error(f"{sig.label}: reply failed ({exc})")
        if sig.status in ("PLACED", "FAILED", "REJECTED", "EXPIRED"):
            self._messages.pop(sig.id, None)

    # ---------------- incoming ----------------

    def _answer(self, callback_id: str, text: str) -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except Exception:
            pass

    def _handle_callback(self, query: dict) -> None:
        callback_id = query.get("id", "")
        action, _, signal_id = str(query.get("data") or "").partition(":")
        chat_id = str(((query.get("message") or {}).get("chat") or {}).get("id", ""))
        user = query.get("from") or {}
        user_id = user.get("id")

        if chat_id != self.chat_id or (self.allowed_user_ids and user_id not in self.allowed_user_ids):
            self._answer(callback_id, "Bạn không có quyền duyệt lệnh này")
            return
        if action not in ("ok", "no"):
            self._answer(callback_id, "Không rõ thao tác")
            return
        sig = self.hub.get(signal_id)
        if sig is None or sig.status != "PENDING":
            self._answer(callback_id, "Tín hiệu đã hết hạn hoặc đã được xử lý")
            return

        who = user.get("username") or user.get("first_name") or str(user_id)
        self._answer(callback_id, "Đang đặt lệnh..." if action == "ok" else "Đã bỏ qua")
        ok, message, _ = self.hub.decide(signal_id, "approve" if action == "ok" else "reject", via="telegram", by=who)
        if not ok and message.startswith("Signal"):
            self.on_log(f"Telegram click ignored: {message}")

    def _run(self) -> None:
        in_conflict = False
        while not self._stop.is_set():
            try:
                updates = self._call(
                    "getUpdates",
                    http_timeout=POLL_SECONDS + 10,
                    offset=self._offset,
                    timeout=POLL_SECONDS,
                    allowed_updates=["callback_query"],
                )
            except Exception as exc:
                if "Conflict" in str(exc):
                    # Telegram allows one reader per token: another bot (e.g. the VPS) uses the same TELEGRAM_TOKEN.
                    if not in_conflict:
                        self.on_error(
                            "another bot instance is reading this Telegram token - button clicks may be lost; "
                            "run only one bot per token (or set approval.telegram: false on this machine)"
                        )
                        in_conflict = True
                    self._stop.wait(CONFLICT_RETRY_SECONDS)
                    continue
                self.on_error(f"poll failed: {exc}")
                self._stop.wait(5)
                continue
            if in_conflict:
                self.on_log("Telegram polling resumed (no other bot instance on this token)")
                in_conflict = False
            for update in updates or []:
                self._offset = update["update_id"] + 1
                query = update.get("callback_query")
                if query:
                    self._handle_callback(query)
