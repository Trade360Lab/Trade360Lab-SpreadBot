from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from exchange import BybitExchange, BybitWebsocketClient, fetch_mark_price_history
from settings import Settings
from utils import ensure_dir, floor_ts, utc_now

LOGGER = logging.getLogger(__name__)

SCHEMA_COLUMNS = ["timestamp", "exchange", "symbol", "channel", "side", "price", "size", "payload"]


@dataclass(slots=True)
class MarketDataBatch:
    channel: str
    frame: pd.DataFrame


class ParquetMarketStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)

    def _target_dir(self, exchange: str, symbol: str, channel: str, trade_date: str) -> Path:
        return self.root_dir / "raw" / f"exchange={exchange}" / f"symbol={symbol}" / f"channel={channel}" / f"date={trade_date}"

    def write(self, batch: MarketDataBatch, exchange: str, symbol: str) -> Path:
        frame = normalize_frame(batch.frame)
        if frame.empty:
            raise ValueError("cannot persist empty frame")
        trade_date = frame["timestamp"].dt.strftime("%Y-%m-%d").iloc[0]
        target_dir = ensure_dir(self._target_dir(exchange, symbol, batch.channel, trade_date))
        path = target_dir / f"{batch.channel}_{int(time.time() * 1000)}.parquet"
        frame.to_parquet(path, index=False)
        return path

    def load(self, exchange: str, symbol: str, channels: list[str] | None = None) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        channel_values = channels or []
        search_root = self.root_dir / "raw" / f"exchange={exchange}" / f"symbol={symbol}"
        if not search_root.exists():
            return pd.DataFrame(columns=SCHEMA_COLUMNS)
        candidates = search_root.rglob("*.parquet")
        for path in candidates:
            channel = path.parent.parent.name.split("=", maxsplit=1)[1]
            if channel_values and channel not in channel_values:
                continue
            frames.append(pd.read_parquet(path))
        if not frames:
            return pd.DataFrame(columns=SCHEMA_COLUMNS)
        return normalize_frame(pd.concat(frames, ignore_index=True))


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=SCHEMA_COLUMNS)
    working = frame.copy()
    for column in SCHEMA_COLUMNS:
        if column not in working.columns:
            working[column] = None
    working["timestamp"] = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    working["price"] = pd.to_numeric(working["price"], errors="coerce")
    working["size"] = pd.to_numeric(working["size"], errors="coerce").fillna(0.0)
    working["side"] = working["side"].fillna("unknown").astype(str)
    working["payload"] = working["payload"].map(lambda value: value if isinstance(value, dict) else ({} if value is None else {"value": value}))
    working = working.dropna(subset=["timestamp", "price"])
    working = working.drop_duplicates(subset=["timestamp", "exchange", "symbol", "channel", "side", "price", "size"])
    working = working.sort_values("timestamp").reset_index(drop=True)
    return working[SCHEMA_COLUMNS]


class HistoricalDataDownloader:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.exchange = BybitExchange(settings)
        self.store = ParquetMarketStore(settings.app.data_dir)

    def download(self) -> list[Path]:
        outputs: list[Path] = []
        exchange = self.settings.app.exchange
        symbol = self.settings.app.symbol
        now_ms = int(time.time() * 1000)
        lookback_ms = self.settings.data.historical_lookback_days * 24 * 60 * 60 * 1000
        since = now_ms - lookback_ms

        trades = self.exchange.fetch_trades(since=since, limit=1000)
        trade_frame = pd.DataFrame([{
            "timestamp": pd.to_datetime(item["timestamp"], unit="ms", utc=True),
            "exchange": exchange,
            "symbol": symbol,
            "channel": "trades",
            "side": item.get("side", "unknown").lower(),
            "price": item.get("price"),
            "size": item.get("amount"),
            "payload": item,
        } for item in trades])
        if not trade_frame.empty:
            outputs.append(self.store.write(MarketDataBatch(channel="trades", frame=trade_frame), exchange, symbol))

        candles = self.exchange.fetch_ohlcv(timeframe="1m", since=since, limit=1000)
        candle_frame = pd.DataFrame([{
            "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
            "exchange": exchange,
            "symbol": symbol,
            "channel": "candles",
            "side": "mid",
            "price": item[4],
            "size": item[5],
            "payload": {"open": item[1], "high": item[2], "low": item[3], "close": item[4], "volume": item[5]},
        } for item in candles])
        if not candle_frame.empty:
            outputs.append(self.store.write(MarketDataBatch(channel="candles", frame=candle_frame), exchange, symbol))

        order_book = self.exchange.fetch_order_book(limit=50)
        order_rows: list[dict[str, Any]] = []
        snapshot_ts = utc_now()
        for side_name, levels in (("bid", order_book.get("bids", [])), ("ask", order_book.get("asks", []))):
            for price, size in levels:
                order_rows.append({
                    "timestamp": snapshot_ts,
                    "exchange": exchange,
                    "symbol": symbol,
                    "channel": "orderbook",
                    "side": side_name,
                    "price": price,
                    "size": size,
                    "payload": {"snapshot": True},
                })
        order_frame = pd.DataFrame(order_rows)
        if not order_frame.empty:
            outputs.append(self.store.write(MarketDataBatch(channel="orderbook", frame=order_frame), exchange, symbol))

        mark_price_frame = pd.DataFrame([{
            "timestamp": pd.to_datetime(int(item[0]), unit="ms", utc=True),
            "exchange": exchange,
            "symbol": symbol,
            "channel": "mark_price",
            "side": "mid",
            "price": float(item[4]),
            "size": 0.0,
            "payload": {"open": item[1], "high": item[2], "low": item[3], "close": item[4]},
        } for item in fetch_mark_price_history(symbol=symbol, testnet=self.settings.env.BYBIT_TESTNET)])
        if not mark_price_frame.empty:
            outputs.append(self.store.write(MarketDataBatch(channel="mark_price", frame=mark_price_frame), exchange, symbol))

        metadata = self.exchange.metadata()
        metadata_frame = pd.DataFrame([{
            "timestamp": utc_now(),
            "exchange": exchange,
            "symbol": symbol,
            "channel": "metadata",
            "side": "meta",
            "price": float(metadata.get("precision", {}).get("price", 0.0) or 0.0),
            "size": float(metadata.get("precision", {}).get("amount", 0.0) or 0.0),
            "payload": metadata,
        }])
        outputs.append(self.store.write(MarketDataBatch(channel="metadata", frame=metadata_frame), exchange, symbol))
        return outputs


class LiveDataRecorder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = ParquetMarketStore(settings.app.data_dir)
        self.buffer: list[dict[str, Any]] = []
        self.websocket = BybitWebsocketClient(settings, settings.data.channels, self.on_message)
        self.last_flush = time.time()

    def on_message(self, payload: dict[str, Any]) -> None:
        topic = payload.get("topic", "")
        data = payload.get("data", [])
        if topic.startswith("publicTrade"):
            for item in data:
                self.buffer.append({
                    "timestamp": pd.to_datetime(item["T"], unit="ms", utc=True),
                    "exchange": self.settings.app.exchange,
                    "symbol": self.settings.app.symbol,
                    "channel": "trades",
                    "side": str(item.get("S", "unknown")).lower(),
                    "price": float(item["p"]),
                    "size": float(item["v"]),
                    "payload": item,
                })
        elif topic.startswith("orderbook"):
            now = utc_now()
            for side_name, side_key in (("bid", "b"), ("ask", "a")):
                for level in data.get(side_key, []):
                    self.buffer.append({
                        "timestamp": now,
                        "exchange": self.settings.app.exchange,
                        "symbol": self.settings.app.symbol,
                        "channel": "orderbook",
                        "side": side_name,
                        "price": float(level[0]),
                        "size": float(level[1]),
                        "payload": data,
                    })
        elif topic.startswith("kline"):
            candle = data[0] if isinstance(data, list) and data else {}
            self.buffer.append({
                "timestamp": pd.to_datetime(candle.get("start", int(time.time() * 1000)), unit="ms", utc=True),
                "exchange": self.settings.app.exchange,
                "symbol": self.settings.app.symbol,
                "channel": "candles",
                "side": "mid",
                "price": float(candle.get("close", 0.0)),
                "size": float(candle.get("volume", 0.0)),
                "payload": candle,
            })
        elif topic.startswith("tickers"):
            self.buffer.append({
                "timestamp": utc_now(),
                "exchange": self.settings.app.exchange,
                "symbol": self.settings.app.symbol,
                "channel": "mark_price",
                "side": "mid",
                "price": float(data.get("markPrice", data.get("lastPrice", 0.0))),
                "size": 0.0,
                "payload": data,
            })

        if time.time() - self.last_flush >= self.settings.data.recorder_flush_seconds:
            self.flush()

    def flush(self) -> list[Path]:
        if not self.buffer:
            return []
        frame = pd.DataFrame(self.buffer)
        outputs: list[Path] = []
        for channel, channel_frame in frame.groupby("channel"):
            batch = MarketDataBatch(channel=channel, frame=channel_frame.reset_index(drop=True))
            outputs.append(self.store.write(batch, self.settings.app.exchange, self.settings.app.symbol))
        self.buffer.clear()
        self.last_flush = time.time()
        return outputs

    def run(self) -> None:
        self.websocket.start()
        LOGGER.info("recorder_started", extra={"event": "recorder_started"})
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            LOGGER.info("recorder_stopping", extra={"event": "recorder_stopping"})
        finally:
            self.websocket.stop()
            self.flush()


def prepare_backtest_features(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = normalize_frame(frame)
    if normalized.empty:
        return normalized
    features = normalized.copy()
    features["minute"] = features["timestamp"].map(lambda ts: floor_ts(ts.to_pydatetime()))
    features["signed_size"] = features.apply(
        lambda row: row["size"] if row["side"] in {"buy", "bid"} else -row["size"],
        axis=1,
    )
    features["mid_price"] = features["price"].rolling(2).mean().bfill()
    features["returns_bps"] = features["price"].pct_change().fillna(0.0) * 10_000
    features["trade_imbalance"] = features["signed_size"].rolling(50).sum().fillna(0.0)
    features["realized_vol_bps"] = features["returns_bps"].rolling(100).std().fillna(0.0)
    return features
