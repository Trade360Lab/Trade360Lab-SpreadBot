from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logger import configure_logging
from backtest import EventDrivenBacktester
from market_data import ParquetMarketStore
from settings import get_settings


def main() -> None:
    settings = get_settings("./configs/backtest.yaml")
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    store = ParquetMarketStore(settings.app.data_dir)
    frame = store.load(settings.app.exchange, settings.app.symbol)
    backtester = EventDrivenBacktester(settings)
    result = backtester.run(frame)
    outputs = backtester.save_reports(result, settings.backtest.report_dir, run_name="backtest")
    print(result.summary)
    print(outputs)


if __name__ == "__main__":
    main()
