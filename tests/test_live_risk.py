from datetime import timedelta

from live import LiveTrader
from settings import load_settings
from strategy import MarketState
from utils import utc_now


def test_live_risk_checks_block_stale_data(monkeypatch) -> None:
    settings = load_settings("./configs/live.yaml")
    trader = LiveTrader(settings)
    trader.runtime.last_market_ts = utc_now() - timedelta(seconds=settings.risk.max_data_staleness_seconds + 1)

    market = MarketState(
        timestamp=utc_now(),
        best_bid=100.0,
        best_ask=100.1,
        last_price=100.05,
        mark_price=100.05,
        bid_size=1.0,
        ask_size=1.0,
        recent_mid_prices=[100.0, 100.02, 100.03],
    )

    allowed, reason = trader.risk_checks(market)
    assert allowed is False
    assert reason == "stale_data"


def test_live_risk_checks_block_extreme_volatility() -> None:
    settings = load_settings("./configs/live.yaml")
    trader = LiveTrader(settings)
    trader.runtime.last_market_ts = utc_now()
    market = MarketState(
        timestamp=utc_now(),
        best_bid=100.0,
        best_ask=100.1,
        last_price=100.05,
        mark_price=100.05,
        bid_size=1.0,
        ask_size=1.0,
        recent_mid_prices=[100.0, 100.5, 101.0, 102.0, 103.0],
    )

    allowed, reason = trader.risk_checks(market)
    assert allowed is False
    assert reason == "extreme_volatility"
