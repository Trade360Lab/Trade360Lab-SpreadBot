from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from market_data import prepare_backtest_features
from settings import Settings
from strategy import MarketState, QuotePlan, SpreadCaptureStrategy, StrategyState
from utils import bps_to_decimal, ensure_dir, save_json


@dataclass(slots=True)
class SimOrder:
    side: str
    price: float
    size: float
    placed_at: pd.Timestamp
    is_active: bool = True
    quote_reference: float = 0.0


@dataclass(slots=True)
class FillRecord:
    timestamp: pd.Timestamp
    side: str
    price: float
    size: float
    fee: float
    liquidity: str
    inventory_after: float
    realized_pnl: float
    adverse_selection_bps: float


@dataclass(slots=True)
class BacktestResult:
    summary: dict[str, Any]
    fills: list[FillRecord] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    orders: list[dict[str, Any]] = field(default_factory=list)


class EventDrivenBacktester:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.strategy = SpreadCaptureStrategy(settings)

    def _build_market_state(self, window: pd.DataFrame, current_idx: int) -> MarketState:
        current = window.iloc[current_idx]
        recent = window.iloc[max(0, current_idx - 200): current_idx + 1]
        orderbook = recent[recent["channel"] == "orderbook"]
        trades = recent[recent["channel"] == "trades"]
        best_bid = orderbook[orderbook["side"] == "bid"]["price"].max() if not orderbook.empty else current["price"]
        best_ask = orderbook[orderbook["side"] == "ask"]["price"].min() if not orderbook.empty else current["price"]
        bid_size = orderbook[orderbook["side"] == "bid"]["size"].sum()
        ask_size = orderbook[orderbook["side"] == "ask"]["size"].sum()
        mark_candidates = recent[recent["channel"] == "mark_price"]["price"]
        mark_price = mark_candidates.iloc[-1] if not mark_candidates.empty else current["price"]
        trade_prices = trades["price"].astype(float).tolist()
        trade_sizes = trades["size"].astype(float).tolist()
        trade_sides = trades["side"].astype(str).tolist()
        mids = recent["mid_price"].astype(float).tolist() if "mid_price" in recent.columns else recent["price"].astype(float).tolist()
        return MarketState(
            timestamp=current["timestamp"].to_pydatetime(),
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            last_price=float(current["price"]),
            mark_price=float(mark_price),
            bid_size=float(bid_size),
            ask_size=float(ask_size),
            recent_trade_prices=trade_prices,
            recent_trade_sizes=trade_sizes,
            recent_trade_sides=trade_sides,
            recent_mid_prices=mids,
        )

    def _fee(self, price: float, size: float, liquidity: str) -> float:
        fee_bps = self.settings.fees.maker_fee_bps if liquidity == "maker" else self.settings.fees.taker_fee_bps
        return price * size * bps_to_decimal(fee_bps)

    def _place_orders(self, plan: QuotePlan, timestamp: pd.Timestamp) -> list[SimOrder]:
        if not plan.eligible:
            return []
        return [
            SimOrder(side="buy", price=plan.bid_price, size=plan.bid_size, placed_at=timestamp, quote_reference=plan.fair_value),
            SimOrder(side="sell", price=plan.ask_price, size=plan.ask_size, placed_at=timestamp, quote_reference=plan.fair_value),
        ]

    def _update_active_orders(
        self,
        orders: list[SimOrder],
        plan: QuotePlan,
        current_ts: pd.Timestamp,
    ) -> list[SimOrder]:
        updated: list[SimOrder] = []
        for order in orders:
            age_ms = (current_ts - order.placed_at).total_seconds() * 1000
            if age_ms < self.settings.backtest.latency_ms:
                updated.append(order)
                continue
            if order.side == "buy" and plan.should_cancel_bid:
                order.is_active = False
            elif order.side == "sell" and plan.should_cancel_ask:
                order.is_active = False
            if order.is_active:
                updated.append(order)
        return updated

    def _maybe_fill(
        self,
        order: SimOrder,
        row: pd.Series,
        next_price: float,
        state: StrategyState,
    ) -> FillRecord | None:
        if not order.is_active or order.size <= 0:
            return None

        price = float(row["price"])
        fill_hit = (order.side == "buy" and price <= order.price) or (order.side == "sell" and price >= order.price)
        queue_factor = min(1.0, float(row.get("size", 0.0)) / max(order.size * self.settings.backtest.queue_ahead_size, 1e-9))
        fill_probability = self.settings.backtest.fill_probability * queue_factor
        if not fill_hit or fill_probability <= 0.1:
            return None

        state_before = state.realized_pnl
        self.strategy.apply_fill(state, order.side, order.price, order.size)
        fee = self._fee(order.price, order.size, liquidity="maker")
        state.realized_pnl -= fee
        order.is_active = False
        adverse_selection = ((next_price - order.price) / order.price) * 10_000
        if order.side == "sell":
            adverse_selection *= -1

        return FillRecord(
            timestamp=row["timestamp"],
            side=order.side,
            price=order.price,
            size=order.size,
            fee=fee,
            liquidity="maker",
            inventory_after=state.inventory,
            realized_pnl=state.realized_pnl - state_before,
            adverse_selection_bps=adverse_selection,
        )

    def run(self, data: pd.DataFrame) -> BacktestResult:
        frame = prepare_backtest_features(data)
        if frame.empty:
            return BacktestResult(summary={"error": "no_data"})

        state = StrategyState()
        active_orders: list[SimOrder] = []
        fills: list[FillRecord] = []
        orders_audit: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        peak_equity = self.settings.backtest.initial_cash
        max_drawdown = 0.0

        for idx in range(len(frame) - 1):
            market = self._build_market_state(frame, idx)
            plan = self.strategy.build_quote_plan(market, state)
            current_ts = frame.iloc[idx]["timestamp"]
            active_orders = self._update_active_orders(active_orders, plan, current_ts)
            if not active_orders and plan.eligible:
                active_orders.extend(self._place_orders(plan, current_ts))
                orders_audit.append({
                    "timestamp": str(current_ts),
                    "fair_value": plan.fair_value,
                    "bid_price": plan.bid_price,
                    "ask_price": plan.ask_price,
                    "reason": plan.reason,
                    "half_spread_bps": plan.half_spread_bps,
                })

            current_row = frame.iloc[idx]
            next_price = float(frame.iloc[idx + 1]["price"])
            for order in list(active_orders):
                fill = self._maybe_fill(order, current_row, next_price, state)
                if fill:
                    fills.append(fill)
            active_orders = [order for order in active_orders if order.is_active]

            unrealized_pnl = (market.mid_price - state.avg_entry_price) * state.inventory if state.avg_entry_price else 0.0
            equity = self.settings.backtest.initial_cash + state.realized_pnl + unrealized_pnl
            peak_equity = max(peak_equity, equity)
            drawdown = peak_equity - equity
            max_drawdown = max(max_drawdown, drawdown)
            equity_rows.append({
                "timestamp": current_ts,
                "inventory": state.inventory,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "equity": equity,
                "drawdown": drawdown,
                "volatility_bps": plan.alpha.realized_vol_bps,
                "toxicity": plan.alpha.toxicity,
            })

        equity_curve = pd.DataFrame(equity_rows)
        maker_fees = sum(fill.fee for fill in fills if fill.liquidity == "maker")
        gross_pnl = state.realized_pnl + (equity_curve["unrealized_pnl"].iloc[-1] if not equity_curve.empty else 0.0) + maker_fees
        net_pnl = state.realized_pnl + (equity_curve["unrealized_pnl"].iloc[-1] if not equity_curve.empty else 0.0)
        maker_ratio = 1.0 if fills else 0.0
        fill_ratio = len(fills) / max(len(orders_audit) * 2, 1)
        pnl_by_hour = {}
        regime_split = {"normal": 0.0, "volatile": 0.0}
        if fills:
            fills_frame = pd.DataFrame([asdict(fill) for fill in fills])
            fills_frame["hour"] = pd.to_datetime(fills_frame["timestamp"], utc=True).dt.hour
            pnl_by_hour = fills_frame.groupby("hour")["realized_pnl"].sum().round(6).to_dict()
            for _, row in equity_curve.iterrows():
                regime = "volatile" if row["volatility_bps"] > self.settings.risk.max_volatility_bps / 2 else "normal"
                regime_split[regime] += row["realized_pnl"] + row["unrealized_pnl"]

        summary = {
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "fees": maker_fees,
            "fills": len(fills),
            "pnl_per_fill": net_pnl / max(len(fills), 1),
            "maker_ratio": maker_ratio,
            "taker_ratio": 0.0,
            "fill_ratio": fill_ratio,
            "max_inventory": float(equity_curve["inventory"].abs().max()) if not equity_curve.empty else 0.0,
            "inventory_variance": float(equity_curve["inventory"].var()) if len(equity_curve) > 1 else 0.0,
            "max_drawdown": max_drawdown,
            "pnl_by_regime": regime_split,
            "pnl_by_hour": pnl_by_hour,
            "adverse_selection_mean_bps": sum(fill.adverse_selection_bps for fill in fills) / max(len(fills), 1),
            "adverse_selection_worst_bps": min((fill.adverse_selection_bps for fill in fills), default=0.0),
        }
        return BacktestResult(summary=summary, fills=fills, equity_curve=equity_curve, orders=orders_audit)

    def save_reports(self, result: BacktestResult, report_dir: str | Path, run_name: str = "run") -> dict[str, Path]:
        target_dir = ensure_dir(report_dir)
        summary_path = target_dir / f"{run_name}_summary.json"
        fills_path = target_dir / f"{run_name}_fills.csv"
        report_path = target_dir / f"{run_name}_report.md"
        equity_path = target_dir / f"{run_name}_equity.csv"

        save_json(summary_path, result.summary)
        result.equity_curve.to_csv(equity_path, index=False)

        with fills_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(result.fills[0]).keys()) if result.fills else [
                "timestamp", "side", "price", "size", "fee", "liquidity", "inventory_after", "realized_pnl", "adverse_selection_bps"
            ])
            writer.writeheader()
            for fill in result.fills:
                writer.writerow(asdict(fill))

        report_lines = [
            "# Backtest Report",
            "",
            f"- Gross PnL: {result.summary.get('gross_pnl', 0.0):.4f}",
            f"- Net PnL: {result.summary.get('net_pnl', 0.0):.4f}",
            f"- Fees: {result.summary.get('fees', 0.0):.4f}",
            f"- Fills: {result.summary.get('fills', 0)}",
            f"- Maker Ratio: {result.summary.get('maker_ratio', 0.0):.2%}",
            f"- Fill Ratio: {result.summary.get('fill_ratio', 0.0):.2%}",
            f"- Max Drawdown: {result.summary.get('max_drawdown', 0.0):.4f}",
            f"- Max Inventory: {result.summary.get('max_inventory', 0.0):.6f}",
            f"- Adverse Selection Mean (bps): {result.summary.get('adverse_selection_mean_bps', 0.0):.4f}",
        ]
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        return {
            "summary": summary_path,
            "fills": fills_path,
            "report": report_path,
            "equity": equity_path,
        }
