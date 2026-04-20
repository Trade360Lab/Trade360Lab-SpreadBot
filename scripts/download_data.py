from logger import configure_logging
from market_data import HistoricalDataDownloader
from settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    outputs = HistoricalDataDownloader(settings).download()
    print("\n".join(str(path) for path in outputs))


if __name__ == "__main__":
    main()
