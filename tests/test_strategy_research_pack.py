from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.research import (
    StrategyResearchConfig,
    StrategyResearchGrid,
    StrategyResearchHypothesis,
    build_strategy_research_candidates,
    run_strategy_research_pack,
    write_strategy_research_pack,
)
from smc_ta.walkforward import WalkForwardConfig


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


def research_config(*, run_walk_forward: bool = False) -> StrategyResearchConfig:
    hypothesis = StrategyResearchHypothesis(
        name="test_intraday_fvg",
        profile_name="intraday_m15",
        thesis="Test FVG continuation evidence.",
        expected_setups=("fvg_continuation",),
        symbols=("EURUSD",),
    )
    return StrategyResearchConfig(
        hypotheses=(hypothesis,),
        grid=StrategyResearchGrid(
            min_signal_score_offsets=(0, 1),
            adx_threshold_offsets=(0,),
            max_poi_atr_distance_multipliers=(1.0,),
            max_spread_pips_multipliers=(1.0,),
            risk_percent_multipliers=(1.0,),
            max_candidates_per_hypothesis=2,
        ),
        symbols=("EURUSD",),
        run_walk_forward=run_walk_forward,
        walk_forward=WalkForwardConfig(train_size=100, test_size=50, objective="total_return_percent"),
        min_trades=0,
        min_profit_factor=0.0,
        max_drawdown_percent=100.0,
    )


def test_strategy_research_candidates_are_deterministic() -> None:
    config = research_config()

    candidates = build_strategy_research_candidates(
        hypotheses=config.hypotheses,
        grid=config.grid,
        symbols=config.symbols,
    )

    assert [candidate.name for candidate in candidates] == [
        "test_intraday_fvg_EURUSD_v01",
        "test_intraday_fvg_EURUSD_v02",
    ]
    assert candidates[0].config.symbol == "EURUSD"
    assert candidates[0].parameters["min_signal_score"] == 6


def test_strategy_research_pack_outputs_candidate_setup_and_session_reports(tmp_path) -> None:
    result = run_strategy_research_pack({"EURUSD": make_candles()}, config=research_config())

    assert len(result.hypotheses) == 1
    assert len(result.candidates) == 2
    assert {"candidate", "research_score", "total_return_percent"}.issubset(result.candidates.columns)
    assert {"setup_name", "signals", "trades"}.issubset(result.setup_report.columns)
    assert {"session", "signals"}.issubset(result.session_report.columns)
    assert {"research_status", "promotion_reasons"}.issubset(result.promotion_report.columns)
    assert result.summary()

    saved = write_strategy_research_pack(result, tmp_path)

    assert saved.artifacts is not None
    assert saved.artifacts.summary_json.exists()
    assert saved.artifacts.candidates_csv.exists()
    assert saved.artifacts.html_report.exists()
    assert "SMC TA Strategy Research Pack" in saved.artifacts.html_report.read_text(encoding="utf-8")


def test_strategy_research_pack_can_run_walk_forward() -> None:
    result = run_strategy_research_pack({"EURUSD": make_candles()}, config=research_config(run_walk_forward=True))

    assert not result.walk_forward_summary.empty
    assert result.walk_forward_summary.iloc[0]["status"] == "ok"
    assert "walk_forward_total_return_percent" in result.walk_forward_summary.columns
    assert not result.walk_forward_rankings.empty
