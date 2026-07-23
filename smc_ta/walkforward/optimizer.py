"""Walk-forward optimization for Forex strategy configurations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import pandas as pd

from smc_ta.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from smc_ta.monitoring.metrics import performance_summary
from smc_ta.news.calendar import NewsFilter
from smc_ta.validation import normalize_ohlcv

ObjectiveName = Literal["net_pnl", "total_return_percent", "profit_factor", "return_over_drawdown"]
ScoreFunction = Callable[[dict[str, float]], float]


@dataclass(frozen=True)
class WalkForwardCandidate:
    """One strategy/backtest configuration candidate."""

    name: str
    config: BacktestConfig


@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-forward split and ranking settings."""

    train_size: int
    test_size: int
    step_size: int | None = None
    min_train_size: int | None = None
    objective: ObjectiveName = "return_over_drawdown"
    require_trades: bool = False


@dataclass(frozen=True)
class WalkForwardFold:
    """One walk-forward train/test fold."""

    fold: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    selected_candidate: str
    train_metrics: dict[str, float]
    test_metrics: dict[str, float]
    train_result: BacktestResult
    test_result: BacktestResult
    candidate_scores: pd.DataFrame


@dataclass(frozen=True)
class WalkForwardResult:
    """Complete walk-forward result."""

    folds: tuple[WalkForwardFold, ...]
    summary: pd.DataFrame
    candidate_rankings: pd.DataFrame
    combined_equity_curve: pd.DataFrame
    combined_trades: pd.DataFrame

    @property
    def selected_candidates(self) -> tuple[str, ...]:
        return tuple(fold.selected_candidate for fold in self.folds)


def generate_rolling_windows(
    index: pd.Index,
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    min_train_size: int | None = None,
) -> list[tuple[slice, slice]]:
    """Return train/test positional slices for rolling walk-forward windows."""

    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")
    minimum_train = min_train_size or train_size
    if minimum_train <= 0 or minimum_train > train_size:
        raise ValueError("min_train_size must be positive and <= train_size")
    windows: list[tuple[slice, slice]] = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        test_start = train_end
        test_end = test_start + test_size
        if test_end > len(index):
            break
        if train_end - train_start >= minimum_train:
            windows.append((slice(train_start, train_end), slice(test_start, test_end)))
        train_start += step
    return windows


def run_walk_forward(
    candles: pd.DataFrame,
    *,
    candidates: list[WalkForwardCandidate],
    config: WalkForwardConfig,
    news_filter: NewsFilter | None = None,
    score_function: ScoreFunction | None = None,
) -> WalkForwardResult:
    """Run rolling walk-forward optimization.

    Candidates are ranked on the training window only. The selected candidate is
    then evaluated on the following unseen test window.
    """

    if not candidates:
        raise ValueError("at least one candidate is required")
    data = normalize_ohlcv(candles)
    windows = generate_rolling_windows(
        data.index,
        train_size=config.train_size,
        test_size=config.test_size,
        step_size=config.step_size,
        min_train_size=config.min_train_size,
    )
    if not windows:
        raise ValueError("not enough candles for the requested walk-forward windows")

    folds: list[WalkForwardFold] = []
    ranking_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []

    for fold_number, (train_slice, test_slice) in enumerate(windows, start=1):
        train_data = data.iloc[train_slice]
        test_data = data.iloc[test_slice]
        train_evaluations = [
            _evaluate_candidate(candidate, train_data, news_filter=news_filter, score_function=score_function, objective=config.objective)
            for candidate in candidates
        ]
        candidate_scores = pd.DataFrame(
            [
                {
                    "fold": fold_number,
                    "candidate": candidate.name,
                    "score": score,
                    **_prefixed(metrics, "train_"),
                }
                for candidate, _, metrics, score in train_evaluations
            ]
        ).sort_values(["score", "candidate"], ascending=[False, True])
        if config.require_trades:
            tradable = candidate_scores[candidate_scores["train_trades"].fillna(0) > 0]
            if not tradable.empty:
                candidate_scores = tradable
        selected_name = str(candidate_scores.iloc[0]["candidate"])
        selected_candidate, train_result, train_metrics, _ = next(
            evaluation for evaluation in train_evaluations if evaluation[0].name == selected_name
        )
        test_result = run_backtest(test_data, config=selected_candidate.config, news_filter=news_filter)
        test_metrics = performance_summary(test_result.equity_curve, test_result.trades)

        ranking_frames.append(candidate_scores)
        test_equity = test_result.equity_curve.copy()
        test_equity["fold"] = fold_number
        test_equity["candidate"] = selected_candidate.name
        equity_frames.append(test_equity)
        if not test_result.trades.empty:
            trades = test_result.trades.copy()
            trades["fold"] = fold_number
            trades["candidate"] = selected_candidate.name
            trade_frames.append(trades)

        folds.append(
            WalkForwardFold(
                fold=fold_number,
                train_start=pd.Timestamp(train_data.index[0]),
                train_end=pd.Timestamp(train_data.index[-1]),
                test_start=pd.Timestamp(test_data.index[0]),
                test_end=pd.Timestamp(test_data.index[-1]),
                selected_candidate=selected_candidate.name,
                train_metrics=train_metrics,
                test_metrics=test_metrics,
                train_result=train_result,
                test_result=test_result,
                candidate_scores=candidate_scores.reset_index(drop=True),
            )
        )

    summary = _summary_frame(folds)
    rankings = pd.concat(ranking_frames, ignore_index=True) if ranking_frames else pd.DataFrame()
    combined_equity = pd.concat(equity_frames).sort_index() if equity_frames else pd.DataFrame()
    combined_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return WalkForwardResult(
        folds=tuple(folds),
        summary=summary,
        candidate_rankings=rankings,
        combined_equity_curve=combined_equity,
        combined_trades=combined_trades,
    )


def _evaluate_candidate(
    candidate: WalkForwardCandidate,
    data: pd.DataFrame,
    *,
    news_filter: NewsFilter | None,
    score_function: ScoreFunction | None,
    objective: ObjectiveName,
) -> tuple[WalkForwardCandidate, BacktestResult, dict[str, float], float]:
    result = run_backtest(data, config=candidate.config, news_filter=news_filter)
    metrics = performance_summary(result.equity_curve, result.trades)
    score = score_function(metrics) if score_function else _score(metrics, objective)
    return candidate, result, metrics, score


def _score(metrics: dict[str, float], objective: ObjectiveName) -> float:
    if objective == "net_pnl":
        return float(metrics.get("net_pnl", 0.0))
    if objective == "total_return_percent":
        return float(metrics.get("total_return_percent", 0.0))
    if objective == "profit_factor":
        value = float(metrics.get("profit_factor", 0.0))
        return 1e9 if value == float("inf") else value
    if objective == "return_over_drawdown":
        drawdown = abs(float(metrics.get("max_drawdown_percent", 0.0)))
        return float(metrics.get("total_return_percent", 0.0)) / max(drawdown, 1e-9)
    raise ValueError(f"unknown objective: {objective}")


def _prefixed(metrics: dict[str, float], prefix: str) -> dict[str, float]:
    return {f"{prefix}{key}": value for key, value in metrics.items()}


def _summary_frame(folds: list[WalkForwardFold]) -> pd.DataFrame:
    rows = []
    for fold in folds:
        rows.append(
            {
                "fold": fold.fold,
                "selected_candidate": fold.selected_candidate,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                **_prefixed(fold.train_metrics, "train_"),
                **_prefixed(fold.test_metrics, "test_"),
            }
        )
    return pd.DataFrame(rows)

