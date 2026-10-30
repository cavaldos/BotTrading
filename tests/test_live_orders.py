"""The live bot only talks to the active ExchangeAdapter: run its order paths on a fake exchange."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import src.exchanges as exchanges
from src.exchanges import get_candle_source, get_exchange, register_exchange, set_active_exchange
from src.exchanges.base import ExchangeAdapter
from tests.fakes import FakeExchange


class FakeExchangeCase(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("EXCHANGE")
        self._active = exchanges._active_name
        self.fake = FakeExchange()
        register_exchange("fake", self.fake)
        set_active_exchange("fake")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        exchanges._adapters.pop("fake", None)
        exchanges._active_name = self._active
        if self._env is None:
            os.environ.pop("EXCHANGE", None)
        else:
            os.environ["EXCHANGE"] = self._env
        self.tmp.cleanup()


class ExchangeFactoryTest(unittest.TestCase):
    def test_trading_adapters_implement_the_interface(self):
        for name in exchanges.TRADING_ADAPTERS:
            adapter = get_exchange(name)  # no network: clients are created on first use
            self.assertIsInstance(adapter, ExchangeAdapter)
            self.assertEqual(adapter.name, name)

    def test_candle_sources_have_the_same_functions(self):
        for name in exchanges.CANDLE_SOURCES:
            module = get_candle_source(name)
            for func in ("fetch_and_save_candles_parallel", "append_latest_candle_parallel"):
                self.assertTrue(callable(getattr(module, func, None)), f"{name}.{func}")

    def test_unsupported_exchange(self):
        with self.assertRaises(ValueError):
            get_exchange("binance")  # candles only
        with self.assertRaises(ValueError):
            set_active_exchange("nope")

    def test_symbol_mapping(self):
        self.assertEqual(get_exchange("bybit").market_symbol("BTCUSDT_1m"), "BTC/USDT:USDT")
        self.assertEqual(get_exchange("lighter").market_symbol("BTCUSDT_1m"), "BTC")


class PlaceOrderTest(FakeExchangeCase):
    def test_market_order_goes_to_active_exchange(self):
        from src.live.core.place_order import format_order_message, place_market_order

        result = place_market_order("BTCUSDT_1m", "LONG", 100.0, 95.0, 110.0, "2026-01-01 00:00:00", risk_per_trade=1)
        self.assertTrue(result["success"])
        self.assertEqual(result["exchange"], "fake")
        kind, kwargs = self.fake.calls[0]
        self.assertEqual((kind, kwargs["side"], kwargs["symbol"]), ("market", "buy", "BTCUSDT_1m"))
        self.assertEqual(format_order_message(result), "FAKE market BTCUSDT ord-1")

    def test_limit_order_and_errors(self):
        from src.live.core.place_order import place_limit_order

        result = place_limit_order("ETHUSDT", "SHORT", 100.0, 105.0, 90.0)
        self.assertEqual((self.fake.calls[0][0], self.fake.calls[0][1]["side"]), ("limit", "sell"))
        self.assertEqual(result["order_type"], "limit")

        with mock.patch.object(self.fake, "place_limit_order", side_effect=RuntimeError("boom")):
            failed = place_limit_order("ETHUSDT", "SHORT", 100.0, 105.0, 90.0)
        self.assertFalse(failed["success"])
        self.assertIn("boom", failed["error"])


class ApprovalFlowTest(FakeExchangeCase):
    def test_approved_signal_places_and_tracks_order(self):
        from src.live.core import send
        from src.live.core.order_tracker import OrderTracker

        log_file = str(self.dir / "sent_orders.json")
        key = "BTCUSDT|LONG|2026-01-01 00:00:00|5m"
        send.append_sent_order(log_file, {"key": key, "status": "PENDING"})
        tracker = OrderTracker(file=str(self.dir / "tracked.json"), exchange=lambda: self.fake)
        row = pd.Series({"Symbol": "BTCUSDT", "Type": "LONG", "Entry_Time": "2026-01-01 00:00:00", "Signal_Timeframe": "5m"})
        place_kwargs = dict(symbol="BTCUSDT", order_type="LONG", entry_price=100.0, stop_loss=95.0,
                            take_profit=110.0, entry_time="2026-01-01 00:00:00", risk_per_trade=1.0)

        class Hub:
            signal = None

            def submit(self, sig):
                Hub.signal = sig

        send.set_order_tracker(tracker)
        try:
            with mock.patch.object(send, "send_custom_message"), mock.patch.object(send, "send_entry_discord"):
                sender = send._make_approval_sender(Hub(), row, key, log_file, place_kwargs)
                sender({"type": "LONG"}, None)
                sig = Hub.signal
                sig.decided_via = "web"
                ok, text = sig.on_approve(sig)
        finally:
            send.set_order_tracker(None)

        self.assertTrue(ok, text)
        self.assertTrue(text.startswith("FAKE\n"))
        saved = json.loads(Path(log_file).read_text())["sent_orders"][0]
        self.assertEqual((saved["status"], saved["order_id"]), ("OPEN", "ord-1"))
        self.assertEqual(tracker.get(sig.tracked_id).order_id, "ord-1")

    def test_late_entry_is_refused(self):
        from src.live.core import send

        self.fake.price = 94.0  # already below the stop
        self.assertIn("SL", send._late_entry_problem("BTCUSDT", "LONG", 95.0, 110.0))
        self.fake.price = 100.0
        self.assertIsNone(send._late_entry_problem("BTCUSDT", "LONG", 95.0, 110.0))


class OrderTrackerTest(FakeExchangeCase):
    def _tracker(self):
        from src.live.core.order_tracker import OrderTracker
        return OrderTracker(file=str(self.dir / "tracked.json"), exchange=lambda: self.fake)

    def _place(self, order_type="market"):
        result = self.fake.place_market_order(symbol="BTCUSDT", side="buy", entry_price=100.0, stop_loss=95.0,
                                              take_profit=110.0, leverage=20, risk_per_trade=1, risk_reward_ratio=2)
        return result["order_id"]

    def test_fill_is_detected(self):
        tracker = self._tracker()
        order_id = self._place()
        order = tracker.track(source="web", ref="r1", symbol="BTCUSDT", side="LONG", order_type="market",
                              order_id=order_id, price=100.0, stop_loss=95.0, take_profit=110.0, quantity=1.0)
        self.fake.states[order_id] = {"status": "closed", "filled": 1.0, "amount": 1.0, "average": 100.5, "reject_reason": ""}
        tracker.check_all()
        self.assertEqual((order.status, order.avg_price), ("FILLED", 100.5))

    def test_expired_limit_order_is_cancelled(self):
        tracker = self._tracker()
        order_id = self._place()
        order = tracker.track(source="web", ref="r2", symbol="BTCUSDT", side="LONG", order_type="limit",
                              order_id=order_id, price=99.0, stop_loss=95.0, take_profit=110.0, quantity=1.0,
                              timeout_minutes=1)
        order.expires_at = time.time() - 1
        tracker.check_all()
        self.assertIn(("cancel", {"symbol": "BTCUSDT", "order_id": order_id}), self.fake.calls)
        self.assertEqual(order.status, "NOT_FILLED")


class WebOrderTest(FakeExchangeCase):
    def test_preview_and_place(self):
        from src.live.web.orders import ManualOrderService

        service = ManualOrderService(symbols=["BTCUSDT_1m"], default_risk=1.0, max_risk=2.0, leverage=20,
                                     cooldown_seconds=0, log_file=str(self.dir / "manual.json"))
        body = {"symbol": "BTCUSDT", "side": "LONG", "orderType": "market", "stopLoss": 95, "takeProfit": 110}
        preview = service.preview(body)
        self.assertTrue(preview["ok"], preview["errors"])
        self.assertAlmostEqual(preview["quantity"], 0.2)
        self.assertEqual(preview["balance"], 1000.0)

        response = service.place(dict(body, confirm=True, clientId="c1"))
        self.assertTrue(response["success"], response["message"])
        self.assertEqual(self.fake.calls[-1][0], "market")

    def test_unknown_market_is_refused(self):
        from src.live.web.orders import ManualOrderService, OrderError

        self.fake.markets = set()
        service = ManualOrderService(symbols=["BTCUSDT"], default_risk=1.0, max_risk=2.0, leverage=20,
                                     cooldown_seconds=0, log_file=str(self.dir / "manual.json"))
        with self.assertRaises(OrderError):
            service.preview({"symbol": "BTCUSDT", "side": "LONG", "stopLoss": 95, "takeProfit": 110})


if __name__ == "__main__":
    unittest.main()


class AutoApproveTest(unittest.TestCase):
    """Auto mode: new signals are approved at once; the switch is saved and served by the web API."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _signal(self, placed):
        from src.live.core.approval import Signal

        def on_approve(sig):
            placed.append(sig.id)
            return True, "ok"

        return Signal(key="k", label="BTCUSDT_5m| LONG", symbol="BTCUSDT", side="LONG", signal_timeframe="5m",
                      entry_time="2026-01-01 00:00:00", entry=100.0, stop_loss=95.0, take_profit=110.0,
                      risk_per_trade=1.0, message_kwargs={}, on_approve=on_approve,
                      on_reject=lambda s: None, on_expire=lambda s: None)

    def _wait(self, cond, seconds=3.0):
        deadline = time.time() + seconds
        while time.time() < deadline and not cond():
            time.sleep(0.02)
        return cond()

    def test_auto_mode_places_new_signals_and_persists(self):
        from src.live.core.approval import ApprovalHub

        auto_file = str(self.dir / "mode.json")
        hub = ApprovalHub(timeout_minutes=5, auto_file=auto_file).start()
        self.addCleanup(hub.stop)
        changes = []
        hub.add_mode_listener(lambda on, by: changes.append((on, by)))
        placed = []

        manual = hub.submit(self._signal(placed))
        time.sleep(0.1)
        self.assertEqual((manual.status, placed), ("PENDING", []))

        hub.set_auto_approve(True, by="web")
        self.assertEqual(changes, [(True, "web")])
        time.sleep(0.1)
        self.assertEqual(manual.status, "PENDING")  # signals already waiting are not traded

        auto = hub.submit(self._signal(placed))
        self.assertTrue(self._wait(lambda: auto.status == "PLACED"), auto.status)
        self.assertEqual((auto.decided_via, placed), ("auto", [auto.id]))

        # Kept after a restart, overrides the config default
        self.assertTrue(ApprovalHub(auto_approve=False, auto_file=auto_file).auto_approve)
        hub.set_auto_approve(False)
        self.assertFalse(ApprovalHub(auto_approve=True, auto_file=auto_file).auto_approve)
        self.assertTrue(ApprovalHub(auto_approve=True, auto_file=str(self.dir / "none.json")).auto_approve)

    def test_web_endpoint_toggles_auto_mode(self):
        import urllib.request

        from src.live.core.approval import ApprovalHub
        from src.live.web.server import WebApiServer

        hub = ApprovalHub(auto_file=str(self.dir / "mode.json")).start()
        self.addCleanup(hub.stop)
        tracker = mock.Mock()
        tracker.recent.return_value = []
        orders = mock.Mock()
        orders.config.return_value = {}
        token = "t" * 20
        server = WebApiServer("127.0.0.1", 0, token, orders=orders, tracker=tracker, hub=hub)
        server.start()
        self.addCleanup(server.stop)
        port = server._httpd.server_address[1]
        events = server.subscribe()

        def post(body):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/approval/auto", data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        self.assertEqual(post({"enabled": True}), (200, {"ok": True, "autoApprove": True}))
        self.assertTrue(hub.auto_approve)
        self.assertTrue(server.snapshot()["autoApprove"])
        self.assertEqual(events.get(timeout=2)[0], "mode")
        self.assertEqual(post({"enabled": "yes"})[0], 400)
        self.assertEqual(post({"enabled": False})[1]["autoApprove"], False)


class EntryImageTest(unittest.TestCase):
    """The chart image of an entry reaches Telegram in every mode (approval, auto, no approval)."""

    KWARGS = dict(type="LONG", entry=100.0, stoploss=95.0, takeprofit=110.0, time="2026-01-01 00:00:00",
                  name_coin="BTCUSDT", risk_per_trade=1.0, signal_timeframe="5m")

    def _post(self, calls):
        def post(url, **kw):
            calls.append((url.rsplit("/", 1)[-1], kw))
            return mock.Mock(json=lambda: {"ok": True, "result": {"message_id": len(calls)}})
        return post

    def test_no_approval_entry_sends_photo_with_result(self):
        from src.live.core import telegram_approval

        calls = []
        with mock.patch.object(telegram_approval.requests, "post", self._post(calls)):
            ok = telegram_approval.send_entry_telegram(dict(self.KWARGS, result="**FAKE**\nOK"), b"png", "t", "1")
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in calls], ["sendPhoto"])
        self.assertIn("FAKE", calls[0][1]["data"]["caption"])
        self.assertNotIn("**", calls[0][1]["data"]["caption"])

    def test_no_approval_order_path_uses_telegram_sender(self):
        from src.live.core import send

        sent = []
        with mock.patch.object(send, "send_entry_discord") as discord, \
                mock.patch.object(send, "send_entry_telegram", lambda kw, img: sent.append(img)):
            send._send_entry_all(dict(self.KWARGS, result="r"), b"png")
        discord.assert_called_once()
        self.assertEqual(sent, [b"png"])

    def test_channel_sends_photo_even_when_text_is_too_long(self):
        from src.live.core.approval import ApprovalHub, Signal
        from src.live.core import telegram_approval

        hub = ApprovalHub()
        channel = telegram_approval.TelegramChannel("t", "1", hub)
        calls = []
        channel._call = lambda method, **kw: calls.append(method) or {"message_id": 1}
        sig = Signal(key="k", label="L", symbol="BTCUSDT", side="LONG", signal_timeframe="5m",
                     entry_time="2026-01-01 00:00:00", entry=100.0, stop_loss=95.0, take_profit=110.0,
                     risk_per_trade=1.0, message_kwargs=self.KWARGS, image=b"png",
                     on_approve=lambda s: (True, "ok"), on_reject=lambda s: None, on_expire=lambda s: None)
        with mock.patch.object(telegram_approval, "CAPTION_LIMIT", 10):
            channel._send_signal(sig)
        self.assertEqual(calls, ["sendPhoto", "sendMessage"])
