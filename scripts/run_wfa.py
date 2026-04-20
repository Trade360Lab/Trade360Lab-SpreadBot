from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logger import configure_logging
from market_data import ParquetMarketStore
from optimizer import RollingWalkForwardAnalyzer
from settings import get_settings


def main() -> None:
    settings = get_settings("./configs/optimization.yaml")
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    frame = ParquetMarketStore(settings.app.data_dir).load(settings.app.exchange, settings.app.symbol)
    summary = RollingWalkForwardAnalyzer(settings).run(frame, output_dir="./reports/wfa")
    print(summary)


if __name__ == "__main__":
    main()
