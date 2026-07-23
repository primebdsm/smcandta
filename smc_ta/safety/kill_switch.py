"""Emergency stop / kill-switch controls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from smc_ta.broker.models import AccountState, Position, utc_now
from smc_ta.reconciliation.models import ReconciliationResult


@dataclass(frozen=True)
class EmergencyStopConfig:
    """Safety limits that can stop all new trading."""

    enabled: bool = True
    close_positions_on_trigger: bool = False
    manual_stop_file: str | Path | None = None
    min_equity: float | None = None
    max_daily_loss_percent: float | None = None
    max_total_drawdown_percent: float | None = None
    max_open_positions: int | None = None
    block_on_reconciliation_failure: bool = True
    max_runtime_errors: int | None = 1


@dataclass(frozen=True)
class EmergencyStopResult:
    """Emergency stop evaluation result."""

    active: bool
    reasons: tuple[str, ...] = ()
    close_positions: bool = False
    triggered_at: datetime | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.active

    def summary(self) -> str:
        return "emergency_stop_ok" if self.ok else ";".join(self.reasons)


class EmergencyStopController:
    """Stateful emergency stop controller.

    The controller latches once triggered. Call `reset()` only after the user
    has reviewed broker state, journal state, and the reason for the stop.
    """

    def __init__(self, config: EmergencyStopConfig | None = None) -> None:
        self.config = config or EmergencyStopConfig()
        self._manual_reasons: list[str] = []
        self._runtime_errors: list[str] = []
        self._latched_result: EmergencyStopResult | None = None
        self._day_start_equity: dict[pd.Timestamp, float] = {}
        self._peak_equity: float | None = None

    @property
    def active(self) -> bool:
        return self._latched_result is not None and self._latched_result.active

    def activate(self, reason: str = "manual_emergency_stop") -> EmergencyStopResult:
        """Manually latch the emergency stop."""

        self._manual_reasons.append(reason)
        self._latched_result = EmergencyStopResult(
            active=True,
            reasons=tuple(dict.fromkeys(self._manual_reasons)),
            close_positions=self.config.close_positions_on_trigger,
            triggered_at=utc_now(),
        )
        return self._latched_result

    def reset(self) -> None:
        """Clear the latched emergency stop and runtime errors."""

        self._manual_reasons.clear()
        self._runtime_errors.clear()
        self._latched_result = None

    def record_runtime_error(self, error: BaseException | str) -> EmergencyStopResult | None:
        """Record an exception from the live loop and latch if threshold is met."""

        self._runtime_errors.append(str(error))
        if self.config.max_runtime_errors is not None and len(self._runtime_errors) >= self.config.max_runtime_errors:
            self._latched_result = EmergencyStopResult(
                active=True,
                reasons=("runtime_error_limit_reached",),
                close_positions=self.config.close_positions_on_trigger,
                triggered_at=utc_now(),
                details={"errors": tuple(self._runtime_errors)},
            )
            return self._latched_result
        return None

    def evaluate(
        self,
        *,
        account: AccountState,
        open_positions: list[Position],
        timestamp: pd.Timestamp,
        reconciliation_result: ReconciliationResult | None = None,
    ) -> EmergencyStopResult:
        """Evaluate current account/broker state against emergency limits."""

        if not self.config.enabled:
            return EmergencyStopResult(active=False)
        if self._latched_result is not None:
            return self._latched_result

        now = pd.Timestamp(timestamp)
        reasons: list[str] = []
        details: dict[str, object] = {}

        manual_file = Path(self.config.manual_stop_file).expanduser() if self.config.manual_stop_file else None
        if manual_file is not None and manual_file.exists():
            reasons.append("manual_stop_file_present")
            details["manual_stop_file"] = str(manual_file)

        if self._manual_reasons:
            reasons.extend(self._manual_reasons)

        if self.config.min_equity is not None and account.equity <= self.config.min_equity:
            reasons.append("min_equity_reached")
            details["equity"] = account.equity
            details["min_equity"] = self.config.min_equity

        day = now.normalize()
        self._day_start_equity.setdefault(day, account.equity)
        day_start = self._day_start_equity[day]
        if self.config.max_daily_loss_percent is not None and day_start > 0:
            daily_loss_percent = max(0.0, (day_start - account.equity) / day_start * 100.0)
            details["daily_loss_percent"] = daily_loss_percent
            if daily_loss_percent >= self.config.max_daily_loss_percent:
                reasons.append("max_daily_loss_reached")

        if self._peak_equity is None:
            self._peak_equity = account.equity
        self._peak_equity = max(self._peak_equity, account.equity)
        if self.config.max_total_drawdown_percent is not None and self._peak_equity > 0:
            total_drawdown_percent = max(0.0, (self._peak_equity - account.equity) / self._peak_equity * 100.0)
            details["total_drawdown_percent"] = total_drawdown_percent
            if total_drawdown_percent >= self.config.max_total_drawdown_percent:
                reasons.append("max_total_drawdown_reached")

        if self.config.max_open_positions is not None and len(open_positions) >= self.config.max_open_positions:
            reasons.append("max_open_positions_reached")
            details["open_positions"] = len(open_positions)
            details["max_open_positions"] = self.config.max_open_positions

        if (
            self.config.block_on_reconciliation_failure
            and reconciliation_result is not None
            and not reconciliation_result.ok
        ):
            reasons.append("reconciliation_failed")
            details["reconciliation_reasons"] = reconciliation_result.blocking_reasons

        if self.config.max_runtime_errors is not None and len(self._runtime_errors) >= self.config.max_runtime_errors:
            reasons.append("runtime_error_limit_reached")
            details["errors"] = tuple(self._runtime_errors)

        if not reasons:
            return EmergencyStopResult(active=False, details=details)

        self._latched_result = EmergencyStopResult(
            active=True,
            reasons=tuple(dict.fromkeys(reasons)),
            close_positions=self.config.close_positions_on_trigger,
            triggered_at=utc_now(),
            details=details,
        )
        return self._latched_result

