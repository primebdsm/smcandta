"""Broker transaction stream normalization for monitoring dashboards."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

TRANSACTION_STREAM_COLUMNS = (
    "transaction_id",
    "timestamp",
    "type",
    "event_class",
    "lifecycle_hint",
    "symbol",
    "instrument",
    "order_id",
    "trade_id",
    "client_order_id",
    "side",
    "units",
    "price",
    "pl",
    "financing",
    "commission",
    "account_balance",
    "reason",
    "description",
)


def broker_transaction_stream_frame(
    transactions: Iterable[Mapping[str, Any]] | pd.DataFrame | None,
    *,
    symbol: str | None = None,
    tail: int | None = None,
) -> pd.DataFrame:
    """Normalize broker/OANDA transaction rows into a dashboard-ready table."""

    raw_frame = _coerce_transactions(transactions)
    if raw_frame.empty:
        return pd.DataFrame(columns=TRANSACTION_STREAM_COLUMNS)
    symbol_filter = symbol.upper() if symbol else None
    rows = []
    for record in raw_frame.to_dict(orient="records"):
        normalized = _normalize_transaction(record)
        if symbol_filter and normalized["symbol"] and normalized["symbol"] != symbol_filter:
            continue
        rows.append(normalized)
    frame = pd.DataFrame(rows, columns=TRANSACTION_STREAM_COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=TRANSACTION_STREAM_COLUMNS)
    frame = _sort_transactions(frame)
    return frame.tail(tail) if tail is not None and tail > 0 else frame


def _coerce_transactions(transactions: Iterable[Mapping[str, Any]] | pd.DataFrame | None) -> pd.DataFrame:
    if transactions is None:
        return pd.DataFrame()
    if isinstance(transactions, pd.DataFrame):
        return transactions.copy()
    return pd.DataFrame(list(transactions))


def _normalize_transaction(raw: Mapping[str, Any]) -> dict[str, Any]:
    tx_type = str(_first(raw, "type", "transactionType") or "")
    reason = _first(raw, "reason", "rejectReason", "cancellingTransactionID")
    instrument = _first(raw, "instrument")
    symbol = _symbol_from_instrument(instrument)
    units = _units(raw)
    event_class = _event_class(tx_type, raw)
    lifecycle_hint = _lifecycle_hint(event_class, tx_type, raw)
    return {
        "transaction_id": _optional_str(_first(raw, "id", "transactionID", "transaction_id")),
        "timestamp": _optional_timestamp(_first(raw, "time", "timestamp", "transactionTime")),
        "type": tx_type,
        "event_class": event_class,
        "lifecycle_hint": lifecycle_hint,
        "symbol": symbol,
        "instrument": _optional_str(instrument),
        "order_id": _optional_str(_first(raw, "orderID", "orderId", "order_id")),
        "trade_id": _trade_id(raw),
        "client_order_id": _client_order_id(raw),
        "side": _side(raw, units),
        "units": abs(units) if units is not None else None,
        "price": _optional_float(_first(raw, "price", "fullPrice")),
        "pl": _optional_float(_first(raw, "pl", "realizedPL", "profitLoss")),
        "financing": _optional_float(_first(raw, "financing", "financingPL")),
        "commission": _optional_float(_first(raw, "commission")),
        "account_balance": _optional_float(_first(raw, "accountBalance", "account_balance", "balance")),
        "reason": _optional_str(reason),
        "description": _description(tx_type, reason, raw),
    }


def _event_class(tx_type: str, raw: Mapping[str, Any]) -> str:
    upper = tx_type.upper()
    if upper == "ORDER_FILL":
        if _first_nested(raw, "tradesClosed") is not None:
            return "close"
        if _first_nested(raw, "tradeReduced") is not None:
            return "reduce"
        return "fill"
    if "FINANCING" in upper:
        return "financing"
    if "ORDER_CANCEL" in upper or "CANCEL" in upper:
        return "cancel"
    if "ORDER_REJECT" in upper or "REJECT" in upper:
        return "reject"
    if "ORDER" in upper:
        return "order"
    if "TRADE_CLOSE" in upper:
        return "close"
    if "TRANSFER" in upper or "FUND" in upper:
        return "funding"
    if "MARGIN" in upper:
        return "margin"
    return "account" if upper else "unknown"


def _lifecycle_hint(event_class: str, tx_type: str, raw: Mapping[str, Any]) -> str:
    if event_class == "fill":
        return "open" if _first_nested(raw, "tradeOpened") is not None else "fill"
    if event_class in {"close", "reduce", "cancel", "reject", "financing", "funding", "margin"}:
        return event_class
    if event_class == "order":
        return "order"
    return tx_type.lower() if tx_type else "unknown"


def _trade_id(raw: Mapping[str, Any]) -> str | None:
    direct = _optional_str(_first(raw, "tradeID", "tradeId", "trade_id"))
    if direct:
        return direct
    for key in ("tradeOpened", "tradeReduced"):
        nested = _as_mapping(raw.get(key))
        if nested:
            value = _optional_str(_first(nested, "tradeID", "tradeId", "id"))
            if value:
                return value
    closed = _first_nested(raw, "tradesClosed")
    if isinstance(closed, Mapping):
        return _optional_str(_first(closed, "tradeID", "tradeId", "id"))
    return None


def _client_order_id(raw: Mapping[str, Any]) -> str | None:
    direct = _optional_str(_first(raw, "clientOrderID", "client_order_id"))
    if direct:
        return direct
    extensions = _as_mapping(raw.get("clientExtensions"))
    return _optional_str(_first(extensions, "id", "clientID")) if extensions else None


def _side(raw: Mapping[str, Any], units: float | None) -> str | None:
    side = _optional_str(_first(raw, "side"))
    if side:
        return side.lower()
    if units is None:
        return None
    return "buy" if units > 0 else "sell"


def _units(raw: Mapping[str, Any]) -> float | None:
    value = _optional_float(_first(raw, "units", "requestedUnits"))
    if value is not None:
        return value
    for key in ("tradeOpened", "tradeReduced"):
        nested = _as_mapping(raw.get(key))
        if nested:
            nested_units = _optional_float(_first(nested, "units"))
            if nested_units is not None:
                return nested_units
    closed = _first_nested(raw, "tradesClosed")
    if isinstance(closed, Mapping):
        return _optional_float(_first(closed, "units"))
    return None


def _description(tx_type: str, reason: Any, raw: Mapping[str, Any]) -> str:
    parts = [part for part in (tx_type, _optional_str(reason)) if part]
    if not parts:
        parts.append("broker_transaction")
    if _first_nested(raw, "tradeOpened") is not None:
        parts.append("trade_opened")
    if _first_nested(raw, "tradeReduced") is not None:
        parts.append("trade_reduced")
    if _first_nested(raw, "tradesClosed") is not None:
        parts.append("trades_closed")
    return ":".join(parts)


def _first(raw: Mapping[str, Any] | None, *keys: str) -> Any:
    if raw is None:
        return None
    for key in keys:
        if key in raw and not _is_missing(raw[key]):
            return raw[key]
    return None


def _first_nested(raw: Mapping[str, Any], key: str) -> Any:
    value = raw.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value if not _is_missing(value) else None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _symbol_from_instrument(instrument: Any) -> str | None:
    text = _optional_str(instrument)
    if not text:
        return None
    return text.replace("_", "").upper()


def _optional_str(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_timestamp(value: Any) -> str | None:
    if _is_missing(value):
        return None
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return _optional_str(value)
    if timestamp is pd.NaT:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def _sort_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    sortable = frame.copy()
    if "timestamp" in sortable.columns:
        sortable["_time"] = pd.to_datetime(sortable["timestamp"], errors="coerce", utc=True)
    else:
        sortable["_time"] = pd.NaT
    if "transaction_id" in sortable.columns:
        sortable["_id"] = pd.to_numeric(sortable["transaction_id"], errors="coerce")
    else:
        sortable["_id"] = pd.NA
    sortable = sortable.sort_values(["_time", "_id"], na_position="first").drop(columns=["_time", "_id"])
    return sortable.reset_index(drop=True)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
