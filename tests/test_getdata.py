import os
import unittest
from unittest import mock

import src.exchanges as exchanges
from src.utils import getdata as getdata_module


class GetDataTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("EXCHANGE")
        self._active = exchanges._active_name
        exchanges._active_name = None

    def tearDown(self):
        exchanges._active_name = self._active
        if self._env is None:
            os.environ.pop("EXCHANGE", None)
        else:
            os.environ["EXCHANGE"] = self._env

    def _run(self, **kwargs):
        source = mock.Mock()
        with mock.patch.object(getdata_module, "get_candle_source", return_value=source) as pick, \
                mock.patch.object(getdata_module, "load_dotenv"):
            name = getdata_module.getdata("cfg.yaml", **kwargs)
        source.getdata.assert_called_once_with("cfg.yaml", max_workers=5)
        return name, pick.call_args.args[0]

    def test_exchange_comes_from_env(self):
        os.environ["EXCHANGE"] = "lighter"
        self.assertEqual(self._run(), ("lighter", "lighter"))

    def test_argument_overrides_env(self):
        os.environ["EXCHANGE"] = "lighter"
        self.assertEqual(self._run(exchange="binance"), ("binance", "binance"))

    def test_unknown_exchange(self):
        with self.assertRaises(ValueError):
            getdata_module.getdata(exchange="okx")


if __name__ == "__main__":
    unittest.main()
