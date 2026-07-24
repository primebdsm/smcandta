"""Hosted authenticated monitoring server for dashboard artifacts."""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

from smc_ta.monitoring.live import LiveMonitoringSnapshot


@dataclass(frozen=True)
class MonitoringAuthConfig:
    """HTTP authentication settings for hosted monitoring."""

    enabled: bool = True
    username: str | None = None
    password: str | None = None
    bearer_token: str | None = None
    realm: str = "SMC TA Monitor"
    public_healthz: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, prefix: str = "SMC_TA_MONITOR_") -> "MonitoringAuthConfig":
        """Build auth config from environment variables."""

        source = os.environ if env is None else env
        return cls(
            enabled=_to_bool(source.get(f"{prefix}AUTH_ENABLED", "true")),
            username=_optional_str(source.get(f"{prefix}USERNAME")),
            password=_optional_str(source.get(f"{prefix}PASSWORD")),
            bearer_token=_optional_str(source.get(f"{prefix}BEARER_TOKEN")),
            realm=source.get(f"{prefix}REALM", "SMC TA Monitor"),
            public_healthz=_to_bool(source.get(f"{prefix}PUBLIC_HEALTHZ", "false")),
        )

    @property
    def has_credentials(self) -> bool:
        return bool((self.username and self.password) or self.bearer_token)


@dataclass(frozen=True)
class HostedMonitoringConfig:
    """Settings for the read-only hosted monitoring server."""

    dashboard_path: str | Path = "live_dashboard.html"
    snapshot_path: str | Path | None = None
    artifact_dir: str | Path | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    auth: MonitoringAuthConfig = MonitoringAuthConfig()
    server_name: str = "SMC TA Hosted Monitor"
    no_store: bool = True


class HostedMonitoringServer:
    """Thin wrapper around a dependency-free HTTP monitoring server."""

    def __init__(self, config: HostedMonitoringConfig) -> None:
        validate_hosted_monitoring_config(config)
        self.config = config
        handler = _handler_factory(config)
        self.httpd = ThreadingHTTPServer((config.host, int(config.port)), handler)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        display_host = "127.0.0.1" if str(host) in {"0.0.0.0", ""} else str(host)
        return f"http://{display_host}:{port}"

    def serve_forever(self) -> None:
        """Serve requests until interrupted or shut down."""

        self.httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        """Start the server in a daemon thread for tests or embedding."""

        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self.serve_forever, name="smc-ta-monitor", daemon=True)
        self._thread.start()
        return self._thread

    def shutdown(self) -> None:
        """Stop the server and close its socket."""

        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def validate_hosted_monitoring_config(config: HostedMonitoringConfig) -> None:
    """Raise when hosted monitoring is not configured safely enough to start."""

    if config.auth.enabled and not config.auth.has_credentials:
        raise ValueError("hosted monitoring auth is enabled but no username/password or bearer token was provided")
    if int(config.port) < 0 or int(config.port) > 65535:
        raise ValueError("port must be between 0 and 65535")


def create_hosted_monitoring_server(config: HostedMonitoringConfig | None = None) -> HostedMonitoringServer:
    """Create a hosted monitoring server without starting it."""

    return HostedMonitoringServer(config or HostedMonitoringConfig())


def monitoring_snapshot_to_jsonable(snapshot: LiveMonitoringSnapshot) -> dict[str, Any]:
    """Return a JSON-safe monitoring snapshot dictionary."""

    return {
        "symbol": snapshot.symbol,
        "timestamp": snapshot.timestamp.isoformat(),
        "mode": snapshot.mode,
        "broker_name": snapshot.broker_name,
        "status": snapshot.status,
        "blocking_reasons": list(snapshot.blocking_reasons),
        "warning_reasons": list(snapshot.warning_reasons),
        "open_position_count": snapshot.open_position_count,
        "active_lifecycle_count": snapshot.active_lifecycle_count,
        "health_ok": snapshot.health_ok,
        "health_messages": list(snapshot.health_messages),
        "account": _jsonable(snapshot.account_dict()),
        "latest_signal": _jsonable(snapshot.latest_signal),
        "latest_features": _jsonable(snapshot.latest_features),
        "performance": _jsonable(snapshot.performance),
        "positions": _frame_records(snapshot.positions_frame()),
        "lifecycle_records": _frame_records(snapshot.lifecycle_frame()),
        "preflight_checks": _frame_records(snapshot.preflight_frame()),
        "journal_events": _frame_records(snapshot.journal_events),
        "blocked_events": _frame_records(snapshot.blocked_events),
        "execution_samples": _frame_records(snapshot.execution_samples),
        "broker_connectivity": _frame_records(snapshot.broker_connectivity_frame()),
        "alert_delivery": _frame_records(snapshot.alert_delivery_frame()),
    }


def write_monitoring_snapshot_json(snapshot: LiveMonitoringSnapshot, path: str | Path) -> Path:
    """Write a JSON monitoring snapshot for the hosted server status endpoint."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(monitoring_snapshot_to_jsonable(snapshot), indent=2, sort_keys=True), encoding="utf-8")
    return output


def build_hosted_monitoring_status(config: HostedMonitoringConfig) -> dict[str, Any]:
    """Build the JSON status payload served by `/status.json`."""

    now = datetime.now(timezone.utc)
    dashboard_path = Path(config.dashboard_path)
    snapshot_path = Path(config.snapshot_path) if config.snapshot_path is not None else None
    dashboard_exists = dashboard_path.is_file()
    dashboard_updated_at = _file_updated_at(dashboard_path) if dashboard_exists else None
    dashboard_age = (now - dashboard_updated_at).total_seconds() if dashboard_updated_at is not None else None
    snapshot = _read_json(snapshot_path) if snapshot_path is not None and snapshot_path.is_file() else None
    snapshot_status = str(snapshot.get("status")) if isinstance(snapshot, dict) and snapshot.get("status") else None
    status = snapshot_status or ("ok" if dashboard_exists else "blocking")
    ok = dashboard_exists and status != "blocking"
    return {
        "ok": ok,
        "status": status,
        "served_at": now.isoformat(),
        "server_name": config.server_name,
        "dashboard": {
            "path": str(dashboard_path),
            "exists": dashboard_exists,
            "updated_at": dashboard_updated_at.isoformat() if dashboard_updated_at is not None else None,
            "age_seconds": dashboard_age,
        },
        "snapshot": snapshot,
        "snapshot_path": str(snapshot_path) if snapshot_path is not None else None,
        "artifact_dir": str(config.artifact_dir) if config.artifact_dir is not None else None,
    }


def _handler_factory(config: HostedMonitoringConfig):
    class MonitoringRequestHandler(BaseHTTPRequestHandler):
        server_version = "SMCTAMonitor/1.0"

        def do_GET(self) -> None:  # noqa: N802
            self._handle_request(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle_request(send_body=False)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_request(self, *, send_body: bool) -> None:
            path = urlparse(self.path).path
            if not _authorized(self.headers.get("Authorization"), config.auth, path):
                self._unauthorized(send_body=send_body)
                return
            if path in {"", "/", "/dashboard", "/dashboard.html"}:
                self._serve_dashboard(send_body=send_body)
                return
            if path == "/status.json":
                self._serve_json(build_hosted_monitoring_status(config), send_body=send_body)
                return
            if path == "/snapshot.json":
                self._serve_snapshot(send_body=send_body)
                return
            if path == "/healthz":
                self._serve_health(send_body=send_body)
                return
            if path.startswith("/artifacts/"):
                self._serve_artifact(path, send_body=send_body)
                return
            self._send_text(HTTPStatus.NOT_FOUND, "not_found\n", content_type="text/plain", send_body=send_body)

        def _serve_dashboard(self, *, send_body: bool) -> None:
            dashboard = Path(config.dashboard_path)
            if not dashboard.is_file():
                self._send_text(
                    HTTPStatus.NOT_FOUND,
                    "dashboard_missing\n",
                    content_type="text/plain",
                    send_body=send_body,
                )
                return
            self._send_file(dashboard, content_type="text/html; charset=utf-8", send_body=send_body)

        def _serve_snapshot(self, *, send_body: bool) -> None:
            if config.snapshot_path is None:
                self._send_text(
                    HTTPStatus.NOT_FOUND,
                    "snapshot_not_configured\n",
                    content_type="text/plain",
                    send_body=send_body,
                )
                return
            snapshot_path = Path(config.snapshot_path)
            if not snapshot_path.is_file():
                self._send_text(
                    HTTPStatus.NOT_FOUND,
                    "snapshot_missing\n",
                    content_type="text/plain",
                    send_body=send_body,
                )
                return
            self._send_file(snapshot_path, content_type="application/json; charset=utf-8", send_body=send_body)

        def _serve_health(self, *, send_body: bool) -> None:
            status = build_hosted_monitoring_status(config)
            code = HTTPStatus.OK if status["dashboard"]["exists"] else HTTPStatus.SERVICE_UNAVAILABLE
            body = "ok\n" if code == HTTPStatus.OK else "dashboard_missing\n"
            self._send_text(code, body, content_type="text/plain", send_body=send_body)

        def _serve_json(self, payload: dict[str, Any], *, send_body: bool) -> None:
            body = json.dumps(_jsonable(payload), sort_keys=True).encode("utf-8")
            self._send_response(HTTPStatus.OK, "application/json; charset=utf-8", body, send_body=send_body)

        def _serve_artifact(self, path: str, *, send_body: bool) -> None:
            if config.artifact_dir is None:
                self._send_text(
                    HTTPStatus.NOT_FOUND,
                    "artifact_dir_not_configured\n",
                    content_type="text/plain",
                    send_body=send_body,
                )
                return
            root = Path(config.artifact_dir).resolve()
            relative = unquote(path.removeprefix("/artifacts/")).lstrip("/")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                self._send_text(HTTPStatus.FORBIDDEN, "forbidden\n", content_type="text/plain", send_body=send_body)
                return
            if not candidate.is_file():
                self._send_text(HTTPStatus.NOT_FOUND, "artifact_missing\n", content_type="text/plain", send_body=send_body)
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self._send_file(candidate, content_type=content_type, send_body=send_body)

        def _send_file(self, path: Path, *, content_type: str, send_body: bool) -> None:
            body = path.read_bytes()
            self._send_response(HTTPStatus.OK, content_type, body, send_body=send_body)

        def _send_text(self, code: HTTPStatus, text: str, *, content_type: str, send_body: bool) -> None:
            self._send_response(code, content_type, text.encode("utf-8"), send_body=send_body)

        def _send_response(self, code: HTTPStatus, content_type: str, body: bytes, *, send_body: bool) -> None:
            self.send_response(int(code))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'none'; frame-ancestors 'none'",
            )
            if config.no_store:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def _unauthorized(self, *, send_body: bool) -> None:
            body = b"unauthorized\n"
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", f'Basic realm="{config.auth.realm}"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

    return MonitoringRequestHandler


def _authorized(header: str | None, auth: MonitoringAuthConfig, path: str) -> bool:
    if not auth.enabled:
        return True
    if path == "/healthz" and auth.public_healthz:
        return True
    if not header:
        return False
    if auth.bearer_token and header.startswith("Bearer "):
        provided = header.removeprefix("Bearer ").strip()
        return hmac.compare_digest(provided, auth.bearer_token)
    if auth.username and auth.password and header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
        except Exception:
            return False
        username, separator, password = decoded.partition(":")
        return bool(separator) and hmac.compare_digest(username, auth.username) and hmac.compare_digest(
            password,
            auth.password,
        )
    return False


def _file_updated_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "warning", "error": "snapshot_json_unreadable"}
    return payload if isinstance(payload, dict) else {"status": "warning", "error": "snapshot_json_not_object"}


def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return _jsonable(frame.to_dict(orient="records"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
