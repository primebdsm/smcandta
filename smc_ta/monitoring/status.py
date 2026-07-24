"""Operational status probes for brokers and alert channels."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

import pandas as pd

from smc_ta.alerts import AlertChannel
from smc_ta.broker.base import BrokerAdapter

OperationalStatus = Literal["ok", "warning", "blocking"]


@dataclass(frozen=True)
class BrokerConnectivityStatus:
    """Read-only broker connectivity probe result."""

    broker_name: str
    status: OperationalStatus
    checked_at: pd.Timestamp
    latency_ms: float
    account_ok: bool = False
    positions_ok: bool = False
    transactions_ok: bool | None = None
    pending_orders_ok: bool | None = None
    symbol: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def blocking(self) -> bool:
        return self.status == "blocking"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["checked_at"] = _utc_timestamp(self.checked_at).isoformat()
        return record


@dataclass(frozen=True)
class AlertDeliveryStatus:
    """Alert channel delivery probe result."""

    channel_name: str
    status: OperationalStatus
    checked_at: pd.Timestamp
    latency_ms: float
    delivered: bool = False
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def blocking(self) -> bool:
        return self.status == "blocking"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["checked_at"] = _utc_timestamp(self.checked_at).isoformat()
        return record


def check_broker_connectivity(
    broker: BrokerAdapter,
    *,
    broker_name: str = "broker",
    symbol: str | None = None,
    include_transactions: bool = False,
    include_pending_orders: bool = False,
    timestamp: object | None = None,
) -> BrokerConnectivityStatus:
    """Probe broker read APIs and return dashboard-ready connectivity status."""

    started = time.perf_counter()
    checked_at = _utc_timestamp(timestamp)
    details: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    account_ok = False
    positions_ok = False
    transactions_ok: bool | None = None
    pending_orders_ok: bool | None = None

    try:
        account = broker.get_account()
        account_ok = True
        details["account_currency"] = getattr(account, "currency", None)
        details["equity"] = getattr(account, "equity", None)
        details["free_margin"] = getattr(account, "free_margin", None)
    except Exception as exc:
        errors.append(f"account_probe_failed:{type(exc).__name__}:{exc}")

    try:
        positions = broker.get_open_positions(symbol)
        positions_ok = True
        details["open_positions"] = len(positions)
    except Exception as exc:
        errors.append(f"positions_probe_failed:{type(exc).__name__}:{exc}")

    if include_transactions:
        transactions_ok = _probe_optional_method(
            broker,
            "get_latest_transaction_id",
            details=details,
            warnings=warnings,
        )

    if include_pending_orders:
        pending_orders_ok = _probe_pending_orders(
            broker,
            symbol=symbol,
            details=details,
            warnings=warnings,
        )

    latency_ms = (time.perf_counter() - started) * 1000.0
    if errors:
        status: OperationalStatus = "blocking"
        message = ";".join(errors)
    elif warnings:
        status = "warning"
        message = ";".join(warnings)
    else:
        status = "ok"
        message = "broker_connectivity_ok"

    return BrokerConnectivityStatus(
        broker_name=broker_name,
        status=status,
        checked_at=checked_at,
        latency_ms=latency_ms,
        account_ok=account_ok,
        positions_ok=positions_ok,
        transactions_ok=transactions_ok,
        pending_orders_ok=pending_orders_ok,
        symbol=symbol.upper() if symbol else None,
        message=message,
        details=details,
    )


def probe_alert_channel(
    channel: AlertChannel,
    *,
    channel_name: str | None = None,
    message: str = "SMC TA alert delivery probe",
    blocking_on_failure: bool = False,
    timestamp: object | None = None,
) -> AlertDeliveryStatus:
    """Send an explicit alert probe and return dashboard-ready delivery status."""

    checked_at = _utc_timestamp(timestamp)
    started = time.perf_counter()
    resolved_name = channel_name or type(channel).__name__
    try:
        channel.send(message)
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return AlertDeliveryStatus(
            channel_name=resolved_name,
            status="blocking" if blocking_on_failure else "warning",
            checked_at=checked_at,
            latency_ms=latency_ms,
            delivered=False,
            message=f"alert_delivery_failed:{type(exc).__name__}:{exc}",
            details={"exception_type": type(exc).__name__},
        )
    latency_ms = (time.perf_counter() - started) * 1000.0
    return AlertDeliveryStatus(
        channel_name=resolved_name,
        status="ok",
        checked_at=checked_at,
        latency_ms=latency_ms,
        delivered=True,
        message="alert_delivery_ok",
    )


def broker_connectivity_frame(statuses: Iterable[BrokerConnectivityStatus]) -> pd.DataFrame:
    """Return broker connectivity statuses as a DataFrame."""

    return pd.DataFrame([status.to_dict() for status in statuses])


def alert_delivery_frame(statuses: Iterable[AlertDeliveryStatus]) -> pd.DataFrame:
    """Return alert delivery statuses as a DataFrame."""

    return pd.DataFrame([status.to_dict() for status in statuses])


def _probe_optional_method(
    broker: BrokerAdapter,
    method_name: str,
    *,
    details: dict[str, Any],
    warnings: list[str],
) -> bool:
    method = getattr(broker, method_name, None)
    if not callable(method):
        warnings.append(f"{method_name}_not_supported")
        return False
    try:
        details[method_name] = method()
    except Exception as exc:
        warnings.append(f"{method_name}_failed:{type(exc).__name__}:{exc}")
        return False
    return True


def _probe_pending_orders(
    broker: BrokerAdapter,
    *,
    symbol: str | None,
    details: dict[str, Any],
    warnings: list[str],
) -> bool:
    method = getattr(broker, "get_pending_orders", None)
    if not callable(method):
        warnings.append("get_pending_orders_not_supported")
        return False
    try:
        orders = method(symbol=symbol)
        details["pending_orders"] = len(orders)
    except TypeError:
        try:
            orders = method()
            details["pending_orders"] = len(orders)
        except Exception as exc:
            warnings.append(f"get_pending_orders_failed:{type(exc).__name__}:{exc}")
            return False
    except Exception as exc:
        warnings.append(f"get_pending_orders_failed:{type(exc).__name__}:{exc}")
        return False
    return True


def _utc_timestamp(value: object | None = None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
