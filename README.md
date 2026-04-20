![PullbackTrader](public/ForPosts(Yellow).png)

<h1 align="center">Trade360Lab-SpreadBot</h1>

<p align="center">
Практический market making / spread-capture бот для Bybit linear perpetual с хранением данных в parquet,
event-driven бэктестом, rolling WFA, Optuna-оптимизацией, dry-run/live runtime и Telegram-мониторингом.
</p>

<h2 align="center">Статус</h2>

Исходный workspace для этой сборки был пустым, поэтому текущий репозиторий представляет собой полноценную рабочую baseline-реализацию, а не буквальный рефактор уже существующего локального кода стратегии. Архитектура намеренно оставлена компактной, а стратегическая логика собрана в одном файле.

<h2 align="center">Структура</h2>

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

<h2 align="center">Установка</h2>

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

<h2 align="center">Переменные Окружения</h2>

Ключевые переменные:

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

<h2 align="center">Локальные Команды</h2>

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

Прямые entrypoints:

```bash
python scripts/download_data.py
python scripts/record_data.py
python scripts/run_backtest.py
python scripts/run_wfa.py
python scripts/run_optuna.py
python scripts/run_live.py
python scripts/run_telegram_bot.py
```

<h2 align="center">Работа С Данными</h2>

Загрузка исторических данных:

```bash
python scripts/download_data.py
```

Запись live-потока:

```bash
python scripts/record_data.py
```

Формат хранения:

- `parquet`
- партиционирование: `data/raw/exchange=.../symbol=.../channel=.../date=...`
- нормализованная схема: `timestamp, exchange, symbol, channel, side, price, size, payload`

<h2 align="center">Бэктест</h2>

```bash
python scripts/run_backtest.py
```

Артефакты:

- `reports/backtest/backtest_summary.json`
- `reports/backtest/backtest_fills.csv`
- `reports/backtest/backtest_equity.csv`
- `reports/backtest/backtest_report.md`

Модель бэктеста включает:

- replay рыночных событий по trades/orderbook/mark price
- приближение maker-fill логики
- post-only quoting
- approximation очереди исполнения
- latency-aware cancel logic
- realized/unrealized inventory PnL
- adverse selection diagnostics

<h2 align="center">WFA И Оптимизация</h2>

Rolling WFA:

```bash
python scripts/run_wfa.py
```

Optuna:

```bash
python scripts/run_optuna.py
```

Целевая функция:

```text
score = net_pnl - a * max_drawdown - b * inventory_variance - c * taker_ratio
```

<h2 align="center">Live Runtime</h2>

Dry-run по умолчанию:

```bash
python scripts/run_live.py
```

Реальная торговля возможна только если одновременно выполнены все условия:

- `LIVE_TRADING_ENABLED=true`
- `DRY_RUN=false`
- заданы `BYBIT_API_KEY` и `BYBIT_API_SECRET`

Защитные механизмы:

- защита от stale data
- защита от экстремальной волатильности
- жёсткий лимит inventory
- kill switch при аварийных условиях
- cancel-all при shutdown
- post-only выставление заявок

<h2 align="center">Telegram</h2>

Запуск бота:

```bash
python scripts/run_telegram_bot.py
```

Поддерживаемые команды:

- `/status`
- `/position`
- `/pnl`
- `/orders`
- `/risk`
- `/lastfills`
- `/health`

Команды, меняющие торговое состояние, отключены, пока `ENABLE_TELEGRAM_TRADE_CONTROL=true` не включён явно.

<h2 align="center">Docker</h2>

Сборка и запуск:

```bash
docker compose up --build
```

Сервисы:

- `live_bot`
- `telegram_bot`
- `recorder`

<h2 align="center">Тесты</h2>

```bash
pytest
```

Покрытие сфокусировано на:

- загрузке конфигов
- сигналах и quoting math
- inventory skew
- smoke-сценарии бэктеста
- live risk checks

<h2 align="center">Предупреждение О Рисках</h2>

Live trading связан с риском реальных убытков. Репозиторий по умолчанию запускается в safe mode, но ошибки конфигурации, поведение биржи, сетевые сбои и ошибки модели всё равно могут привести к потерям. Перед любым live-запуском обязательно проверьте всё на testnet и в dry-run режиме.
