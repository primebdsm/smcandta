"""Integrated paper/OANDA practice startup monitoring drill."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import pandas as pd

from smc_ta.alerts import AlertChannel
from smc_ta.broker import OandaBroker, OandaCandleDataSource, OandaConfig, PaperBroker
from smc_ta.config import RuntimeConfig, load_env_file
from smc_ta.dashboard import write_live_dashboard
from smc_ta.engine import analyze_forex
from smc_ta.lifecycle import (
    LifecycleRecoveryConfig,
    SQLiteTradeLifecycleStore,
    recover_lifecycle_after_restart,
    write_lifecycle_recovery_report,
)
from smc_ta.monitoring import (
    AlertDeliveryStatus,
    BrokerConnectivityStatus,
    alert_delivery_frame,
    broker_connectivity_frame,
    build_live_monitoring_snapshot,
    check_broker_connectivity,
    probe_alert_channel,
    write_monitoring_snapshot_json,
)
from smc_ta.ops.incident import write_incident_report_bundle
from smc_ta.ops.secrets import EnvFileSecretSource, EnvSecretSource, SecretResolutionConfig, resolve_runtime_secrets, write_secret_resolution_report
from smc_ta.preflight import PreflightConfig, PreflightReport, run_preflight
from smc_ta.reconciliation import (
    RestartSyncConfig,
    RestartSyncReport,
    SQLitePositionLedger,
    SQLiteSyncCheckpointStore,
    sync_broker_state_after_restart,
    write_restart_sync_report,
)
from smc_ta.safety import EmergencyStopController

PracticeBrokerName = Literal["paper", "oanda"]


@dataclass(frozen=True)
class PracticeStartupRunConfig:
    """Settings for one integrated practice startup/monitoring drill."""

    broker: PracticeBrokerName = "paper"
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    output_dir: str | Path = "reports/practice_startup"
    env_file: str | Path | None = None
    candle_csv: str | Path | None = None
    candle_limit: int = 200
    max_spread_pips: float | None = None
    max_price_age_seconds: float = 15.0
    timeout: float = 20.0
    ledger_path: str | Path | None = None
    checkpoint_path: str | Path | None = None
    lifecycle_path: str | Path | None = None
    adopt_unmanaged_positions: bool = False
    mark_missing_positions_closed: bool = False
    update_mismatched_positions: bool = False
    allow_unlinked_pending_orders: bool = False
    create_missing_lifecycles: bool = False
    mark_missing_lifecycles_closed: bool = False
    fail_unfilled_lifecycles: bool = False
    match_lifecycle_symbol_side: bool = False
    probe_memory_alert: bool = True
    alert_probe_message: str = "SMC TA practice startup alert probe"
    write_incident_on_failure: bool = True


@dataclass(frozen=True)
class PracticeStartupRunResult:
    """Result and artifact paths for an integrated practice startup drill."""

    config: PracticeStartupRunConfig
    output_dir: Path
    artifacts: Mapping[str, Path] = field(default_factory=dict)
    secret_summary: str | None = None
    oanda_readiness_summary: str | None = None
    restart_sync: RestartSyncReport | None = None
    lifecycle_recovery_summary: str | None = None
    preflight: PreflightReport | None = None
    broker_connectivity: tuple[BrokerConnectivityStatus, ...] = ()
    alert_delivery: tuple[AlertDeliveryStatus, ...] = ()
    startup_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        if self.startup_errors:
            return False
        if self.secret_summary and self.secret_summary != "secrets_ok":
            return False
        if self.oanda_readiness_summary and not _ok_oanda_readiness_summary(self.oanda_readiness_summary):
            return False
        if self.restart_sync is not None and not self.restart_sync.ok:
            return False
        if self.lifecycle_recovery_summary and self.lifecycle_recovery_summary != "lifecycle_recovery_ok":
            return False
        if self.preflight is not None and not self.preflight.ok:
            return False
        if any(status.blocking for status in self.broker_connectivity):
            return False
        if any(status.blocking for status in self.alert_delivery):
            return False
        return True

    def summary(self) -> str:
        if self.ok:
            return "practice_startup_monitoring_ok"
        parts = list(self.startup_errors)
        if self.secret_summary and self.secret_summary != "secrets_ok":
            parts.append(f"secrets:{self.secret_summary}")
        if self.oanda_readiness_summary and not _ok_oanda_readiness_summary(self.oanda_readiness_summary):
            parts.append(f"oanda_readiness:{self.oanda_readiness_summary}")
        if self.restart_sync is not None and not self.restart_sync.ok:
            parts.append(f"restart_sync:{self.restart_sync.summary()}")
        if self.lifecycle_recovery_summary and self.lifecycle_recovery_summary != "lifecycle_recovery_ok":
            parts.append(f"lifecycle_recovery:{self.lifecycle_recovery_summary}")
        if self.preflight is not None and not self.preflight.ok:
            parts.append(f"preflight:{self.preflight.summary()}")
        parts.extend(f"broker:{status.broker_name}:{status.message}" for status in self.broker_connectivity if status.blocking)
        parts.extend(f"alert:{status.channel_name}:{status.message}" for status in self.alert_delivery if status.blocking)
        return ";".join(parts or ("practice_startup_monitoring_blocked",))

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "broker": self.config.broker,
            "symbol": self.config.symbol.upper(),
            "timeframe": self.config.timeframe,
            "output_dir": str(self.output_dir),
            "artifacts": {name: str(path) for name, path in sorted(self.artifacts.items())},
            "secret_summary": self.secret_summary,
            "oanda_readiness_summary": self.oanda_readiness_summary,
            "restart_sync_summary": self.restart_sync.summary() if self.restart_sync is not None else None,
            "lifecycle_recovery_summary": self.lifecycle_recovery_summary,
            "preflight_summary": self.preflight.summary() if self.preflight is not None else None,
            "broker_connectivity": [status.to_dict() for status in self.broker_connectivity],
            "alert_delivery": [status.to_dict() for status in self.alert_delivery],
            "startup_errors": list(self.startup_errors),
        }


def run_practice_startup_monitoring(
    config: PracticeStartupRunConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
    broker: Any | None = None,
    candles: pd.DataFrame | None = None,
    alert_channels: Iterable[tuple[str, AlertChannel]] | None = None,
) -> PracticeStartupRunResult:
    """Run a full paper/OANDA startup monitoring drill and write artifacts."""

    cfg = config or PracticeStartupRunConfig()
    root = Path(cfg.output_dir)
    startup_dir = root / "startup"
    dashboard_dir = root / "dashboard"
    state_dir = root / "state"
    startup_dir.mkdir(parents=True, exist_ok=True)
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}
    startup_errors: list[str] = []
    symbol = cfg.symbol.upper()
    runtime_env = _merged_env(env, cfg.env_file)

    secret_summary: str | None = None
    if cfg.broker == "oanda":
        secret_report = resolve_runtime_secrets(
            SecretResolutionConfig(
                sources=_secret_sources(cfg),
                required_keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"),
            ),
            env=runtime_env,
        )
        artifacts["secrets"] = write_secret_resolution_report(secret_report, startup_dir / "secrets.json")
        secret_summary = secret_report.summary()
        if not secret_report.ok:
            return _finalize_partial_result(cfg, root, artifacts, secret_summary, startup_errors)
        runtime_env = {**runtime_env, **secret_report.values}

    active_broker = broker
    oanda_readiness_summary: str | None = None
    if active_broker is None:
        try:
            active_broker = _build_broker(cfg, runtime_env)
        except Exception as exc:
            startup_errors.append(_startup_error("broker_init", exc))
            return _finalize_partial_result(cfg, root, artifacts, secret_summary, startup_errors)

    try:
        active_candles = candles if candles is not None else _load_candles(cfg, runtime_env)
    except Exception as exc:
        startup_errors.append(_startup_error("candles", exc))
        return _finalize_partial_result(cfg, root, artifacts, secret_summary, startup_errors)
    artifacts["candles"] = _write_csv(active_candles.reset_index(), startup_dir / "candles.csv")

    if cfg.broker == "oanda" and hasattr(active_broker, "practice_readiness"):
        try:
            readiness = active_broker.practice_readiness((symbol,))
        except Exception as exc:
            oanda_readiness_summary = _startup_error("oanda_readiness", exc)
            return _finalize_partial_result(
                cfg,
                root,
                artifacts,
                secret_summary,
                startup_errors,
                oanda_readiness_summary=oanda_readiness_summary,
            )
        else:
            oanda_readiness_summary = readiness.summary()
            artifacts["oanda_readiness"] = _write_csv(readiness.to_frame(), startup_dir / "oanda_readiness.csv")

    ledger = SQLitePositionLedger(cfg.ledger_path or state_dir / "positions.sqlite")
    checkpoint_store = SQLiteSyncCheckpointStore(cfg.checkpoint_path or state_dir / "positions.sqlite")
    lifecycle_store = SQLiteTradeLifecycleStore(cfg.lifecycle_path or state_dir / "trade_lifecycle.sqlite")

    try:
        restart_sync = sync_broker_state_after_restart(
            active_broker,
            ledger,
            symbol=symbol,
            checkpoint_store=checkpoint_store,
            config=RestartSyncConfig(
                adopt_unmanaged_broker_positions=cfg.adopt_unmanaged_positions,
                mark_missing_expected_positions_closed=cfg.mark_missing_positions_closed,
                update_mismatched_expected_positions=cfg.update_mismatched_positions,
                fetch_broker_transactions=cfg.broker == "oanda",
                fetch_pending_orders=cfg.broker == "oanda",
                block_on_unlinked_pending_orders=not cfg.allow_unlinked_pending_orders,
            ),
        )
    except Exception as exc:
        startup_errors.append(_startup_error("restart_sync", exc))
        return _finalize_partial_result(
            cfg,
            root,
            artifacts,
            secret_summary,
            startup_errors,
            oanda_readiness_summary=oanda_readiness_summary,
        )
    artifacts["restart_sync"] = write_restart_sync_report(restart_sync, startup_dir / "restart_sync.json")
    artifacts["restart_sync_actions"] = _write_csv(restart_sync.to_frame(), startup_dir / "restart_sync_actions.csv")
    artifacts["pending_orders"] = _write_csv(restart_sync.orders_frame(), startup_dir / "pending_orders.csv")
    artifacts["transactions"] = _write_csv(restart_sync.transactions_frame(), startup_dir / "transactions.csv")

    try:
        lifecycle_report = recover_lifecycle_after_restart(
            active_broker,
            lifecycle_store,
            symbol=symbol,
            restart_report=restart_sync,
            config=LifecycleRecoveryConfig(
                create_missing_lifecycles_for_broker_positions=cfg.create_missing_lifecycles,
                mark_missing_broker_positions_closed=cfg.mark_missing_lifecycles_closed,
                fail_unfilled_lifecycles_without_broker_position=cfg.fail_unfilled_lifecycles,
                match_unlinked_records_by_symbol_side=cfg.match_lifecycle_symbol_side,
            ),
        )
    except Exception as exc:
        startup_errors.append(_startup_error("lifecycle_recovery", exc))
        return _finalize_partial_result(
            cfg,
            root,
            artifacts,
            secret_summary,
            startup_errors,
            oanda_readiness_summary=oanda_readiness_summary,
            restart_sync=restart_sync,
        )
    lifecycle_summary = lifecycle_report.summary()
    artifacts["lifecycle_recovery"] = write_lifecycle_recovery_report(
        lifecycle_report,
        startup_dir / "lifecycle_recovery.json",
    )
    artifacts["lifecycle_records"] = _write_csv(lifecycle_report.records_frame(), startup_dir / "lifecycle_records.csv")

    runtime = _runtime_config(cfg, runtime_env)
    emergency_stop = EmergencyStopController()
    try:
        preflight = run_preflight(
            runtime_config=runtime,
            candles_by_symbol={symbol: active_candles},
            broker=active_broker,
            emergency_stop=emergency_stop,
            lifecycle_store=lifecycle_store,
            config=PreflightConfig(require_candles=True),
        )
    except Exception as exc:
        startup_errors.append(_startup_error("preflight", exc))
        return _finalize_partial_result(
            cfg,
            root,
            artifacts,
            secret_summary,
            startup_errors,
            oanda_readiness_summary=oanda_readiness_summary,
            restart_sync=restart_sync,
            lifecycle_recovery_summary=lifecycle_summary,
        )
    artifacts["preflight"] = _write_csv(preflight.to_frame(), startup_dir / "preflight.csv")

    broker_status = check_broker_connectivity(
        active_broker,
        broker_name=cfg.broker,
        symbol=symbol,
        include_transactions=cfg.broker == "oanda",
        include_pending_orders=cfg.broker == "oanda",
    )
    alert_statuses = _probe_alerts(cfg, alert_channels)
    artifacts["broker_connectivity"] = _write_csv(
        broker_connectivity_frame((broker_status,)),
        startup_dir / "broker_connectivity.csv",
    )
    artifacts["alert_delivery"] = _write_csv(alert_delivery_frame(alert_statuses), startup_dir / "alert_delivery.csv")

    try:
        analysis = analyze_forex(active_candles, symbol=symbol)
        snapshot = build_live_monitoring_snapshot(
            symbol=symbol,
            signals=analysis.signals,
            features=analysis.features,
            account=preflight.account,
            open_positions=preflight.open_positions,
            preflight=preflight,
            emergency_stop=preflight.emergency_stop_result,
            lifecycle_store=lifecycle_store,
            broker_connectivity=(broker_status,),
            alert_delivery=alert_statuses,
            mode="demo" if cfg.broker == "oanda" else "paper",
            broker_name=cfg.broker,
        )
        artifacts["dashboard"] = write_live_dashboard(dashboard_dir / "live.html", snapshot, refresh_seconds=30)
        artifacts["snapshot"] = write_monitoring_snapshot_json(snapshot, dashboard_dir / "snapshot.json")
    except Exception as exc:
        startup_errors.append(_startup_error("dashboard_snapshot", exc))
        result = PracticeStartupRunResult(
            config=cfg,
            output_dir=root,
            artifacts=artifacts,
            secret_summary=secret_summary,
            oanda_readiness_summary=oanda_readiness_summary,
            restart_sync=restart_sync,
            lifecycle_recovery_summary=lifecycle_summary,
            preflight=preflight,
            broker_connectivity=(broker_status,),
            alert_delivery=alert_statuses,
            startup_errors=tuple(startup_errors),
        )
        artifacts["summary"] = _write_summary(result, root / "summary.json")
        return result

    result = PracticeStartupRunResult(
        config=cfg,
        output_dir=root,
        artifacts=artifacts,
        secret_summary=secret_summary,
        oanda_readiness_summary=oanda_readiness_summary,
        restart_sync=restart_sync,
        lifecycle_recovery_summary=lifecycle_summary,
        preflight=preflight,
        broker_connectivity=(broker_status,),
        alert_delivery=alert_statuses,
        startup_errors=tuple(startup_errors),
    )
    if cfg.write_incident_on_failure and not result.ok:
        artifacts["incident"] = write_incident_report_bundle(
            root / "incident",
            title="practice startup monitoring blocked",
            severity="SEV2",
            symbol=symbol,
            runtime_config=runtime,
            preflight_report=preflight,
            restart_sync_report=restart_sync,
            lifecycle_recovery_report=lifecycle_report,
            monitoring_snapshot=snapshot,
            emergency_stop_result=preflight.emergency_stop_result,
            notes=(result.summary(),),
        ).markdown_report
    artifacts["summary"] = _write_summary(result, root / "summary.json")
    return result


def _finalize_partial_result(
    cfg: PracticeStartupRunConfig,
    root: Path,
    artifacts: dict[str, Path],
    secret_summary: str | None,
    startup_errors: list[str],
    *,
    oanda_readiness_summary: str | None = None,
    restart_sync: RestartSyncReport | None = None,
    lifecycle_recovery_summary: str | None = None,
    preflight: PreflightReport | None = None,
) -> PracticeStartupRunResult:
    result = PracticeStartupRunResult(
        config=cfg,
        output_dir=root,
        artifacts=artifacts,
        secret_summary=secret_summary,
        oanda_readiness_summary=oanda_readiness_summary,
        restart_sync=restart_sync,
        lifecycle_recovery_summary=lifecycle_recovery_summary,
        preflight=preflight,
        startup_errors=tuple(startup_errors),
    )
    artifacts["summary"] = _write_summary(result, root / "summary.json")
    return result


def _secret_sources(cfg: PracticeStartupRunConfig):
    sources: list[Any] = [EnvSecretSource(keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"))]
    if cfg.env_file is not None:
        sources.append(EnvFileSecretSource(cfg.env_file))
    return tuple(sources)


def _merged_env(env: Mapping[str, str] | None, env_file: str | Path | None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    if env_file is not None:
        merged.update(load_env_file(env_file))
    return merged


def _ok_oanda_readiness_summary(summary: str) -> bool:
    return summary == "oanda_practice_ready" or summary.startswith("warning:")


def _build_broker(cfg: PracticeStartupRunConfig, env: Mapping[str, str]):
    if cfg.broker == "oanda":
        return OandaBroker(_oanda_config(cfg, env))
    return PaperBroker(initial_balance=10_000)


def _load_candles(cfg: PracticeStartupRunConfig, env: Mapping[str, str]) -> pd.DataFrame:
    if cfg.candle_csv is not None:
        from smc_ta.data import load_csv_candles

        return load_csv_candles(cfg.candle_csv).tail(cfg.candle_limit)
    if cfg.broker == "oanda":
        return OandaCandleDataSource(_oanda_config(cfg, env)).get_candles(
            cfg.symbol,
            cfg.timeframe,
            limit=cfg.candle_limit,
        )
    return _sample_candles(cfg.candle_limit)


def _oanda_config(cfg: PracticeStartupRunConfig, env: Mapping[str, str]) -> OandaConfig:
    account_id = _secret(env, "OANDA_ACCOUNT_ID")
    token = _secret(env, "OANDA_TOKEN")
    if not account_id or not token:
        raise ValueError("OANDA_ACCOUNT_ID and OANDA_TOKEN are required for OANDA practice startup monitoring")
    return OandaConfig(
        account_id=account_id,
        token=token,
        practice=True,
        timeout=cfg.timeout,
        max_spread_pips=cfg.max_spread_pips,
        max_price_age_seconds=cfg.max_price_age_seconds,
    )


def _runtime_config(cfg: PracticeStartupRunConfig, env: Mapping[str, str]) -> RuntimeConfig:
    if cfg.broker == "oanda":
        return RuntimeConfig(
            mode="demo",
            broker="oanda",
            symbols=(cfg.symbol.upper(),),
            timeframes=(cfg.timeframe,),
            oanda_account_id=_secret(env, "OANDA_ACCOUNT_ID"),
            oanda_token=_secret(env, "OANDA_TOKEN"),
            oanda_practice=True,
            oanda_max_spread_pips=cfg.max_spread_pips,
            oanda_max_price_age_seconds=cfg.max_price_age_seconds,
        )
    return RuntimeConfig(mode="paper", broker="paper", symbols=(cfg.symbol.upper(),), timeframes=(cfg.timeframe,))


def _secret(env: Mapping[str, str], key: str) -> str | None:
    return env.get(key) or env.get(f"SMC_TA_{key}")


def _startup_error(stage: str, exc: Exception) -> str:
    return f"{stage}_failed:{type(exc).__name__}:{exc}"


def _probe_alerts(
    cfg: PracticeStartupRunConfig,
    channels: Iterable[tuple[str, AlertChannel]] | None,
) -> tuple[AlertDeliveryStatus, ...]:
    statuses = []
    for name, channel in channels or ():
        statuses.append(probe_alert_channel(channel, channel_name=name, message=cfg.alert_probe_message))
    if cfg.probe_memory_alert:
        statuses.append(probe_alert_channel(_MemoryAlert(), channel_name="memory", message=cfg.alert_probe_message))
    return tuple(statuses)


def _write_summary(result: PracticeStartupRunResult, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_safe_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(frame).to_csv(output, index=False)
    return output


def _sample_candles(rows: int) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=max(rows, 60), freq="15min", tz="UTC")
    sequence = pd.Series(range(len(index)), index=index, dtype=float)
    close = 1.1000 + sequence * 0.00001 + ((sequence % 12) - 6) * 0.00002
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00025
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00025
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (sequence % 20),
            "spread": 0.0001,
        },
        index=index,
    ).tail(rows)


class _MemoryAlert:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)
