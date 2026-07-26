from __future__ import annotations

from pathlib import Path

import pytest


WORKFLOW = Path(".github/workflows/ci.yml")


def _workflow_text() -> str:
    if not WORKFLOW.exists():
        pytest.skip("GitHub workflow is only available in a full repository checkout")
    return WORKFLOW.read_text(encoding="utf-8")


def test_github_ci_workflow_exists() -> None:
    assert _workflow_text()


def test_github_ci_runs_tests_import_smoke_and_package_build() -> None:
    text = _workflow_text()

    assert "python -m pytest" in text
    assert "python -m build" in text
    assert "python -m pip install -e \".[dev]\"" in text
    assert "TransactionReconciliationEvent" in text


def test_github_ci_does_not_require_live_broker_secrets() -> None:
    text = _workflow_text()

    forbidden = (
        "OANDA_TOKEN:",
        "OANDA_ACCOUNT_ID:",
        "SMC_TA_OANDA_TOKEN:",
        "SMC_TA_OANDA_ACCOUNT_ID:",
        "TRADING_ECONOMICS_API_KEY:",
    )
    assert not any(item in text for item in forbidden)
