"""Turn an image input into a URL a model accepts.

Accepted: file path (str or Path), bytes, a file-like object (BytesIO, open file),
raw base64, a data: URL, or an http(s) URL (sent as is).
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any

from .schema import BadInput

ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_DATA_URL = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def bytes_to_data_url(data: bytes, max_bytes: int | None = None) -> str:
    if not data:
        raise BadInput("image is empty")
    if max_bytes and len(data) > max_bytes:
        raise BadInput(f"image is {len(data) / 1e6:.1f} MB, limit is {max_bytes / 1e6:.1f} MB")
    mime = sniff_mime(data)
    if mime not in ALLOWED_MIME:
        raise BadInput("unsupported image type (use png, jpeg, webp or gif)")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def to_image_url(src: Any, *, max_bytes: int | None = None) -> str:
    if isinstance(src, (bytes, bytearray, memoryview)):
        return bytes_to_data_url(bytes(src), max_bytes)
    if isinstance(src, Path):
        return _read_file(src, max_bytes)
    if hasattr(src, "getvalue"):  # BytesIO
        return bytes_to_data_url(src.getvalue(), max_bytes)
    if hasattr(src, "read"):  # open file
        return bytes_to_data_url(src.read(), max_bytes)
    if not isinstance(src, str):
        raise BadInput(f"unsupported image input: {type(src).__name__}")

    s = src.strip()
    if s.startswith(("http://", "https://")):
        return s
    m = _DATA_URL.match(s)
    if m:
        return bytes_to_data_url(_b64(m.group("data")), max_bytes)
    if len(s) < 4096 and Path(s).expanduser().is_file():
        return _read_file(Path(s).expanduser(), max_bytes)
    try:
        raw = _b64(s)
    except BadInput:
        raise BadInput(f"image is not a file, URL, data URL or base64: {s[:80]!r}") from None
    return bytes_to_data_url(raw, max_bytes)


def _read_file(path: Path, max_bytes: int | None) -> str:
    if not path.is_file():
        raise BadInput(f"image file not found: {path}")
    if max_bytes and path.stat().st_size > max_bytes:
        raise BadInput(f"image {path.name} is larger than {max_bytes / 1e6:.1f} MB")
    return bytes_to_data_url(path.read_bytes(), max_bytes)


def _b64(s: str) -> bytes:
    try:
        return base64.b64decode("".join(s.split()), validate=True)
    except (binascii.Error, ValueError):
        raise BadInput("invalid base64 image data") from None
