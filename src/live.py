from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from exchange import BybitExchange, OrderInstruction
from settings import Settings
from strategy import MarketState, SpreadCaptureStrategy, StrategyState
from utils import utc_now

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveRuntimeState:
    strategy_state: StrategyState = field(default_factory=StrategyState)
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    last_price: float = 0.0
    mark_price: float = 0.0
    recent_trade_prices: list[float] = field(default_factory=list)
    recent_trade_sizes: list[float] = field(default_factory=list)
    recent_trade_sides: list[str] = field(default_factory=list)
    recent_mid_prices: list[float] = field(default_factory=list)
    last_market_ts: Any = None
    last_heartbeat_ts: Any = None
    open_orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_error: str = ""
    kill_reason: str = ""


class LiveTrader:
    def __init__(self, settings: Settings, telegram_notifier: Any | None = None):
        self.settings = settings
        self.exchange = BybitExchange(settings)
        self.strategy = SpreadCaptureStrategy(settings)
        self.runtime = LiveRuntimeState()
        self._shutdown = threading.Event()
        self.telegram_notifier = telegram_notifier

    def register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, _frame: Any) -> None:
        LOGGER.info("shutdown_signal_received=%s", signum, extra={"event": "shutdown"})
        self._shutdown.set()

    def _notify(self, message: str) -> None:
        if self.telegram_notifier:
            self.telegram_notifier.send_message(message)

    def get_market_state(self) -> MarketState:
        return MarketState(
            timestamp=utc_now(),
            best_bid=self.runtime.best_bid,
            best_ask=self.runtime.best_ask,
            last_price=self.runtime.last_price,
            mark_price=self.runtime.mark_price or self.runtime.last_price,
            bid_size=self.runtime.bid_size,
            ask_size=self.runtime.ask_size,
            recent_trade_prices=self.runtime.recent_trade_prices[-200:],
            recent_trade_sizes=self.runtime.recent_trade_sizes[-200:],
            recent_trade_sides=self.runtime.recent_trade_sides[-200:],
            recent_mid_prices=self.runtime.recent_mid_prices[-200:],
        )

    def ingest_market_snapshot(self) -> None:
        order_book = self.exchange.fetch_order_book(limit=25)
        ticker = self.exchange.fetch_ticker()
        self.runtime.best_bid = float(order_book["bids"][0][0]) if order_book.get("bids") else float(ticker["last"])
        self.runtime.best_ask = float(order_book["asks"][0][0]) if order_book.get("asks") else float(ticker["last"])
        self.runtime.bid_size = float(order_book["bids"][0][1]) if order_book.get("bids") else 0.0
        self.runtime.ask_size = float(order_book["asks"][0][1]) if order_book.get("asks") else 0.0
        self.runtime.last_price = float(ticker["last"])
        self.runtime.mark_price = float(ticker.get("info", {}).get("markPrice", ticker["last"]))
        self.runtime.recent_mid_prices.append((self.runtime.best_bid + self.runtime.best_ask) / 2)
        trades = self.exchange.fetch_trades(limit=50)
        for trade in trades[-20:]:
            self.runtime.recent_trade_prices.append(float(trade["price"]))
            self.runtime.recent_trade_sizes.append(float(trade["amount"]))
            self.runtime.recent_trade_sides.append(str(trade.get("side", "buy")).lower())
        self.runtime.last_market_ts = utc_now()

    def _is_stale(self) -> bool:
        if self.runtime.last_market_ts is None:
            return True
        age = (utc_now() - self.runtime.last_market_ts).total_seconds()
        return age > self.settings.risk.max_data_staleness_seconds

    def risk_checks(self, market: MarketState) -> tuple[bool, str]:
        if self._is_stale():
            return False, "stale_data"
        if abs(self.runtime.strategy_state.inventory) >= self.settings.risk.hard_inventory_limit:
            return False, "hard_inventory_limit"
        if market.mid_price <= 0:
            return False, "invalid_market"
        if len(market.recent_mid_prices) >= 5:
            recent = market.recent_mid_prices[-5:]
            realized_move_bps = abs(recent[-1] - recent[0]) / recent[0] * 10_000
            if realized_move_bps > self.settings.risk.max_volatility_bps:
                return False, "extreme_volatility"
        return True, "ok"

    def cancel_all(self) -> None:
        try:
            self.exchange.cancel_all_orders()
        finally:
            self.runtime.open_orders.clear()

    def kill_switch(self, reason: str) -> None:
        self.runtime.strategy_state.is_kill_switch_active = True
        self.runtime.kill_reason = reason
        self.cancel_all()
        self._notify(f"[spreadbot] kill switch: {reason}")

    def reconcile_orders(self, plan: Any) -> None:
        for order_id, order in list(self.runtime.open_orders.items()):
            should_cancel = order["side"] == "buy" and plan.should_cancel_bid or order["side"] == "sell" and plan.should_cancel_ask
            if should_cancel:
                self.exchange.cancel_order(order_id)
                self.runtime.open_orders.pop(order_id, None)
                self._notify(f"[spreadbot] canceled {order['side']} {order['price']}")

        if not plan.eligible:
            return
        if not any(order["side"] == "buy" for order in self.runtime.open_orders.values()) and plan.bid_size > 0:
            placed = self.exchange.create_limit_order(OrderInstruction(side="buy", price=plan.bid_price, size=plan.bid_size, post_only=True))
            self.runtime.strategy_state.bid_quote_ts = utc_now()
            self.runtime.strategy_state.open_bid_id = placed["id"]
            self.runtime.open_orders[placed["id"]] = placed | {"side": "buy", "price": plan.bid_price, "size": plan.bid_size}
        if not any(order["side"] == "sell" for order in self.runtime.open_orders.values()) and plan.ask_size > 0:
            placed = self.exchange.create_limit_order(OrderInstruction(side="sell", price=plan.ask_price, size=plan.ask_size, post_only=True))
            self.runtime.strategy_state.ask_quote_ts = utc_now()
            self.runtime.strategy_state.open_ask_id = placed["id"]
            self.runtime.open_orders[placed["id"]] = placed | {"side": "sell", "price": plan.ask_price, "size": plan.ask_size}

    def refresh_position(self) -> None:
        snapshot = self.exchange.get_position_snapshot()
        contracts = float(snapshot.get("contracts", snapshot.get("contractsSize", 0.0)) or 0.0)
        self.runtime.strategy_state.inventory = contracts
        self.runtime.strategy_state.realized_pnl = float(snapshot.get("unrealizedPnl", 0.0) or 0.0)
        entry = snapshot.get("entryPrice")
        if entry is not None:
            self.runtime.strategy_state.avg_entry_price = float(entry)

    def status_snapshot(self) -> dict[str, Any]:
        market = self.get_market_state()
        return {
            "safe_mode": self.settings.is_safe_mode,
            "real_trading_enabled": self.settings.real_trading_enabled,
            "inventory": self.runtime.strategy_state.inventory,
            "avg_entry_price": self.runtime.strategy_state.avg_entry_price,
            "realized_pnl": self.runtime.strategy_state.realized_pnl,
            "best_bid": market.best_bid,
            "best_ask": market.best_ask,
            "mid": market.mid_price,
            "open_orders": len(self.runtime.open_orders),
            "kill_switch": self.runtime.strategy_state.is_kill_switch_active,
            "kill_reason": self.runtime.kill_reason,
            "last_error": self.runtime.last_error,
        }

    def run_once(self) -> dict[str, Any]:
        self.ingest_market_snapshot()
        self.refresh_position()
        market = self.get_market_state()
        allowed, reason = self.risk_checks(market)
        if not allowed:
            if reason in {"hard_inventory_limit", "extreme_volatility"}:
                self.kill_switch(reason)
            return {"action": "skip", "reason": reason}

        plan = self.strategy.build_quote_plan(market, self.runtime.strategy_state)
        if plan.emergency_action:
            self.kill_switch(plan.emergency_action)
            return {"action": "kill", "reason": plan.emergency_action}
        self.reconcile_orders(plan)
        return {
            "action": "quote" if plan.eligible else "idle",
            "reason": plan.reason,
            "plan": asdict(plan),
        }

    def run(self) -> None:
        self.register_signal_handlers()
        self._notify(f"[spreadbot] live runtime started safe_mode={self.settings.is_safe_mode}")
        LOGGER.info("live_runtime_started", extra={"event": "live_start", "mode": "dry" if self.settings.is_safe_mode else "live"})
        try:
            while not self._shutdown.is_set():
                try:
                    result = self.run_once()
                    LOGGER.info("runtime_loop %s", result, extra={"event": "runtime_loop"})
                except Exception as exc:
                    self.runtime.last_error = str(exc)
                    LOGGER.exception("runtime_loop_failed")
                    self._notify(f"[spreadbot] runtime error: {exc}")
                    time.sleep(3)

                if self.runtime.last_heartbeat_ts is None or utc_now() - self.runtime.last_heartbeat_ts > timedelta(seconds=self.settings.live.heartbeat_seconds):
                    self.runtime.last_heartbeat_ts = utc_now()
                    self._notify(f"[spreadbot] heartbeat {self.status_snapshot()}")
                time.sleep(self.settings.live.loop_interval_ms / 1000)
        finally:
            self.cancel_all()
            self._notify("[spreadbot] live runtime stopped")
