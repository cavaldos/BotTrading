"""Lighter transaction signer (native library shipped with the official Lighter SDK).

Lighter is a zk-rollup DEX: every order, cancel and leverage change is a transaction
signed with an API key, then sent to POST /api/v1/sendTx. Private reads (open orders,
trade history) need an auth token, also made by the signer.

The signing code exists only as a native library built by Lighter (Go). Its function
signatures change between SDK versions (e.g. 1.1.x added integrator and skip-nonce
arguments), so this file binds to exactly one version: SIGNER_VERSION. Download that
library once with:

    python -m src.live.lighter.signer --download

It is saved to res/lighter/ (ignored by git). LIGHTER_SIGNER_PATH can point to another
copy, but it must be the same version.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import platform
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional, Sequence

import requests  # pyright: ignore[reportMissingModuleSource]

# lighter-sdk version whose signer library matches the bindings below
SIGNER_VERSION = "1.1.8"
SIGNER_DIR = Path(__file__).resolve().parents[3] / "res" / "lighter"

# Order types / time in force / expiry / grouping, as defined by the Lighter SDK
ORDER_TYPE_LIMIT = 0
ORDER_TYPE_MARKET = 1
ORDER_TYPE_STOP_LOSS = 2
ORDER_TYPE_STOP_LOSS_LIMIT = 3
ORDER_TYPE_TAKE_PROFIT = 4
ORDER_TYPE_TAKE_PROFIT_LIMIT = 5

TIF_IMMEDIATE_OR_CANCEL = 0
TIF_GOOD_TILL_TIME = 1
TIF_POST_ONLY = 2

EXPIRY_28_DAYS = -1  # resting orders and SL/TP
EXPIRY_IOC = 0  # immediate-or-cancel orders

GROUPING_ONE_TRIGGERS_THE_OTHER = 1  # entry -> SL
GROUPING_ONE_CANCELS_THE_OTHER = 2  # SL <-> TP (tied to the position)
GROUPING_ONE_TRIGGERS_A_ONE_CANCELS_THE_OTHER = 3  # entry -> SL + TP

MARGIN_MODE_CROSS = 0
MARGIN_MODE_ISOLATED = 1

ALL_MARKETS = 255  # market index meaning "every market" (cancel all)
CANCEL_ALL_IMMEDIATE = 0


class LighterSignerError(Exception):
    pass


class CreateOrderTxReq(ctypes.Structure):
    """One order of a grouped transaction (CreateOrderTxReq in the signer header)."""

    _fields_ = [
        ("MarketIndex", ctypes.c_longlong),
        ("ClientOrderIndex", ctypes.c_longlong),
        ("BaseAmount", ctypes.c_longlong),
        ("Price", ctypes.c_uint32),
        ("IsAsk", ctypes.c_uint8),
        ("Type", ctypes.c_uint8),
        ("TimeInForce", ctypes.c_uint8),
        ("ReduceOnly", ctypes.c_uint8),
        ("TriggerPrice", ctypes.c_uint32),
        ("OrderExpiry", ctypes.c_longlong),
    ]

    def describe(self) -> dict:
        return {name: getattr(self, name) for name, _ in self._fields_}


# Returned strings are allocated by the library and released with its Free(),
# so every char* is declared as c_void_p and read by _take_str().
class _StrOrErr(ctypes.Structure):
    _fields_ = [("str", ctypes.c_void_p), ("err", ctypes.c_void_p)]


class _SignedTxResponse(ctypes.Structure):
    _fields_ = [
        ("txType", ctypes.c_uint8),
        ("txInfo", ctypes.c_void_p),
        ("txHash", ctypes.c_void_p),
        ("messageToSign", ctypes.c_void_p),
        ("err", ctypes.c_void_p),
    ]


class _ApiKeyResponse(ctypes.Structure):
    _fields_ = [("privateKey", ctypes.c_void_p), ("publicKey", ctypes.c_void_p), ("err", ctypes.c_void_p)]


_ll, _int, _u8 = ctypes.c_longlong, ctypes.c_int, ctypes.c_uint8

# Signatures copied from lighter-signer-*.h of lighter-sdk SIGNER_VERSION
_SIGNATURES = {
    "GenerateAPIKey": ([], _ApiKeyResponse),
    "CreateClient": ([ctypes.c_char_p, ctypes.c_char_p, _int, _int, _ll], ctypes.c_void_p),
    "CheckClient": ([_int, _ll], ctypes.c_void_p),
    "CreateAuthToken": ([_ll, _int, _ll], _StrOrErr),
    # market, client order index, base amount, price, is ask, type, time in force, reduce only,
    # trigger price, expiry, integrator account/taker fee/maker fee, self-trade behavior/equality,
    # skip nonce, nonce, api key index, account index
    "SignCreateOrder": (
        [_ll, _ll, _ll, _int, _int, _int, _int, _int, _int, _ll, _ll, _int, _int, _u8, _u8, _u8, _ll, _int, _ll],
        _SignedTxResponse,
    ),
    # grouping type, orders, count, integrator account/taker fee/maker fee,
    # self-trade behavior/equality, skip nonce, nonce, api key index, account index
    "SignCreateGroupedOrders": (
        [_u8, ctypes.POINTER(CreateOrderTxReq), _int, _ll, _int, _int, _u8, _u8, _u8, _ll, _int, _ll],
        _SignedTxResponse,
    ),
    # market, order index, skip nonce, nonce, api key index, account index
    "SignCancelOrder": ([_ll, _ll, _u8, _ll, _int, _ll], _SignedTxResponse),
    # time in force, time, market, skip nonce, nonce, api key index, account index
    "SignCancelAllOrders": ([_int, _ll, _ll, _u8, _ll, _int, _ll], _SignedTxResponse),
    # market, initial margin fraction, margin mode, skip nonce, nonce, api key index, account index
    "SignUpdateLeverage": ([_ll, _int, _int, _u8, _ll, _int, _ll], _SignedTxResponse),
    "Free": ([ctypes.c_void_p], None),
}

_library = None
_library_path: Optional[Path] = None
_library_lock = threading.Lock()


def library_filename() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    x64 = machine in ("x86_64", "amd64")
    if system == "Darwin" and (arm or x64):
        return f"lighter-signer-darwin-{'arm64' if arm else 'amd64'}.dylib"
    if system == "Linux" and (arm or x64):
        return f"lighter-signer-linux-{'arm64' if arm else 'amd64'}.so"
    if system == "Windows" and x64:
        return "lighter-signer-windows-amd64.dll"
    raise LighterSignerError(f"Lighter has no signer library for {system}/{machine}")


def find_library_path() -> Path:
    """LIGHTER_SIGNER_PATH, else res/lighter/<library for this platform>."""
    custom = os.getenv("LIGHTER_SIGNER_PATH", "").strip()
    if custom:
        path = Path(custom).expanduser()
        if not path.is_file():
            raise LighterSignerError(f"LIGHTER_SIGNER_PATH does not exist: {path}")
        return path

    path = SIGNER_DIR / library_filename()
    if path.is_file():
        return path
    raise LighterSignerError(
        f"Lighter signer library not found ({path}). "
        "Download it with: python -m src.live.lighter.signer --download"
    )


def _load_library(path: Optional[Path] = None):
    global _library, _library_path
    with _library_lock:
        if _library is not None:
            return _library
        path = Path(path) if path else find_library_path()
        lib = ctypes.CDLL(str(path))
        for name, (argtypes, restype) in _SIGNATURES.items():
            function = getattr(lib, name)
            function.argtypes = argtypes
            function.restype = restype
        _library, _library_path = lib, path
        return lib


def _take_str(lib, pointer) -> Optional[str]:
    """Read a library-owned C string and free it."""
    if not pointer:
        return None
    try:
        value = ctypes.cast(pointer, ctypes.c_char_p).value
        return value.decode("utf-8") if value is not None else None
    finally:
        lib.Free(pointer)


def generate_api_key(library_path: Optional[Path] = None) -> tuple[str, str]:
    """New random API key pair (private, public). Used by tests; registering a key
    on an account needs the wallet's L1 signature (Lighter app or SDK)."""
    lib = _load_library(library_path)
    result = lib.GenerateAPIKey()
    private_key = _take_str(lib, result.privateKey)
    public_key = _take_str(lib, result.publicKey)
    error = _take_str(lib, result.err)
    if error:
        raise LighterSignerError(f"GenerateAPIKey: {error}")
    return private_key, public_key


class LighterSigner:
    """Signs Lighter transactions for one (account index, API key index)."""

    def __init__(
        self,
        base_url: str,
        private_key: str,
        account_index: int,
        api_key_index: int,
        chain_id: int,
        library_path: Optional[Path] = None,
    ):
        self.lib = _load_library(library_path)
        self.account_index = int(account_index)
        self.api_key_index = int(api_key_index)
        self._lock = threading.Lock()

        key = private_key.strip()
        if key.startswith("0x"):
            key = key[2:]
        error = _take_str(
            self.lib,
            self.lib.CreateClient(
                base_url.encode("utf-8"), key.encode("utf-8"), int(chain_id), self.api_key_index, self.account_index
            ),
        )
        if error:
            raise LighterSignerError(f"Could not create Lighter signer: {error}")

    @property
    def library_path(self) -> Optional[Path]:
        return _library_path

    def check_client(self) -> Optional[str]:
        """None when the API key registered on Lighter matches LIGHTER_API_PRIVATE_KEY, else the error."""
        with self._lock:
            return _take_str(self.lib, self.lib.CheckClient(self.api_key_index, self.account_index))

    def create_auth_token(self, ttl_seconds: int = 600) -> str:
        """Auth token for private GET endpoints, valid for ttl_seconds."""
        deadline = int(time.time()) + int(ttl_seconds)
        with self._lock:
            result = self.lib.CreateAuthToken(deadline, self.api_key_index, self.account_index)
            token = _take_str(self.lib, result.str)
            error = _take_str(self.lib, result.err)
        if error or not token:
            raise LighterSignerError(f"Could not create auth token: {error or 'empty token'}")
        return token

    def _decode(self, result: _SignedTxResponse, label: str) -> tuple[int, str]:
        tx_info = _take_str(self.lib, result.txInfo)
        _take_str(self.lib, result.txHash)
        _take_str(self.lib, result.messageToSign)
        error = _take_str(self.lib, result.err)
        if error or not tx_info:
            raise LighterSignerError(f"{label}: {error or 'empty transaction'}")
        return int(result.txType), tx_info

    def sign_create_order(self, order: CreateOrderTxReq, nonce: int) -> tuple[int, str]:
        with self._lock:
            result = self.lib.SignCreateOrder(
                order.MarketIndex, order.ClientOrderIndex, order.BaseAmount, order.Price, order.IsAsk,
                order.Type, order.TimeInForce, order.ReduceOnly, order.TriggerPrice, order.OrderExpiry,
                0, 0, 0,  # no integrator
                0, 0,  # default self-trade handling
                0, int(nonce), self.api_key_index, self.account_index,
            )
            return self._decode(result, "SignCreateOrder")

    def sign_create_grouped_orders(
        self, grouping_type: int, orders: Sequence[CreateOrderTxReq], nonce: int
    ) -> tuple[int, str]:
        array = (CreateOrderTxReq * len(orders))(*orders)
        with self._lock:
            result = self.lib.SignCreateGroupedOrders(
                int(grouping_type), array, len(orders),
                0, 0, 0,  # no integrator
                0, 0,  # default self-trade handling
                0, int(nonce), self.api_key_index, self.account_index,
            )
            return self._decode(result, "SignCreateGroupedOrders")

    def sign_cancel_order(self, market_index: int, order_index: int, nonce: int) -> tuple[int, str]:
        with self._lock:
            result = self.lib.SignCancelOrder(
                int(market_index), int(order_index), 0, int(nonce), self.api_key_index, self.account_index
            )
            return self._decode(result, "SignCancelOrder")

    def sign_cancel_all_orders(self, nonce: int, market_index: int = ALL_MARKETS) -> tuple[int, str]:
        with self._lock:
            result = self.lib.SignCancelAllOrders(
                CANCEL_ALL_IMMEDIATE, 0, int(market_index), 0, int(nonce), self.api_key_index, self.account_index
            )
            return self._decode(result, "SignCancelAllOrders")

    def sign_update_leverage(self, market_index: int, leverage: float, margin_mode: int, nonce: int) -> tuple[int, str]:
        # Lighter stores leverage as initial margin fraction in 1/10000 (20x -> 500)
        initial_margin_fraction = int(10_000 / float(leverage))
        with self._lock:
            result = self.lib.SignUpdateLeverage(
                int(market_index), initial_margin_fraction, int(margin_mode), 0, int(nonce),
                self.api_key_index, self.account_index,
            )
            return self._decode(result, "SignUpdateLeverage")


# ---------------------------------------------------------------------------
# Download the library that matches SIGNER_VERSION
# ---------------------------------------------------------------------------

def download_library(dest_dir: Path = SIGNER_DIR, version: str = SIGNER_VERSION) -> Path:
    """Download the lighter-sdk wheel from PyPI, check its sha256 and extract the
    signer library for this platform into dest_dir."""
    name = library_filename()
    meta = requests.get(f"https://pypi.org/pypi/lighter-sdk/{version}/json", timeout=30)
    meta.raise_for_status()
    wheels = [item for item in meta.json().get("urls", []) if item.get("filename", "").endswith(".whl")]
    if not wheels:
        raise LighterSignerError(f"lighter-sdk {version} has no wheel on PyPI")
    wheel = wheels[0]
    expected_sha256 = (wheel.get("digests") or {}).get("sha256")

    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wheel_path = Path(tmp) / wheel["filename"]
        digest = hashlib.sha256()
        print(f"Downloading {wheel['filename']} ({wheel.get('size', 0) / 1e6:.0f} MB)...")
        with requests.get(wheel["url"], stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(wheel_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    file.write(chunk)
                    digest.update(chunk)
        if expected_sha256 and digest.hexdigest() != expected_sha256:
            raise LighterSignerError("Downloaded wheel does not match the sha256 published on PyPI")

        with zipfile.ZipFile(wheel_path) as archive:
            data = archive.read(f"lighter/signers/{name}")

    target = dest_dir / name
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(data)
    os.replace(partial, target)
    (dest_dir / "VERSION").write_text(f"lighter-sdk {version}\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Lighter signer library")
    parser.add_argument("--download", action="store_true", help=f"download the signer of lighter-sdk {SIGNER_VERSION}")
    args = parser.parse_args()

    if args.download:
        path = download_library()
        print(f"Saved: {path}")
    try:
        path = find_library_path()
        _load_library(path)
        print(f"Signer library OK: {path} (bindings for lighter-sdk {SIGNER_VERSION})")
    except (LighterSignerError, OSError) as error:
        print(f"Signer library not ready: {error}")


if __name__ == "__main__":
    main()
