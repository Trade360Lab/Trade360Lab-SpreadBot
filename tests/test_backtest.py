from datetime import UTC, datetime, timedelta

import pandas as pd

from backtest import EventDrivenBacktester
from settings import load_settings


def test_backtest_smoke_runs_and_returns_summary() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for idx in range(200):
        ts = start + timedelta(seconds=idx)
        base = 100.0 + idx * 0.01
        rows.extend([
            {"timestamp": ts, "exchange": "bybit", "symbol": "BTCUSDT", "channel": "orderbook", "side": "bid", "price": base - 0.05, "size": 2.0, "payload": {}},
            {"timestamp": ts, "exchange": "bybit", "symbol": "BTCUSDT", "channel": "orderbook", "side": "ask", "price": base + 0.05, "size": 2.0, "payload": {}},
            {"timestamp": ts, "exchange": "bybit", "symbol": "BTCUSDT", "channel": "trades", "side": "buy" if idx % 2 == 0 else "sell", "price": base, "size": 1.0 + idx * 0.01, "payload": {}},
            {"timestamp": ts, "exchange": "bybit", "symbol": "BTCUSDT", "channel": "mark_price", "side": "mid", "price": base, "size": 0.0, "payload": {}},
        ])
    frame = pd.DataFrame(rows)
    result = EventDrivenBacktester(load_settings("./configs/backtest.yaml")).run(frame)

    assert "net_pnl" in result.summary
    assert "max_drawdown" in result.summary
    assert result.equity_curve.empty is False
