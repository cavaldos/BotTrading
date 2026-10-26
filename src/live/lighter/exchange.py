"""Lighter (zk-rollup perp DEX) trading client for the live tools.

Same module functions as src/live/bybit/exchange.py (get_exchange, calculate_position_size,
place_market_order_with_sl, place_limit_order_with_sl, set_position_sl_tp, cancel_order, ...),
and the client offers the ccxt-style methods the tools use (fetch_balance, fetch_positions,
fetch_open_orders, fetch_ticker, cancel_order, price_to_precision, ...).

How Lighter differs from Bybit / Binance:
- Markets settle in USDC and are named by base asset ("BTC"). Config symbols such as
  BTCUSDT or BTCUSDT_1m map to the Lighter market "BTC".
- One position per market (no hedge mode): an order on the other side reduces or flips the
  position, so opening against an open position is refused here.
- Account, balance and positions are public: only LIGHTER_ACCOUNT_INDEX is needed to read them.
  Orders, cancels and leverage changes are transactions signed with an API key (signer.py);
  open orders and trade history need an auth token from the same key.
- SL/TP go out with the entry in one grouped transaction (entry triggers SL + TP, SL and TP
  cancel each other). Their size follows the filled size of the entry.
- A market order needs a worst acceptable price: best bid/ask +/- LIGHTER_MAX_SLIPPAGE_PCT.
  SL/TP are stop-limit orders whose limit sits LIGHTER_TRIGGER_SLIPPAGE_PCT past the trigger.
- Standard accounts get 60 REST requests per minute per IP. Requests are spread out by a
  limiter (LIGHTER_REQUESTS_PER_MINUTE); the candle download in the bot uses the same budget.

.env keys: see the Lighter section of .env.example.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any, Callable, Iterable, Optional

import requests  # pyright: ignore[reportMissingModuleSource]
from dotenv import load_dotenv

from src.live.lighter import signer as lighter_signer
from src.live.lighter.signer import CreateOrderTxReq, LighterSigner, LighterSignerError
from src.utils.rounding import round_price

load_dotenv()

MAINNET_URL = "https://mainnet.zklighter.elliot.ai"
TESTNET_URL = "https://testnet.zklighter.elliot.ai"
HTTP_TIMEOUT = (5.0, 20.0)

# kind -> (order type, time in force, expiry)
_ORDER_KINDS = {
    "limit": (lighter_signer.ORDER_TYPE_LIMIT, lighter_signer.TIF_GOOD_TILL_TIME, lighter_signer.EXPIRY_28_DAYS),
    "post_only": (lighter_signer.ORDER_TYPE_LIMIT, lighter_signer.TIF_POST_ONLY, lighter_signer.EXPIRY_28_DAYS),
    # marketable limit order: fills now up to `price`, the rest is cancelled
    "ioc": (lighter_signer.ORDER_TYPE_LIMIT, lighter_signer.TIF_IMMEDIATE_OR_CANCEL, lighter_signer.EXPIRY_IOC),
    "stop_loss": (lighter_signer.ORDER_TYPE_STOP_LOSS_LIMIT, lighter_signer.TIF_GOOD_TILL_TIME, lighter_signer.EXPIRY_28_DAYS),
    "take_profit": (lighter_signer.ORDER_TYPE_TAKE_PROFIT_LIMIT, lighter_signer.TIF_GOOD_TILL_TIME, lighter_signer.EXPIRY_28_DAYS),
}

# Lighter has no fill-or-kill
_TIME_IN_FORCE_KINDS = {"GTC": "limit", "GTT": "limit", "POSTONLY": "post_only", "PO": "post_only", "IOC": "ioc"}

_ORDER_STATUS = {
    "in-progress": "open",
    "pending": "open",
    "open": "open",
    "filled": "closed",
    "canceled-expired": "expired",
    "canceled-position-not-allowed": "rejected",
    "canceled-margin-not-allowed": "rejected",
    "canceled-invalid-balance": "rejected",
}

TRIGGER_ORDER_TYPES = ("stop-loss", "stop-loss-limit", "take-profit", "take-profit-limit")


class LighterAPIError(Exception):
    def __init__(self, message: str, code: Any = None, status: Optional[int] = None, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        # True when a transaction may have reached Lighter even though the call failed
        self.uncertain = uncertain


class LighterConfigError(LighterAPIError):
    """Missing or invalid .env settings."""


# ---------------------------------------------------------------------------
# Settings and small helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _env_int(name: str) -> Optional[int]:
    value = _env(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise LighterConfigError(f"{name} must be an integer (got '{value}')")


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        raise LighterConfigError(f"{name} must be a number (got '{value}')")


def get_base_url() -> str:
    if _env("LIGHTER_TESTNET").lower() in {"1", "true", "yes", "on"}:
        return TESTNET_URL
    return _env("LIGHTER_BASE_URL", MAINNET_URL).rstrip("/") or MAINNET_URL


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_ms(value: Any) -> Optional[int]:
    """Lighter mixes seconds, milliseconds and microseconds; return milliseconds."""
    number = _float(value, 0.0)
    if number <= 0:
        return None
    if number > 1e14:  # microseconds
        return int(number / 1000)
    if number > 1e11:  # milliseconds
        return int(number)
    return int(number * 1000)  # seconds


def _decimal_places(value: float) -> int:
    text = f"{value:.12f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def clean_symbol(symbol: str) -> str:
    """Config/bot symbol -> Lighter market symbol: BTCUSDT_1m, BTC/USDC:USDC, BTC-PERP -> BTC."""
    raw = str(symbol).strip().upper()
    base = raw.split("_", 1)[0].split(":", 1)[0].split("/", 1)[0]
    for suffix in ("-PERP", "PERP", "USDT", "USDC", "USD"):
        if base.endswith(suffix) and len(base) > len(suffix):
            return base[: -len(suffix)]
    return base


def format_symbol(symbol: str) -> str:
    """Same role as bybit.format_symbol: the symbol the exchange uses (here 'BTC')."""
    return clean_symbol(symbol)


def new_client_order_index() -> int:
    """Unique client order index; the next two numbers are free for the SL/TP of the same group."""
    return (int(time.time() * 1000) * 4) % (2 ** 47) or 4


def _normalize_side(side: str) -> str:
    value = str(side).strip().lower()
    if value in ("buy", "long"):
        return "buy"
    if value in ("sell", "short"):
        return "sell"
    raise ValueError(f"side phải là buy/sell (nhận được '{side}')")


class _RateLimiter:
    """At most `per_minute` requests in any 60 s window, and `min_interval` between two."""

    def __init__(self, per_minute: int, min_interval: float):
        self.per_minute = max(1, int(per_minute))
        self.min_interval = max(0.0, float(min_interval))
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                while self._sent and now - self._sent[0] >= 60.0:
                    self._sent.popleft()
                delay = 0.0
                if len(self._sent) >= self.per_minute:
                    delay = 60.0 - (now - self._sent[0])
                if self._sent:
                    delay = max(delay, self.min_interval - (now - self._sent[-1]))
                if delay <= 0:
                    self._sent.append(now)
                    return
                time.sleep(delay)


_rate_limiter = _RateLimiter(
    per_minute=int(_env_float("LIGHTER_REQUESTS_PER_MINUTE", 40)),
    min_interval=_env_float("LIGHTER_MIN_SPACING_SECONDS", 0.25),
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LighterExchange:
    """REST client for one Lighter account. Read-only without an API key."""

    id = "lighter"
    name = "Lighter"

    def __init__(
        self,
        account_index: Optional[int] = None,
        api_key_index: Optional[int] = None,
        private_key: Optional[str] = None,
        wallet_address: Optional[str] = None,
        base_url: Optional[str] = None,
        margin_mode: str = "cross",
        max_slippage_pct: float = 0.5,
        trigger_slippage_pct: float = 1.0,
    ):
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.testnet = "testnet" in self.base_url
        self.chain_id = 300 if self.testnet else 304
        self.wallet_address = (wallet_address or "").strip() or None
        self.api_key_index = api_key_index
        self._private_key = (private_key or "").strip() or None
        self._account_index = account_index
        self.margin_mode = margin_mode if margin_mode in ("cross", "isolated") else "cross"
        self.max_slippage_pct = max(0.0, float(max_slippage_pct))
        self.trigger_slippage_pct = max(0.0, float(trigger_slippage_pct))

        self.session = requests.Session()
        self.markets: dict[str, dict] = {}
        self.markets_by_id: dict[int, dict] = {}
        self._market_lookup: dict[str, dict] = {}
        self._tickers: dict[str, dict] = {}
        self._tickers_at = 0.0

        self._signer: Optional[LighterSigner] = None
        self._auth_token: Optional[str] = None
        self._auth_expires_at = 0.0
        self._nonce: Optional[int] = None
        self._tx_lock = threading.Lock()
        self._lock = threading.Lock()

    # -- configuration -------------------------------------------------------

    @property
    def has_api_key(self) -> bool:
        return bool(self._private_key) and self.api_key_index is not None

    @property
    def account_index(self) -> int:
        if self._account_index is None:
            if not self.wallet_address:
                raise LighterConfigError("Thiếu LIGHTER_ACCOUNT_INDEX (hoặc LIGHTER_WALLET_ADDRESS) trong .env")
            data = self._request("GET", "accountsByL1Address", {"l1_address": self.wallet_address})
            accounts = data.get("sub_accounts") or []
            if not accounts:
                raise LighterConfigError(f"Ví {self.wallet_address} chưa có tài khoản Lighter")
            main = next((a for a in accounts if str(a.get("account_type")) == "0"), accounts[0])
            self._account_index = int(main["index"])
        return self._account_index

    @property
    def signer(self) -> LighterSigner:
        if self._signer is None:
            if not self.has_api_key:
                raise LighterConfigError(
                    "Thiếu API key Lighter: cần LIGHTER_API_PRIVATE_KEY và LIGHTER_API_KEY_INDEX trong .env"
                )
            with self._lock:
                if self._signer is None:
                    self._signer = LighterSigner(
                        self.base_url, self._private_key, self.account_index, self.api_key_index, self.chain_id
                    )
        return self._signer

    # -- HTTP ------------------------------------------------------------------

    def _auth_header(self) -> str:
        now = time.time()
        if self._auth_token is None or now > self._auth_expires_at:
            self._auth_token = self.signer.create_auth_token(ttl_seconds=600)
            self._auth_expires_at = now + 540  # renew one minute before it expires
        return self._auth_token

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        auth: bool = False,
        retries: int = 3,
    ) -> dict:
        url = f"{self.base_url}/api/v1/{path}"
        headers = {"authorization": self._auth_header()} if auth else None

        for attempt in range(1, retries + 1):
            _rate_limiter.wait()
            try:
                response = self.session.request(method, url, params=params, data=data, headers=headers, timeout=HTTP_TIMEOUT)
            except requests.RequestException as error:
                if attempt >= retries:
                    raise LighterAPIError(f"{path}: {error}") from error
                time.sleep(0.5 * attempt)
                continue

            if response.status_code in (405, 429):  # rate limited (405 comes from the firewall)
                if attempt >= retries:
                    raise LighterAPIError(f"{path}: rate limited (HTTP {response.status_code})", status=response.status_code)
                time.sleep(min(60.0, 15.0 * attempt))
                continue
            if response.status_code >= 500 and attempt < retries:
                time.sleep(0.5 * attempt)
                continue

            try:
                payload = response.json()
            except ValueError:
                raise LighterAPIError(f"{path}: HTTP {response.status_code} {response.text[:200]}", status=response.status_code)

            code = payload.get("code") if isinstance(payload, dict) else None
            if response.status_code >= 400 or code not in (None, 0, 200):
                message = payload.get("message") if isinstance(payload, dict) else None
                raise LighterAPIError(
                    f"{path}: {(message or response.text[:200]).strip()} (code {code})", code=code, status=response.status_code
                )
            return payload

        raise LighterAPIError(f"{path}: no response")

    # -- markets and prices ----------------------------------------------------

    def load_markets(self, reload: bool = False) -> dict[str, dict]:
        if self.markets and not reload:
            return self.markets
        data = self._request("GET", "orderBookDetails")
        self._store_details(data.get("order_book_details") or [], replace=True)
        return self.markets

    def _store_details(self, details: Iterable[dict], replace: bool = False) -> None:
        markets = {} if replace else dict(self.markets)
        tickers = {} if replace else dict(self._tickers)
        for item in details:
            if str(item.get("market_type", "perp")) != "perp" or item.get("status") != "active":
                continue
            market = self._parse_market(item)
            markets[market["symbol"]] = market
            tickers[market["symbol"]] = self._parse_ticker(item, market)
        self.markets = markets
        self.markets_by_id = {m["id"]: m for m in markets.values()}
        self._market_lookup = {symbol.upper(): m for symbol, m in markets.items()}
        self._tickers = tickers
        self._tickers_at = time.time()

    @staticmethod
    def _parse_market(item: dict) -> dict:
        size_decimals = int(item.get("size_decimals") or 0)
        price_decimals = int(item.get("price_decimals") or 0)
        min_imf = _float(item.get("min_initial_margin_fraction"))
        default_imf = _float(item.get("default_initial_margin_fraction"))
        return {
            "id": int(item["market_id"]),
            "symbol": str(item["symbol"]),
            "base": str(item["symbol"]),
            "quote": "USDC",
            "settle": "USDC",
            "size_decimals": size_decimals,
            "price_decimals": price_decimals,
            "amount_step": 10 ** -size_decimals,
            "price_step": 10 ** -price_decimals,
            "min_amount": _float(item.get("min_base_amount")),
            "min_cost": _float(item.get("min_quote_amount")),
            # margin fractions are in 1/10000: 200 -> 2% -> 50x
            "max_leverage": 10_000 / min_imf if min_imf else None,
            "default_leverage": 10_000 / default_imf if default_imf else None,
            "taker_fee": _float(item.get("taker_fee")),
            "maker_fee": _float(item.get("maker_fee")),
        }

    @staticmethod
    def _parse_ticker(item: dict, market: dict) -> dict:
        last = _float(item.get("last_trade_price"), None)
        return {
            "symbol": market["symbol"],
            "last": last,
            "close": last,
            "markPrice": _float(item.get("mark_price"), None),
            "indexPrice": _float(item.get("index_price"), None),
            "high": _float(item.get("daily_price_high"), None),
            "low": _float(item.get("daily_price_low"), None),
            "percentage": _float(item.get("daily_price_change"), None),
            "baseVolume": _float(item.get("daily_base_token_volume"), None),
            "quoteVolume": _float(item.get("daily_quote_token_volume"), None),
            "openInterestUsd": _float(item.get("open_interest_usd"), None),
            "bid": None,
            "ask": None,
            "timestamp": int(time.time() * 1000),
        }

    def market(self, symbol: str) -> dict:
        self.load_markets()
        found = self._market_lookup.get(str(symbol).strip().upper()) or self._market_lookup.get(clean_symbol(symbol))
        if found is None:
            raise ValueError(f"Lighter không có market perp cho '{symbol}' (đã thử '{clean_symbol(symbol)}')")
        return found

    def has_market(self, symbol: str) -> bool:
        try:
            self.market(symbol)
            return True
        except ValueError:
            return False

    def fetch_tickers(self, symbols: Optional[Iterable[str]] = None, max_age: float = 15.0) -> dict[str, dict]:
        """Prices of every market from one request (cached for max_age seconds)."""
        if not self._tickers or time.time() - self._tickers_at > max_age:
            data = self._request("GET", "orderBookDetails")
            self._store_details(data.get("order_book_details") or [], replace=True)
        if symbols is None:
            return dict(self._tickers)
        wanted = {self.market(s)["symbol"] for s in symbols}
        return {s: t for s, t in self._tickers.items() if s in wanted}

    def fetch_ticker(self, symbol: str, max_age: float = 0.0) -> dict:
        market = self.market(symbol)
        cached = self._tickers.get(market["symbol"])
        if cached and max_age > 0 and time.time() * 1000 - cached["timestamp"] <= max_age * 1000:
            return dict(cached)
        data = self._request("GET", "orderBookDetails", {"market_id": market["id"]})
        details = data.get("order_book_details") or []
        if not details:
            raise LighterAPIError(f"No price for {market['symbol']}")
        ticker = self._parse_ticker(details[0], market)
        self._tickers[market["symbol"]] = ticker
        return dict(ticker)

    def fetch_order_book_top(self, symbol: str) -> dict:
        """Best bid / ask."""
        market = self.market(symbol)
        data = self._request("GET", "orderBookOrders", {"market_id": market["id"], "limit": 1})
        bids, asks = data.get("bids") or [], data.get("asks") or []
        return {
            "symbol": market["symbol"],
            "bid": _float(bids[0].get("price"), None) if bids else None,
            "ask": _float(asks[0].get("price"), None) if asks else None,
        }

    # -- precision -------------------------------------------------------------

    def amount_to_units(self, symbol: str, amount: float) -> int:
        market = self.market(symbol)
        scaled = Decimal(str(amount)) * (Decimal(10) ** market["size_decimals"])
        return int(scaled.to_integral_value(rounding=ROUND_DOWN))

    def price_to_units(self, symbol: str, price: float) -> int:
        market = self.market(symbol)
        scaled = Decimal(str(price)) * (Decimal(10) ** market["price_decimals"])
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        """Round down to the market's size step (never above the requested risk)."""
        market = self.market(symbol)
        units = self.amount_to_units(symbol, amount)
        return f"{Decimal(units) / (Decimal(10) ** market['size_decimals']):f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        market = self.market(symbol)
        units = self.price_to_units(symbol, price)
        return f"{Decimal(units) / (Decimal(10) ** market['price_decimals']):f}"

    # -- account (public) ------------------------------------------------------

    def fetch_account(self) -> dict:
        data = self._request("GET", "account", {"by": "index", "value": self.account_index})
        accounts = data.get("accounts") or []
        if not accounts:
            raise LighterAPIError(f"Không tìm thấy tài khoản Lighter index {self.account_index}")
        return accounts[0]

    def account_summary(self, account: Optional[dict] = None) -> dict:
        account = account or self.fetch_account()
        positions = self.parse_positions(account)
        equity = _float(account.get("total_asset_value"))
        return {
            "account_index": int(account.get("account_index") or account.get("index") or self.account_index),
            "l1_address": account.get("l1_address"),
            "equity": equity,  # collateral + unrealized PnL
            "collateral": _float(account.get("collateral")),
            "available": _float(account.get("available_balance")),
            "initial_margin": _float(account.get("cross_initial_margin_requirement")),
            "maintenance_margin": _float(account.get("cross_maintenance_margin_requirement")),
            "unrealized_pnl": sum(p["unrealizedPnl"] for p in positions),
            "positions": len(positions),
            "pending_orders": int(_float(account.get("pending_order_count"))),
        }

    def fetch_balance(self) -> dict:
        """ccxt-style balance in USDC (total = equity incl. unrealized PnL)."""
        summary = self.account_summary()
        free = summary["available"]
        total = summary["equity"]
        used = max(0.0, total - free)
        return {
            "USDC": {"free": free, "used": used, "total": total},
            "free": {"USDC": free},
            "used": {"USDC": used},
            "total": {"USDC": total},
            "info": summary,
        }

    def parse_positions(self, account: dict, include_empty: bool = False) -> list[dict]:
        positions = []
        for item in account.get("positions") or []:
            position = self._parse_position(item)
            if include_empty or position["contracts"] > 0:
                positions.append(position)
        return positions

    def fetch_positions(self, symbols: Optional[Iterable[str]] = None) -> list[dict]:
        positions = self.parse_positions(self.fetch_account())
        if symbols:
            wanted = {self.market(s)["symbol"] for s in symbols}
            positions = [p for p in positions if p["symbol"] in wanted]
        return positions

    def _parse_position(self, item: dict) -> dict:
        size = abs(_float(item.get("position")))
        value = abs(_float(item.get("position_value")))
        imf_pct = _float(item.get("initial_margin_fraction"))  # percent here: "5.00" -> 20x
        sign = int(_float(item.get("sign"), 0))
        return {
            "symbol": str(item.get("symbol") or ""),
            "market_id": int(_float(item.get("market_id"), -1)),
            "side": "long" if sign >= 0 else "short",
            "contracts": size,
            "entryPrice": _float(item.get("avg_entry_price")),
            "markPrice": value / size if size else None,
            "notional": value,
            "unrealizedPnl": _float(item.get("unrealized_pnl")),
            "realizedPnl": _float(item.get("realized_pnl")),
            "liquidationPrice": _float(item.get("liquidation_price"), None) or None,
            "leverage": 100.0 / imf_pct if imf_pct > 0 else None,
            "marginMode": "isolated" if str(item.get("margin_mode")) == "1" else "cross",
            "collateral": _float(item.get("allocated_margin")),
            "fundingPaid": _float(item.get("total_funding_paid_out")),
            "openOrders": int(_float(item.get("open_order_count"))),
            "info": item,
        }

    # -- orders and trades (need the API key) --------------------------------

    def _parse_order(self, item: dict) -> dict:
        market = self.markets_by_id.get(int(_float(item.get("market_index"), -1)))
        filled = _float(item.get("filled_base_amount"))
        filled_quote = _float(item.get("filled_quote_amount"))
        raw_status = str(item.get("status") or "")
        parent = item.get("parent_order_index")
        client_id = item.get("client_order_index")
        return {
            "id": str(item.get("order_index") or item.get("order_id") or ""),
            "clientOrderId": str(client_id) if client_id not in (None, "", 0, "0") else None,
            "symbol": market["symbol"] if market else str(item.get("market_index")),
            "market_id": market["id"] if market else None,
            "side": "sell" if item.get("is_ask") in (True, 1, "1", "true") else "buy",
            "type": str(item.get("type") or ""),
            "status": _ORDER_STATUS.get(raw_status, "canceled" if raw_status.startswith("canceled") else raw_status),
            "raw_status": raw_status,
            "price": _float(item.get("price"), None),
            "triggerPrice": _float(item.get("trigger_price"), None) or None,
            "amount": _float(item.get("initial_base_amount")),
            "filled": filled,
            "remaining": _float(item.get("remaining_base_amount")),
            "average": filled_quote / filled if filled > 0 else None,
            "cost": filled_quote,
            "reduceOnly": item.get("reduce_only") in (True, 1, "1", "true"),
            "timeInForce": item.get("time_in_force"),
            "triggerStatus": item.get("trigger_status"),
            "parentId": str(parent) if parent not in (None, "", 0, "0") else None,
            "timestamp": _to_ms(item.get("timestamp") or item.get("created_at")),
            "updated": _to_ms(item.get("updated_at")),
            "info": item,
        }

    def fetch_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        self.load_markets()
        params = {
            "account_index": self.account_index,
            "market_id": self.market(symbol)["id"] if symbol else lighter_signer.ALL_MARKETS,
        }
        data = self._request("GET", "accountActiveOrders", params, auth=True)
        return [self._parse_order(o) for o in data.get("orders") or []]

    def fetch_closed_orders(self, symbol: Optional[str] = None, limit: int = 50, since: Optional[int] = None) -> list[dict]:
        """Filled / cancelled orders, newest first (`since` in ms)."""
        self.load_markets()
        params: dict[str, Any] = {"account_index": self.account_index}
        if symbol:
            params["market_id"] = self.market(symbol)["id"]
        return self._paginate("accountInactiveOrders", "orders", params, limit, since, self._parse_order)

    def fetch_my_trades(self, symbol: Optional[str] = None, limit: int = 100, since: Optional[int] = None) -> list[dict]:
        """Fills of this account, newest first, seen from this account's side."""
        self.load_markets()
        params: dict[str, Any] = {"account_index": self.account_index, "sort_by": "timestamp"}
        if symbol:
            params["market_id"] = self.market(symbol)["id"]
        return self._paginate("trades", "trades", params, limit, since, self._parse_trade)

    def _paginate(self, path: str, key: str, params: dict, limit: int, since: Optional[int], parse: Callable) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while len(results) < limit:
            page = dict(params, limit=min(100, limit - len(results)))
            if cursor:
                page["cursor"] = cursor
            data = self._request("GET", path, page, auth=True)
            items = [parse(item) for item in data.get(key) or []]
            if since is not None:
                kept = [i for i in items if (i.get("timestamp") or 0) >= since]
                results.extend(kept)
                if len(kept) < len(items):
                    break
            else:
                results.extend(items)
            cursor = data.get("next_cursor")
            if not cursor or not items:
                break
        return results[:limit]

    def _parse_trade(self, item: dict) -> dict:
        account_index = self.account_index
        we_sell = int(_float(item.get("ask_account_id"), -1)) == account_index
        we_are_maker = we_sell == bool(item.get("is_maker_ask"))
        prefix = "maker" if we_are_maker else "taker"
        size = _float(item.get("size"))
        price = _float(item.get("price"))
        before = _float(item.get(f"{prefix}_position_size_before"))  # signed: < 0 is short
        entry_quote = abs(_float(item.get(f"{prefix}_entry_quote_before")))
        entry_price = entry_quote / abs(before) if before else None

        closing = before != 0 and ((before > 0) == we_sell)  # selling a long or buying back a short
        closed_qty = min(size, abs(before)) if closing else 0.0
        pnl_value = item.get("ask_account_pnl" if we_sell else "bid_account_pnl")
        if pnl_value not in (None, ""):
            pnl = _float(pnl_value)
        elif closing and entry_price:
            pnl = (price - entry_price) * closed_qty * (1 if before > 0 else -1)
        else:
            pnl = 0.0

        market = self.markets_by_id.get(int(_float(item.get("market_id"), -1)))
        return {
            "id": str(item.get("trade_id") or ""),
            "order": str(item.get("ask_id") if we_sell else item.get("bid_id")),
            "symbol": market["symbol"] if market else str(item.get("market_id")),
            "side": "sell" if we_sell else "buy",
            "takerOrMaker": "maker" if we_are_maker else "taker",
            "type": str(item.get("type") or "trade"),
            "price": price,
            "amount": size,
            "cost": _float(item.get("usd_amount")),
            "timestamp": _to_ms(item.get("timestamp")),
            "position_before": before,
            "entry_price_before": entry_price,
            "closed_qty": closed_qty,
            "realized_pnl": pnl,
            "info": item,
        }

    def fetch_closed_pnl(self, symbol: Optional[str] = None, limit: int = 50, since: Optional[int] = None) -> list[dict]:
        """Realized PnL per closing order (like Bybit's closed-pnl list), newest first."""
        trades = self.fetch_my_trades(symbol=symbol, limit=max(100, limit * 3), since=since)
        records: dict[str, dict] = {}
        for trade in trades:
            if trade["closed_qty"] <= 0:
                continue
            record = records.setdefault(trade["order"], {
                "order_id": trade["order"],
                "symbol": trade["symbol"],
                # side of the position that was closed
                "side": "BUY" if trade["position_before"] > 0 else "SELL",
                "qty": 0.0, "entry_value": 0.0, "exit_value": 0.0, "closed_pnl": 0.0,
                "time": 0, "type": trade["type"],
            })
            qty = trade["closed_qty"]
            record["qty"] += qty
            record["entry_value"] += (trade["entry_price_before"] or 0.0) * qty
            record["exit_value"] += trade["price"] * qty
            record["closed_pnl"] += trade["realized_pnl"]
            record["time"] = max(record["time"], trade["timestamp"] or 0)
            if trade["type"] != "trade":
                record["type"] = trade["type"]  # liquidation / deleverage

        result = []
        for record in records.values():
            qty = record.pop("qty")
            entry_value, exit_value = record.pop("entry_value"), record.pop("exit_value")
            record.update(qty=qty, avg_entry_price=entry_value / qty if qty else 0.0, avg_exit_price=exit_value / qty if qty else 0.0)
            result.append(record)
        result.sort(key=lambda r: r["time"], reverse=True)
        return result[:limit]

    def find_order(self, order_id: str, symbol: Optional[str] = None, closed_limit: int = 100) -> tuple[Optional[dict], Optional[str]]:
        """Find an order by order index or client order index. Returns (order, 'open'|'closed')."""
        wanted = str(order_id).strip()
        for order in self.fetch_open_orders(symbol):
            if wanted in (order["id"], order["clientOrderId"]):
                return order, "open"
        for order in self.fetch_closed_orders(symbol, limit=closed_limit):
            if wanted in (order["id"], order["clientOrderId"]):
                return order, "closed"
        return None, None

    def wait_for_order(self, client_order_index: int, symbol: str, timeout: float = 8.0) -> tuple[Optional[dict], list[dict]]:
        """Wait until the order sent with client_order_index shows up.
        Returns (order or None, open orders of the market from the last check)."""
        wanted = str(client_order_index)
        deadline = time.time() + timeout
        open_orders: list[dict] = []
        time.sleep(0.4)  # the sequencer needs a moment
        while True:
            try:
                open_orders = self.fetch_open_orders(symbol)
                order = next((o for o in open_orders if o["clientOrderId"] == wanted), None)
                if order is None:
                    order = next((o for o in self.fetch_closed_orders(symbol, limit=20) if o["clientOrderId"] == wanted), None)
                if order is not None:
                    return order, open_orders
            except LighterAPIError:
                pass
            if time.time() >= deadline:
                return None, open_orders
            time.sleep(1.0)

    # -- transactions (need the API key) --------------------------------------

    def _next_nonce(self, refresh: bool) -> int:
        if self._nonce is None or refresh:
            data = self._request(
                "GET", "nextNonce", {"account_index": self.account_index, "api_key_index": self.api_key_index}
            )
            return int(data["nonce"])
        return self._nonce + 1

    def _send_tx(self, tx_type: int, tx_info: str) -> dict:
        """POST one signed transaction. A request without an answer is sent again unchanged: the
        same signed tx (same nonce) cannot run twice. If that also fails, the error is marked
        `uncertain` because the first send may have gone through."""
        data = {"tx_type": tx_type, "tx_info": tx_info}
        try:
            return self._request("POST", "sendTx", data=data, retries=1)
        except LighterAPIError as error:
            if error.code is not None and error.status is not None and error.status < 500 and error.status not in (405, 429):
                raise  # rejected by Lighter: nothing ran
            first_error = error
        time.sleep(15.0 if first_error.status in (405, 429) else 1.0)
        try:
            return self._request("POST", "sendTx", data=data, retries=2)
        except LighterAPIError as error:
            raise LighterAPIError(
                f"{error} — lần gửi đầu không có phản hồi ({first_error}); lệnh có thể đã lên sàn, kiểm tra lại trên Lighter",
                code=error.code, status=error.status, uncertain=True,
            ) from error

    def _send_signed(self, sign: Callable[[int], tuple[int, str]]) -> dict:
        """Sign with the next nonce and send. When Lighter rejects the nonce as stale (another
        process used the same API key), sign again once with a fresh nonce."""
        with self._tx_lock:
            for attempt in (1, 2):
                nonce = self._next_nonce(refresh=attempt > 1)
                tx_type, tx_info = sign(nonce)
                try:
                    response = self._send_tx(tx_type, tx_info)
                except LighterAPIError as error:
                    self._nonce = None  # ask the API again next time
                    if attempt == 1 and not error.uncertain and "nonce" in str(error).lower():
                        continue
                    raise
                self._nonce = nonce
                return response
        raise LighterAPIError("sendTx: no response")

    def build_order(
        self,
        symbol: str,
        side: str,
        kind: str,
        amount: float,
        price: float,
        trigger_price: Optional[float] = None,
        reduce_only: bool = False,
        client_order_index: int = 0,
    ) -> CreateOrderTxReq:
        """kind: limit, post_only, ioc (market-like), stop_loss, take_profit.
        amount 0 = SL/TP that follows the filled entry or the whole position."""
        market = self.market(symbol)
        order_type, time_in_force, expiry = _ORDER_KINDS[kind]
        return CreateOrderTxReq(
            MarketIndex=market["id"],
            ClientOrderIndex=int(client_order_index),
            BaseAmount=self.amount_to_units(market["symbol"], amount) if amount else 0,
            Price=self.price_to_units(market["symbol"], price),
            IsAsk=1 if _normalize_side(side) == "sell" else 0,
            Type=order_type,
            TimeInForce=time_in_force,
            ReduceOnly=1 if reduce_only else 0,
            TriggerPrice=self.price_to_units(market["symbol"], trigger_price) if trigger_price else 0,
            OrderExpiry=expiry,
        )

    def send_order(self, order: CreateOrderTxReq) -> dict:
        return self._send_signed(lambda nonce: self.signer.sign_create_order(order, nonce))

    def send_grouped_orders(self, grouping_type: int, orders: list[CreateOrderTxReq]) -> dict:
        return self._send_signed(lambda nonce: self.signer.sign_create_grouped_orders(grouping_type, orders, nonce))

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        market = self.market(symbol)
        return self._send_signed(lambda nonce: self.signer.sign_cancel_order(market["id"], int(order_id), nonce))

    def cancel_all_orders(self) -> dict:
        return self._send_signed(lambda nonce: self.signer.sign_cancel_all_orders(nonce))

    def set_leverage(self, leverage: float, symbol: str, margin_mode: Optional[str] = None) -> dict:
        market = self.market(symbol)
        mode = lighter_signer.MARGIN_MODE_ISOLATED if (margin_mode or self.margin_mode) == "isolated" else lighter_signer.MARGIN_MODE_CROSS
        return self._send_signed(lambda nonce: self.signer.sign_update_leverage(market["id"], leverage, mode, nonce))


# ---------------------------------------------------------------------------
# Module functions (same names as src/live/bybit/exchange.py)
# ---------------------------------------------------------------------------

_exchange_instance: Optional[LighterExchange] = None


def create_lighter_exchange() -> LighterExchange:
    """Client configured from .env (read-only when there is no API key)."""
    margin_mode = _env("LIGHTER_MARGIN_MODE", "cross").lower()
    if margin_mode not in ("cross", "isolated"):
        raise LighterConfigError(f"LIGHTER_MARGIN_MODE must be cross or isolated (got '{margin_mode}')")
    return LighterExchange(
        account_index=_env_int("LIGHTER_ACCOUNT_INDEX"),
        api_key_index=_env_int("LIGHTER_API_KEY_INDEX"),
        private_key=_env("LIGHTER_API_PRIVATE_KEY") or None,
        wallet_address=_env("LIGHTER_WALLET_ADDRESS") or None,
        margin_mode=margin_mode,
        max_slippage_pct=_env_float("LIGHTER_MAX_SLIPPAGE_PCT", 0.5),
        trigger_slippage_pct=_env_float("LIGHTER_TRIGGER_SLIPPAGE_PCT", 1.0),
    )


def get_exchange(force_new: bool = False) -> LighterExchange:
    global _exchange_instance
    if _exchange_instance is not None and not force_new:
        return _exchange_instance
    exchange = create_lighter_exchange()
    exchange.load_markets()
    _exchange_instance = exchange
    return exchange


def calculate_position_size(
    entry_price: float,
    stop_loss: float,
    leverage: int,
    risk_per_trade: float = 5,
    side: str = 'buy',
    skip_validation: bool = False,
    exchange: Optional[LighterExchange] = None,
    symbol: Optional[str] = None,
) -> dict:
    """
    Same formula and result keys as bybit.calculate_position_size:
    - quantity = risk_per_trade / |entry - stop_loss|, rounded down to the market's size step
    - raised to the market minimum (size and $ value) when below it; risk_diff_pct shows the change
    """
    if leverage <= 0:
        raise ValueError("Leverage phải > 0")

    if not skip_validation:
        if side.lower() in ('buy', 'long'):
            if entry_price <= stop_loss:
                raise ValueError("LONG: Entry price phải cao hơn stop loss")
        elif entry_price >= stop_loss:
            raise ValueError("SHORT: Stop loss phải cao hơn entry price")

    price_diff = abs(entry_price - stop_loss)
    if price_diff == 0:
        raise ValueError("Entry price và stop loss không được bằng nhau")

    raw_quantity = risk_per_trade / price_diff
    min_notice = None
    if exchange is not None and symbol:
        market = exchange.market(symbol)
        quantity = float(exchange.amount_to_precision(market["symbol"], raw_quantity))
        step = Decimal(str(market["amount_step"]))
        minimum = max(market["min_amount"], (market["min_cost"] / entry_price) if entry_price > 0 else 0.0)
        if quantity < minimum:
            quantity = float((Decimal(str(minimum)) / step).to_integral_value(rounding=ROUND_UP) * step)
            min_notice = f"Khối lượng tăng lên mức tối thiểu của sàn ({market['min_amount']} {market['symbol']} / ${market['min_cost']:g})"
    else:
        quantity = round(raw_quantity, 4)

    notional_value = quantity * entry_price
    actual_risk = price_diff * quantity
    result = {
        'quantity': quantity,
        'raw_quantity': raw_quantity,
        'margin_required': round_price(notional_value / leverage),
        'notional_value': round_price(notional_value),
        'actual_risk': round_price(actual_risk),
        'expected_risk': risk_per_trade,
        'risk_diff_pct': round((actual_risk - risk_per_trade) / risk_per_trade * 100, 2) if risk_per_trade > 0 else 0,
        'price_diff': round_price(price_diff),
        'leverage': leverage,
        'side': 'buy' if side.lower() in ('buy', 'long') else 'sell',
    }
    if min_notice:
        result['min_size_notice'] = min_notice
    return result


def get_market_info(exchange: LighterExchange, symbol: str) -> dict:
    """Same keys as bybit.get_market_info."""
    try:
        market = exchange.market(symbol)
    except ValueError:
        return {}
    return {
        'symbol': market['symbol'],
        'base': market['base'],
        'quote': market['quote'],
        'settle': market['settle'],
        'contract_size': 1,
        'min_amount': market['min_amount'],
        'max_amount': None,
        'min_cost': market['min_cost'],
        'price_precision': market['price_step'],
        'amount_precision': market['amount_step'],
        'max_leverage': market['max_leverage'],
        'default_leverage': market['default_leverage'],
        'market_id': market['id'],
    }


def validate_symbol(exchange: LighterExchange, symbol: str) -> bool:
    return exchange.has_market(symbol)


def get_position(exchange: LighterExchange, symbol: str, account: Optional[dict] = None) -> Optional[dict]:
    """The open position of a market, or None."""
    market_symbol = exchange.market(symbol)["symbol"]
    positions = exchange.parse_positions(account) if account is not None else exchange.fetch_positions()
    return next((p for p in positions if p["symbol"] == market_symbol), None)


def get_position_mode(exchange: LighterExchange = None, symbol: str = None) -> dict:
    """Lighter has one position per market (like Bybit one-way mode)."""
    return {'mode': 'one_way', 'position_idx_long': 0, 'position_idx_short': 0}


def _exit_side(side: str) -> str:
    return 'sell' if _normalize_side(side) == 'buy' else 'buy'


def _past_trigger_price(exit_side: str, trigger: float, slippage_pct: float) -> float:
    """Limit price of a SL/TP: a little past the trigger so it still fills in a fast move."""
    factor = slippage_pct / 100.0
    return trigger * (1 - factor) if exit_side == 'sell' else trigger * (1 + factor)


def _worst_price(side: str, reference: float, slippage_pct: float) -> float:
    factor = slippage_pct / 100.0
    return reference * (1 + factor) if _normalize_side(side) == 'buy' else reference * (1 - factor)


def _exit_orders(
    exchange: LighterExchange,
    symbol: str,
    side: str,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    client_order_index: int,
    amount: float = 0,
) -> list[CreateOrderTxReq]:
    """TP and SL closing a `side` position (amount 0 = size of the filled entry / whole position)."""
    exit_side = _exit_side(side)
    orders = []
    if take_profit:
        orders.append(exchange.build_order(
            symbol, exit_side, 'take_profit', amount,
            price=_past_trigger_price(exit_side, take_profit, exchange.trigger_slippage_pct),
            trigger_price=take_profit, reduce_only=True, client_order_index=client_order_index + 1,
        ))
    if stop_loss:
        orders.append(exchange.build_order(
            symbol, exit_side, 'stop_loss', amount,
            price=_past_trigger_price(exit_side, stop_loss, exchange.trigger_slippage_pct),
            trigger_price=stop_loss, reduce_only=True, client_order_index=client_order_index + 2,
        ))
    return orders


def _exit_level_problem(side: str, price: float, stop_loss: float, take_profit: Optional[float]) -> Optional[str]:
    """SL/TP on the wrong side of the price would trigger right after the entry."""
    if side == 'buy':
        if stop_loss >= price:
            return f"LONG: Stop loss ({stop_loss}) phải thấp hơn giá vào ({price})"
        if take_profit is not None and take_profit <= price:
            return f"LONG: Take profit ({take_profit}) phải cao hơn giá vào ({price})"
    else:
        if stop_loss <= price:
            return f"SHORT: Stop loss ({stop_loss}) phải cao hơn giá vào ({price})"
        if take_profit is not None and take_profit >= price:
            return f"SHORT: Take profit ({take_profit}) phải thấp hơn giá vào ({price})"
    return None


def _api_key_problem(exchange: LighterExchange) -> Optional[str]:
    if not exchange.has_api_key:
        return "Thiếu API key Lighter (LIGHTER_API_PRIVATE_KEY, LIGHTER_API_KEY_INDEX trong .env) — chưa đặt lệnh được"
    return None


def _opposite_position_problem(position: Optional[dict], side: str, symbol: str) -> Optional[str]:
    if position and position['side'] != ('long' if side == 'buy' else 'short'):
        return (f"Lighter chỉ có 1 vị thế mỗi market: đang {position['side'].upper()} "
                f"{position['contracts']} {symbol}. Đóng vị thế đó trước khi vào lệnh ngược chiều")
    return None


def _capped_leverage(market: dict, leverage: float) -> float:
    """Leverage limits differ per market (BTC 50x, some alts 3-5x)."""
    return min(float(leverage), market['max_leverage']) if market['max_leverage'] else float(leverage)


def _ensure_leverage(exchange: LighterExchange, market: dict, leverage: float, account: dict) -> dict:
    """Set leverage + margin mode for the market when they differ from the account's setting."""
    target = _capped_leverage(market, leverage)
    current_leverage, current_mode = None, None
    for item in account.get('positions') or []:
        if int(_float(item.get('market_id'), -1)) == market['id']:
            imf_pct = _float(item.get('initial_margin_fraction'))
            current_leverage = 100.0 / imf_pct if imf_pct > 0 else None
            current_mode = 'isolated' if str(item.get('margin_mode')) == '1' else 'cross'
            break
    if current_leverage and abs(current_leverage - target) < 0.01 and current_mode == exchange.margin_mode:
        return {'leverage': current_leverage, 'changed': False}
    try:
        exchange.set_leverage(target, market['symbol'])
        return {'leverage': target, 'changed': True}
    except (LighterAPIError, LighterSignerError) as error:
        return {'leverage': current_leverage or market['default_leverage'], 'changed': False, 'warning': f"Không đổi được leverage: {error}"}


def _reference_price(exchange: LighterExchange, symbol: str, side: str) -> tuple[Optional[float], Optional[float]]:
    """(price a market order would pay, last price): best ask for buys, best bid for sells."""
    top = exchange.fetch_order_book_top(symbol)
    ticker = exchange.fetch_ticker(symbol, max_age=10)
    last = ticker.get('last') or ticker.get('markPrice')
    best = top['ask'] if side == 'buy' else top['bid']
    return (best or last), last


def _find_children(
    exchange: LighterExchange,
    open_orders: list[dict],
    symbol: str,
    client_order_index: int,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> tuple[Optional[dict], Optional[dict]]:
    """(TP order, SL order) of a grouped entry: by client order index, else by type + trigger price."""
    def find(offset: int, prefix: str, trigger: Optional[float]) -> Optional[dict]:
        if not trigger:
            return None
        wanted_trigger = exchange.price_to_units(symbol, trigger)
        for order in open_orders:
            if order['symbol'] != symbol or not order['type'].startswith(prefix):
                continue
            if order['clientOrderId'] == str(client_order_index + offset):
                return order
            if order['triggerPrice'] and exchange.price_to_units(symbol, order['triggerPrice']) == wanted_trigger:
                return order
        return None

    return find(1, 'take-profit', take_profit), find(2, 'stop-loss', stop_loss)


def _check_exit_orders(
    exchange: LighterExchange,
    open_orders: list[dict],
    symbol: str,
    client_order_index: int,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> tuple[Optional[dict], Optional[dict], Optional[str]]:
    """Find the SL/TP of a filled entry; look again once if they are not visible yet.
    Returns (TP order, SL order, warning or None)."""
    tp_order, sl_order = _find_children(exchange, open_orders, symbol, client_order_index, stop_loss, take_profit)
    if sl_order is None or (take_profit and tp_order is None):
        time.sleep(1.0)
        try:
            open_orders = exchange.fetch_open_orders(symbol)
        except (LighterAPIError, LighterSignerError) as error:
            return tp_order, sl_order, f"Không kiểm tra được SL/TP trên sàn ({error}) — kiểm tra lại trên Lighter"
        tp_order, sl_order = _find_children(exchange, open_orders, symbol, client_order_index, stop_loss, take_profit)
    missing = [name for name, order, wanted in (("SL", sl_order, stop_loss), ("TP", tp_order, take_profit))
               if wanted and order is None]
    if missing:
        return tp_order, sl_order, f"Không thấy lệnh {'/'.join(missing)} trên sàn sau khi vào lệnh — kiểm tra ngay trên Lighter"
    return tp_order, sl_order, None


def place_market_order_with_sl(
    exchange: LighterExchange,
    symbol: str,
    side: str = 'buy',
    leverage: int = 20,
    stop_loss: float = None,
    risk_per_trade: float = 5,
    expected_entry: float = None,
    take_profit: float = None,
) -> dict:
    """
    Market entry with SL (and TP) in one grouped transaction. Same result keys as
    bybit.place_market_order_with_sl; SL/TP are already on the exchange when it returns.

    Args:
        expected_entry: entry price from the signal, used for the size (None = current price)
        take_profit: optional; Bybit sets it after the entry, Lighter sends it with the entry
    """
    if not stop_loss:
        raise ValueError("Phải nhập stop_loss")
    side = _normalize_side(side)
    market = exchange.market(symbol)
    market_symbol = market['symbol']
    problem = _api_key_problem(exchange)
    if problem:
        return {'success': False, 'error': problem, 'symbol': market_symbol}
    leverage = _capped_leverage(market, leverage)

    account = exchange.fetch_account()
    position_before = get_position(exchange, market_symbol, account)
    problem = _opposite_position_problem(position_before, side, market_symbol)
    if problem:
        return {'success': False, 'error': problem, 'symbol': market_symbol}

    current_price, last_price = _reference_price(exchange, market_symbol, side)
    if current_price is None:
        return {'success': False, 'error': 'Không lấy được giá hiện tại từ sàn', 'symbol': market_symbol}

    stop_loss = float(exchange.price_to_precision(market_symbol, stop_loss))
    if take_profit:
        take_profit = float(exchange.price_to_precision(market_symbol, take_profit))
    problem = _exit_level_problem(side, current_price, stop_loss, take_profit)
    if problem:
        return {'success': False, 'error': problem, 'symbol': market_symbol, 'current_price': current_price}

    entry_for_calculation = expected_entry if expected_entry else current_price
    position_info = calculate_position_size(
        entry_price=entry_for_calculation,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_per_trade=risk_per_trade,
        side=side,
        skip_validation=True,
        exchange=exchange,
        symbol=market_symbol,
    )
    position_info['expected_entry'] = entry_for_calculation
    position_info['current_price'] = current_price

    leverage_result = _ensure_leverage(exchange, market, leverage, account)
    position_info['leverage'] = round(leverage_result['leverage'] or leverage, 2)

    client_order_index = new_client_order_index()
    worst_price = _worst_price(side, current_price, exchange.max_slippage_pct)
    entry_order = exchange.build_order(
        market_symbol, side, 'ioc', position_info['quantity'], worst_price, client_order_index=client_order_index
    )
    exits = _exit_orders(exchange, market_symbol, side, stop_loss, take_profit, client_order_index)
    grouping = (lighter_signer.GROUPING_ONE_TRIGGERS_A_ONE_CANCELS_THE_OTHER if take_profit
                else lighter_signer.GROUPING_ONE_TRIGGERS_THE_OTHER)

    try:
        response = exchange.send_grouped_orders(grouping, [entry_order, *exits])
    except (LighterAPIError, LighterSignerError) as error:
        return {'success': False, 'error': str(error), 'position_info': position_info, 'symbol': market_symbol,
                'uncertain': getattr(error, 'uncertain', False)}

    order, open_orders = exchange.wait_for_order(client_order_index, market_symbol)
    result = {
        'symbol': market_symbol,
        'client_order_index': client_order_index,
        'tx_hash': response.get('tx_hash'),
        'position_info': position_info,
        'expected_entry': entry_for_calculation,
        'current_price': last_price or current_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'position_mode': 'one_way',
        'order_type': 'market',
    }
    if leverage_result.get('warning'):
        result['leverage_warning'] = leverage_result['warning']

    if order is None:
        # Order not visible yet: the position size tells whether the entry filled
        position_after = get_position(exchange, market_symbol)
        before_size = position_before['contracts'] if position_before else 0.0
        if position_after is None or position_after['contracts'] <= before_size:
            result.update(success=False, error=f"Không xác nhận được lệnh trên sàn (tx {response.get('tx_hash')}). Kiểm tra lại trên Lighter")
            return result
        filled, average = position_after['contracts'] - before_size, position_after['entryPrice']
    else:
        filled, average = order['filled'], order['average']
        if filled <= 0:
            reason = order['raw_status'] or order['status']
            hint = " (giá chạy quá LIGHTER_MAX_SLIPPAGE_PCT)" if 'slippage' in reason or 'liquidity' in reason else ""
            result.update(success=False, order=order, error=f"Lệnh market không khớp: {reason}{hint}")
            return result

    tp_order, sl_order, warning = _check_exit_orders(
        exchange, open_orders, market_symbol, client_order_index, stop_loss, take_profit
    )
    position_info['quantity'] = filled
    result.update(
        success=True,
        order=order,
        order_id=order['id'] if order else str(client_order_index),
        entry_price=average or current_price,
        contracts=filled,
        sl_order_id=sl_order['id'] if sl_order else None,
        tp_order_id=tp_order['id'] if tp_order else None,
    )
    if warning:
        result['sl_tp_warning'] = warning
    return result


def place_limit_order_with_sl(
    exchange: LighterExchange,
    symbol: str,
    side: str = 'buy',
    leverage: int = 20,
    entry_price: float = None,
    stop_loss: float = None,
    take_profit: float = None,
    risk_per_trade: float = 5,
    time_in_force: str = 'GTC',
    reduce_only: bool = False,
) -> dict:
    """
    Limit entry with SL/TP that activate when it fills (one grouped transaction).
    Same result keys as bybit.place_limit_order_with_sl.

    time_in_force: 'GTC' (rests up to 28 days on Lighter), 'PostOnly', 'IOC'
    """
    if not entry_price:
        raise ValueError("Phải nhập entry_price cho limit order")
    if not stop_loss:
        raise ValueError("Phải nhập stop_loss")
    side = _normalize_side(side)
    market = exchange.market(symbol)
    market_symbol = market['symbol']
    kind = _TIME_IN_FORCE_KINDS.get(str(time_in_force).replace("_", "").upper())
    if kind is None:
        return {'success': False, 'error': f"Lighter không hỗ trợ time_in_force '{time_in_force}' (dùng GTC, PostOnly, IOC)",
                'symbol': market_symbol, 'order_type': 'limit'}
    problem = _api_key_problem(exchange)
    if problem:
        return {'success': False, 'error': problem, 'symbol': market_symbol, 'order_type': 'limit'}
    leverage = _capped_leverage(market, leverage)

    account = exchange.fetch_account()
    if not reduce_only:
        problem = _opposite_position_problem(get_position(exchange, market_symbol, account), side, market_symbol)
        if problem:
            return {'success': False, 'error': problem, 'symbol': market_symbol, 'order_type': 'limit'}

    entry_price = float(exchange.price_to_precision(market_symbol, entry_price))
    stop_loss = float(exchange.price_to_precision(market_symbol, stop_loss))
    if take_profit:
        take_profit = float(exchange.price_to_precision(market_symbol, take_profit))
    problem = _exit_level_problem(side, entry_price, stop_loss, take_profit)
    if problem:
        return {'success': False, 'error': problem, 'symbol': market_symbol, 'order_type': 'limit'}

    ticker = exchange.fetch_ticker(market_symbol, max_age=10)
    current_price = ticker.get('last') or ticker.get('markPrice')
    if current_price is None:
        return {'success': False, 'error': 'Không lấy được giá hiện tại từ sàn', 'symbol': market_symbol, 'order_type': 'limit'}

    price_warning = None
    if side == 'buy' and entry_price > current_price:
        price_warning = f"LONG limit: entry ({entry_price}) > current ({current_price}), lệnh có thể fill ngay như market"
    elif side == 'sell' and entry_price < current_price:
        price_warning = f"SHORT limit: entry ({entry_price}) < current ({current_price}), lệnh có thể fill ngay như market"

    position_info = calculate_position_size(
        entry_price=entry_price,
        stop_loss=stop_loss,
        leverage=leverage,
        risk_per_trade=risk_per_trade,
        side=side,
        skip_validation=False,
        exchange=exchange,
        symbol=market_symbol,
    )
    position_info['entry_price'] = entry_price
    position_info['current_price'] = current_price

    leverage_result = _ensure_leverage(exchange, market, leverage, account)
    position_info['leverage'] = round(leverage_result['leverage'] or leverage, 2)

    client_order_index = new_client_order_index()
    entry_order = exchange.build_order(
        market_symbol, side, kind, position_info['quantity'], entry_price,
        reduce_only=reduce_only, client_order_index=client_order_index,
    )
    exits = [] if reduce_only else _exit_orders(exchange, market_symbol, side, stop_loss, take_profit, client_order_index)

    try:
        if exits:
            grouping = (lighter_signer.GROUPING_ONE_TRIGGERS_A_ONE_CANCELS_THE_OTHER if take_profit
                        else lighter_signer.GROUPING_ONE_TRIGGERS_THE_OTHER)
            response = exchange.send_grouped_orders(grouping, [entry_order, *exits])
        else:
            response = exchange.send_order(entry_order)
    except (LighterAPIError, LighterSignerError) as error:
        return {'success': False, 'error': str(error), 'position_info': position_info, 'symbol': market_symbol,
                'order_type': 'limit', 'uncertain': getattr(error, 'uncertain', False)}

    order, open_orders = exchange.wait_for_order(client_order_index, market_symbol, timeout=6.0)
    result = {
        'symbol': market_symbol,
        'client_order_index': client_order_index,
        'tx_hash': response.get('tx_hash'),
        'order_type': 'limit',
        'position_info': position_info,
        'entry_price': entry_price,
        'current_price': current_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'position_mode': 'one_way',
        'time_in_force': time_in_force,
    }
    if price_warning:
        result['price_warning'] = price_warning
    if leverage_result.get('warning'):
        result['leverage_warning'] = leverage_result['warning']

    if order is None:
        result.update(success=False, error=f"Không thấy lệnh limit trên sàn (tx {response.get('tx_hash')}). Kiểm tra lại trên Lighter")
        return result
    if order['status'] in ('canceled', 'rejected', 'expired') and order['filled'] <= 0:
        result.update(success=False, order=order, error=f"Lệnh limit bị hủy: {order['raw_status']}")
        return result

    # Before the entry fills, its SL/TP wait on it ("parent-order"); after a fill they are live
    tp_order, sl_order = _find_children(exchange, open_orders, market_symbol, client_order_index, stop_loss, take_profit)
    if order['filled'] > 0 and exits:
        tp_order, sl_order, warning = _check_exit_orders(
            exchange, open_orders, market_symbol, client_order_index, stop_loss, take_profit
        )
        if warning:
            result['sl_tp_warning'] = warning
    result.update(
        success=True,
        order=order,
        order_id=order['id'],
        order_status=order['status'],
        actual_fill_price=order['average'] or entry_price,
        sl_order_id=sl_order['id'] if sl_order else None,
        tp_order_id=tp_order['id'] if tp_order else None,
    )
    return result


def _cancel_exit_orders(exchange: LighterExchange, symbol: str) -> list[dict]:
    """Cancel the reduce-only SL/TP orders of a market. Returns one result per order."""
    results = []
    for order in exchange.fetch_open_orders(symbol):
        if order['reduceOnly'] and order['type'] in TRIGGER_ORDER_TYPES:
            try:
                exchange.cancel_order(order['id'], symbol)
                results.append({'order_id': order['id'], 'success': True})
            except (LighterAPIError, LighterSignerError) as error:
                results.append({'order_id': order['id'], 'success': False, 'error': str(error)})
    return results


def set_position_sl_tp(
    exchange: LighterExchange,
    symbol: str,
    side: str,
    stop_loss: float,
    take_profit: float = None,
) -> dict:
    """
    Replace the SL/TP of an open position (like Bybit trading-stop).
    With both SL and TP they are tied to the position: they close all of it, whatever its size later.
    """
    try:
        side = _normalize_side(side)
        market_symbol = exchange.market(symbol)['symbol']
        position = get_position(exchange, market_symbol)
        if not position or position['side'] != ('long' if side == 'buy' else 'short'):
            return {'success': False, 'error': f'Position not found for {side} {market_symbol}'}

        stop_loss = float(exchange.price_to_precision(market_symbol, stop_loss))
        if take_profit:
            take_profit = float(exchange.price_to_precision(market_symbol, take_profit))
        mark = position['markPrice'] or position['entryPrice']
        problem = _exit_level_problem(side, mark, stop_loss, take_profit)
        if problem:
            return {'success': False, 'error': problem.replace('giá vào', 'giá hiện tại')}

        cancelled = _cancel_exit_orders(exchange, market_symbol)
        client_order_index = new_client_order_index()
        if take_profit:
            orders = _exit_orders(exchange, market_symbol, side, stop_loss, take_profit, client_order_index, amount=0)
            response = exchange.send_grouped_orders(lighter_signer.GROUPING_ONE_CANCELS_THE_OTHER, orders)
        else:
            orders = _exit_orders(exchange, market_symbol, side, stop_loss, None, client_order_index, amount=position['contracts'])
            response = exchange.send_order(orders[0])
        return {
            'success': True,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'position_size': position['contracts'],
            'position_mode': 'one_way',
            'cancelled_orders': cancelled,
            'tx_hash': response.get('tx_hash'),
            'result': response,
        }
    except (LighterAPIError, LighterSignerError, ValueError) as error:
        return {'success': False, 'error': str(error)}


def close_position_market(exchange: LighterExchange, symbol: str) -> dict:
    """Close the whole position of a market at market price, then cancel its SL/TP."""
    try:
        market_symbol = exchange.market(symbol)['symbol']
        position = get_position(exchange, market_symbol)
        if not position:
            return {'success': False, 'error': f'Không có vị thế {market_symbol}'}
        exit_side = 'sell' if position['side'] == 'long' else 'buy'
        reference, _ = _reference_price(exchange, market_symbol, exit_side)
        if reference is None:
            return {'success': False, 'error': 'Không lấy được giá hiện tại từ sàn'}
        client_order_index = new_client_order_index()
        order = exchange.build_order(
            market_symbol, exit_side, 'ioc', position['contracts'],
            _worst_price(exit_side, reference, exchange.max_slippage_pct * 2),
            reduce_only=True, client_order_index=client_order_index,
        )
        response = exchange.send_order(order)
        filled_order, _ = exchange.wait_for_order(client_order_index, market_symbol)
        filled = filled_order['filled'] if filled_order else None
        cancelled = _cancel_exit_orders(exchange, market_symbol) if filled else []
        return {
            'success': bool(filled),
            'error': None if filled else 'Lệnh đóng chưa khớp — kiểm tra lại trên Lighter',
            'symbol': market_symbol,
            'side': position['side'],
            'closed_qty': filled,
            'exit_price': filled_order['average'] if filled_order else None,
            'order': filled_order,
            'cancelled_orders': cancelled,
            'tx_hash': response.get('tx_hash'),
        }
    except (LighterAPIError, LighterSignerError, ValueError) as error:
        return {'success': False, 'error': str(error)}


def cancel_order(exchange: LighterExchange, symbol: str, order_id: str) -> dict:
    try:
        response = exchange.cancel_order(order_id, symbol)
        return {'success': True, 'order_id': order_id, 'result': response}
    except (LighterAPIError, LighterSignerError, ValueError) as error:
        return {'success': False, 'error': str(error), 'order_id': order_id}


def get_order_status(exchange: LighterExchange, symbol: str, order_id: str) -> dict:
    try:
        order, source = exchange.find_order(order_id, symbol)
        if order is None:
            return {'success': False, 'error': 'Không tìm thấy order', 'order_id': order_id}
        return {
            'success': True,
            'order_id': order['id'],
            'status': order['status'],
            'raw_status': order['raw_status'],
            'filled': order['filled'],
            'remaining': order['remaining'],
            'average': order['average'],
            'source': source,
            'order': order,
        }
    except (LighterAPIError, LighterSignerError, ValueError) as error:
        return {'success': False, 'error': str(error), 'order_id': order_id}


def test_connection(check_private: bool = True, symbol: str = 'BTC') -> list[dict]:
    """Check the Lighter setup step by step. Each step: {'step', 'ok', 'detail'}."""
    steps: list[dict] = []

    def step(name: str, action: Callable[[], str]) -> bool:
        try:
            steps.append({'step': name, 'ok': True, 'detail': action()})
            return True
        except Exception as error:  # report every failure as a step result
            steps.append({'step': name, 'ok': False, 'detail': str(error)})
            return False

    try:
        exchange = create_lighter_exchange()
    except LighterConfigError as error:
        return [{'step': 'Đọc .env', 'ok': False, 'detail': str(error)}]

    if not step('Kết nối API public', lambda: f"{exchange.base_url} — {len(exchange.load_markets())} market perp"):
        return steps

    def check_symbol() -> str:
        ticker = exchange.fetch_ticker(symbol)
        return f"{ticker['symbol']} last ${ticker['last']:,} mark ${ticker['markPrice']:,}"

    step(f'Giá {symbol}', check_symbol)

    def check_account() -> str:
        summary = exchange.account_summary()
        return (f"index {summary['account_index']} — equity ${summary['equity']:,.2f}, "
                f"khả dụng ${summary['available']:,.2f}, {summary['positions']} vị thế")

    if not step('Tài khoản (LIGHTER_ACCOUNT_INDEX)', check_account) or not check_private:
        return steps

    if not exchange.has_api_key:
        steps.append({'step': 'API key', 'ok': False, 'detail': 'Chưa có LIGHTER_API_PRIVATE_KEY / LIGHTER_API_KEY_INDEX — chỉ xem được, chưa đặt lệnh được'})
        return steps

    step('Thư viện ký (signer)', lambda: f"{lighter_signer.find_library_path()} (lighter-sdk {lighter_signer.SIGNER_VERSION})")
    if not step('Tạo signer', lambda: f"API key index {exchange.signer.api_key_index}, "
                                      f"account {exchange.signer.account_index}, chain {exchange.chain_id}"):
        return steps

    def check_key() -> str:
        error = exchange.signer.check_client()
        if error:
            raise LighterSignerError(error)
        return "API key khớp với key đã đăng ký trên Lighter"

    if not step('Kiểm tra API key', check_key):
        return steps
    step('Đọc lệnh đang mở (auth token)', lambda: f"{len(exchange.fetch_open_orders())} lệnh đang mở")
    step('Đọc lịch sử khớp lệnh', lambda: f"{len(exchange.fetch_my_trades(limit=5))} lệnh khớp gần nhất")
    return steps


if __name__ == '__main__':
    for item in test_connection():
        print(f"{'✅' if item['ok'] else '❌'} {item['step']}: {item['detail']}")
