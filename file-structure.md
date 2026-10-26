# Project structure

Two swap points keep changes local:

- **Exchange**: `exchange:` in `live.yaml` picks `src/exchanges/<name>/`. The live bot only
  calls the `ExchangeAdapter` interface (`src/exchanges/base.py`), so switching Bybit -> Lighter
  is a config change.
- **Strategy**: `strategy:` in `strategy.yaml` picks `src/strategy/<name>/`. Backtest, live
  bot, snapshots and verify tools all load it through `src/strategy/registry.py`, so a new or
  changed strategy only touches its own package.

Dependency direction (a module only imports from the layers to its left):

```
core  <-  indicators  <-  strategy  <-  backtest / evaluate
core  <-  exchanges   <-  live (through src.exchanges.get_exchange / get_candle_source only)
```

```
src/
├── core/                          # shared helpers, no project imports
│   ├── timeframe.py               # timeframe_to_minutes, normalize_timeframes, convert_timeframe (+ CLI batch convert)
│   └── symbols.py                 # strip_timeframe ("BTCUSDT_1m" -> "BTCUSDT"), symbol_from_filename
├── config/
│   ├── load_config.py             # load_live_config / load_backtest_config / load_strategy_config
│   ├── live.ex.yaml               # exchange, data feed, approval, web, tracking   (copy to live.yaml)
│   ├── backtest.ex.yaml           # backtest data / output folders                  (copy to backtest.yaml)
│   └── strategy.ex.yaml           # strategy: smc + its settings                    (copy to strategy.yaml)
├── exchanges/                     # everything exchange-specific
│   ├── __init__.py                # get_exchange(), get_candle_source(), configure_exchange(); registry of exchanges
│   ├── base.py                    # ExchangeAdapter interface + OrderState (the contract)
│   ├── messages.py                # order result texts shared by all exchanges
│   ├── bybit/
│   │   ├── adapter.py             # BybitAdapter
│   │   ├── candles.py             # candle download (python -m src.exchanges.bybit.candles)
│   │   ├── exchange.py            # ccxt client, sizing, SL/TP, order helpers
│   │   ├── market_order.py / limit_order.py / risk_calculator.py
│   │   └── tools/                 # bybit_dashboard, order_manager, live_order_manager,
│   │                              # position_manager (trailing / ratchet manager), get_history, ...
│   ├── lighter/
│   │   ├── adapter.py             # LighterAdapter
│   │   ├── candles.py
│   │   ├── exchange.py            # REST client, grouped SL/TP orders (python -m ... = setup check)
│   │   ├── signer.py              # signer library bindings (--download to get it)
│   │   ├── market_order.py / limit_order.py
│   │   └── tools/                 # lighter_dashboard, order_manager, get_history, symbol_helper, test_connect
│   └── binance/                   # candles.py (data source); trading helpers not wired into the bot
├── indicators/                    # pure indicator functions (BOS, CHoCH, OB, FVG, IFVG, EQH/EQL, swings, ADX, ...)
├── strategy/
│   ├── __init__.py                # public API + plug-in contract
│   ├── base.py                    # BaseConfig, BaseStrategy, BaseSignal, BaseZone, TradePlan
│   ├── registry.py                # load_strategy, build_strategy_config, create_strategy
│   └── smc/                       # SMC strategy: Config, Strategy, STRATEGY_NAME
├── backtest/
│   ├── main.py                    # python -m src.backtest.main
│   └── statistics.py              # TradeSimulator (exits), trade CSVs, reports
├── live/
│   ├── run.py                     # python -m src.live.run (start.sh starts it with the chart)
│   ├── dashboard.py / stats.py / show_orders.py
│   ├── core/
│   │   ├── execute.py             # data -> strategy -> trade CSVs
│   │   ├── send.py                # new OPEN trades -> approval -> place_order
│   │   ├── place_order.py         # orders on the active exchange (adapter)
│   │   ├── order_tracker.py       # follows placed orders until they fill (adapter)
│   │   ├── approval.py / telegram_approval.py / discord_notify.py / snapshot.py
│   │   └── calculate.py / notify.py
│   └── web/                       # trading API for the chart: server, manual orders, history
├── evaluate/                      # research + chart export: verify_orders, analyze_*, export_live_data, ...
├── manager_order/                 # trade-management replay on backtest trades (logic.py shared rules)
└── utils/                         # getdata (candles from EXCHANGE in .env), JsonDB, rounding, console logger,
                                   # benchmark_patterns (SMC), price_chart
tests/                             # python -m unittest discover -s tests -t .
chart/                             # React web chart (Vite)
res/                               # data, logs, outputs (not in git)
```

## Adding an exchange

1. Create `src/exchanges/<name>/adapter.py` with a subclass of `ExchangeAdapter` (see `base.py`;
   `bybit/adapter.py` and `lighter/adapter.py` are complete examples).
2. Add `"<name>": "src.exchanges.<name>.adapter:<Class>"` to `TRADING_ADAPTERS` and its
   `candles.py` module to `CANDLE_SOURCES` in `src/exchanges/__init__.py`.
3. Optional: `configure(live_config)` in `src/exchanges/<name>/__init__.py` for exchange-specific settings.
4. Set `exchange: <name>` in `live.yaml`.

## Adding a strategy

1. Create `src/strategy/<name>/__init__.py` exporting `STRATEGY_NAME`, `Config` (subclass of
   `BaseConfig`) and `Strategy` (subclass of `BaseStrategy` with `generate_signals`).
2. Set `strategy: <name>` in `strategy.yaml` (plus the settings of its `Config`).
3. Run `python -m src.backtest.main`; the live bot uses the same strategy without other changes.
