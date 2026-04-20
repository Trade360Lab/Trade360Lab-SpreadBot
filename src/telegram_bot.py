from __future__ import annotations

import logging
import time
from typing import Any

import requests

from live import LiveTrader
from settings import Settings

LOGGER = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = f"https://api.telegram.org/bot{settings.env.TELEGRAM_BOT_TOKEN}"

    def enabled(self) -> bool:
        return bool(self.settings.app.enable_telegram and self.settings.env.TELEGRAM_BOT_TOKEN and self.settings.env.TELEGRAM_CHAT_ID)

    def send_message(self, text: str) -> None:
        if not self.enabled():
            return
        try:
            requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.settings.env.TELEGRAM_CHAT_ID, "text": text},
                timeout=10,
            ).raise_for_status()
        except Exception:
            LOGGER.exception("telegram_send_failed")


class TelegramCommandBot:
    def __init__(self, settings: Settings, trader: LiveTrader):
        self.settings = settings
        self.trader = trader
        self.notifier = TelegramNotifier(settings)
        self.offset = 0

    def _get_updates(self) -> list[dict[str, Any]]:
        if not self.notifier.enabled():
            return []
        response = requests.get(
            f"{self.notifier.base_url}/getUpdates",
            params={"timeout": 25, "offset": self.offset},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result", [])

    def _handle_command(self, command: str) -> str:
        status = self.trader.status_snapshot()
        if command == "/status":
            return str(status)
        if command == "/position":
            return str({"inventory": status["inventory"], "avg_entry_price": status["avg_entry_price"]})
        if command == "/pnl":
            return str({"realized_pnl": status["realized_pnl"]})
        if command == "/orders":
            return str(self.trader.runtime.open_orders)
        if command == "/risk":
            return str({"kill_switch": status["kill_switch"], "kill_reason": status["kill_reason"]})
        if command == "/lastfills":
            state = self.trader.runtime.strategy_state
            return str({"side": state.latest_fill_side, "price": state.latest_fill_price, "ts": state.latest_fill_ts})
        if command == "/health":
            return str({"last_error": status["last_error"], "safe_mode": status["safe_mode"]})
        if command in {"/starttrading", "/stoptrading", "/flatten"}:
            return "trade control disabled" if not self.settings.app.enable_telegram_trade_control else "unsupported in baseline"
        return "unknown command"

    def run(self) -> None:
        self.notifier.send_message("[spreadbot] telegram bot started")
        while True:
            try:
                for update in self._get_updates():
                    self.offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    if text.startswith("/"):
                        reply = self._handle_command(text.strip())
                        self.notifier.send_message(reply)
            except Exception:
                LOGGER.exception("telegram_bot_loop_failed")
                time.sleep(5)
