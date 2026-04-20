from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from settings import Settings, StrategyConfig
from utils import bps_to_decimal, clamp, rolling_mean, safe_div, stddev, utc_now


@dataclass(slots=True)
class MarketState:
    timestamp: datetime
    best_bid: float
    best_ask: float
    last_price: float
    mark_price: float
    bid_size: float
    ask_size: float
    recent_trade_prices: list[float] = field(default_factory=list)
    recent_trade_sizes: list[float] = field(default_factory=list)
    recent_trade_sides: list[str] = field(default_factory=list)
    recent_mid_prices: list[float] = field(default_factory=list)
    funding_rate: float = 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return self.last_price or self.mark_price

    @property
    def spread_bps(self) -> float:
        return safe_div(self.best_ask - self.best_bid, self.mid_price) * 10_000


@dataclass(slots=True)
class StrategyState:
    inventory: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    open_bid_id: str | None = None
    open_ask_id: str | None = None
    bid_quote_ts: datetime | None = None
    ask_quote_ts: datetime | None = None
    latest_fill_price: float = 0.0
    latest_fill_side: str = ""
    latest_fill_ts: datetime | None = None
    is_kill_switch_active: bool = False


@dataclass(slots=True)
class AlphaSignals:
    imbalance: float
    trade_flow: float
    short_term_alpha: float
    toxicity: float
    realized_vol_bps: float


@dataclass(slots=True)
class QuotePlan:
    eligible: bool
    reason: str
    fair_value: float
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    alpha: AlphaSignals
    inventory_skew_bps: float
    half_spread_bps: float
    should_cancel_bid: bool
    should_cancel_ask: bool
    emergency_action: str | None = None


class SpreadCaptureStrategy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.config: StrategyConfig = settings.strategy

    # Signals
    def compute_signals(self, market: MarketState) -> AlphaSignals:
        depth_total = market.bid_size + market.ask_size
        imbalance = safe_div(market.bid_size - market.ask_size, depth_total)

        signed_flow = 0.0
        for side, size in zip(market.recent_trade_sides[-self.config.trade_flow_window:], market.recent_trade_sizes[-self.config.trade_flow_window:]):
            signed_flow += size if side.lower() == "buy" else -size
        trade_flow = safe_div(signed_flow, sum(market.recent_trade_sizes[-self.config.trade_flow_window:]) or 1.0)

        mid_series = market.recent_mid_prices[-self.config.volatility_window:] or [market.mid_price]
        realized_vol_bps = stddev([
            safe_div(curr - prev, prev) * 10_000
            for prev, curr in zip(mid_series[:-1], mid_series[1:])
            if prev > 0
        ])

        last_prices = market.recent_trade_prices[-self.config.imbalance_window:] or [market.last_price]
        micro_trend = safe_div(last_prices[-1] - rolling_mean(last_prices), market.mid_price) * 10_000
        short_term_alpha = clamp(0.5 * imbalance + 0.35 * trade_flow + 0.15 * micro_trend, -self.config.alpha_clip, self.config.alpha_clip)
        toxicity = min(1.0, abs(trade_flow) + realized_vol_bps / max(self.settings.risk.max_volatility_bps, 1.0))

        return AlphaSignals(
            imbalance=imbalance,
            trade_flow=trade_flow,
            short_term_alpha=short_term_alpha,
            toxicity=toxicity,
            realized_vol_bps=realized_vol_bps,
        )

    # Fair value
    def compute_fair_value(self, market: MarketState, alpha: AlphaSignals) -> float:
        mid = market.mid_price
        alpha_shift = mid * bps_to_decimal(alpha.short_term_alpha)
        microprice = safe_div(
            market.best_ask * market.bid_size + market.best_bid * market.ask_size,
            market.bid_size + market.ask_size,
            default=mid,
        )
        return (0.65 * mid) + (0.25 * microprice) + (0.10 * (mid + alpha_shift))

    # Spread logic
    def compute_half_spread_bps(self, market: MarketState, alpha: AlphaSignals) -> float:
        base = max(market.spread_bps / 2, self.config.min_spread_bps / 2)
        vol_component = alpha.realized_vol_bps * self.config.volatility_multiplier / 2
        toxicity_component = alpha.toxicity * 2.0
        total = base + vol_component + toxicity_component
        return clamp(total, self.config.min_spread_bps / 2, self.config.max_spread_bps / 2)

    # Inventory skew
    def compute_inventory_skew_bps(self, state: StrategyState) -> float:
        inv_ratio = safe_div(state.inventory, self.settings.risk.max_inventory)
        return clamp(inv_ratio * self.config.inventory_skew_coefficient * 10.0, -self.config.max_spread_bps, self.config.max_spread_bps)

    # Filters
    def quote_eligibility(self, market: MarketState, state: StrategyState, alpha: AlphaSignals) -> tuple[bool, str]:
        if state.is_kill_switch_active:
            return False, "kill_switch"
        if market.mid_price <= 0:
            return False, "invalid_mid"
        if alpha.toxicity > self.settings.risk.toxicity_threshold:
            return False, "toxic_flow"
        if alpha.realized_vol_bps > self.settings.risk.max_volatility_bps:
            return False, "high_vol"
        if abs(state.inventory) >= self.settings.risk.hard_inventory_limit:
            return False, "hard_inventory_limit"
        return True, "ok"

    # Sizing
    def compute_sizes(self, state: StrategyState, alpha: AlphaSignals) -> tuple[float, float]:
        base = self.config.order_size
        inv_pressure = abs(safe_div(state.inventory, self.settings.risk.max_inventory))
        toxicity_penalty = clamp(alpha.toxicity, 0.0, 1.0)
        size_scalar = max(0.25, 1.0 - 0.5 * inv_pressure - 0.35 * toxicity_penalty)
        bid_size = base * size_scalar * (0.7 if state.inventory > 0 else 1.0)
        ask_size = base * size_scalar * (0.7 if state.inventory < 0 else 1.0)
        return max(0.0, bid_size), max(0.0, ask_size)

    # Cancel / replace
    def should_cancel_quotes(self, market: MarketState, plan: QuotePlan, state: StrategyState) -> tuple[bool, bool]:
        now = utc_now()
        bid_age = (now - state.bid_quote_ts).total_seconds() if state.bid_quote_ts else 0.0
        ask_age = (now - state.ask_quote_ts).total_seconds() if state.ask_quote_ts else 0.0
        should_cancel_bid = bid_age > self.config.max_quote_age_seconds
        should_cancel_ask = ask_age > self.config.max_quote_age_seconds
        if state.open_bid_id and market.best_bid > 0:
            edge_bps = abs(plan.bid_price - market.best_bid) / market.mid_price * 10_000
            should_cancel_bid = should_cancel_bid or edge_bps > self.config.cancel_edge_bps
        if state.open_ask_id and market.best_ask > 0:
            edge_bps = abs(plan.ask_price - market.best_ask) / market.mid_price * 10_000
            should_cancel_ask = should_cancel_ask or edge_bps > self.config.cancel_edge_bps
        return should_cancel_bid, should_cancel_ask

    # Emergency rules
    def emergency_action(self, market: MarketState, state: StrategyState) -> str | None:
        unrealized = (market.mid_price - state.avg_entry_price) * state.inventory if state.avg_entry_price else 0.0
        if unrealized + state.realized_pnl <= self.settings.risk.emergency_flatten_pnl:
            return "flatten_pnl_breach"
        if abs(state.inventory) >= self.settings.risk.emergency_flatten_inventory:
            return "flatten_inventory_breach"
        return None

    def build_quote_plan(self, market: MarketState, state: StrategyState) -> QuotePlan:
        alpha = self.compute_signals(market)
        eligible, reason = self.quote_eligibility(market, state, alpha)
        fair_value = self.compute_fair_value(market, alpha)
        half_spread_bps = self.compute_half_spread_bps(market, alpha)
        inventory_skew_bps = self.compute_inventory_skew_bps(state)
        bid_size, ask_size = self.compute_sizes(state, alpha)

        bid_price = fair_value * (1 - bps_to_decimal(half_spread_bps - inventory_skew_bps))
        ask_price = fair_value * (1 + bps_to_decimal(half_spread_bps + inventory_skew_bps))
        emergency_action = self.emergency_action(market, state)
        if emergency_action:
            eligible = False
            reason = emergency_action

        provisional = QuotePlan(
            eligible=eligible,
            reason=reason,
            fair_value=fair_value,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_size=bid_size,
            ask_size=ask_size,
            alpha=alpha,
            inventory_skew_bps=inventory_skew_bps,
            half_spread_bps=half_spread_bps,
            should_cancel_bid=False,
            should_cancel_ask=False,
            emergency_action=emergency_action,
        )
        provisional.should_cancel_bid, provisional.should_cancel_ask = self.should_cancel_quotes(market, provisional, state)
        return provisional

    def apply_fill(self, state: StrategyState, side: str, price: float, size: float) -> StrategyState:
        signed_size = size if side.lower() == "buy" else -size
        prior_inventory = state.inventory
        new_inventory = prior_inventory + signed_size

        if prior_inventory == 0 or (prior_inventory > 0 and signed_size > 0) or (prior_inventory < 0 and signed_size < 0):
            gross_notional = abs(prior_inventory) * state.avg_entry_price + size * price
            state.inventory = new_inventory
            state.avg_entry_price = safe_div(gross_notional, abs(new_inventory), default=price)
        else:
            closing_size = min(abs(prior_inventory), abs(signed_size))
            pnl_sign = 1 if prior_inventory > 0 else -1
            state.realized_pnl += closing_size * (price - state.avg_entry_price) * pnl_sign
            state.inventory = new_inventory
            if new_inventory == 0:
                state.avg_entry_price = 0.0
            elif abs(signed_size) > abs(prior_inventory):
                state.avg_entry_price = price

        state.latest_fill_price = price
        state.latest_fill_side = side.lower()
        state.latest_fill_ts = utc_now()
        return state
