"""OANDA practice-account execution validation workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

import pandas as pd

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, OrderSide, Position
from smc_ta.broker.oanda import (
    OandaApiError,
    OandaBroker,
    OandaInstrumentSpec,
    OandaOrderRejected,
    OandaPriceSnapshot,
)
from smc_ta.reconciliation import BrokerReconciler, MemoryPositionLedger, SQLitePositionLedger
from smc_ta.reconciliation.ledger import PositionLedger
from smc_ta.reconciliation.models import ReconciliationResult


@dataclass(frozen=True)
class OandaExecutionValidationConfig:
    """Controls OANDA practice execution validation."""

    symbol: str = "EURUSD"
    side: OrderSide = "buy"
    units: float | None = None
    stop_loss_pips: float = 20.0
    take_profit_pips: float = 20.0
    ledger_path: str | Path | None = None
    run_rejected_order: bool = True
    allow_existing_positions: bool = False
    close_positions_on_error: bool = True
    client_order_prefix: str = "smc-ta-oanda-validation"


@dataclass(frozen=True)
class OandaExecutionValidationCheck:
    """One execution validation check."""

    component: str
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class OandaExecutionSample:
    """One order/close execution sample for spread and slippage review."""

    label: str
    side: str
    units: float
    reference_price: float
    fill_price: float
    spread: float
    spread_pips: float
    slippage: float
    slippage_pips: float
    commission: float
    order_id: str
    position_id: str | None = None


@dataclass(frozen=True)
class OandaExecutionValidationReport:
    """Result of an OANDA practice execution validation run."""

    executed: bool
    checks: tuple[OandaExecutionValidationCheck, ...]
    account_before: AccountState | None = None
    account_after: AccountState | None = None
    instrument: OandaInstrumentSpec | None = None
    price_before: OandaPriceSnapshot | None = None
    price_after: OandaPriceSnapshot | None = None
    samples: tuple[OandaExecutionSample, ...] = ()
    reconciliation_result: ReconciliationResult | None = None

    @property
    def ok(self) -> bool:
        return not any(check.blocking for check in self.checks)

    def summary(self) -> str:
        if self.ok:
            warnings = [f"warning:{check.code}" for check in self.checks if check.severity == "warning"]
            if warnings:
                return ";".join(warnings)
            return "oanda_execution_validation_ok" if self.executed else "oanda_execution_dry_run_ready"
        return ";".join(check.code for check in self.checks if check.blocking)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(check) for check in self.checks])

    def execution_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(sample) for sample in self.samples])


class OandaPracticeExecutionValidator:
    """Validate OANDA practice execution with minimum-size trades."""

    def __init__(self, broker: OandaBroker, config: OandaExecutionValidationConfig | None = None) -> None:
        self.broker = broker
        self.config = config or OandaExecutionValidationConfig()

    def run(self, *, execute: bool = False) -> OandaExecutionValidationReport:
        checks: list[OandaExecutionValidationCheck] = []
        samples: list[OandaExecutionSample] = []
        opened_positions: list[Position] = []
        reconciliation_result: ReconciliationResult | None = None
        account_before: AccountState | None = None
        account_after: AccountState | None = None
        instrument: OandaInstrumentSpec | None = None
        price_before: OandaPriceSnapshot | None = None
        price_after: OandaPriceSnapshot | None = None

        symbol = self.config.symbol.upper()

        if not self.broker.config.practice:
            checks.append(
                OandaExecutionValidationCheck(
                    "config",
                    "not_practice_endpoint",
                    "blocking",
                    "execution validation must use OANDA practice mode",
                )
            )
            return self._report(False, checks)

        try:
            account_before = self.broker.get_account()
            checks.append(
                OandaExecutionValidationCheck(
                    "account",
                    "account_before_ok",
                    "info",
                    "loaded OANDA account before validation",
                    {"equity": account_before.equity, "free_margin": account_before.free_margin},
                )
            )
            instrument = self.broker.get_instrument_spec(symbol)
            units = self._validation_units(instrument)
            instrument.validate_units(units)
            price_before = self.broker.get_price(symbol)
            checks.append(
                OandaExecutionValidationCheck(
                    "plan",
                    "execution_plan_ok",
                    "info",
                    "built minimum-unit OANDA practice validation plan",
                    {
                        "symbol": symbol,
                        "instrument": instrument.name,
                        "units": units,
                        "side": self.config.side,
                        "spread_pips": price_before.spread_pips,
                    },
                )
            )
            existing_positions = self.broker.get_open_positions(symbol)
            if existing_positions and not self.config.allow_existing_positions:
                checks.append(
                    OandaExecutionValidationCheck(
                        "broker",
                        "existing_positions_block_execution",
                        "blocking",
                        "symbol has existing OANDA positions; validation refuses to disturb them",
                        {"positions": len(existing_positions), "symbol": symbol},
                    )
                )
                return self._report(
                    False,
                    checks,
                    account_before=account_before,
                    instrument=instrument,
                    price_before=price_before,
                )

            if not execute:
                stop_loss, take_profit = self._protective_prices(price_before.execution_price(self.config.side), instrument)
                checks.append(
                    OandaExecutionValidationCheck(
                        "execution",
                        "dry_run_execution_not_requested",
                        "warning",
                        "no practice orders were placed; pass execute=True or CLI --execute to run order tests",
                        {"planned_stop_loss": stop_loss, "planned_take_profit": take_profit},
                    )
                )
                return self._report(
                    False,
                    checks,
                    account_before=account_before,
                    instrument=instrument,
                    price_before=price_before,
                )

            min_position, min_samples = self._place_find_and_close(
                label="minimum_unit_order",
                units=units,
                instrument=instrument,
                stop_loss=None,
                take_profit=None,
                opened_positions=opened_positions,
                checks=checks,
            )
            samples.extend(min_samples)
            checks.append(
                OandaExecutionValidationCheck(
                    "execution",
                    "minimum_unit_order_closed",
                    "info",
                    "minimum-unit practice order opened and closed",
                    {"position_id": min_position.position_id},
                )
            )

            price_for_sltp = self.broker.get_price(symbol)
            stop_loss, take_profit = self._protective_prices(price_for_sltp.execution_price(self.config.side), instrument)
            sltp_position, sltp_samples = self._place_find_and_close(
                label="sl_tp_order",
                units=units,
                instrument=instrument,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_positions=opened_positions,
                checks=checks,
                reconcile_before_close=True,
            )
            samples.extend(sltp_samples)
            checks.append(
                OandaExecutionValidationCheck(
                    "execution",
                    "sl_tp_order_closed",
                    "info",
                    "SL/TP practice order opened, reconciled, and closed",
                    {"position_id": sltp_position.position_id, "stop_loss": stop_loss, "take_profit": take_profit},
                )
            )

            if self.config.run_rejected_order:
                checks.append(self._rejected_order_probe(instrument))
            else:
                checks.append(
                    OandaExecutionValidationCheck(
                        "rejection",
                        "rejected_order_skipped",
                        "warning",
                        "intentional rejected-order probe was skipped",
                    )
                )

            ledger = self._ledger()
            reconciliation_result = BrokerReconciler(ledger).reconcile_broker(self.broker, symbol)
            checks.append(_reconciliation_check(reconciliation_result, code="final_reconciliation_ok"))
            price_after = self.broker.get_price(symbol)
            account_after = self.broker.get_account()
            checks.append(
                OandaExecutionValidationCheck(
                    "report",
                    "spread_slippage_report_ready",
                    "info",
                    "spread/slippage samples are available",
                    {"samples": len(samples)},
                )
            )
        except Exception as exc:
            checks.append(
                OandaExecutionValidationCheck(
                    "execution",
                    "execution_validation_failed",
                    "blocking",
                    str(exc),
                    {"exception_type": type(exc).__name__},
                )
            )
        finally:
            if execute and self.config.close_positions_on_error:
                self._cleanup_positions(opened_positions, checks)

        return self._report(
            execute,
            checks,
            account_before=account_before,
            account_after=account_after,
            instrument=instrument,
            price_before=price_before,
            price_after=price_after,
            samples=samples,
            reconciliation_result=reconciliation_result,
        )

    def _place_find_and_close(
        self,
        *,
        label: str,
        units: float,
        instrument: OandaInstrumentSpec,
        stop_loss: float | None,
        take_profit: float | None,
        opened_positions: list[Position],
        checks: list[OandaExecutionValidationCheck],
        reconcile_before_close: bool = False,
    ) -> tuple[Position, list[OandaExecutionSample]]:
        symbol = self.config.symbol.upper()
        before_ids = {position.position_id for position in self.broker.get_open_positions(symbol)}
        reference_price = self.broker.get_price(symbol).execution_price(self.config.side)
        fill = self.broker.place_order(
            OrderRequest(
                symbol=symbol,
                side=self.config.side,
                units=units,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=self._client_order_id(label),
            ),
            market_price=reference_price,
        )
        position = self._find_opened_position(fill, before_ids)
        opened_positions.append(position)
        open_sample = self._sample(label, fill, reference_price, instrument, position.position_id)
        samples = [open_sample]
        checks.append(
            OandaExecutionValidationCheck(
                "execution",
                f"{label}_opened",
                "info",
                f"{label} opened in OANDA practice account",
                {"position_id": position.position_id, "units": position.units, "price": fill.price},
            )
        )

        ledger = self._ledger()
        ledger.record_open_position(position)
        restarted_ledger = self._restarted_ledger(ledger)
        pre_close_reconciliation = BrokerReconciler(restarted_ledger).reconcile_broker(self.broker, symbol)
        if reconcile_before_close:
            checks.append(_reconciliation_check(pre_close_reconciliation, code="restart_reconciliation_ok"))

        close_reference = self.broker.get_price(symbol).execution_price("sell" if self.config.side == "buy" else "buy")
        close_fill = self.broker.close_position(position.position_id, market_price=close_reference)
        restarted_ledger.record_closed_position(
            position.position_id,
            exit_price=close_fill.price,
            closed_at=close_fill.timestamp,
        )
        opened_positions[:] = [item for item in opened_positions if item.position_id != position.position_id]
        samples.append(self._sample(f"{label}_close", close_fill, close_reference, instrument, position.position_id))
        post_close_reconciliation = BrokerReconciler(restarted_ledger).reconcile_broker(self.broker, symbol)
        checks.append(_reconciliation_check(post_close_reconciliation, code=f"{label}_post_close_reconciliation_ok"))
        return position, samples

    def _find_opened_position(self, fill: OrderFill, before_ids: set[str]) -> Position:
        trade_id = fill.metadata.get("oanda_trade_opened_id")
        positions = self.broker.get_open_positions(self.config.symbol)
        if trade_id:
            exact = [position for position in positions if position.position_id == trade_id]
            if exact:
                return exact[-1]
        new_positions = [position for position in positions if position.position_id not in before_ids]
        if new_positions:
            return new_positions[-1]
        matching = [
            position
            for position in positions
            if position.side == ("long" if fill.side == "buy" else "short")
            and abs(position.units - fill.units) <= 1e-6
        ]
        if matching:
            return matching[-1]
        raise RuntimeError("could not identify OANDA trade opened by validation order")

    def _rejected_order_probe(self, instrument: OandaInstrumentSpec) -> OandaExecutionValidationCheck:
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument.name,
                "units": "0",
                "timeInForce": self.broker.config.market_order_time_in_force,
                "positionFill": self.broker.config.position_fill,
                "clientExtensions": {"id": self._client_order_id("rejected_order_probe")},
            }
        }
        try:
            response = self.broker.client.request(
                "POST",
                f"/accounts/{self.broker.config.account_id}/orders",
                payload=payload,
            )
        except OandaOrderRejected as exc:
            return OandaExecutionValidationCheck(
                "rejection",
                "rejected_order_probe_ok",
                "info",
                "OANDA rejected the intentionally invalid order",
                {"error_code": exc.error_code, "error_message": exc.error_message},
            )
        except OandaApiError as exc:
            return OandaExecutionValidationCheck(
                "rejection",
                "rejected_order_probe_unclassified",
                "warning",
                "OANDA rejected the invalid order, but not as a classified order rejection",
                {"status_code": exc.status_code, "error_code": exc.error_code, "error_message": exc.error_message},
            )
        if "orderRejectTransaction" in response or "orderCancelTransaction" in response:
            return OandaExecutionValidationCheck(
                "rejection",
                "rejected_order_probe_ok",
                "info",
                "OANDA returned a rejection/cancel transaction for the invalid order",
                {"response_keys": tuple(response.keys())},
            )
        return OandaExecutionValidationCheck(
            "rejection",
            "rejected_order_probe_failed",
            "blocking",
            "intentional invalid order was not rejected",
            {"response_keys": tuple(response.keys())},
        )

    def _cleanup_positions(
        self,
        opened_positions: list[Position],
        checks: list[OandaExecutionValidationCheck],
    ) -> None:
        for position in list(opened_positions):
            try:
                side: OrderSide = "sell" if position.side == "long" else "buy"
                reference = self.broker.get_price(position.symbol).execution_price(side)
                self.broker.close_position(position.position_id, market_price=reference)
                opened_positions.remove(position)
                checks.append(
                    OandaExecutionValidationCheck(
                        "cleanup",
                        "cleanup_close_ok",
                        "info",
                        "closed validation position during cleanup",
                        {"position_id": position.position_id},
                    )
                )
            except Exception as exc:
                checks.append(
                    OandaExecutionValidationCheck(
                        "cleanup",
                        "cleanup_close_failed",
                        "blocking",
                        str(exc),
                        {"position_id": position.position_id, "exception_type": type(exc).__name__},
                    )
                )

    def _validation_units(self, instrument: OandaInstrumentSpec) -> float:
        units = instrument.minimum_trade_size if self.config.units is None else self.config.units
        return max(instrument.minimum_trade_size, float(units))

    def _protective_prices(self, reference_price: float, instrument: OandaInstrumentSpec) -> tuple[float, float]:
        stop_distance = self.config.stop_loss_pips * instrument.pip_size
        target_distance = self.config.take_profit_pips * instrument.pip_size
        if self.config.side == "buy":
            return reference_price - stop_distance, reference_price + target_distance
        return reference_price + stop_distance, reference_price - target_distance

    def _sample(
        self,
        label: str,
        fill: OrderFill,
        reference_price: float,
        instrument: OandaInstrumentSpec,
        position_id: str | None,
    ) -> OandaExecutionSample:
        return OandaExecutionSample(
            label=label,
            side=fill.side,
            units=fill.units,
            reference_price=reference_price,
            fill_price=fill.price,
            spread=fill.spread,
            spread_pips=fill.spread / instrument.pip_size if instrument.pip_size else 0.0,
            slippage=fill.slippage,
            slippage_pips=fill.slippage / instrument.pip_size if instrument.pip_size else 0.0,
            commission=fill.commission,
            order_id=fill.order_id,
            position_id=position_id,
        )

    def _ledger(self) -> PositionLedger:
        if self.config.ledger_path is None:
            return MemoryPositionLedger()
        return SQLitePositionLedger(self.config.ledger_path)

    def _restarted_ledger(self, ledger: PositionLedger) -> PositionLedger:
        if self.config.ledger_path is None:
            return ledger
        return SQLitePositionLedger(self.config.ledger_path)

    def _client_order_id(self, label: str) -> str:
        return f"{self.config.client_order_prefix}-{label}-{uuid4().hex[:8]}"[:128]

    @staticmethod
    def _report(
        executed: bool,
        checks: list[OandaExecutionValidationCheck],
        *,
        account_before: AccountState | None = None,
        account_after: AccountState | None = None,
        instrument: OandaInstrumentSpec | None = None,
        price_before: OandaPriceSnapshot | None = None,
        price_after: OandaPriceSnapshot | None = None,
        samples: list[OandaExecutionSample] | None = None,
        reconciliation_result: ReconciliationResult | None = None,
    ) -> OandaExecutionValidationReport:
        return OandaExecutionValidationReport(
            executed=executed,
            checks=tuple(checks),
            account_before=account_before,
            account_after=account_after,
            instrument=instrument,
            price_before=price_before,
            price_after=price_after,
            samples=tuple(samples or []),
            reconciliation_result=reconciliation_result,
        )


def run_oanda_practice_execution_validation(
    broker: OandaBroker,
    *,
    config: OandaExecutionValidationConfig | None = None,
    execute: bool = False,
) -> OandaExecutionValidationReport:
    """Run guarded OANDA practice execution validation."""

    return OandaPracticeExecutionValidator(broker, config).run(execute=execute)


def _reconciliation_check(result: ReconciliationResult, *, code: str) -> OandaExecutionValidationCheck:
    if result.ok:
        return OandaExecutionValidationCheck(
            "reconciliation",
            code,
            "info",
            "broker positions match expected validation ledger",
            {
                "broker_positions": len(result.broker_positions),
                "expected_positions": len(result.expected_positions),
            },
        )
    return OandaExecutionValidationCheck(
        "reconciliation",
        code.replace("_ok", "_failed"),
        "blocking",
        result.summary(),
        {"blocking_reasons": result.blocking_reasons},
    )
