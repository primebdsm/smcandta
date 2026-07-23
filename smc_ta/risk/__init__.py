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
]
