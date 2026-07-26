"""Risk and position management."""

from smc_ta.risk.manager import RiskConfig, RiskDecision, RiskManager
from smc_ta.risk.portfolio import (
    PortfolioRiskConfig,
    PortfolioRiskDecision,
    PortfolioRiskManager,
    aggregate_currency_gross_exposure,
    aggregate_currency_exposure,
    compute_return_correlations,
    currency_direction_counts,
    order_currency_exposure,
    position_currency_exposure,
)

_STRESS_EXPORTS = {
    "DEFAULT_RISK_STRESS_SCENARIOS",
    "RiskStressConfig",
    "RiskStressReportBundle",
    "RiskStressResult",
    "RiskStressScenario",
    "RiskStressScenarioResult",
    "RiskStressStatus",
    "render_risk_stress_report_html",
    "run_risk_stress_test",
    "write_risk_stress_report_bundle",
}

__all__ = [
    "PortfolioRiskConfig",
    "PortfolioRiskDecision",
    "PortfolioRiskManager",
    "RiskConfig",
    "RiskDecision",
    "RiskManager",
    "aggregate_currency_gross_exposure",
    "aggregate_currency_exposure",
    "compute_return_correlations",
    "currency_direction_counts",
    "order_currency_exposure",
    "position_currency_exposure",
] + sorted(_STRESS_EXPORTS)


def __getattr__(name: str):
    if name in _STRESS_EXPORTS:
        from smc_ta.risk import stress

        return getattr(stress, name)
    raise AttributeError(f"module 'smc_ta.risk' has no attribute {name!r}")
