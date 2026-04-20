from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import ccxt
import requests
from websocket import WebSocketApp

from settings import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OrderInstruction:
    side: str
    price: float
    size: float
    post_only: bool = True
    reduce_only: bool = False
    client_order_id: str | None = None


class ExchangeError(RuntimeError):
    pass


class BybitExchange:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.exchange = ccxt.bybit({
            "apiKey": settings.env.BYBIT_API_KEY,
            "secret": settings.env.BYBIT_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": settings.app.market_type,
            },
        })
        if settings.env.BYBIT_TESTNET:
            self.exchange.set_sandbox_mode(True)
        self.symbol = settings.app.symbol

    def load_markets(self) -> dict[str, Any]:
        return self.exchange.load_markets()

    def fetch_order_book(self, limit: int = 50) -> dict[str, Any]:
        return self.exchange.fetch_order_book(self.symbol, limit=limit)

    def fetch_trades(self, since: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.exchange.fetch_trades(self.symbol, since=since, limit=limit)

    def fetch_ohlcv(self, timeframe: str = "1m", since: int | None = None, limit: int = 500) -> list[list[Any]]:
        return self.exchange.fetch_ohlcv(self.symbol, timeframe=timeframe, since=since, limit=limit)

    def fetch_funding_rate(self) -> dict[str, Any]:
        try:
            return self.exchange.fetch_funding_rate(self.symbol)
        except Exception as exc:
            raise ExchangeError("failed to fetch funding rate") from exc

    def fetch_ticker(self) -> dict[str, Any]:
        return self.exchange.fetch_ticker(self.symbol)

    def fetch_balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def fetch_positions(self) -> list[dict[str, Any]]:
        try:
            return self.exchange.fetch_positions([self.symbol])
        except Exception:
            return []

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(self.symbol)

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        try:
            return self.exchange.cancel_all_orders(self.symbol)
        except Exception as exc:
            LOGGER.exception("cancel_all_orders_failed")
            raise ExchangeError("failed to cancel all orders") from exc

    def create_limit_order(self, instruction: OrderInstruction) -> dict[str, Any]:
        params = {
            "timeInForce": "PostOnly" if instruction.post_only else "GTC",
            "reduceOnly": instruction.reduce_only,
        }
        if instruction.client_order_id:
            params["orderLinkId"] = instruction.client_order_id
        if self.settings.is_safe_mode:
            return {
                "id": instruction.client_order_id or f"dry-{int(time.time() * 1000)}",
                "symbol": self.symbol,
                "side": instruction.side,
                "price": instruction.price,
                "amount": instruction.size,
                "status": "open",
                "info": {"dry_run": True},
            }
        try:
            return self.exchange.create_order(
                symbol=self.symbol,
                type="limit",
                side=instruction.side.lower(),
                amount=instruction.size,
                price=instruction.price,
                params=params,
            )
        except Exception as exc:
            raise ExchangeError(f"failed to place {instruction.side} order") from exc

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.settings.is_safe_mode:
            return {"id": order_id, "status": "canceled", "info": {"dry_run": True}}
        try:
            return self.exchange.cancel_order(order_id, self.symbol)
        except Exception as exc:
            raise ExchangeError(f"failed to cancel order {order_id}") from exc

    def get_position_snapshot(self) -> dict[str, Any]:
        positions = self.fetch_positions()
        for position in positions:
            if position.get("symbol") == self.symbol:
                return position
        return {"symbol": self.symbol, "contracts": 0.0, "unrealizedPnl": 0.0}

    def metadata(self) -> dict[str, Any]:
        markets = self.load_markets()
        return markets.get(self.symbol, {})


class BybitWebsocketClient:
    PUBLIC_ENDPOINT = "wss://stream.bybit.com/v5/public/linear"
    TESTNET_ENDPOINT = "wss://stream-testnet.bybit.com/v5/public/linear"

    def __init__(self, settings: Settings, channels: list[str], on_message: Callable[[dict[str, Any]], None]):
        self.settings = settings
        self.channels = channels
        self.on_message = on_message
        self.ws_app: WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _build_subscription(self) -> list[str]:
        symbol = self.settings.app.symbol
        mapping = {
            "trades": f"publicTrade.{symbol}",
            "orderbook": f"orderbook.50.{symbol}",
            "candles": f"kline.1.{symbol}",
            "mark_price": f"tickers.{symbol}",
        }
        return [mapping[channel] for channel in self.channels if channel in mapping]

    def _on_open(self, ws: WebSocketApp) -> None:
        payload = {"op": "subscribe", "args": self._build_subscription()}
        ws.send(json.dumps(payload))
        LOGGER.info("ws_subscribed", extra={"event": "ws_subscribed"})

    def _on_message(self, _ws: WebSocketApp, message: str) -> None:
        try:
            parsed = json.loads(message)
            self.on_message(parsed)
        except Exception:
            LOGGER.exception("ws_message_parse_failed")

    def _on_error(self, _ws: WebSocketApp, error: Any) -> None:
        LOGGER.error("ws_error: %s", error, extra={"event": "ws_error"})

    def _on_close(self, _ws: WebSocketApp, status_code: int, message: str) -> None:
        LOGGER.warning("ws_closed code=%s message=%s", status_code, message, extra={"event": "ws_closed"})

    def _run(self) -> None:
        endpoint = self.TESTNET_ENDPOINT if self.settings.env.BYBIT_TESTNET else self.PUBLIC_ENDPOINT
        while not self._stop_event.is_set():
            self.ws_app = WebSocketApp(
                endpoint,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self.ws_app.run_forever(ping_interval=20, ping_timeout=10)
            if not self._stop_event.is_set():
                time.sleep(3)

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.ws_app:
            self.ws_app.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)


def fetch_mark_price_history(symbol: str, testnet: bool = True, limit: int = 200) -> list[dict[str, Any]]:
    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    endpoint = "/v5/market/mark-price-kline"
    query = urlencode({"category": "linear", "symbol": symbol, "interval": "1", "limit": limit})
    response = requests.get(f"{base_url}{endpoint}?{query}", timeout=15)
    response.raise_for_status()
    payload = response.json()
    return payload.get("result", {}).get("list", [])
