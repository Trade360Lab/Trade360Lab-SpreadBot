from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import optuna
import pandas as pd

from backtest import EventDrivenBacktester
from settings import Settings, StrategyConfig
from utils import daterange, ensure_dir, save_json


def _score(summary: dict[str, Any], settings: Settings) -> float:
    return (
        float(summary.get("net_pnl", 0.0))
        - settings.optimizer.dd_penalty * float(summary.get("max_drawdown", 0.0))
        - settings.optimizer.inventory_penalty * float(summary.get("inventory_variance", 0.0))
        - settings.optimizer.taker_penalty * float(summary.get("taker_ratio", 0.0))
    )


def _slice_frame(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    mask = (frame["timestamp"] >= start) & (frame["timestamp"] < end)
    return frame.loc[mask].reset_index(drop=True)


@dataclass(slots=True)
class FoldResult:
    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_score: float
    test_score: float
    params: dict[str, Any]
    train_summary: dict[str, Any]
    test_summary: dict[str, Any]


class ParameterOptimizer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _apply_params(self, params: dict[str, Any]) -> Settings:
        cloned = self.settings.model_copy(deep=True)
        cloned.strategy = StrategyConfig(**{**cloned.strategy.model_dump(), **params})
        return cloned

    def suggest_params(self, trial: optuna.Trial) -> dict[str, Any]:
        return {
            "min_spread_bps": trial.suggest_float("min_spread_bps", 1.0, 5.0),
            "max_spread_bps": trial.suggest_float("max_spread_bps", 6.0, 20.0),
            "volatility_multiplier": trial.suggest_float("volatility_multiplier", 0.8, 3.0),
            "inventory_skew_coefficient": trial.suggest_float("inventory_skew_coefficient", 0.2, 1.5),
            "toxicity_threshold": trial.suggest_float("toxicity_threshold", 0.3, 1.0),
            "cancel_edge_bps": trial.suggest_float("cancel_edge_bps", 0.3, 2.0),
            "max_quote_age_seconds": trial.suggest_float("max_quote_age_seconds", 1.0, 10.0),
            "order_size": trial.suggest_float("order_size", 0.0005, 0.005),
            "alpha_threshold": trial.suggest_float("alpha_threshold", 0.005, 0.05),
        }

    def optimize(self, train_frame: pd.DataFrame, n_trials: int | None = None, timeout: int | None = None) -> dict[str, Any]:
        def objective(trial: optuna.Trial) -> float:
            params = self.suggest_params(trial)
            tuned_settings = self._apply_params(params)
            tuned_settings.risk.toxicity_threshold = params["toxicity_threshold"]
            result = EventDrivenBacktester(tuned_settings).run(train_frame)
            score = _score(result.summary, tuned_settings)
            trial.set_user_attr("summary", result.summary)
            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(
            objective,
            n_trials=n_trials or self.settings.optimizer.n_trials,
            timeout=timeout or self.settings.optimizer.timeout_seconds,
        )
        return {
            "best_params": study.best_trial.params,
            "best_score": study.best_value,
            "summary": study.best_trial.user_attrs.get("summary", {}),
        }


class RollingWalkForwardAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.optimizer = ParameterOptimizer(settings)

    def run(self, frame: pd.DataFrame, output_dir: str | Path | None = None) -> dict[str, Any]:
        data = frame.copy()
        data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
        start = data["timestamp"].min().floor("D")
        end = data["timestamp"].max().ceil("D")
        train_delta = timedelta(days=self.settings.optimizer.train_days)
        test_delta = timedelta(days=self.settings.optimizer.test_days)
        step_delta = timedelta(days=self.settings.optimizer.step_days)

        fold_results: list[FoldResult] = []
        for fold_idx, fold_start in enumerate(daterange(start.to_pydatetime(), end.to_pydatetime() - train_delta - test_delta, step_delta), start=1):
            train_start = pd.Timestamp(fold_start, tz="UTC")
            train_end = train_start + train_delta
            test_end = train_end + test_delta
            train_frame = _slice_frame(data, train_start, train_end)
            test_frame = _slice_frame(data, train_end, test_end)
            if train_frame.empty or test_frame.empty:
                continue

            opt_result = self.optimizer.optimize(train_frame)
            tuned_settings = self.optimizer._apply_params(opt_result["best_params"])
            tuned_settings.risk.toxicity_threshold = opt_result["best_params"].get("toxicity_threshold", tuned_settings.risk.toxicity_threshold)
            train_result = EventDrivenBacktester(tuned_settings).run(train_frame)
            test_result = EventDrivenBacktester(tuned_settings).run(test_frame)

            fold_results.append(FoldResult(
                fold=fold_idx,
                train_start=str(train_start),
                train_end=str(train_end),
                test_start=str(train_end),
                test_end=str(test_end),
                train_score=_score(train_result.summary, tuned_settings),
                test_score=_score(test_result.summary, tuned_settings),
                params=opt_result["best_params"],
                train_summary=train_result.summary,
                test_summary=test_result.summary,
            ))

        oos_net_pnl = sum(result.test_summary.get("net_pnl", 0.0) for result in fold_results)
        oos_drawdown = max((result.test_summary.get("max_drawdown", 0.0) for result in fold_results), default=0.0)
        summary = {
            "folds": [result.__dict__ for result in fold_results],
            "oos_net_pnl": oos_net_pnl,
            "oos_max_drawdown": oos_drawdown,
            "oos_score": sum(result.test_score for result in fold_results),
            "fold_count": len(fold_results),
        }

        if output_dir:
            target_dir = ensure_dir(output_dir)
            save_json(Path(target_dir) / "wfa_summary.json", summary)
        return summary
