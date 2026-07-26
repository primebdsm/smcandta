from __future__ import annotations

import json

import numpy as np
import pandas as pd

from smc_ta import DemoForwardConfig, DemoForwardScheduleConfig, run_demo_forward_schedule
from smc_ta.risk import RiskConfig


def test_demo_forward_scheduler_writes_timestamped_reports_and_history(tmp_path) -> None:
    result = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=tmp_path / "schedule",
            interval_seconds=0,
            max_runs=1,
            demo_forward=_config(),
        ),
        candles_loader=lambda: _candles(),
        now_fn=_clock(),
    )

    assert result.ok
    assert result.summary() == "demo_forward_schedule_ok:ok=1;skipped=0"
    assert result.history_csv.exists()
    assert result.summary_json.exists()
    assert len(result.runs) == 1
    assert result.runs[0].ok
    assert result.runs[0].html_report is not None
    assert result.runs[0].html_report.exists()
    assert result.runs[0].summary_json is not None
    assert result.runs[0].summary_json.exists()

    history = pd.read_csv(result.history_csv)
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))

    assert history.iloc[0]["status"] == "ok"
    assert payload["runs"][0]["status"] == "ok"
    assert payload["config"]["demo_forward"]["symbol"] == "EURUSD"


def test_demo_forward_scheduler_skips_unchanged_candle_window(tmp_path) -> None:
    sleeps: list[float] = []
    result = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=tmp_path / "schedule",
            interval_seconds=0,
            max_runs=2,
            skip_when_no_new_candle=True,
            demo_forward=_config(),
        ),
        candles_loader=lambda: _candles(),
        sleep_fn=sleeps.append,
        now_fn=_clock(),
    )

    assert result.ok
    assert [run.status for run in result.runs] == ["ok", "skipped"]
    assert result.runs[1].message == "no_new_candle"
    assert result.runs[1].html_report is None
    assert sleeps == [0]

    history = pd.read_csv(result.history_csv)
    assert list(history["status"]) == ["ok", "skipped"]


def test_demo_forward_scheduler_uses_existing_history_after_restart(tmp_path) -> None:
    output_dir = tmp_path / "schedule"
    first = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=output_dir,
            interval_seconds=0,
            max_runs=1,
            demo_forward=_config(),
        ),
        candles_loader=lambda: _candles(),
        now_fn=_clock("2024-01-01T00:00:00Z"),
    )
    second = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=output_dir,
            interval_seconds=0,
            max_runs=1,
            demo_forward=_config(),
        ),
        candles_loader=lambda: _candles(),
        now_fn=_clock("2024-01-01T01:00:00Z"),
    )

    assert first.runs[0].ok
    assert second.ok
    assert second.runs[0].skipped
    assert second.runs[0].message == "no_new_candle"

    history = pd.read_csv(second.history_csv)
    assert list(history["status"]) == ["ok", "skipped"]


def test_demo_forward_scheduler_records_failures_without_crashing(tmp_path) -> None:
    result = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=tmp_path / "schedule",
            interval_seconds=0,
            max_runs=2,
            stop_on_failure=True,
            demo_forward=DemoForwardConfig(warmup_candles=20),
        ),
        candles_loader=lambda: _candles(20),
        now_fn=_clock(),
    )

    assert not result.ok
    assert result.summary() == "demo_forward_schedule_failed:failed=1;ok=0;warning=0;skipped=0"
    assert len(result.runs) == 1
    assert result.runs[0].failed
    assert result.runs[0].exception_type == "ValueError"
    assert "not enough candles" in result.runs[0].message
    assert result.history_csv.exists()


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


def _clock(start: str = "2024-01-01T00:00:00Z"):
    current = pd.Timestamp(start)

    def now():
        nonlocal current
        value = current
        current += pd.Timedelta(seconds=1)
        return value

    return now
