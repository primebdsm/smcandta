"""Live monitoring snapshot models for dashboards and reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

import pandas as pd

from smc_ta.broker.models import AccountState, Position
from smc_ta.lifecycle import TradeLifecycleRecord, TradeLifecycleStore
from smc_ta.monitoring.metrics import health_check, performance_summary
from smc_ta.preflight import PreflightReport
from smc_ta.safety import EmergencyStopResult


@dataclass(frozen=True)
class LiveMonitoringSnapshot:
    """One dashboard-ready live/demo monitoring snapshot."""

    symbol: str
    timestamp: pd.Timestamp
    mode: str = "paper"
    broker_name: str = "paper"
    account: AccountState | None = None
    open_positions: tuple[Position, ...] = ()
    latest_signal: dict[str, object] = field(default_factory=dict)
    latest_features: dict[str, object] = field(default_factory=dict)
    performance: dict[str, object] = field(default_factory=dict)
    health_messages: tuple[str, ...] = ()
    health_ok: bool = True
    preflight: PreflightReport | None = None
    emergency_stop: EmergencyStopResult | None = None
    lifecycle_records: tuple[TradeLifecycleRecord, ...] = ()
    journal_events: pd.DataFrame = field(default_factory=pd.DataFrame)
    blocked_events: pd.DataFrame = field(default_factory=pd.DataFrame)
    execution_samples: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def status(self) -> str:
        """Return operational status: ok, warning, or blocking."""

        if self.blocking_reasons:
            return "blocking"
        if self.warning_reasons:
            return "warning"
        return "ok"

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.preflight is not None:
            reasons.extend(self.preflight.blocking_reasons)
        if self.emergency_stop is not None and self.emergency_stop.active:
            reasons.extend(self.emergency_stop.reasons)
        if not self.health_ok:
            reasons.extend(message for message in self.health_messages if message != "ok")
        return tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason)))

    @property
    def warning_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.preflight is not None:
            reasons.extend(f"preflight:{check.code}" for check in self.preflight.warnings)
        if self.blocked_events is not None and not self.blocked_events.empty:
            reasons.append("blocked_events_present")
        return tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason)))

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)

    @property
    def active_lifecycle_count(self) -> int:
        return sum(1 for record in self.lifecycle_records if record.is_active)

    def account_dict(self) -> dict[str, object]:
        if self.account is None:
            return {}
        return {
            "balance": self.account.balance,
            "equity": self.account.equity,
            "margin_used": self.account.margin_used,
            "free_margin": self.account.free_margin,
            "currency": self.account.currency,
        }

    def positions_frame(self) -> pd.DataFrame:
        return positions_to_frame(self.open_positions)

    def lifecycle_frame(self, *, tail: int | None = None) -> pd.DataFrame:
        frame = lifecycle_records_to_frame(self.lifecycle_records)
        return frame.tail(tail) if tail is not None and not frame.empty else frame

    def preflight_frame(self) -> pd.DataFrame:
        return self.preflight.to_frame() if self.preflight is not None else pd.DataFrame()


def build_live_monitoring_snapshot(
    *,
    symbol: str,
    signals: pd.DataFrame | None = None,
    features: pd.DataFrame | None = None,
    account: AccountState | None = None,
    open_positions: Iterable[Position] | None = None,
    equity_curve: pd.DataFrame | None = None,
    trades: pd.DataFrame | None = None,
    blocked_events: pd.DataFrame | None = None,
    preflight: PreflightReport | None = None,
    emergency_stop: EmergencyStopResult | None = None,
    lifecycle_records: Iterable[TradeLifecycleRecord] | None = None,
    lifecycle_store: TradeLifecycleStore | None = None,
    journal_events: pd.DataFrame | None = None,
    execution_samples: pd.DataFrame | None = None,
    mode: str = "paper",
    broker_name: str = "paper",
    timestamp: pd.Timestamp | None = None,
) -> LiveMonitoringSnapshot:
    """Build one dashboard-ready snapshot from live/demo runtime objects."""

    now = _utc_timestamp(timestamp)
    symbol_upper = symbol.upper()
    signal = _latest_row(signals)
    feature = _latest_row(features)
    equity = _copy_frame(equity_curve)
    trade_frame = _copy_frame(trades)
    perf: dict[str, object] = {}
    health_messages: tuple[str, ...] = ("ok",)
    health_ok = True
    if not equity.empty and "equity" in equity.columns:
        perf = performance_summary(equity, trade_frame if not trade_frame.empty else None)
        health = health_check(equity)
        health_ok = health.ok
        health_messages = health.messages
    records = tuple(lifecycle_records or ())
    if lifecycle_store is not None:
        records = tuple(lifecycle_store.list_records(symbol=symbol_upper))
    return LiveMonitoringSnapshot(
        symbol=symbol_upper,
        timestamp=now,
        mode=mode,
        broker_name=broker_name,
        account=account,
        open_positions=tuple(open_positions or ()),
        latest_signal=signal,
        latest_features=feature,
        performance=perf,
        health_messages=health_messages,
        health_ok=health_ok,
        preflight=preflight,
        emergency_stop=emergency_stop,
        lifecycle_records=records,
        journal_events=_copy_frame(journal_events),
        blocked_events=_copy_frame(blocked_events),
        execution_samples=_copy_frame(execution_samples),
        equity_curve=equity,
        trades=trade_frame,
    )


def positions_to_frame(positions: Iterable[Position]) -> pd.DataFrame:
    """Convert broker positions to a monitoring table."""

    rows = []
    for position in positions:
        rows.append(
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "units": position.units,
                "entry_price": position.entry_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "opened_at": position.opened_at,
                "realized_pnl": position.realized_pnl,
            }
        )
    return pd.DataFrame(rows)


def lifecycle_records_to_frame(records: Iterable[TradeLifecycleRecord]) -> pd.DataFrame:
    """Convert lifecycle records to a monitoring table."""

    rows = []
    for record in records:
        rows.append(
            {
                "trade_id": record.trade_id,
                "symbol": record.symbol,
                "side": record.side,
                "state": record.state,
                "setup_name": record.setup_name,
                "confidence": record.confidence,
                "units": record.units,
                "filled_units": record.filled_units,
                "closed_units": record.closed_units,
                "realized_pnl": record.realized_pnl,
                "updated_at": record.updated_at,
                "reasons": ";".join(record.reasons),
            }
        )
    return pd.DataFrame(rows)


def _latest_row(frame: pd.DataFrame | None) -> dict[str, object]:
    if frame is None or frame.empty:
        return {}
    row = frame.iloc[-1]
    return {str(key): _clean_value(value) for key, value in row.to_dict().items()}


def _copy_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    return pd.DataFrame() if frame is None else frame.copy()


def _clean_value(value: object) -> object:
    if isinstance(value, (list, tuple, dict, set)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


def _utc_timestamp(value: object | None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
