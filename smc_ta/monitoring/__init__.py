"""Monitoring and performance checks."""

from smc_ta.monitoring.live import (
    LiveMonitoringSnapshot,
    build_live_monitoring_snapshot,
    lifecycle_records_to_frame,
    positions_to_frame,
)
from smc_ta.monitoring.metrics import HealthCheck, health_check, max_drawdown, performance_summary
from smc_ta.monitoring.server import (
    HostedMonitoringConfig,
    HostedMonitoringServer,
    MonitoringAuthConfig,
    build_hosted_monitoring_status,
    create_hosted_monitoring_server,
    monitoring_snapshot_to_jsonable,
    validate_hosted_monitoring_config,
    write_monitoring_snapshot_json,
)

__all__ = [
    "HealthCheck",
    "HostedMonitoringConfig",
    "HostedMonitoringServer",
    "LiveMonitoringSnapshot",
    "MonitoringAuthConfig",
    "build_hosted_monitoring_status",
    "build_live_monitoring_snapshot",
    "create_hosted_monitoring_server",
    "health_check",
    "lifecycle_records_to_frame",
    "max_drawdown",
    "monitoring_snapshot_to_jsonable",
    "performance_summary",
    "positions_to_frame",
    "validate_hosted_monitoring_config",
    "write_monitoring_snapshot_json",
]
