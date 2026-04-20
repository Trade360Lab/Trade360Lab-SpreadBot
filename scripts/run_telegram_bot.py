from logger import configure_logging
from live import LiveTrader
from settings import get_settings
from telegram_bot import TelegramCommandBot, TelegramNotifier


def main() -> None:
    settings = get_settings("./configs/live.yaml")
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    trader = LiveTrader(settings, telegram_notifier=TelegramNotifier(settings))
    TelegramCommandBot(settings, trader).run()


if __name__ == "__main__":
    main()
