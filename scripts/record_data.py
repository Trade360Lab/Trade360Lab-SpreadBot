from logger import configure_logging
from market_data import LiveDataRecorder
from settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    LiveDataRecorder(settings).run()


if __name__ == "__main__":
    main()
