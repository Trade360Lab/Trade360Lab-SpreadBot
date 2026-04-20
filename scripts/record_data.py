from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logger import configure_logging
from market_data import LiveDataRecorder
from settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    LiveDataRecorder(settings).run()


if __name__ == "__main__":
    main()
