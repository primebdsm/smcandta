"""Portfolio and signal risk manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from smc_ta.broker.models import AccountState, OrderRequest, Position
from smc_ta.forex.risk import position_size_units

DecisionStatus = Literal["approved", "blocked"]


@dataclass(frozen=True)
class RiskConfig:
    """Risk limits for converting analysis signals into orders."""

    risk_percent_per_trade: float = 1.0
    max_daily_loss_percent: float = 3.0
    max_open_positions: int = 3
    max_symbol_positions: int = 1
    min_confidence: float = 0.55
    min_reward_to_risk: float = 1.5
    max_units: float | None = None


@dataclass(frozen=True)
class RiskDecision:
    """Risk-manager decision."""

    status: DecisionStatus
    reasons: tuple[str, ...]
    order: OrderRequest | None = None
    units: float = 0.0

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class RiskManager:
    """Apply account, exposure, and signal-quality limits."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._day_start_equity: dict[date, float] = {}

    def _daily_drawdown_percent(self, account: AccountState, now: pd.Timestamp) -> float:
        day = now.date()
        self._day_start_equity.setdefault(day, account.equity)
        start_equity = self._day_start_equity[day]
        if start_equity <= 0:
            return 100.0
        return max(0.0, (start_equity - account.equity) / start_equity * 100.0)

    def evaluate_signal(
        self,
        signal: pd.Series,
        *,
        symbol: str,
        account: AccountState,
        open_positions: list[Position],
        timestamp: pd.Timestamp,
    ) -> RiskDecision:
        """Return an order request if the latest analysis signal passes risk limits."""

        reasons: list[str] = []
        side = signal.get("side", "flat")
        if side not in {"long", "short"}:
            reasons.append("signal_is_flat")

        confidence = float(signal.get("confidence", 0.0) or 0.0)
        if confidence < self.config.min_confidence:
            reasons.append("confidence_below_minimum")

        rr = signal.get("reference_rr")
        if pd.isna(rr) or float(rr) < self.config.min_reward_to_risk:
            reasons.append("reward_to_risk_below_minimum")

        entry = signal.get("entry_reference")
        stop = signal.get("stop_reference")
        if pd.isna(entry) or pd.isna(stop) or float(entry) == float(stop):
            reasons.append("missing_entry_or_stop")

        if len(open_positions) >= self.config.max_open_positions:
            reasons.append("max_open_positions_reached")
        symbol_positions = [position for position in open_positions if position.symbol == symbol.upper()]
        if len(symbol_positions) >= self.config.max_symbol_positions:
            reasons.append("max_symbol_positions_reached")

        drawdown = self._daily_drawdown_percent(account, pd.Timestamp(timestamp))
        if drawdown >= self.config.max_daily_loss_percent:
            reasons.append("daily_loss_limit_reached")

        if reasons:
            return RiskDecision(status="blocked", reasons=tuple(reasons))

        units = position_size_units(
            account_equity=account.equity,
            risk_percent=self.config.risk_percent_per_trade,
            entry=float(entry),
            stop=float(stop),
            symbol=symbol,
            account_currency=account.currency,
        )
        if self.config.max_units is not None:
            units = min(units, self.config.max_units)
        if units <= 0:
            return RiskDecision(status="blocked", reasons=("position_size_is_zero",))

        order_side = "buy" if side == "long" else "sell"
        order = OrderRequest(
            symbol=symbol.upper(),
            side=order_side,
            units=units,
            stop_loss=float(stop),
            take_profit=float(signal["target_reference"]),
            metadata={
                "confidence": confidence,
                "reasons": signal.get("reasons", ""),
                "reference_rr": float(rr),
            },
        )
        return RiskDecision(status="approved", reasons=("approved",), order=order, units=units)

