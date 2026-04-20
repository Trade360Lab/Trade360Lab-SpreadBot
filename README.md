# SpreadBot

Practical market making / spread-capture bot for Bybit linear perpetuals, with parquet market-data storage, event-driven backtest, rolling WFA, Optuna search, dry-run live runtime and Telegram monitoring.

## Status

The workspace provided for this build was empty, so the current repository is a full baseline implementation rather than a literal refactor of pre-existing strategy code. The architecture stays intentionally compact and keeps the strategy in one file.

## Structure

```text
repo/
  pyproject.toml
  README.md
  .env.example
  Dockerfile
  docker-compose.yml
  Makefile
  configs/
  data/
  logs/
  src/
  scripts/
  tests/
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

## Env Variables

Required / important:

- `APP_ENV`
- `LOG_LEVEL`
- `EXCHANGE`
- `SYMBOL`
- `MARKET_TYPE`
- `DRY_RUN`
- `LIVE_TRADING_ENABLED`
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `BYBIT_TESTNET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ENABLE_TELEGRAM`
- `ENABLE_TELEGRAM_TRADE_CONTROL`
- `DATA_DIR`
- `LOG_DIR`
- `CONFIG_PATH`
- `APP_NAME`
- `MAKER_FEE_BPS`
- `TAKER_FEE_BPS`
- `MAX_INVENTORY`
- `HARD_INVENTORY_LIMIT`
- `ORDER_SIZE`
- `MIN_SPREAD_BPS`
- `MAX_SPREAD_BPS`
- `VOLATILITY_MULTIPLIER`
- `INVENTORY_SKEW_COEFFICIENT`
- `TOXICITY_THRESHOLD`
- `CANCEL_EDGE_BPS`
- `MAX_QUOTE_AGE_SECONDS`
- `MAX_DATA_STALENESS_SECONDS`
- `MAX_VOLATILITY_BPS`
- `EMERGENCY_FLATTEN_PNL`
- `EMERGENCY_FLATTEN_INVENTORY`
- `HISTORICAL_LOOKBACK_DAYS`
- `LIVE_LOOP_INTERVAL_MS`
- `LATENCY_MS`

## Local Commands

```bash
make install
make test
make download
make record
make backtest
make wfa
make optuna
make live
make telegram
```

Direct entrypoints:

```bash
python scripts/download_data.py
python scripts/record_data.py
python scripts/run_backtest.py
python scripts/run_wfa.py
python scripts/run_optuna.py
python scripts/run_live.py
python scripts/run_telegram_bot.py
```

## Data Workflow

Historical download:

```bash
python scripts/download_data.py
```

Live recorder:

```bash
python scripts/record_data.py
```

Stored format:

- `parquet`
- partitioned as `data/raw/exchange=.../symbol=.../channel=.../date=...`
- normalized schema: `timestamp, exchange, symbol, channel, side, price, size, payload`

## Backtest

```bash
python scripts/run_backtest.py
```

Artifacts:

- `reports/backtest/backtest_summary.json`
- `reports/backtest/backtest_fills.csv`
- `reports/backtest/backtest_equity.csv`
- `reports/backtest/backtest_report.md`

Backtest model includes:

- event replay over trades/orderbook/mark price data
- maker-only fill approximation
- post-only quote logic
- queue-ahead approximation
- latency-aware cancel logic
- realized and unrealized inventory PnL
- adverse selection diagnostics

## WFA And Optimization

Rolling WFA:

```bash
python scripts/run_wfa.py
```

Optuna:

```bash
python scripts/run_optuna.py
```

Objective:

```text
score = net_pnl - a * max_drawdown - b * inventory_variance - c * taker_ratio
```

## Live Runtime

Dry-run by default:

```bash
python scripts/run_live.py
```

Real trading becomes possible only when all of the following are true:

- `LIVE_TRADING_ENABLED=true`
- `DRY_RUN=false`
- `BYBIT_API_KEY` and `BYBIT_API_SECRET` are set

Safety controls:

- stale-data guard
- extreme-volatility guard
- hard inventory limit
- kill switch on emergency conditions
- cancel-all on shutdown
- post-only order placement

## Telegram

Start the bot:

```bash
python scripts/run_telegram_bot.py
```

Supported commands:

- `/status`
- `/position`
- `/pnl`
- `/orders`
- `/risk`
- `/lastfills`
- `/health`

Trade-state-changing commands are disabled unless `ENABLE_TELEGRAM_TRADE_CONTROL=true`.

## Docker

Build and start:

```bash
docker compose up --build
```

Services:

- `live_bot`
- `telegram_bot`
- `recorder`

## Tests

```bash
pytest
```

Coverage focus:

- config loading
- signal and quoting math
- inventory skew
- backtest smoke path
- live risk checks

## Risk Warning

Live trading is dangerous. This repository defaults to safe mode, but configuration mistakes, exchange-side behavior, network failures, and model errors can still cause losses. Validate on testnet and in dry-run before any live capital is exposed.
