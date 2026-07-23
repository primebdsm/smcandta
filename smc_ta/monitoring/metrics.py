"""Equity and strategy health metrics."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class HealthCheck:
    """Simple health status for a strategy run."""

    ok: bool
    messages: tuple[str, ...]


def max_drawdown(equity: pd.Series) -> float:
    """Return max drawdown as a negative percentage."""

    if equity.empty:
        return 0.0
    running_high = equity.cummax()
    drawdown = equity / running_high - 1.0
    return float(drawdown.min() * 100.0)


def performance_summary(equity_curve: pd.DataFrame, trades: pd.DataFrame | None = None) -> dict[str, float]:
    """Return basic backtest/live-monitoring metrics."""

    if "equity" not in equity_curve.columns or equity_curve.empty:
        raise ValueError("equity_curve must contain an equity column")
    equity = equity_curve["equity"]
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0 if equity.iloc[0] else 0.0
    summary = {
        "start_equity": float(equity.iloc[0]),
        "end_equity": float(equity.iloc[-1]),
        "total_return_percent": float(total_return),
        "max_drawdown_percent": max_drawdown(equity),
    }
    if trades is not None and not trades.empty and "realized_pnl" in trades.columns:
        pnl = trades["realized_pnl"].fillna(0.0)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        summary.update(
            {
                "trades": float(len(pnl)),
                "win_rate_percent": float((len(wins) / len(pnl)) * 100.0) if len(pnl) else 0.0,
                "profit_factor": float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else float("inf"),
                "net_pnl": float(pnl.sum()),
            }
        )
    return summary


def health_check(
    equity_curve: pd.DataFrame,
    *,
    max_allowed_drawdown_percent: float = 10.0,
    min_equity: float = 0.0,
) -> HealthCheck:
    """Return a basic strategy health check."""

    messages: list[str] = []
    if equity_curve.empty or "equity" not in equity_curve.columns:
        return HealthCheck(ok=False, messages=("missing_equity_curve",))
    current_equity = float(equity_curve["equity"].iloc[-1])
    if current_equity <= min_equity:
        messages.append("equity_below_minimum")
    drawdown = abs(max_drawdown(equity_curve["equity"]))
    if drawdown >= max_allowed_drawdown_percent:
        messages.append("drawdown_limit_reached")
    return HealthCheck(ok=not messages, messages=tuple(messages or ["ok"]))

