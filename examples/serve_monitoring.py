"""Serve the live dashboard through an authenticated read-only HTTP server."""

from __future__ import annotations

import argparse
import os

from smc_ta import HostedMonitoringConfig, MonitoringAuthConfig, create_hosted_monitoring_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", default="live_dashboard.html")
    parser.add_argument("--snapshot")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--username")
    parser.add_argument("--password-env", default="SMC_TA_MONITOR_PASSWORD")
    parser.add_argument("--bearer-token-env", default="SMC_TA_MONITOR_BEARER_TOKEN")
    parser.add_argument("--public-healthz", action="store_true")
    parser.add_argument("--no-auth", action="store_true")
    args = parser.parse_args()

    password = os.getenv(args.password_env) if args.password_env else None
    bearer_token = os.getenv(args.bearer_token_env) if args.bearer_token_env else None
    auth = MonitoringAuthConfig(
        enabled=not args.no_auth,
        username=args.username,
        password=password,
        bearer_token=bearer_token,
        public_healthz=args.public_healthz,
    )
    config = HostedMonitoringConfig(
        dashboard_path=args.dashboard,
        snapshot_path=args.snapshot,
        artifact_dir=args.artifact_dir,
        host=args.host,
        port=args.port,
        auth=auth,
    )
    server = create_hosted_monitoring_server(config)
    print(f"monitoring_server={server.base_url}")
    print("routes=/dashboard,/status.json,/snapshot.json,/healthz,/artifacts/<path>")
    server.serve_forever()


if __name__ == "__main__":
    main()
