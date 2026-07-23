"""Preflight readiness checks before demo/live trading loops."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import pandas as pd

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import AccountState, Position
from smc_ta.config import RuntimeConfig, validate_runtime_config
from smc_ta.data import DataQualityConfig, DataQualityReport, validate_candle_quality
from smc_ta.lifecycle import TradeLifecycleStore
from smc_ta.news import NewsFilter
from smc_ta.reconciliation import BrokerReconciler, ReconciliationResult
from smc_ta.safety import EmergencyStopController, EmergencyStopResult

CheckSeverity = Literal["info", "warning", "blocking"]


@dataclass(frozen=True)
class PreflightConfig:
    """Controls which preflight checks are required."""

    require_config: bool = True
    require_broker_for_demo_live: bool = True
    require_candles: bool = True
    require_news_filter_when_configured: bool = True
    require_emergency_stop_for_demo_live: bool = True
    require_lifecycle_store_for_demo_live: bool = True
    check_reconciliation: bool = True
    check_persistence_paths: bool = True
    min_account_equity: float = 0.0
    min_free_margin: float | None = None
    data_quality: DataQualityConfig | None = None
    sample_limit: int = 10


@dataclass(frozen=True)
class PreflightCheck:
    """One readiness check result."""

    component: str
    code: str
    severity: CheckSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class PreflightReport:
    """Full startup readiness report."""

    timestamp: pd.Timestamp
    checks: tuple[PreflightCheck, ...]
    account: AccountState | None = None
    open_positions: tuple[Position, ...] = ()
    data_quality_reports: Mapping[str, DataQualityReport] = field(default_factory=dict)
    reconciliation_result: ReconciliationResult | None = None
    emergency_stop_result: EmergencyStopResult | None = None

    @property
    def ok(self) -> bool:
        return not self.blocking_checks

    @property
    def blocking_checks(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.blocking)

    @property
    def warnings(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.severity == "warning")

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(check.code for check in self.blocking_checks)

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "preflight_ok"
        parts = [check.code for check in self.blocking_checks]
        parts.extend(f"warning:{check.code}" for check in self.warnings)
        return ";".join(parts)

    def to_frame(self) -> pd.DataFrame:
        """Return checks as a DataFrame."""

        return pd.DataFrame([asdict(check) for check in self.checks])


class PreflightValidationError(RuntimeError):
    """Raised when preflight has blocking checks."""

    def __init__(self, report: PreflightReport) -> None:
        super().__init__(report.summary())
        self.report = report


def run_preflight(
    *,
    runtime_config: RuntimeConfig | None = None,
    candles_by_symbol: Mapping[str, pd.DataFrame] | None = None,
    broker: BrokerAdapter | None = None,
    news_filter: NewsFilter | None = None,
    emergency_stop: EmergencyStopController | None = None,
    reconciler: BrokerReconciler | None = None,
    lifecycle_store: TradeLifecycleStore | None = None,
    journal_path: str | Path | None = None,
    lifecycle_db_path: str | Path | None = None,
    timestamp: pd.Timestamp | None = None,
    config: PreflightConfig | None = None,
) -> PreflightReport:
    """Run startup readiness checks before a demo/live bot loop."""

    cfg = config or PreflightConfig()
    runtime = runtime_config or RuntimeConfig()
    now = _utc_timestamp(timestamp)
    checks: list[PreflightCheck] = []
    data_reports: dict[str, DataQualityReport] = {}
    account: AccountState | None = None
    open_positions: list[Position] = []
    reconciliation_result: ReconciliationResult | None = None
    emergency_stop_result: EmergencyStopResult | None = None

    if cfg.require_config:
        checks.extend(_config_checks(runtime))

    if cfg.require_candles:
        if candles_by_symbol:
            for symbol, candles in candles_by_symbol.items():
                report = validate_candle_quality(
                    candles,
                    config=_data_quality_config(cfg, runtime, symbol),
                )
                data_reports[symbol.upper()] = report
                checks.extend(_data_quality_checks(symbol.upper(), report))
        else:
            severity: CheckSeverity = "blocking" if runtime.mode in {"demo", "live"} else "warning"
            checks.append(
                PreflightCheck(
                    "data",
                    "candles_not_provided",
                    severity,
                    "no candle sample was provided for startup validation",
                )
            )

    if broker is None:
        if cfg.require_broker_for_demo_live and runtime.mode in {"demo", "live"}:
            checks.append(
                PreflightCheck(
                    "broker",
                    "broker_not_provided",
                    "blocking",
                    "demo/live preflight requires a broker adapter instance",
                )
            )
    else:
        account, open_positions = _probe_broker(broker, runtime, cfg, checks)

    if cfg.check_reconciliation and broker is not None and reconciler is not None:
        try:
            reconciliation_result = reconciler.reconcile_broker(broker)
            checks.extend(_reconciliation_checks(reconciliation_result))
        except Exception as exc:
            checks.append(
                PreflightCheck(
                    "reconciliation",
                    "reconciliation_probe_failed",
                    "blocking",
                    str(exc),
                    {"exception_type": type(exc).__name__},
                )
            )
    elif cfg.check_reconciliation and runtime.mode in {"demo", "live"}:
        checks.append(
            PreflightCheck(
                "reconciliation",
                "reconciler_not_provided",
                "warning",
                "demo/live mode should pass a BrokerReconciler for startup checks",
            )
        )

    if emergency_stop is None:
        if cfg.require_emergency_stop_for_demo_live and runtime.mode in {"demo", "live"}:
            checks.append(
                PreflightCheck(
                    "safety",
                    "emergency_stop_not_provided",
                    "warning",
                    "demo/live mode should pass an EmergencyStopController",
                )
            )
    elif account is not None:
        emergency_stop_result = emergency_stop.evaluate(
            account=account,
            open_positions=open_positions,
            timestamp=now,
            reconciliation_result=reconciliation_result,
        )
        checks.extend(_emergency_stop_checks(emergency_stop_result))

    checks.extend(_news_checks(runtime, news_filter, cfg))

    resolved_journal_path = journal_path or runtime.journal_path
    resolved_lifecycle_path = lifecycle_db_path or runtime.lifecycle_db_path
    if cfg.check_persistence_paths:
        if resolved_journal_path is not None:
            checks.append(_path_check("journal", "journal_path", resolved_journal_path))
        if resolved_lifecycle_path is not None:
            checks.append(_path_check("lifecycle", "lifecycle_db_path", resolved_lifecycle_path))

    if lifecycle_store is None:
        if cfg.require_lifecycle_store_for_demo_live and runtime.mode in {"demo", "live"}:
            checks.append(
                PreflightCheck(
                    "lifecycle",
                    "lifecycle_store_not_provided",
                    "warning",
                    "demo/live mode should pass a lifecycle store",
                )
            )
    else:
        checks.append(_lifecycle_store_check(lifecycle_store))

    if not any(check.blocking for check in checks):
        checks.append(PreflightCheck("preflight", "preflight_complete", "info", "startup checks completed"))

    return PreflightReport(
        timestamp=now,
        checks=tuple(checks),
        account=account,
        open_positions=tuple(open_positions),
        data_quality_reports=data_reports,
        reconciliation_result=reconciliation_result,
        emergency_stop_result=emergency_stop_result,
    )


def assert_preflight_ready(**kwargs) -> PreflightReport:
    """Run preflight and raise when blocking checks are present."""

    report = run_preflight(**kwargs)
    if not report.ok:
        raise PreflightValidationError(report)
    return report


def _config_checks(runtime: RuntimeConfig) -> list[PreflightCheck]:
    report = validate_runtime_config(runtime)
    checks: list[PreflightCheck] = []
    for issue in report.issues:
        severity: CheckSeverity = "blocking" if issue.severity == "error" else "warning"
        checks.append(
            PreflightCheck(
                "config",
                issue.code,
                severity,
                issue.message,
            )
        )
    if not checks:
        checks.append(PreflightCheck("config", "runtime_config_ok", "info", "runtime config passed validation"))
    return checks


def _data_quality_config(cfg: PreflightConfig, runtime: RuntimeConfig, symbol: str) -> DataQualityConfig:
    if cfg.data_quality is not None:
        return cfg.data_quality
    timeframe = runtime.timeframes[0] if runtime.timeframes else None
    return DataQualityConfig(symbol=symbol, timeframe=timeframe, min_rows=max(2, cfg.sample_limit))


def _data_quality_checks(symbol: str, report: DataQualityReport) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    if report.ok:
        checks.append(
            PreflightCheck(
                "data",
                "data_quality_ok",
                "info",
                f"{symbol} candle sample passed quality validation",
                {"symbol": symbol, "rows": report.row_count, "timeframe": str(report.inferred_timeframe)},
            )
        )
    for issue in report.issues:
        severity: CheckSeverity = "blocking" if issue.blocking else "warning"
        checks.append(
            PreflightCheck(
                "data",
                issue.kind,
                severity,
                issue.message,
                {"symbol": symbol, "count": issue.count, "sample": list(issue.sample), **issue.details},
            )
        )
    return checks


def _probe_broker(
    broker: BrokerAdapter,
    runtime: RuntimeConfig,
    cfg: PreflightConfig,
    checks: list[PreflightCheck],
) -> tuple[AccountState | None, list[Position]]:
    account: AccountState | None = None
    positions: list[Position] = []
    try:
        account = broker.get_account()
        checks.append(
            PreflightCheck(
                "broker",
                "account_probe_ok",
                "info",
                "broker account probe succeeded",
                {
                    "currency": account.currency,
                    "balance": account.balance,
                    "equity": account.equity,
                    "free_margin": account.free_margin,
                },
            )
        )
        if account.equity <= cfg.min_account_equity:
            checks.append(
                PreflightCheck(
                    "broker",
                    "account_equity_below_minimum",
                    "blocking",
                    "account equity is below the configured startup minimum",
                    {"equity": account.equity, "minimum": cfg.min_account_equity},
                )
            )
        if cfg.min_free_margin is not None and account.free_margin < cfg.min_free_margin:
            checks.append(
                PreflightCheck(
                    "broker",
                    "free_margin_below_minimum",
                    "blocking",
                    "free margin is below the configured startup minimum",
                    {"free_margin": account.free_margin, "minimum": cfg.min_free_margin},
                )
            )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "broker",
                "account_probe_failed",
                "blocking",
                str(exc),
                {"exception_type": type(exc).__name__},
            )
        )
        return None, positions

    try:
        for symbol in runtime.symbols:
            positions.extend(broker.get_open_positions(symbol))
        checks.append(
            PreflightCheck(
                "broker",
                "positions_probe_ok",
                "info",
                "broker positions probe succeeded",
                {"positions": len(positions)},
            )
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "broker",
                "positions_probe_failed",
                "blocking",
                str(exc),
                {"exception_type": type(exc).__name__},
            )
        )
    return account, positions


def _reconciliation_checks(result: ReconciliationResult) -> list[PreflightCheck]:
    if result.ok:
        return [
            PreflightCheck(
                "reconciliation",
                "reconciliation_ok",
                "info",
                "broker positions match expected ledger",
                {
                    "broker_positions": len(result.broker_positions),
                    "expected_positions": len(result.expected_positions),
                },
            )
        ]
    return [
        PreflightCheck(
            "reconciliation",
            issue.kind,
            "blocking" if issue.blocking else "warning",
            issue.message,
            {
                "symbol": issue.symbol,
                "broker_position_id": issue.broker_position_id,
                "expected_position_id": issue.expected_position_id,
                **issue.details,
            },
        )
        for issue in result.issues
    ]


def _emergency_stop_checks(result: EmergencyStopResult) -> list[PreflightCheck]:
    if result.ok:
        return [PreflightCheck("safety", "emergency_stop_ok", "info", "emergency stop is not active")]
    return [
        PreflightCheck(
            "safety",
            "emergency_stop_active",
            "blocking",
            result.summary(),
            result.details,
        )
    ]


def _news_checks(
    runtime: RuntimeConfig,
    news_filter: NewsFilter | None,
    cfg: PreflightConfig,
) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    if news_filter is None:
        if cfg.require_news_filter_when_configured and runtime.require_news_filter:
            checks.append(
                PreflightCheck(
                    "news",
                    "news_filter_not_provided",
                    "blocking",
                    "runtime config requires a news filter but none was provided",
                )
            )
        return checks
    event_count = len(news_filter.events)
    severity: CheckSeverity = "warning" if runtime.mode in {"demo", "live"} and event_count == 0 else "info"
    code = "news_filter_empty" if event_count == 0 else "news_filter_ok"
    message = "news filter has no events loaded" if event_count == 0 else "news filter is loaded"
    checks.append(PreflightCheck("news", code, severity, message, {"events": event_count}))
    return checks


def _path_check(component: str, code: str, path: str | Path) -> PreflightCheck:
    resolved = Path(path).expanduser()
    target = resolved if resolved.exists() else resolved.parent
    if not target.exists():
        return PreflightCheck(
            component,
            f"{code}_parent_missing",
            "blocking",
            "configured persistence parent path does not exist",
            {"path": str(resolved), "parent": str(resolved.parent)},
        )
    if not os.access(target, os.W_OK):
        return PreflightCheck(
            component,
            f"{code}_not_writable",
            "blocking",
            "configured persistence path is not writable",
            {"path": str(resolved)},
        )
    return PreflightCheck(component, f"{code}_writable", "info", "configured persistence path is writable", {"path": str(resolved)})


def _lifecycle_store_check(store: TradeLifecycleStore) -> PreflightCheck:
    try:
        records = store.list_records()
    except Exception as exc:
        return PreflightCheck(
            "lifecycle",
            "lifecycle_store_probe_failed",
            "blocking",
            str(exc),
            {"exception_type": type(exc).__name__},
        )
    return PreflightCheck(
        "lifecycle",
        "lifecycle_store_ok",
        "info",
        "lifecycle store probe succeeded",
        {"records": len(records)},
    )


def _utc_timestamp(value: object | None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
