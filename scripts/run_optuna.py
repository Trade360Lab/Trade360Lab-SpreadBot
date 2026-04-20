from logger import configure_logging
from market_data import ParquetMarketStore
from optimizer import ParameterOptimizer
from settings import get_settings
from utils import save_json


def main() -> None:
    settings = get_settings("./configs/optimization.yaml")
    configure_logging(settings.env.LOG_LEVEL, settings.app.log_dir, settings.app.name)
    frame = ParquetMarketStore(settings.app.data_dir).load(settings.app.exchange, settings.app.symbol)
    result = ParameterOptimizer(settings).optimize(frame)
    save_json("./reports/optuna/best_params.json", result)
    print(result)


if __name__ == "__main__":
    main()
