from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logger import configure_logging
from live import LiveTrader
from settings import get_settings
from telegram_bot import TelegramNotifier


def main() -> None:
    settings = get_settings("./configs/live.yaml")
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    notifier = TelegramNotifier(settings) if settings.app.enable_telegram else None
    LiveTrader(settings, telegram_notifier=notifier).run()


if __name__ == "__main__":
    main()
