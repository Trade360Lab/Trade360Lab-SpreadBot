from datetime import UTC, datetime

from settings import load_settings
from strategy import MarketState, SpreadCaptureStrategy, StrategyState


def _market_state() -> MarketState:
    return MarketState(
        timestamp=datetime.now(tz=UTC),
        best_bid=100.0,
        best_ask=100.1,
        last_price=100.05,
        mark_price=100.05,
        bid_size=10.0,
        ask_size=8.0,
        recent_trade_prices=[100.0, 100.02, 100.03, 100.05],
        recent_trade_sizes=[1.0, 1.2, 0.8, 1.5],
        recent_trade_sides=["buy", "buy", "sell", "buy"],
        recent_mid_prices=[100.0, 100.01, 100.02, 100.05],
    )


def test_signal_calculation_and_quote_math() -> None:
    settings = load_settings("./configs/base.yaml")
    strategy = SpreadCaptureStrategy(settings)
    market = _market_state()
    state = StrategyState(inventory=0.0)

    plan = strategy.build_quote_plan(market, state)

    assert plan.fair_value > 0
    assert plan.bid_price < plan.ask_price
    assert plan.bid_size > 0
    assert plan.ask_size > 0
    assert abs(plan.alpha.short_term_alpha) <= settings.strategy.alpha_clip


def test_inventory_skew_widens_quotes_against_inventory() -> None:
    settings = load_settings("./configs/base.yaml")
    strategy = SpreadCaptureStrategy(settings)
    market = _market_state()

    neutral = strategy.build_quote_plan(market, StrategyState(inventory=0.0))
    long_inventory = strategy.build_quote_plan(market, StrategyState(inventory=0.015))

    assert long_inventory.bid_price < neutral.bid_price
    assert long_inventory.ask_price < neutral.ask_price
