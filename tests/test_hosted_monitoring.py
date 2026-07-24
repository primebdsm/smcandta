from __future__ import annotations

import base64
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

from smc_ta import (
    HostedMonitoringConfig,
    MonitoringAuthConfig,
    build_live_monitoring_snapshot,
    create_hosted_monitoring_server,
    write_monitoring_snapshot_json,
)
from smc_ta.broker import AccountState, Position


def test_hosted_monitoring_requires_basic_auth_and_serves_status(tmp_path) -> None:
    dashboard = tmp_path / "live_dashboard.html"
    snapshot_path = tmp_path / "snapshot.json"
    dashboard.write_text("<html><body>secure dashboard</body></html>", encoding="utf-8")
    snapshot = build_live_monitoring_snapshot(
        symbol="EURUSD",
        account=AccountState(balance=10_000, equity=10_050),
        open_positions=(
            Position(
                position_id="p1",
                symbol="EURUSD",
                side="long",
                units=1000,
                entry_price=1.1,
                opened_at=pd.Timestamp("2024-01-01T00:00:00Z").to_pydatetime(),
            ),
        ),
        mode="demo",
        broker_name="paper",
    )
    write_monitoring_snapshot_json(snapshot, snapshot_path)
    server = create_hosted_monitoring_server(
        HostedMonitoringConfig(
            dashboard_path=dashboard,
            snapshot_path=snapshot_path,
            host="127.0.0.1",
            port=0,
            auth=MonitoringAuthConfig(username="admin", password="secret"),
        )
    )
    server.start_background()
    try:
        try:
            _request(f"{server.base_url}/dashboard")
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("dashboard request without auth should fail")

        headers = {"Authorization": _basic("admin", "secret")}
        html = _request(f"{server.base_url}/dashboard", headers=headers)
        status = json.loads(_request(f"{server.base_url}/status.json", headers=headers))
        raw_snapshot = json.loads(_request(f"{server.base_url}/snapshot.json", headers=headers))

        assert "secure dashboard" in html
        assert status["ok"]
        assert status["snapshot"]["symbol"] == "EURUSD"
        assert raw_snapshot["open_position_count"] == 1
    finally:
        server.shutdown()


def test_hosted_monitoring_bearer_auth_artifacts_and_traversal_protection(tmp_path) -> None:
    dashboard = tmp_path / "live_dashboard.html"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    dashboard.write_text("<html>dashboard</html>", encoding="utf-8")
    (artifacts / "report.txt").write_text("artifact-ok", encoding="utf-8")
    server = create_hosted_monitoring_server(
        HostedMonitoringConfig(
            dashboard_path=dashboard,
            artifact_dir=artifacts,
            host="127.0.0.1",
            port=0,
            auth=MonitoringAuthConfig(bearer_token="token"),
        )
    )
    server.start_background()
    try:
        headers = {"Authorization": "Bearer token"}
        artifact = _request(f"{server.base_url}/artifacts/report.txt", headers=headers)
        assert artifact == "artifact-ok"

        try:
            _request(f"{server.base_url}/artifacts/../live_dashboard.html", headers=headers)
        except HTTPError as exc:
            assert exc.code in {403, 404}
        else:
            raise AssertionError("path traversal should fail")
    finally:
        server.shutdown()


def test_hosted_monitoring_public_healthz_and_missing_dashboard(tmp_path) -> None:
    server = create_hosted_monitoring_server(
        HostedMonitoringConfig(
            dashboard_path=tmp_path / "missing.html",
            host="127.0.0.1",
            port=0,
            auth=MonitoringAuthConfig(username="admin", password="secret", public_healthz=True),
        )
    )
    server.start_background()
    try:
        try:
            _request(f"{server.base_url}/healthz")
        except HTTPError as exc:
            assert exc.code == 503
            assert "dashboard_missing" in exc.read().decode("utf-8")
        else:
            raise AssertionError("missing dashboard should fail healthz")
    finally:
        server.shutdown()


def _request(url: str, *, headers: dict[str, str] | None = None) -> str:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"
