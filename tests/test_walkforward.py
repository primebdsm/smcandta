from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smc_ta.backtest import BacktestConfig
from smc_ta.engine import ConfluenceConfig
from smc_ta.risk import RiskConfig
from smc_ta.walkforward import (
    WalkForwardCandidate,
    WalkForwardConfig,
    generate_rolling_windows,
    run_walk_forward,
)


def make_candles(n: int = 260) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    base = 1.1000 + np.sin(np.arange(n) / 8) * 0.001 + np.arange(n) * 0.000015
    close = pd.Series(base, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00035
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00035
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 15),
            "spread": 0.0001,
        },
        index=index,
    )


def candidates() -> list[WalkForwardCandidate]:
    return [
        WalkForwardCandidate(
            "loose",
            BacktestConfig(
                symbol="EURUSD",
                confluence=ConfluenceConfig(min_signal_score=4),
                risk=RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0, max_units=5_000),
            ),
        ),
        WalkForwardCandidate(
            "strict",
            BacktestConfig(
                symbol="EURUSD",
                confluence=ConfluenceConfig(min_signal_score=8),
                risk=RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0, max_units=5_000),
            ),
        ),
    ]


def test_generate_rolling_windows() -> None:
    index = pd.RangeIndex(100)
    windows = generate_rolling_windows(index, train_size=40, test_size=20)

    assert windows == [(slice(0, 40), slice(40, 60)), (slice(20, 60), slice(60, 80)), (slice(40, 80), slice(80, 100))]


def test_walk_forward_outputs_train_test_summary() -> None:
    candles = make_candles()
    result = run_walk_forward(
        candles,
        candidates=candidates(),
        config=WalkForwardConfig(train_size=100, test_size=50, objective="total_return_percent"),
    )

    assert len(result.folds) == 3
    assert len(result.summary) == 3
    assert {"selected_candidate", "train_total_return_percent", "test_total_return_percent"}.issubset(result.summary.columns)
    assert {"fold", "candidate", "score"}.issubset(result.candidate_rankings.columns)
    assert not result.combined_equity_curve.empty
    assert set(result.selected_candidates).issubset({"loose", "strict"})


def test_walk_forward_custom_score_function_selects_named_candidate() -> None:
    candles = make_candles()

    def score(metrics: dict[str, float]) -> float:
        return 1.0 if metrics.get("trades", 0) >= 0 else 0.0

    result = run_walk_forward(
        candles,
        candidates=candidates(),
        config=WalkForwardConfig(train_size=100, test_size=50),
        score_function=score,
    )

    assert result.summary.iloc[0]["selected_candidate"] == "loose"


def test_walk_forward_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        generate_rolling_windows(pd.RangeIndex(10), train_size=0, test_size=2)

    with pytest.raises(ValueError):
        run_walk_forward(make_candles(30), candidates=[], config=WalkForwardConfig(train_size=10, test_size=5))

    with pytest.raises(ValueError):
        run_walk_forward(make_candles(30), candidates=candidates(), config=WalkForwardConfig(train_size=50, test_size=10))

