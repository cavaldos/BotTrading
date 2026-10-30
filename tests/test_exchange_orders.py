"""Orders tab: the read-only account views and GET /api/exchange/orders (no exchange is called)."""

import json
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from src.exchanges.account import finish, make_order, make_position, order_kind
from src.live.web.exchange_orders import ExchangeOrdersService, ExchangeReadError
from src.live.web.orders import OrderError

VIEWS = [
    {"name": "bybit", "label": "Bybit demo", "configured": True},
    {"name": "binance", "label": "Binance", "configured": False},
]


class AccountFormatTest(unittest.TestCase):
    def test_order_kind(self):
        self.assertEqual(order_kind("Market", True, "StopLoss"), "stop_loss")
        self.assertEqual(order_kind("Market", True, "TakeProfit"), "take_profit")
        self.assertEqual(order_kind("Market", False, "Stop"), "entry")  # Bybit conditional entry
        self.assertEqual(order_kind("STOP_MARKET", True), "stop_loss")  # Binance closePosition stop
        self.assertEqual(order_kind("TAKE_PROFIT_MARKET", True), "take_profit")
        self.assertEqual(order_kind("stop-loss-limit", True), "stop_loss")  # Lighter
        self.assertEqual(order_kind("limit", True), "close")
        self.assertEqual(order_kind("limit", False), "entry")

    def test_make_order_defaults(self):
        o = make_order(id=1, symbol="BTCUSDT", side="Sell", kind="stop_loss", type="Market", status="open",
                       price="0", triggerPrice="77757.3", amount="0.021", time="1793349486708")
        self.assertEqual((o["side"], o["positionSide"], o["price"], o["triggerPrice"]), ("sell", "LONG", None, 77757.3))
        self.assertAlmostEqual(o["time"], 1793349486.708)
        self.assertEqual(o["updated"], o["time"])
        self.assertEqual(make_order(side="buy", kind="entry")["positionSide"], "LONG")

    def test_finish_keeps_open_orders_and_drops_duplicates(self):
        old = [make_order(id=i, symbol="BTCUSDT", status="filled", time=1000 + i) for i in range(10)]
        working = make_order(id="w", symbol="BTCUSDT", status="open", time=1)
        dup = make_order(id="w", symbol="BTCUSDT", status="canceled", time=1)
        result = finish(old + [dup, working], [], limit=3)
        self.assertEqual([o["id"] for o in result["orders"]], ["9", "8", "w"])
        self.assertEqual(result["orders"][-1]["status"], "open")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.calls = []

        def fetch_account(**kwargs):
            self.calls.append(kwargs)
            return {"orders": [make_order(id="1", symbol="BTCUSDT", status="open")], "positions": []}

        self.module = SimpleNamespace(fetch_account=fetch_account)
        self.service = ExchangeOrdersService(
            symbols=["btcusdt"], ttl=10, clock=lambda: self.now,
            views=lambda: VIEWS, loader=lambda name: self.module,
        )

    def test_cache_and_arguments(self):
        with mock.patch("src.live.web.exchange_orders.active_exchange_name", return_value="bybit"):
            first = self.service.fetch("", "btcusdt", "3")
        self.assertEqual(first["exchange"], "bybit")
        self.assertEqual(self.calls[0]["symbol"], "BTCUSDT")
        self.assertEqual(self.calls[0]["since_ms"], int((self.now - 3 * 86400) * 1000))
        self.assertEqual(self.calls[0]["symbols"], ["BTCUSDT"])

        self.now += 5
        self.assertIs(self.service.fetch("bybit", "BTCUSDT", 3), first)  # cached
        self.service.fetch("bybit", "BTCUSDT", 3, force=True)  # refresh button
        self.assertEqual(len(self.calls), 2)
        self.service.fetch("bybit", "BTCUSDT", 3, force=True)  # not twice within 2 s
        self.assertEqual(len(self.calls), 2)
        self.now += 11
        self.service.fetch("bybit", "BTCUSDT", 3)
        self.assertEqual(len(self.calls), 3)

    def test_bad_requests(self):
        with self.assertRaises(OrderError):
            self.service.fetch("kraken")
        with self.assertRaisesRegex(OrderError, "no API key"):
            self.service.fetch("binance")
        with self.assertRaises(OrderError):
            self.service.fetch("bybit", "BTC/USDT")
        days = []
        self.module.fetch_account = lambda **kw: days.append(kw["since_ms"]) or {"orders": [], "positions": []}
        self.service.fetch("bybit", None, 999)
        self.assertEqual(days[0], int((self.now - 30 * 86400) * 1000))  # capped at 30 days

    def test_exchange_error(self):
        def broken(**kwargs):
            raise RuntimeError("bybit {\"retCode\":10003,\"retMsg\":\"API key is invalid.\"}\nmore")

        self.module.fetch_account = broken
        with self.assertRaisesRegex(ExchangeReadError, "Bybit demo: bybit .*API key is invalid"):
            self.service.fetch("bybit")


class EndpointTest(unittest.TestCase):
    def test_routes(self):
        from src.live.web.server import WebApiServer

        service = mock.Mock()
        service.accounts.return_value = VIEWS
        service.fetch.return_value = {"orders": [], "positions": []}
        tracker = mock.Mock()
        tracker.recent.return_value = []
        orders = mock.Mock()
        orders.config.return_value = {}
        token = "t" * 20
        server = WebApiServer("127.0.0.1", 0, token, orders=orders, tracker=tracker, exchange_orders=service)
        server.start()
        self.addCleanup(server.stop)
        port = server._httpd.server_address[1]

        def get(path, auth=True):
            headers = {"Authorization": f"Bearer {token}"} if auth else {}
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        self.assertEqual(get("/api/exchange/accounts"), (200, VIEWS))
        self.assertEqual(get("/api/exchange/orders?exchange=bybit&symbol=ETHUSDT&days=14&fresh=1")[0], 200)
        service.fetch.assert_called_with("bybit", "ETHUSDT", "14", force=True)
        service.trades.return_value = {"trades": []}
        self.assertEqual(get("/api/exchange/trades?exchange=lighter&symbol=BTCUSDT&limit=30"), (200, {"trades": []}))
        service.trades.assert_called_with("lighter", "BTCUSDT", "30", force=False)
        self.assertEqual(get("/api/exchange/orders", auth=False)[0], 401)

        service.fetch.side_effect = OrderError("Unknown exchange")
        self.assertEqual(get("/api/exchange/orders?exchange=x"), (400, {"error": "Unknown exchange"}))
        service.fetch.side_effect = ExchangeReadError("Bybit demo: timeout")
        self.assertEqual(get("/api/exchange/orders"), (502, {"error": "Bybit demo: timeout"}))



class BuildTradesTest(unittest.TestCase):
    """Orders -> trades (entry -> exit with the planned SL / TP) for the chart."""

    @staticmethod
    def order(oid, kind, side, t, *, status="filled", qty=1.0, avg=None, trigger=None, updated=None, pos=None):
        return make_order(id=oid, symbol="BTCUSDT", side=side, kind=kind, status=status, amount=qty,
                          filled=qty if status == "filled" else 0, average=avg, triggerPrice=trigger,
                          time=t, updated=updated or t, positionSide=pos)

    def test_long_with_add_closed_by_stop(self):
        from src.exchanges.trades import build_trades

        o = self.order
        orders = [
            o("e1", "entry", "buy", 100, qty=2, avg=100.0),
            o("sl1", "stop_loss", "sell", 103, qty=2, avg=95.0, trigger=95.0, updated=500),   # first stop: the plan
            o("tp1", "take_profit", "sell", 103, status="canceled", qty=2, trigger=110.0, updated=500),
            o("e2", "entry", "buy", 200, qty=1, avg=103.0),
            o("sl2", "stop_loss", "sell", 205, status="canceled", qty=3, trigger=98.0, updated=500),
            o("x1", "stop_loss", "sell", 205, qty=1, avg=96.0, trigger=96.0, updated=500),
        ]
        (t,) = build_trades(orders, [])
        self.assertEqual((t["type"], t["status"], t["result"], t["exitReason"]), ("LONG", "closed", "Loss", "SL"))
        self.assertAlmostEqual(t["entryPrice"], 101.0)       # (2 * 100 + 1 * 103) / 3
        self.assertAlmostEqual(t["exitPrice"], (95 * 2 + 96) / 3)
        self.assertEqual((t["stopLoss"], t["takeProfit"]), (95.0, 110.0))
        self.assertEqual((t["entryTime"], t["exitTime"], t["size"]), (100, 500, 3))
        self.assertAlmostEqual(t["pnl"], (95 - 101) * 2 + (96 - 101) * 1)
        self.assertIn("sl1", t["orders"])

    def test_open_short_and_old_exits(self):
        from src.exchanges.trades import build_trades

        o = self.order
        orders = [
            o("old-exit", "take_profit", "sell", 50, qty=1, avg=120.0, trigger=120.0),  # entry before the window
            o("e1", "entry", "sell", 100, qty=2, avg=100.0),
            o("sl", "stop_loss", "buy", 101, status="open", qty=2, trigger=104.0),
            o("tp", "take_profit", "buy", 101, status="open", qty=2, trigger=90.0),
        ]
        position = make_position(symbol="BTCUSDT", side="SHORT", size=2, entryPrice=100.0, unrealizedPnl=3.0)
        (t,) = build_trades(orders, [position], now=300)
        self.assertEqual((t["type"], t["status"], t["result"], t["exitTime"]), ("SHORT", "open", "Pending", None))
        self.assertEqual((t["stopLoss"], t["takeProfit"], t["pnl"]), (104.0, 90.0, 3.0))

        # Same orders but the exchange has no position: the exit is missing, nothing is drawn
        self.assertEqual(build_trades(orders, [], now=300), [])

    def test_position_older_than_history_and_limit(self):
        from src.exchanges.trades import build_trades

        o = self.order
        orders = [o("sl", "stop_loss", "sell", 400, status="open", trigger=90.0)]
        for i in range(5):  # five closed round trips
            orders += [o(f"e{i}", "entry", "buy", 10 * i + 1, avg=100.0), o(f"x{i}", "close", "sell", 10 * i + 5, avg=101.0)]
        position = make_position(symbol="BTCUSDT", side="LONG", size=1, entryPrice=99.0)
        trades = build_trades(orders, [position], limit=2, now=1000)
        self.assertEqual([t["status"] for t in trades], ["closed", "closed", "open"])
        self.assertEqual([t["entryTime"] for t in trades], [31, 41, 400])  # the open one starts at its stop order
        self.assertEqual((trades[-1]["entryPrice"], trades[-1]["stopLoss"]), (99.0, 90.0))
        self.assertTrue(all(t["result"] == "Win" and t["exitReason"] == "Close" for t in trades[:2]))



class KnownLevelsTest(unittest.TestCase):
    def test_levels_the_exchange_forgot_come_from_the_bot(self):
        from src.exchanges.trades import build_trades

        o = BuildTradesTest.order
        # Bybit after a few days: the entry and the TP that filled, the cancelled SL is gone
        orders = [
            o("e1", "entry", "buy", 100, qty=1.8, avg=277.85),
            o("tp", "take_profit", "sell", 103, qty=1.8, avg=278.68, trigger=278.66, updated=600),
        ]
        (t,) = build_trades(orders, [])
        self.assertEqual((t["stopLoss"], t["takeProfit"]), (None, 278.66))
        (t,) = build_trades(orders, [], known_levels={"e1": {"stopLoss": 276.99, "takeProfit": 278.7}})
        self.assertEqual((t["stopLoss"], t["takeProfit"]), (276.99, 278.66))  # the exchange's TP stays
        self.assertAlmostEqual(t["pnlR"], (278.68 - 277.85) / (277.85 - 276.99))
        # Lighter may record the client order index instead of the order index
        orders[0] = make_order(id="e1", clientId="777", symbol="BTCUSDT", side="buy", kind="entry", status="filled",
                               amount=1.8, filled=1.8, average=277.85, time=100)
        (t,) = build_trades(orders, [], known_levels={"777": {"stopLoss": 276.99}})
        self.assertEqual(t["stopLoss"], 276.99)

    def test_server_collects_levels_from_history_and_tracker(self):
        from src.live.web.server import WebApiServer

        tracker = mock.Mock()
        tracker.recent.return_value = [{"orderId": "b", "stopLoss": 9.0, "takeProfit": 11.0}, {"orderId": "a", "stopLoss": 1.0}]
        orders = mock.Mock()
        orders.config.return_value = {}
        server = WebApiServer("127.0.0.1", 0, "t" * 20, orders=orders, tracker=tracker,
                              history=lambda: [{"orderId": "a", "stopLoss": 95.0, "takeProfit": 110.0}, {"orderId": None}])
        self.assertEqual(server.known_levels(), {
            "a": {"stopLoss": 95.0, "takeProfit": 110.0},  # history (placed levels) wins
            "b": {"stopLoss": 9.0, "takeProfit": 11.0},
        })



class ClientGoneTest(unittest.TestCase):
    """A browser that drops the connection (e.g. a signal image no longer needed) is not an error."""

    def test_broken_pipe_is_not_reported(self):
        import http.client

        from src.live.web.server import WebApiServer, _Handler

        errors = []
        tracker = mock.Mock()
        tracker.recent.return_value = []
        orders = mock.Mock()
        orders.config.return_value = {}
        token = "t" * 20
        server = WebApiServer("127.0.0.1", 0, token, orders=orders, tracker=tracker, hub=mock.Mock(),
                              on_error=errors.append).start()
        self.addCleanup(server.stop)
        port = server._httpd.server_address[1]

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
            try:
                return conn.getresponse().status
            except (http.client.RemoteDisconnected, ConnectionError):
                return None
            finally:
                conn.close()

        with mock.patch.object(_Handler, "_signal_image", side_effect=BrokenPipeError(32, "Broken pipe")):
            self.assertIsNone(get("/api/signals/0123456789abcdef/image"))
        with mock.patch("builtins.print") as printed:
            # The 500 answer itself hits the closed connection: ignored by the server too
            with mock.patch.object(_Handler, "_signal_image", side_effect=RuntimeError("boom")), \
                 mock.patch.object(_Handler, "_send_json", side_effect=BrokenPipeError(32, "Broken pipe")):
                self.assertIsNone(get("/api/signals/0123456789abcdef/image"))
            printed.assert_not_called()
        self.assertEqual(errors, ["GET /api/signals/0123456789abcdef/image: boom"])


if __name__ == "__main__":
    unittest.main()
