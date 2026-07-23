"""Evaluate portfolio risk for a proposed Forex order."""

from __future__ import annotations

from datetime import datetime, timezone

from smc_ta.broker import OrderRequest, Position
from smc_ta.risk import PortfolioRiskConfig, PortfolioRiskManager


def main() -> None:
    open_positions = [
        Position(
            position_id="demo_1",
            symbol="EURUSD",
            side="long",
            units=10_000,
            entry_price=1.1000,
            opened_at=datetime.now(timezone.utc),
        )
    ]
    proposed = OrderRequest(symbol="GBPUSD", side="buy", units=5_000)
    manager = PortfolioRiskManager(
        PortfolioRiskConfig(
            max_same_currency_direction_positions=1,
            max_currency_gross_exposure=15_000,
        )
    )
    decision = manager.evaluate_order(
        proposed,
        open_positions=open_positions,
        market_price=1.3000,
    )
    print(decision)


if __name__ == "__main__":
    main()
