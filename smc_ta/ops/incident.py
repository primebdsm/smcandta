"""Incident report bundle helpers for demo/live operations."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from smc_ta.broker.models import AccountState, Position
from smc_ta.monitoring import LiveMonitoringSnapshot


@dataclass(frozen=True)
class IncidentReportBundle:
    """Paths written for one operational incident report."""

    incident_id: str
    output_dir: Path
    summary_json: Path
    markdown_report: Path
    artifact_paths: dict[str, Path]


def write_incident_report_bundle(
    output_dir: str | Path,
    *,
    incident_id: str | None = None,
    title: str = "Trading bot incident",
    severity: str = "SEV2",
    status: str = "open",
    symbol: str | None = None,
    runtime_config: Any | None = None,
    account: AccountState | None = None,
    open_positions: Iterable[Position] | None = None,
    preflight_report: Any | None = None,
    restart_sync_report: Any | None = None,
    lifecycle_recovery_report: Any | None = None,
    monitoring_snapshot: LiveMonitoringSnapshot | None = None,
    emergency_stop_result: Any | None = None,
    journal_events: pd.DataFrame | None = None,
    dashboard_path: str | Path | None = None,
    notes: Iterable[str] | None = None,
    operator_actions: Iterable[str] | None = None,
    timestamp: Any | None = None,
) -> IncidentReportBundle:
    """Write a standardized incident evidence bundle.

    This helper is intentionally passive. It serializes already-collected
    runtime objects and report objects; it does not call a broker, place orders,
    close positions, or reset safety controls.
    """

    now = _utc_timestamp(timestamp)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    resolved_id = incident_id or f"incident-{now.strftime('%Y%m%d-%H%M%SZ')}"

    if monitoring_snapshot is not None:
        symbol = symbol or monitoring_snapshot.symbol
        account = account or monitoring_snapshot.account
        if open_positions is None:
            open_positions = monitoring_snapshot.open_positions
        preflight_report = preflight_report or monitoring_snapshot.preflight
        emergency_stop_result = emergency_stop_result or monitoring_snapshot.emergency_stop
        if journal_events is None and not monitoring_snapshot.journal_events.empty:
            journal_events = monitoring_snapshot.journal_events

    positions = tuple(open_positions or ())
    artifact_paths: dict[str, Path] = {}

    preflight_status = _report_status(preflight_report)
    restart_status = _report_status(restart_sync_report)
    lifecycle_status = _report_status(lifecycle_recovery_report)
    emergency_status = _emergency_status(emergency_stop_result)
    monitoring_status = _monitoring_status(monitoring_snapshot)

    _write_report_frames(root, "preflight", preflight_report, artifact_paths)
    _write_report_frames(
        root,
        "restart_sync",
        restart_sync_report,
        artifact_paths,
        extra_methods=("orders_frame", "transactions_frame"),
    )
    _write_report_frames(
        root,
        "lifecycle_recovery",
        lifecycle_recovery_report,
        artifact_paths,
        extra_methods=("records_frame",),
    )

    positions_frame = _positions_frame(positions)
    if not positions_frame.empty:
        artifact_paths["open_positions"] = _write_csv(positions_frame, root / "open_positions.csv")

    if monitoring_snapshot is not None:
        _write_monitoring_frames(root, monitoring_snapshot, artifact_paths)

    if journal_events is not None and not journal_events.empty:
        artifact_paths["journal_events"] = _write_csv(journal_events, root / "journal_events.csv")

    runtime_payload = _runtime_payload(runtime_config)
    account_payload = _payload(account) if account is not None else None
    blocking_reasons = _collect_blocking_reasons(
        preflight_report=preflight_report,
        restart_sync_report=restart_sync_report,
        lifecycle_recovery_report=lifecycle_recovery_report,
        emergency_stop_result=emergency_stop_result,
        monitoring_snapshot=monitoring_snapshot,
    )

    summary = {
        "incident_id": resolved_id,
        "title": title,
        "severity": severity.upper(),
        "status": status.lower(),
        "symbol": symbol.upper() if symbol else None,
        "created_at": now.isoformat(),
        "runtime_config": runtime_payload,
        "account": account_payload,
        "open_position_count": len(positions),
        "report_status": {
            "preflight": preflight_status,
            "restart_sync": restart_status,
            "lifecycle_recovery": lifecycle_status,
            "emergency_stop": emergency_status,
            "monitoring": monitoring_status,
        },
        "blocking_reasons": blocking_reasons,
        "dashboard_path": str(dashboard_path) if dashboard_path is not None else None,
        "notes": list(notes or ()),
        "operator_actions": list(operator_actions or ()),
        "artifacts": {name: str(path) for name, path in artifact_paths.items()},
    }

    summary_json = root / "incident_summary.json"
    markdown_report = root / "incident_report.md"
    summary_json.write_text(json.dumps(_jsonable(summary), indent=2, sort_keys=True), encoding="utf-8")
    markdown_report.write_text(_render_markdown(summary), encoding="utf-8")

    artifact_paths["summary_json"] = summary_json
    artifact_paths["markdown_report"] = markdown_report
    return IncidentReportBundle(
        incident_id=resolved_id,
        output_dir=root,
        summary_json=summary_json,
        markdown_report=markdown_report,
        artifact_paths=artifact_paths,
    )


def _write_report_frames(
    root: Path,
    prefix: str,
    report: Any | None,
    artifact_paths: dict[str, Path],
    *,
    extra_methods: tuple[str, ...] = (),
) -> None:
    if report is None:
        return
    _write_frame_method(root, prefix, report, "to_frame", artifact_paths)
    for method_name in extra_methods:
        frame_name = f"{prefix}_{method_name.removesuffix('_frame')}"
        _write_frame_method(root, frame_name, report, method_name, artifact_paths)


def _write_frame_method(
    root: Path,
    name: str,
    source: Any,
    method_name: str,
    artifact_paths: dict[str, Path],
) -> None:
    method = getattr(source, method_name, None)
    if not callable(method):
        return
    frame = method()
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        artifact_paths[name] = _write_csv(frame, root / f"{name}.csv")


def _write_monitoring_frames(
    root: Path,
    snapshot: LiveMonitoringSnapshot,
    artifact_paths: dict[str, Path],
) -> None:
    frames = {
        "monitoring_positions": snapshot.positions_frame(),
        "monitoring_lifecycle": snapshot.lifecycle_frame(),
        "monitoring_preflight": snapshot.preflight_frame(),
        "monitoring_journal_events": snapshot.journal_events,
        "monitoring_blocked_events": snapshot.blocked_events,
        "monitoring_execution_samples": snapshot.execution_samples,
        "monitoring_equity_curve": snapshot.equity_curve,
        "monitoring_trades": snapshot.trades,
    }
    for name, frame in frames.items():
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            artifact_paths[name] = _write_csv(frame, root / f"{name}.csv")


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _runtime_payload(runtime_config: Any | None) -> dict[str, Any] | None:
    if runtime_config is None:
        return None
    safe_dict = getattr(runtime_config, "to_safe_dict", None)
    if callable(safe_dict):
        return _jsonable(safe_dict())
    return _jsonable(_payload(runtime_config))


def _report_status(report: Any | None) -> dict[str, Any] | None:
    if report is None:
        return None
    summary = getattr(report, "summary", None)
    warnings = getattr(report, "warnings", ())
    return {
        "ok": bool(getattr(report, "ok", False)),
        "summary": summary() if callable(summary) else None,
        "blocking_reasons": [str(reason) for reason in getattr(report, "blocking_reasons", ())],
        "warnings": _warning_codes(warnings),
    }


def _emergency_status(result: Any | None) -> dict[str, Any] | None:
    if result is None:
        return None
    summary = getattr(result, "summary", None)
    return {
        "active": bool(getattr(result, "active", False)),
        "ok": bool(getattr(result, "ok", not bool(getattr(result, "active", False)))),
        "summary": summary() if callable(summary) else None,
        "reasons": [str(reason) for reason in getattr(result, "reasons", ())],
        "close_positions": bool(getattr(result, "close_positions", False)),
        "triggered_at": _jsonable(getattr(result, "triggered_at", None)),
    }


def _monitoring_status(snapshot: LiveMonitoringSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "status": snapshot.status,
        "health_ok": snapshot.health_ok,
        "blocking_reasons": list(snapshot.blocking_reasons),
        "warning_reasons": list(snapshot.warning_reasons),
        "open_position_count": snapshot.open_position_count,
        "active_lifecycle_count": snapshot.active_lifecycle_count,
    }


def _collect_blocking_reasons(**sources: Any) -> list[str]:
    reasons: list[str] = []
    for name, source in sources.items():
        if source is None:
            continue
        if name == "emergency_stop_result":
            if getattr(source, "active", False):
                reasons.extend(f"emergency_stop:{reason}" for reason in getattr(source, "reasons", ()))
            continue
        if name == "monitoring_snapshot":
            reasons.extend(f"monitoring:{reason}" for reason in getattr(source, "blocking_reasons", ()))
            continue
        report_name = name.removesuffix("_report")
        reasons.extend(f"{report_name}:{reason}" for reason in getattr(source, "blocking_reasons", ()))
    return list(dict.fromkeys(str(reason) for reason in reasons if str(reason)))


def _warning_codes(warnings: Any) -> list[str]:
    codes: list[str] = []
    try:
        iterable = tuple(warnings)
    except TypeError:
        return codes
    for item in iterable:
        code = getattr(item, "code", None)
        codes.append(str(code if code is not None else item))
    return codes


def _positions_frame(positions: Iterable[Position]) -> pd.DataFrame:
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
                "closed_at": position.closed_at,
                "realized_pnl": position.realized_pnl,
            }
        )
    return pd.DataFrame(rows)


def _payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): _payload(item) for key, item in value.items()}
    if is_dataclass(value):
        return asdict(value)
    return value


def _render_markdown(summary: Mapping[str, Any]) -> str:
    report_status = summary.get("report_status", {}) or {}
    artifacts = summary.get("artifacts", {}) or {}
    lines = [
        f"# {summary.get('incident_id')}",
        "",
        f"- Title: {summary.get('title')}",
        f"- Severity: {summary.get('severity')}",
        f"- Status: {summary.get('status')}",
        f"- Symbol: {summary.get('symbol') or 'n/a'}",
        f"- Created: {summary.get('created_at')}",
        f"- Open positions: {summary.get('open_position_count')}",
        "",
        "## Blocking Reasons",
    ]
    blocking = summary.get("blocking_reasons") or []
    lines.extend(f"- {reason}" for reason in blocking)
    if not blocking:
        lines.append("- none")
    lines.extend(["", "## Report Status"])
    for name, status in report_status.items():
        if status is None:
            lines.append(f"- {name}: not captured")
        else:
            label = status.get("summary") or status.get("status") or ("active" if status.get("active") else "ok")
            lines.append(f"- {name}: {label}")
    lines.extend(["", "## Operator Notes"])
    notes = summary.get("notes") or []
    lines.extend(f"- {note}" for note in notes)
    if not notes:
        lines.append("- none")
    lines.extend(["", "## Operator Actions"])
    actions = summary.get("operator_actions") or []
    lines.extend(f"- {action}" for action in actions)
    if not actions:
        lines.append("- none")
    lines.extend(["", "## Artifacts"])
    for name, path in artifacts.items():
        lines.append(f"- {name}: `{path}`")
    if not artifacts:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _utc_timestamp(value: Any | None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
