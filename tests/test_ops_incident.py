from __future__ import annotations

import json

import pandas as pd

from smc_ta import (
    RuntimeConfig,
    build_live_monitoring_snapshot,
    run_preflight,
    write_incident_report_bundle,
)
from smc_ta.broker import OrderRequest, PaperBroker
from smc_ta.lifecycle import MemoryTradeLifecycleStore
from smc_ta.safety import EmergencyStopController


def make_candles() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=40, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + pd.RangeIndex(40) * 0.00002, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0002
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0002
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100,
            "spread": 0.0001,
        },
        index=index,
    )


def test_write_incident_report_bundle_collects_operational_artifacts(tmp_path) -> None:
    runtime = RuntimeConfig(
        mode="paper",
        broker="paper",
        symbols=("EURUSD",),
        timeframes=("M15",),
        oanda_token="secret-token",
    )
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000, stop_loss=1.095, take_profit=1.11),
        market_price=1.1000,
    )
    emergency_stop = EmergencyStopController()
    emergency_stop_result = emergency_stop.activate("manual_test_stop")
    lifecycle_store = MemoryTradeLifecycleStore()
    preflight = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={"EURUSD": make_candles()},
        broker=broker,
        emergency_stop=emergency_stop,
        lifecycle_store=lifecycle_store,
    )
    journal_events = pd.DataFrame(
        [{"timestamp": pd.Timestamp("2024-01-01T12:00:00Z"), "symbol": "EURUSD", "event": "manual_stop"}]
    )
    snapshot = build_live_monitoring_snapshot(
        symbol="EURUSD",
        account=broker.get_account(),
        open_positions=broker.get_open_positions("EURUSD"),
        preflight=preflight,
        emergency_stop=emergency_stop_result,
        lifecycle_store=lifecycle_store,
        journal_events=journal_events,
        mode="paper",
        broker_name="paper",
    )

    bundle = write_incident_report_bundle(
        tmp_path / "incident",
        incident_id="incident-test",
        title="manual stop test",
        severity="sev1",
        symbol="EURUSD",
        runtime_config=runtime,
        monitoring_snapshot=snapshot,
        notes=("operator stopped trading",),
        operator_actions=("checked broker manually",),
    )

    summary_text = bundle.summary_json.read_text(encoding="utf-8")
    payload = json.loads(summary_text)

    assert bundle.incident_id == "incident-test"
    assert bundle.markdown_report.exists()
    assert payload["severity"] == "SEV1"
    assert payload["open_position_count"] == 1
    assert "secret-token" not in summary_text
    assert "preflight:emergency_stop_active" in payload["blocking_reasons"]
    assert "emergency_stop:manual_test_stop" in payload["blocking_reasons"]
    assert (bundle.output_dir / "preflight.csv").exists()
    assert (bundle.output_dir / "open_positions.csv").exists()
    assert (bundle.output_dir / "journal_events.csv").exists()
    assert "manual stop test" in bundle.markdown_report.read_text(encoding="utf-8")
