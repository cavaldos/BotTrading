
```
src/
├── __init__.py
├── main.py
├── config/
│   ├── __init__.py
│   ├── strategy.ex.yaml
│   ├── strategy.yaml
│   ├── live.ex.yaml
│   ├── live.yaml
│   ├── backtest.ex.yaml
│   ├── backtest.yaml
│   └── load_config.py
├── evaluate/
│   ├── __init__.py
│   ├── analyze_trades.py
│   ├── export_chart_data.py
│   ├── export_live_data.py
│   ├── verify_look_bias.py
│   └── verify_trades.py
├── indicators/
│   ├── BOS.py
│   ├── breaker_block.py
│   ├── CHOCH.py
│   ├── eqh_eql.py
│   ├── FVG.py
│   ├── imbalance.py
│   ├── main.py
│   ├── mitigation_block.py
│   ├── order_block.py
│   ├── retracement.py
│   └── swing.py
├── live/
│   ├── __init__.py
│   ├── dashboard.py
│   ├── run.py
│   ├── show_orders.py
│   ├── stats.py
│   ├── binance/
│   │   ├── __init__.py
│   │   ├── binance_dashboard.py
│   │   ├── exchange.py
│   │   ├── getprice.py
│   │   ├── order_executor.py
│   │   └── symbol_helper.py
│   ├── bybit/
│   │   ├── exchange.py
│   │   ├── limit_order.py
│   │   ├── market_order.py
│   │   ├── risk_calculator.py
│   │   └── tool/
│   │       ├── bybit_dashboard.py
│   │       ├── compare_orders.py
│   │       ├── get_history.py
│   │       ├── order_manager.py
│   │       ├── sent_orders_tui.py
│   │       └── symbol_helper.py
│   └── core/
│       ├── calculate.py
│       ├── discord_notify.py
│       ├── execute.py
│       ├── notify.py
│       ├── place_order.py
│       └── send.py
├── utils/
│   ├── __init__.py
│   ├── benchmark_patterns.py
│   ├── console_logger.py
│   ├── convertTF.py
│   ├── get_data_binance.py
│   ├── get_data_bybit.py
│   ├── get_data.py
│   ├── JsonDB.py
│   ├── price_chart.py
│   ├── remove_all_files.py
│   └── rounding.py
├── backtest/
│   ├── __init__.py
│   ├── statistics.py
│   ├── main.py
├── strategy/
│   ├── __init__.py
│   ├── base_strategy.py
│   ├── smc_strategy.py
```
