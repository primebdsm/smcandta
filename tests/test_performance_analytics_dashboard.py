from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smc_ta import (
    DemoForwardConfig,
    DemoForwardScheduleConfig,
    PerformanceAnalyticsDashboardConfig,
    load_performance_analytics_data,
    render_performance_analytics_dashboard,
    run_demo_forward_schedule,
    run_demo_forward_test,
    write_demo_forward_report_bundle,
    write_performance_analytics_dashboard,
)
from smc_ta.risk import RiskConfig


def test_performance_analytics_dashboard_loads_scheduler_artifacts(tmp_path) -> None:
    schedule = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=tmp_path / "schedule",
            interval_seconds=0,
            max_runs=1,
            demo_forward=_config(),
        ),
        candles_loader=lambda: _candles(),
        now_fn=_clock(),
    )

    data = load_performance_analytics_data(schedule.output_dir)
    html = render_performance_analytics_dashboard(
        data,
        PerformanceAnalyticsDashboardConfig(title="Performance Test", max_table_rows=10),
    )
    output = write_performance_analytics_dashboard(schedule.output_dir, tmp_path / "performance.html")

    assert data.source_type == "demo_forward_schedule"
    assert data.latest_run_dir is not None
    assert data.latest_run_dir.exists()
    assert data.status == "ok"
    assert not data.run_metrics.empty
    assert output.exists()
    assert "Performance Test" in html
    assert "Equity Curve" in html
    assert "Drawdown" in html
    assert "Setup Performance" in html
    assert "Scheduler History" in html
    assert "Run Metrics" in html


def test_performance_analytics_dashboard_loads_single_report_bundle(tmp_path) -> None:
    result = run_demo_forward_test(_candles(), config=_config())
    saved = write_demo_forward_report_bundle(result, tmp_path / "report")

    data = load_performance_analytics_data(saved.artifacts.output_dir)
    html = render_performance_analytics_dashboard(data)

    assert data.source_type == "demo_forward_report"
    assert data.scheduler_history.empty
    assert data.summary["symbol"] == "EURUSD"
    assert "demo_forward_report" in html
    assert "Recent Trades" in html


def test_performance_analytics_dashboard_rejects_missing_source(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_performance_analytics_data(tmp_path / "missing")


def _config() -> DemoForwardConfig:
    return DemoForwardConfig(
        symbol="EURUSD",
        warmup_candles=80,
        max_cycles=20,
        risk=RiskConfig(min_confidence=0.5, min_reward_to_risk=1.0, max_units=10_000),
    )


def _candles(rows: int = 140) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    wave = np.sin(np.arange(rows) / 5) * 0.001
    drift = np.arange(rows) * 0.00002
    close = pd.Series(1.1000 + wave + drift, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(rows) % 25),
            "spread": 0.00012,
        },
        index=index,
    )


def _clock():
    current = pd.Timestamp("2024-01-01T00:00:00Z")

    def now():
        nonlocal current
        value = current
        current += pd.Timedelta(seconds=1)
        return value

    return now
