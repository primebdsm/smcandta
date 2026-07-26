from __future__ import annotations

import json

import numpy as np
import pandas as pd

from smc_ta import (
    DemoForwardConfig,
    RiskStressConfig,
    RiskStressScenario,
    run_risk_stress_test,
    write_risk_stress_report_bundle,
)
from smc_ta.risk import RiskConfig


def test_risk_stress_runs_scenarios_and_compares_to_baseline() -> None:
    result = run_risk_stress_test(
        _candles(),
        config=RiskStressConfig(
            demo_forward=_config(),
            scenarios=(
                RiskStressScenario("baseline"),
                RiskStressScenario("wide_spread", spread_multiplier=2.0, additional_spread_pips=0.5),
                RiskStressScenario("high_slippage", slippage_multiplier=3.0),
            ),
            max_allowed_drawdown_percent=None,
        ),
    )
    frame = result.to_frame()

    assert result.ok
    assert result.summary() == "risk_stress_ok:ok=3"
    assert list(frame["scenario"]) == ["baseline", "wide_spread", "high_slippage"]
    assert frame.loc[0, "net_pnl_delta"] == 0
    assert frame.loc[1, "spread_multiplier"] == 2.0
    assert {"net_pnl", "final_equity", "return_delta", "max_drawdown_percent"}.issubset(frame.columns)


def test_risk_stress_report_bundle_writes_scenario_artifacts(tmp_path) -> None:
    result = run_risk_stress_test(
        _candles(),
        config=RiskStressConfig(
            demo_forward=_config(),
            scenarios=(RiskStressScenario("baseline"), RiskStressScenario("half_risk", risk_percent_multiplier=0.5)),
            max_allowed_drawdown_percent=None,
        ),
    )
    saved = write_risk_stress_report_bundle(result, tmp_path / "stress")

    assert saved.artifacts is not None
    assert saved.artifacts.summary_json.exists()
    assert saved.artifacts.scenarios_csv.exists()
    assert saved.artifacts.html_report.exists()
    assert saved.scenarios[0].summary_json is not None
    assert saved.scenarios[0].summary_json.exists()
    assert saved.scenarios[0].html_report is not None
    assert saved.scenarios[0].html_report.exists()

    payload = json.loads(saved.artifacts.summary_json.read_text(encoding="utf-8"))
    html = saved.artifacts.html_report.read_text(encoding="utf-8")

    assert payload["summary"] == "risk_stress_ok:ok=2"
    assert payload["scenarios"][0]["scenario"] == "baseline"
    assert "Risk Stress Test" in html
    assert "Scenario Summary" in html


def test_risk_stress_records_failed_scenarios_without_crashing() -> None:
    result = run_risk_stress_test(
        _candles(20),
        config=RiskStressConfig(
            demo_forward=DemoForwardConfig(warmup_candles=20),
            scenarios=(RiskStressScenario("too_short"),),
            continue_on_failure=True,
        ),
    )

    assert not result.ok
    assert result.summary() == "risk_stress_failed:failed=1;warning=0;ok=0"
    assert result.scenarios[0].failed
    assert result.scenarios[0].exception_type == "ValueError"
    assert "not enough candles" in result.scenarios[0].message


def test_risk_stress_thresholds_can_warn() -> None:
    result = run_risk_stress_test(
        _candles(),
        config=RiskStressConfig(
            demo_forward=_config(),
            scenarios=(RiskStressScenario("baseline"),),
            min_final_equity=20_000,
        ),
    )

    assert not result.ok
    assert result.scenarios[0].warning
    assert result.scenarios[0].message == "final_equity_below_stress_minimum"


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
