from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from smc_ta import (
    DemoForwardConfig,
    render_demo_forward_html_report,
    run_demo_forward_test,
    write_demo_forward_report_bundle,
)
from smc_ta.journal import SQLiteTradeJournal
from smc_ta.lifecycle import MemoryTradeLifecycleStore
from smc_ta.risk import RiskConfig


def make_candles(n: int = 140) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    wave = np.sin(np.arange(n) / 5) * 0.001
    drift = np.arange(n) * 0.00002
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
            "tick_volume": 100 + (np.arange(n) % 25),
            "spread": 0.00012,
        },
        index=index,
    )


def config() -> DemoForwardConfig:
    return DemoForwardConfig(
        symbol="EURUSD",
        warmup_candles=80,
        max_cycles=20,
        risk=RiskConfig(min_confidence=0.5, min_reward_to_risk=1.0, max_units=10_000),
    )


def test_demo_forward_runner_outputs_reports() -> None:
    result = run_demo_forward_test(make_candles(), config=config())

    assert result.ok
    assert result.summary["cycles"] == 20
    assert result.summary["orders"] >= 1
    assert result.summary["trades"] >= 1
    assert len(result.equity_curve) == 20
    assert {"cycle", "timestamp", "action", "setup_name", "equity"}.issubset(result.cycle_report.columns)
    assert not result.trades.empty
    assert not result.fills.empty
    assert not result.setup_report.empty
    assert not result.session_report.empty
    assert not result.daily_report.empty
    assert not result.blocked_reasons.empty
    assert not result.position_events.empty


def test_demo_forward_report_bundle_writes_artifacts(tmp_path) -> None:
    result = run_demo_forward_test(make_candles(), config=config())
    saved = write_demo_forward_report_bundle(result, tmp_path / "reports")

    assert saved.artifacts is not None
    for path in as_paths(saved.artifacts):
        assert path.exists()
    payload = json.loads(saved.artifacts.summary_json.read_text())
    assert payload["symbol"] == "EURUSD"
    assert payload["cycles"] == 20
    html = saved.artifacts.html_report.read_text()
    assert "Demo Forward Report" in html
    assert "Setup Report" in html


def test_demo_forward_updates_lifecycle_and_sqlite_journal(tmp_path) -> None:
    lifecycle_store = MemoryTradeLifecycleStore()
    journal = SQLiteTradeJournal(tmp_path / "journal.sqlite")

    result = run_demo_forward_test(
        make_candles(),
        config=config(),
        lifecycle_store=lifecycle_store,
        journal=journal,
    )

    lifecycle_records = lifecycle_store.list_records(symbol="EURUSD")
    assert lifecycle_records
    assert any(record.state == "closed" for record in lifecycle_records)
    events = journal.read(symbol="EURUSD")
    assert {"signal", "fill"}.issubset(set(events["event_type"]))
    assert result.summary["orders"] >= 1


def test_demo_forward_rejects_too_short_sample() -> None:
    with pytest.raises(ValueError, match="not enough candles"):
        run_demo_forward_test(make_candles(20), config=DemoForwardConfig(warmup_candles=20))


def test_demo_forward_html_renderer_handles_empty_optional_tables() -> None:
    result = run_demo_forward_test(
        make_candles(130),
        config=DemoForwardConfig(
            warmup_candles=120,
            max_cycles=5,
            risk=RiskConfig(min_confidence=2.0),
        ),
    )

    html = render_demo_forward_html_report(result)
    assert "Demo Forward Report" in html
    assert "Blocked Reasons" in html
    assert result.summary["orders"] == 0


def as_paths(bundle):
    return [
        bundle.summary_json,
        bundle.html_report,
        bundle.cycles_csv,
        bundle.equity_csv,
        bundle.trades_csv,
        bundle.fills_csv,
        bundle.setup_report_csv,
        bundle.session_report_csv,
        bundle.daily_report_csv,
        bundle.blocked_reasons_csv,
        bundle.position_events_csv,
    ]
