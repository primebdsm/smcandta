"""Reconciliation data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from smc_ta.broker.models import Position, utc_now

IssueSeverity = Literal["info", "warning", "blocking"]


@dataclass(frozen=True)
class ReconciliationConfig:
    """Controls how strict broker-state reconciliation should be."""

    unit_tolerance: float = 1e-6
    price_tolerance: float = 1e-6
    max_positions_per_symbol_side: int = 1
    block_on_unmanaged_broker_positions: bool = True
    block_on_missing_broker_positions: bool = True
    block_on_position_mismatch: bool = True
    block_on_duplicate_symbol_side: bool = True


@dataclass(frozen=True)
class ReconciliationIssue:
    """One broker-vs-ledger state difference."""

    kind: str
    severity: IssueSeverity
    message: str
    symbol: str | None = None
    broker_position_id: str | None = None
    expected_position_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class ReconciliationResult:
    """Full reconciliation result."""

    broker_positions: tuple[Position, ...]
    expected_positions: tuple[Position, ...]
    issues: tuple[ReconciliationIssue, ...]
    checked_at: datetime = field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(issue.kind for issue in self.issues if issue.blocking)

    def summary(self) -> str:
        if self.ok:
            return "reconciliation_ok"
        return ";".join(f"{issue.kind}:{issue.message}" for issue in self.issues if issue.blocking)

