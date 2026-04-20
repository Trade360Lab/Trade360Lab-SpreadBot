from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils import ensure_dir


class AppConfig(BaseModel):
    name: str = "spreadbot"
    env: str = "dev"
    exchange: str = "bybit"
    symbol: str = "BTCUSDT"
    market_type: str = "linear"
    data_dir: str = "./data"
    log_dir: str = "./logs"
    dry_run: bool = True
    live_trading_enabled: bool = False
    enable_telegram: bool = False
    enable_telegram_trade_control: bool = False
    config_name: str = "base"


class FeeConfig(BaseModel):
    maker_fee_bps: float = 1.0
    taker_fee_bps: float = 5.5


class RiskConfig(BaseModel):
    max_inventory: float = 0.02
    hard_inventory_limit: float = 0.03
    emergency_flatten_pnl: float = -250.0
    emergency_flatten_inventory: float = 0.025
    max_data_staleness_seconds: float = 2.5
    max_volatility_bps: float = 35.0
    toxicity_threshold: float = 0.7


class StrategyConfig(BaseModel):
    order_size: float = 0.001
    min_spread_bps: float = 2.0
    max_spread_bps: float = 12.0
    volatility_multiplier: float = 1.5
    inventory_skew_coefficient: float = 0.75
    cancel_edge_bps: float = 0.8
    max_quote_age_seconds: float = 3.0
    quote_levels: int = 1
    imbalance_window: int = 50
    trade_flow_window: int = 100
    volatility_window: int = 100
    alpha_clip: float = 3.0
    alpha_threshold: float = 0.02


class BacktestConfig(BaseModel):
    initial_cash: float = 100_000.0
    latency_ms: int = 120
    fill_probability: float = 0.35
    queue_ahead_size: float = 1.5
    report_dir: str = "./reports/backtest"


class OptimizerConfig(BaseModel):
    train_days: int = 3
    test_days: int = 1
    step_days: int = 1
    n_trials: int = 20
    timeout_seconds: int = 300
    dd_penalty: float = 2.0
    inventory_penalty: float = 0.5
    taker_penalty: float = 20.0


class DataConfig(BaseModel):
    historical_lookback_days: int = 7
    recorder_flush_seconds: int = 10
    channels: list[str] = Field(default_factory=lambda: ["trades", "orderbook", "candles", "mark_price"])


class LiveConfig(BaseModel):
    loop_interval_ms: int = 500
    heartbeat_seconds: int = 60


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    EXCHANGE: str = "bybit"
    SYMBOL: str = "BTCUSDT"
    MARKET_TYPE: str = "linear"
    DRY_RUN: bool = True
    LIVE_TRADING_ENABLED: bool = False
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = True
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    ENABLE_TELEGRAM: bool = False
    ENABLE_TELEGRAM_TRADE_CONTROL: bool = False
    DATA_DIR: str = "./data"
    LOG_DIR: str = "./logs"
    CONFIG_PATH: str = "./configs/base.yaml"
    APP_NAME: str = "spreadbot"
    MAKER_FEE_BPS: float = 1.0
    TAKER_FEE_BPS: float = 5.5
    MAX_INVENTORY: float = 0.02
    HARD_INVENTORY_LIMIT: float = 0.03
    ORDER_SIZE: float = 0.001
    MIN_SPREAD_BPS: float = 2.0
    MAX_SPREAD_BPS: float = 12.0
    VOLATILITY_MULTIPLIER: float = 1.5
    INVENTORY_SKEW_COEFFICIENT: float = 0.75
    TOXICITY_THRESHOLD: float = 0.7
    CANCEL_EDGE_BPS: float = 0.8
    MAX_QUOTE_AGE_SECONDS: float = 3.0
    MAX_DATA_STALENESS_SECONDS: float = 2.5
    MAX_VOLATILITY_BPS: float = 35.0
    EMERGENCY_FLATTEN_PNL: float = -250.0
    EMERGENCY_FLATTEN_INVENTORY: float = 0.025
    HISTORICAL_LOOKBACK_DAYS: int = 7
    LIVE_LOOP_INTERVAL_MS: int = 500
    LATENCY_MS: int = 120


class Settings(BaseModel):
    env: EnvSettings
    app: AppConfig
    fees: FeeConfig
    risk: RiskConfig
    strategy: StrategyConfig
    backtest: BacktestConfig
    optimizer: OptimizerConfig
    data: DataConfig
    live: LiveConfig

    @property
    def is_safe_mode(self) -> bool:
        return self.app.dry_run or not self.app.live_trading_enabled

    @property
    def has_exchange_credentials(self) -> bool:
        return bool(self.env.BYBIT_API_KEY and self.env.BYBIT_API_SECRET)

    @property
    def real_trading_enabled(self) -> bool:
        return self.app.live_trading_enabled and not self.app.dry_run and self.has_exchange_credentials


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_update(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.exists():
        return {}
    loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML config must be a mapping: {candidate}")
    return loaded


def _env_or_default(env: EnvSettings, key: str, fallback: Any) -> Any:
    return getattr(env, key) if key in os.environ else fallback


def load_settings(config_path: str | None = None) -> Settings:
    env = EnvSettings()
    base_config = _load_yaml("./configs/base.yaml")
    overlay_config = _load_yaml(config_path or env.CONFIG_PATH)
    merged = _deep_update(base_config, overlay_config)

    app = AppConfig(**{
        **merged.get("app", {}),
        "name": env.APP_NAME or merged.get("app", {}).get("name", "spreadbot"),
        "env": env.APP_ENV,
        "exchange": env.EXCHANGE,
        "symbol": env.SYMBOL,
        "market_type": env.MARKET_TYPE,
        "data_dir": env.DATA_DIR,
        "log_dir": env.LOG_DIR,
        "dry_run": env.DRY_RUN,
        "live_trading_enabled": env.LIVE_TRADING_ENABLED,
        "enable_telegram": env.ENABLE_TELEGRAM,
        "enable_telegram_trade_control": env.ENABLE_TELEGRAM_TRADE_CONTROL,
    })

    settings = Settings(
        env=env,
        app=app,
        fees=FeeConfig(**{
            **merged.get("fees", {}),
            "maker_fee_bps": _env_or_default(env, "MAKER_FEE_BPS", merged.get("fees", {}).get("maker_fee_bps", 1.0)),
            "taker_fee_bps": _env_or_default(env, "TAKER_FEE_BPS", merged.get("fees", {}).get("taker_fee_bps", 5.5)),
        }),
        risk=RiskConfig(**{
            **merged.get("risk", {}),
            "max_inventory": _env_or_default(env, "MAX_INVENTORY", merged.get("risk", {}).get("max_inventory", 0.02)),
            "hard_inventory_limit": _env_or_default(env, "HARD_INVENTORY_LIMIT", merged.get("risk", {}).get("hard_inventory_limit", 0.03)),
            "emergency_flatten_pnl": _env_or_default(env, "EMERGENCY_FLATTEN_PNL", merged.get("risk", {}).get("emergency_flatten_pnl", -250.0)),
            "emergency_flatten_inventory": _env_or_default(env, "EMERGENCY_FLATTEN_INVENTORY", merged.get("risk", {}).get("emergency_flatten_inventory", 0.025)),
            "max_data_staleness_seconds": _env_or_default(env, "MAX_DATA_STALENESS_SECONDS", merged.get("risk", {}).get("max_data_staleness_seconds", 2.5)),
            "max_volatility_bps": _env_or_default(env, "MAX_VOLATILITY_BPS", merged.get("risk", {}).get("max_volatility_bps", 35.0)),
            "toxicity_threshold": _env_or_default(env, "TOXICITY_THRESHOLD", merged.get("risk", {}).get("toxicity_threshold", 0.7)),
        }),
        strategy=StrategyConfig(**{
            **merged.get("strategy", {}),
            "order_size": _env_or_default(env, "ORDER_SIZE", merged.get("strategy", {}).get("order_size", 0.001)),
            "min_spread_bps": _env_or_default(env, "MIN_SPREAD_BPS", merged.get("strategy", {}).get("min_spread_bps", 2.0)),
            "max_spread_bps": _env_or_default(env, "MAX_SPREAD_BPS", merged.get("strategy", {}).get("max_spread_bps", 12.0)),
            "volatility_multiplier": _env_or_default(env, "VOLATILITY_MULTIPLIER", merged.get("strategy", {}).get("volatility_multiplier", 1.5)),
            "inventory_skew_coefficient": _env_or_default(env, "INVENTORY_SKEW_COEFFICIENT", merged.get("strategy", {}).get("inventory_skew_coefficient", 0.75)),
            "cancel_edge_bps": _env_or_default(env, "CANCEL_EDGE_BPS", merged.get("strategy", {}).get("cancel_edge_bps", 0.8)),
            "max_quote_age_seconds": _env_or_default(env, "MAX_QUOTE_AGE_SECONDS", merged.get("strategy", {}).get("max_quote_age_seconds", 3.0)),
        }),
        backtest=BacktestConfig(**{
            **merged.get("backtest", {}),
            "latency_ms": _env_or_default(env, "LATENCY_MS", merged.get("backtest", {}).get("latency_ms", 120)),
        }),
        optimizer=OptimizerConfig(**merged.get("optimizer", {})),
        data=DataConfig(**{
            **merged.get("data", {}),
            "historical_lookback_days": _env_or_default(env, "HISTORICAL_LOOKBACK_DAYS", merged.get("data", {}).get("historical_lookback_days", 7)),
        }),
        live=LiveConfig(**{
            **merged.get("live", {}),
            "loop_interval_ms": _env_or_default(env, "LIVE_LOOP_INTERVAL_MS", merged.get("live", {}).get("loop_interval_ms", 500)),
        }),
    )

    ensure_dir(settings.app.data_dir)
    ensure_dir(settings.app.log_dir)
    ensure_dir(Path(settings.app.data_dir) / "raw")
    ensure_dir(Path(settings.app.data_dir) / "processed")
    return settings


@lru_cache(maxsize=4)
def get_settings(config_path: str | None = None) -> Settings:
    return load_settings(config_path=config_path)
