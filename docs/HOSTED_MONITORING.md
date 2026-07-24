# Hosted Authenticated Monitoring

This repository includes a dependency-free hosted monitoring server for dashboard artifacts.

It serves the existing static dashboard and snapshot JSON through a read-only HTTP server with Basic or Bearer authentication.

## Safety Rule

Bind to `127.0.0.1` by default.

If you expose the monitor outside the local machine, put it behind HTTPS, a VPN, SSH tunnel, or a trusted reverse proxy. Basic and Bearer authentication protect access, but they are not encryption.

The hosted monitor does not place orders, close positions, reset emergency stops, install services, or mutate broker state.

## Main APIs

```python
from smc_ta import (
    HostedMonitoringConfig,
    MonitoringAuthConfig,
    create_hosted_monitoring_server,
    write_monitoring_snapshot_json,
)
```

## Generate Dashboard And Snapshot

```bash
python examples/live_dashboard_monitor.py \
  --output reports/dashboard/live.html \
  --snapshot-output reports/dashboard/snapshot.json \
  --refresh-seconds 30
```

In a real bot loop, write the HTML and snapshot after each cycle:

```python
from smc_ta import write_live_dashboard, write_monitoring_snapshot_json

write_live_dashboard("reports/dashboard/live.html", snapshot, refresh_seconds=30)
write_monitoring_snapshot_json(snapshot, "reports/dashboard/snapshot.json")
```

## Serve With Basic Auth

```bash
export SMC_TA_MONITOR_PASSWORD="change-me"

python examples/serve_monitoring.py \
  --dashboard reports/dashboard/live.html \
  --snapshot reports/dashboard/snapshot.json \
  --artifact-dir reports \
  --host 127.0.0.1 \
  --port 8080 \
  --username admin \
  --password-env SMC_TA_MONITOR_PASSWORD
```

Open:

- `http://127.0.0.1:8080/dashboard`
- `http://127.0.0.1:8080/status.json`
- `http://127.0.0.1:8080/snapshot.json`
- `http://127.0.0.1:8080/healthz`

## Serve With Bearer Auth

```bash
export SMC_TA_MONITOR_BEARER_TOKEN="change-me"

python examples/serve_monitoring.py \
  --dashboard reports/dashboard/live.html \
  --snapshot reports/dashboard/snapshot.json \
  --host 127.0.0.1 \
  --port 8080
```

Then send:

```text
Authorization: Bearer change-me
```

## Python Server

```python
from smc_ta import HostedMonitoringConfig, MonitoringAuthConfig, create_hosted_monitoring_server

server = create_hosted_monitoring_server(
    HostedMonitoringConfig(
        dashboard_path="reports/dashboard/live.html",
        snapshot_path="reports/dashboard/snapshot.json",
        artifact_dir="reports",
        host="127.0.0.1",
        port=8080,
        auth=MonitoringAuthConfig(username="admin", password="change-me"),
    )
)

server.serve_forever()
```

For embedded tests or a supervisor-managed process:

```python
thread = server.start_background()
server.shutdown()
```

## Routes

`/dashboard`

Serves the latest dashboard HTML.

`/status.json`

Returns server status, dashboard freshness, snapshot status, and snapshot payload if configured.

`/snapshot.json`

Serves the latest monitoring snapshot JSON.

`/healthz`

Returns `200` when the dashboard file exists and `503` when it is missing. Keep it authenticated unless your process supervisor requires public health checks.

`/artifacts/<path>`

Serves read-only files under the configured artifact directory. Path traversal outside the artifact root is blocked.

## Security Headers

The server sends:

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- a restrictive Content Security Policy that blocks scripts

## Reverse Proxy Pattern

For a VPS:

1. Run the monitor on `127.0.0.1:8080`.
2. Put Nginx, Caddy, Cloudflare Tunnel, Tailscale, or an SSH tunnel in front of it.
3. Terminate TLS at the proxy or tunnel.
4. Keep Basic or Bearer auth enabled inside the monitor.
5. Do not expose broker credentials, SQLite files, or raw logs through `artifact_dir`.

## Incident Behavior

If `/healthz` fails, `/status.json` reports `blocking`, or the dashboard timestamp is stale, treat monitoring as unsafe. Keep trading blocked until broker state is checked manually and follow `docs/INCIDENT_PROCEDURES.md`.
