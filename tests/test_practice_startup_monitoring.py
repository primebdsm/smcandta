from __future__ import annotations

import json

import pandas as pd

from smc_ta import PracticeStartupRunConfig, run_practice_startup_monitoring
from smc_ta.broker import PaperBroker


def test_practice_startup_monitoring_paper_writes_startup_and_dashboard_artifacts(tmp_path) -> None:
    result = run_practice_startup_monitoring(
        PracticeStartupRunConfig(
            broker="paper",
            output_dir=tmp_path / "practice_startup",
            candle_limit=120,
        )
    )

    assert result.ok
    assert result.summary() == "practice_startup_monitoring_ok"
    assert result.restart_sync is not None
    assert result.restart_sync.summary() == "restart_sync_ok"
    assert result.lifecycle_recovery_summary == "lifecycle_recovery_ok"
    assert result.preflight is not None
    assert result.preflight.summary() == "preflight_ok"
    assert result.broker_connectivity[0].ok
    assert result.alert_delivery[0].ok

    expected_artifacts = {
        "alert_delivery",
        "broker_connectivity",
        "candles",
        "dashboard",
        "lifecycle_recovery",
        "lifecycle_records",
        "pending_orders",
        "preflight",
        "restart_sync",
        "restart_sync_actions",
        "snapshot",
        "summary",
        "transactions",
    }
    assert expected_artifacts.issubset(result.artifacts)
    for artifact in expected_artifacts:
        assert result.artifacts[artifact].exists(), artifact

    summary = json.loads(result.artifacts["summary"].read_text(encoding="utf-8"))
    snapshot = json.loads(result.artifacts["snapshot"].read_text(encoding="utf-8"))
    dashboard_html = result.artifacts["dashboard"].read_text(encoding="utf-8")

    assert summary["ok"] is True
    assert summary["broker"] == "paper"
    assert summary["restart_sync_summary"] == "restart_sync_ok"
    assert summary["lifecycle_recovery_summary"] == "lifecycle_recovery_ok"
    assert summary["preflight_summary"] == "preflight_ok"
    assert snapshot["symbol"] == "EURUSD"
    assert snapshot["broker_connectivity"][0]["status"] == "ok"
    assert snapshot["alert_delivery"][0]["status"] == "ok"
    assert "Broker Connectivity" in dashboard_html
    assert "Alert Delivery" in dashboard_html


def test_practice_startup_monitoring_oanda_blocks_without_required_secrets(tmp_path) -> None:
    result = run_practice_startup_monitoring(
        PracticeStartupRunConfig(
            broker="oanda",
            output_dir=tmp_path / "oanda_startup",
        ),
        env={},
    )

    assert not result.ok
    assert result.summary() == "secrets:missing_required_secret"
    assert result.secret_summary == "missing_required_secret"
    assert result.restart_sync is None
    assert result.preflight is None
    assert {"secrets", "summary"}.issubset(result.artifacts)

    secrets = json.loads(result.artifacts["secrets"].read_text(encoding="utf-8"))
    summary = json.loads(result.artifacts["summary"].read_text(encoding="utf-8"))

    assert secrets["ok"] is False
    assert secrets["summary"] == "missing_required_secret"
    missing_keys = sorted(issue["key"] for issue in secrets["issues"] if issue["code"] == "missing_required_secret")
    assert missing_keys == ["OANDA_ACCOUNT_ID", "OANDA_TOKEN"]
    assert summary["ok"] is False
    assert summary["secret_summary"] == "missing_required_secret"
    assert "OANDA_TOKEN" not in summary.get("values", {})


def test_practice_startup_monitoring_accepts_prefixed_oanda_env_keys(tmp_path) -> None:
    result = run_practice_startup_monitoring(
        PracticeStartupRunConfig(
            broker="oanda",
            output_dir=tmp_path / "prefixed_oanda_startup",
            candle_limit=120,
        ),
        env={
            "SMC_TA_OANDA_ACCOUNT_ID": "practice-account-id",
            "SMC_TA_OANDA_TOKEN": "practice-token",
        },
        broker=PaperBroker(initial_balance=10_000),
        candles=_candles(),
    )

    assert result.ok
    assert result.secret_summary == "secrets_ok"
    assert result.preflight is not None
    assert result.preflight.ok
    secrets = json.loads(result.artifacts["secrets"].read_text(encoding="utf-8"))

    assert secrets["ok"] is True
    assert secrets["used_sources"]["SMC_TA_OANDA_ACCOUNT_ID"] == "env_smc_ta"
    assert secrets["used_sources"]["SMC_TA_OANDA_TOKEN"] == "env_smc_ta"
    assert "practice-token" not in result.artifacts["secrets"].read_text(encoding="utf-8")


def _candles(rows: int = 120) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    sequence = pd.Series(range(rows), index=index, dtype=float)
    close = 1.1000 + sequence * 0.00001 + ((sequence % 12) - 6) * 0.00002
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00025
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00025
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (sequence % 20),
            "spread": 0.0001,
        },
        index=index,
    )
