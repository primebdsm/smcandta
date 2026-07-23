"""Broker reconciliation service."""

from __future__ import annotations

from collections import Counter

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import Position
from smc_ta.reconciliation.ledger import PositionLedger
from smc_ta.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationIssue,
    ReconciliationResult,
)


class BrokerReconciler:
    """Compare broker positions with expected bot ledger state."""

    def __init__(
        self,
        ledger: PositionLedger | None = None,
        config: ReconciliationConfig | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config or ReconciliationConfig()

    def reconcile_broker(self, broker: BrokerAdapter, symbol: str | None = None) -> ReconciliationResult:
        """Fetch broker and ledger positions, then reconcile them."""

        broker_positions = broker.get_open_positions(symbol)
        expected_positions = self.ledger.open_positions(symbol) if self.ledger else []
        return self.reconcile(broker_positions, expected_positions)

    def reconcile(
        self,
        broker_positions: list[Position],
        expected_positions: list[Position],
    ) -> ReconciliationResult:
        """Compare two open-position snapshots."""

        issues: list[ReconciliationIssue] = []
        broker_by_id = {position.position_id: position for position in broker_positions}
        expected_by_id = {position.position_id: position for position in expected_positions}

        for broker_position in broker_positions:
            expected = expected_by_id.get(broker_position.position_id)
            if expected is None:
                issues.append(
                    self._issue(
                        "unmanaged_broker_position",
                        self.config.block_on_unmanaged_broker_positions,
                        f"broker has open position {broker_position.position_id} not found in expected ledger",
                        broker_position=broker_position,
                    )
                )
                continue
            issues.extend(self._compare_position(expected, broker_position))

        for expected in expected_positions:
            if expected.position_id not in broker_by_id:
                issues.append(
                    self._issue(
                        "missing_broker_position",
                        self.config.block_on_missing_broker_positions,
                        f"expected position {expected.position_id} is not open at broker",
                        expected_position=expected,
                    )
                )

        issues.extend(self._duplicate_issues(broker_positions, source="broker"))
        issues.extend(self._duplicate_issues(expected_positions, source="expected"))
        return ReconciliationResult(
            broker_positions=tuple(broker_positions),
            expected_positions=tuple(expected_positions),
            issues=tuple(issues),
        )

    def record_expected_position(self, position: Position) -> None:
        """Record a broker position as expected after the bot opens it."""

        if self.ledger is None:
            return
        self.ledger.record_open_position(position)

    def record_closed_position(self, position_id: str, *, exit_price: float | None = None, closed_at=None) -> None:
        """Mark an expected position closed."""

        if self.ledger is None:
            return
        self.ledger.record_closed_position(position_id, exit_price=exit_price, closed_at=closed_at)

    def _compare_position(self, expected: Position, broker_position: Position) -> list[ReconciliationIssue]:
        issues: list[ReconciliationIssue] = []
        if expected.symbol != broker_position.symbol:
            issues.append(
                self._issue(
                    "symbol_mismatch",
                    self.config.block_on_position_mismatch,
                    f"expected {expected.symbol}, broker has {broker_position.symbol}",
                    broker_position=broker_position,
                    expected_position=expected,
                )
            )
        if expected.side != broker_position.side:
            issues.append(
                self._issue(
                    "side_mismatch",
                    self.config.block_on_position_mismatch,
                    f"expected {expected.side}, broker has {broker_position.side}",
                    broker_position=broker_position,
                    expected_position=expected,
                )
            )
        if abs(expected.units - broker_position.units) > self.config.unit_tolerance:
            issues.append(
                self._issue(
                    "units_mismatch",
                    self.config.block_on_position_mismatch,
                    f"expected {expected.units}, broker has {broker_position.units}",
                    broker_position=broker_position,
                    expected_position=expected,
                    details={"expected_units": expected.units, "broker_units": broker_position.units},
                )
            )
        if abs(expected.entry_price - broker_position.entry_price) > self.config.price_tolerance:
            issues.append(
                self._issue(
                    "entry_price_mismatch",
                    self.config.block_on_position_mismatch,
                    f"expected {expected.entry_price}, broker has {broker_position.entry_price}",
                    broker_position=broker_position,
                    expected_position=expected,
                    details={"expected_entry": expected.entry_price, "broker_entry": broker_position.entry_price},
                )
            )
        return issues

    def _duplicate_issues(self, positions: list[Position], *, source: str) -> list[ReconciliationIssue]:
        if not self.config.block_on_duplicate_symbol_side:
            return []
        counts = Counter((position.symbol, position.side) for position in positions)
        issues: list[ReconciliationIssue] = []
        for (symbol, side), count in counts.items():
            if count > self.config.max_positions_per_symbol_side:
                issues.append(
                    ReconciliationIssue(
                        kind=f"duplicate_{source}_positions",
                        severity="blocking",
                        message=f"{source} has {count} open {symbol} {side} positions",
                        symbol=symbol,
                        details={"side": side, "count": count},
                    )
                )
        return issues

    @staticmethod
    def _issue(
        kind: str,
        blocking: bool,
        message: str,
        *,
        broker_position: Position | None = None,
        expected_position: Position | None = None,
        details: dict[str, object] | None = None,
    ) -> ReconciliationIssue:
        return ReconciliationIssue(
            kind=kind,
            severity="blocking" if blocking else "warning",
            message=message,
            symbol=(broker_position or expected_position).symbol if (broker_position or expected_position) else None,
            broker_position_id=broker_position.position_id if broker_position else None,
            expected_position_id=expected_position.position_id if expected_position else None,
            details=details or {},
        )

