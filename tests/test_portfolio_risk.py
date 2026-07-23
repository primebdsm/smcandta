from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smc_ta.broker import OrderRequest, PaperBroker, Position
from smc_ta.live import DemoTradingBot
from smc_ta.risk import (
    PortfolioRiskConfig,
    PortfolioRiskManager,
    aggregate_currency_gross_exposure,
    RiskDecision,
    compute_return_correlations,
    currency_direction_counts,
    order_currency_exposure,
    position_currency_exposure,
)


def make_position(symbol: str, side: str, *, units: float = 10_000, entry: float = 1.1000) -> Position:
    return Position(
        position_id=f"{symbol}_{side}",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        units=units,
        entry_price=entry,
        opened_at=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )


def make_candles(n: int = 120, *, invert: bool = False) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    direction = -1 if invert else 1
    close = pd.Series(1.1000 + direction * np.sin(np.arange(n) / 6) * 0.001, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0002
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0002
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "tick_volume": 100}, index=index)


def test_currency_exposure_for_positions_and_orders() -> None:
    long_position = make_position("EURUSD", "long", units=10_000, entry=1.1000)
    short_order = OrderRequest(symbol="GBPUSD", side="sell", units=5_000)

    assert position_currency_exposure(long_position) == {"EUR": 10_000, "USD": -11_000}
    assert order_currency_exposure(short_order, 1.3000) == {"GBP": -5_000, "USD": 6_500}


def test_same_currency_direction_and_gross_exposure_blocks() -> None:
    manager = PortfolioRiskManager(
        PortfolioRiskConfig(
            max_same_currency_direction_positions=1,
            max_currency_gross_exposure=15_000,
        )
    )
    open_positions = [make_position("EURUSD", "long", units=10_000, entry=1.1000)]
    order = OrderRequest(symbol="GBPUSD", side="buy", units=5_000)
    decision = manager.evaluate_order(order, open_positions=open_positions, market_price=1.3000)

    assert not decision.approved
    assert "max_same_currency_direction_positions_reached" in decision.reasons
    assert "max_currency_gross_exposure_reached" in decision.reasons
    counts = currency_direction_counts(open_positions)
    assert counts["USD"]["short"] == 1

    offsetting = [
        make_position("EURUSD", "long", units=10_000, entry=1.1000),
        make_position("EURUSD", "short", units=10_000, entry=1.1000),
    ]
    assert aggregate_currency_gross_exposure(offsetting)["USD"] == 22_000


def test_correlation_matrix_blocks_correlated_positions() -> None:
    matrix = pd.DataFrame(
        [[1.0, 0.9], [0.9, 1.0]],
        index=["EURUSD", "GBPUSD"],
        columns=["EURUSD", "GBPUSD"],
    )
    manager = PortfolioRiskManager(
        PortfolioRiskConfig(max_correlated_positions=1, correlation_threshold=0.8),
        correlation_matrix=matrix,
    )
    decision = manager.evaluate_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000),
        open_positions=[make_position("GBPUSD", "long", units=1_000, entry=1.3000)],
        market_price=1.1000,
    )

    assert not decision.approved
    assert decision.correlated_symbols == ("GBPUSD",)
    assert "max_correlated_positions_reached" in decision.reasons


def test_compute_return_correlations() -> None:
    matrix = compute_return_correlations(
        {
            "EURUSD": make_candles(),
            "GBPUSD": make_candles(),
            "USDCHF": make_candles(invert=True),
        }
    )

    assert matrix.at["EURUSD", "GBPUSD"] == pytest.approx(1.0)
    assert matrix.at["EURUSD", "USDCHF"] < -0.99


def test_demo_bot_blocks_on_portfolio_risk() -> None:
    class ApprovedRiskManager:
        def evaluate_signal(self, *args, **kwargs):
            return RiskDecision(
                status="approved",
                reasons=("approved",),
                order=OrderRequest(symbol="EURUSD", side="buy", units=1_000, stop_loss=1.09, take_profit=1.12),
                units=1_000,
            )

    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=PaperBroker(initial_balance=10_000),
        risk_manager=ApprovedRiskManager(),  # type: ignore[arg-type]
        portfolio_risk_manager=PortfolioRiskManager(PortfolioRiskConfig(max_total_open_positions=0)),
    )
    result = bot.run_cycle(make_candles())

    assert result.action == "blocked_by_portfolio_risk"
    assert result.portfolio_risk_decision is not None
    assert "max_total_open_positions_reached" in result.portfolio_risk_decision.reasons
