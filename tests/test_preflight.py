from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smc_ta import (
    PreflightConfig,
    PreflightValidationError,
    RuntimeConfig,
    assert_preflight_ready,
    run_preflight,
)
from smc_ta.broker import PaperBroker
from smc_ta.lifecycle import MemoryTradeLifecycleStore
from smc_ta.news import EconomicEvent, NewsFilter
from smc_ta.reconciliation import BrokerReconciler
from smc_ta.safety import EmergencyStopController


def make_candles(n: int = 40) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + np.arange(n) * 0.00005, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0002
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0002
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 10),
            "spread": 0.0001,
        },
        index=index,
    )


def codes(report) -> set[str]:
    return {check.code for check in report.checks}


def test_preflight_passes_for_paper_config_with_core_objects() -> None:
    runtime = RuntimeConfig(mode="paper", broker="paper", symbols=("EURUSD",), timeframes=("M15",))
    report = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={"EURUSD": make_candles()},
        broker=PaperBroker(initial_balance=10_000),
        emergency_stop=EmergencyStopController(),
        reconciler=BrokerReconciler(),
        lifecycle_store=MemoryTradeLifecycleStore(),
    )

    assert report.ok
    assert report.summary() == "preflight_ok"
    assert {"runtime_config_ok", "data_quality_ok", "account_probe_ok", "positions_probe_ok"}.issubset(codes(report))
    assert report.account is not None
    assert report.data_quality_reports["EURUSD"].ok


def test_preflight_blocks_unsafe_live_config() -> None:
    runtime = RuntimeConfig(mode="live", broker="paper")

    report = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={"EURUSD": make_candles()},
        config=PreflightConfig(require_broker_for_demo_live=False),
    )

    assert not report.ok
    assert {"live_not_armed", "missing_live_confirmation", "paper_broker_in_live_mode"}.issubset(codes(report))
    with pytest.raises(PreflightValidationError):
        assert_preflight_ready(
            runtime_config=runtime,
            candles_by_symbol={"EURUSD": make_candles()},
            config=PreflightConfig(require_broker_for_demo_live=False),
        )


def test_preflight_blocks_broker_probe_failure() -> None:
    class BrokenBroker:
        def get_account(self):
            raise RuntimeError("account unavailable")

        def get_open_positions(self, symbol=None):
            return []

    report = run_preflight(
        runtime_config=RuntimeConfig(mode="paper", broker="paper"),
        candles_by_symbol={"EURUSD": make_candles()},
        broker=BrokenBroker(),
    )

    assert not report.ok
    assert "account_probe_failed" in codes(report)


def test_preflight_blocks_active_emergency_stop() -> None:
    emergency_stop = EmergencyStopController()
    emergency_stop.activate("manual_test_stop")

    report = run_preflight(
        runtime_config=RuntimeConfig(mode="paper", broker="paper"),
        candles_by_symbol={"EURUSD": make_candles()},
        broker=PaperBroker(initial_balance=10_000),
        emergency_stop=emergency_stop,
    )

    assert not report.ok
    assert "emergency_stop_active" in codes(report)
    assert report.emergency_stop_result is not None
    assert report.emergency_stop_result.active


def test_preflight_requires_news_filter_when_runtime_requires_it() -> None:
    runtime = RuntimeConfig(
        mode="paper",
        broker="paper",
        require_news_filter=True,
        trading_economics_api_key="calendar-key",
    )

    missing = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={"EURUSD": make_candles()},
        broker=PaperBroker(initial_balance=10_000),
    )
    event = EconomicEvent(
        timestamp=pd.Timestamp("2024-01-01T12:00:00Z"),
        currency="USD",
        impact="high",
        title="FOMC",
    )
    loaded = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={"EURUSD": make_candles()},
        broker=PaperBroker(initial_balance=10_000),
        news_filter=NewsFilter([event]),
    )

    assert not missing.ok
    assert "news_filter_not_provided" in codes(missing)
    assert loaded.ok
    assert "news_filter_ok" in codes(loaded)


def test_preflight_reports_bad_candle_quality() -> None:
    candles = make_candles()
    candles.iloc[5, candles.columns.get_loc("high")] = candles.iloc[5]["low"] - 0.001

    report = run_preflight(
        runtime_config=RuntimeConfig(mode="paper", broker="paper"),
        candles_by_symbol={"EURUSD": candles},
        broker=PaperBroker(initial_balance=10_000),
    )

    assert not report.ok
    assert "invalid_ohlc_relationship" in codes(report)
    assert not report.to_frame().empty
