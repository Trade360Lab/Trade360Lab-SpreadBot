.PHONY: help install test download record backtest wfa optuna live telegram docker-up

PYTHON ?= python3
PIP ?= pip3

help:
	@echo "install    Install package"
	@echo "test       Run pytest"
	@echo "download   Download historical data"
	@echo "record     Start live recorder"
	@echo "backtest   Run event-driven backtest"
	@echo "wfa        Run rolling WFA"
	@echo "optuna     Run parameter search"
	@echo "live       Run live/dry runtime"
	@echo "telegram   Run Telegram command bot"
	@echo "docker-up  Start docker compose"

install:
	$(PIP) install -e .[dev]

test:
	pytest

download:
	$(PYTHON) scripts/download_data.py

record:
	$(PYTHON) scripts/record_data.py

backtest:
	$(PYTHON) scripts/run_backtest.py

wfa:
	$(PYTHON) scripts/run_wfa.py

optuna:
	$(PYTHON) scripts/run_optuna.py

live:
	$(PYTHON) scripts/run_live.py

telegram:
	$(PYTHON) scripts/run_telegram_bot.py

docker-up:
	docker compose up --build
