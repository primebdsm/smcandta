"""Deployment and incident operations helpers."""

from smc_ta.ops.alert_validation import (
    AlertChannelBuildResult,
    AlertChannelValidationConfig,
    AlertChannelValidationResult,
    configured_alert_channels_from_env,
    parse_alert_channel_names,
    redact_alert_status,
    validate_alert_channels,
    write_alert_validation_report,
)
from smc_ta.ops.incident import IncidentReportBundle, write_incident_report_bundle
from smc_ta.ops.credentials import (
    OandaCredentialOnboardingConfig,
    OandaCredentialOnboardingResult,
    check_oanda_credential_onboarding,
    oanda_secret_sources,
)
from smc_ta.ops.logging import (
    LogrotateConfig,
    RuntimeLogConfig,
    configure_runtime_logging,
    render_logrotate_config,
    write_logrotate_config,
)
from smc_ta.ops.secrets import (
    CommandSecretSource,
    EnvFileSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    SecretResolutionConfig,
    SecretResolutionIssue,
    SecretResolutionReport,
    resolve_runtime_secrets,
    write_secret_resolution_report,
)
from smc_ta.ops.practice import (
    PracticeStartupRunConfig,
    PracticeStartupRunResult,
    run_practice_startup_monitoring,
)
from smc_ta.ops.supervision import (
    SupervisorArtifactBundle,
    SupervisorConfig,
    render_launchd_plist,
    render_systemd_unit,
    write_supervisor_artifacts,
)

__all__ = [
    "AlertChannelBuildResult",
    "AlertChannelValidationConfig",
    "AlertChannelValidationResult",
    "CommandSecretSource",
    "EnvFileSecretSource",
    "EnvSecretSource",
    "IncidentReportBundle",
    "JsonSecretSource",
    "LogrotateConfig",
    "OandaCredentialOnboardingConfig",
    "OandaCredentialOnboardingResult",
    "PracticeStartupRunConfig",
    "PracticeStartupRunResult",
    "RuntimeLogConfig",
    "SecretResolutionConfig",
    "SecretResolutionIssue",
    "SecretResolutionReport",
    "SupervisorArtifactBundle",
    "SupervisorConfig",
    "check_oanda_credential_onboarding",
    "configured_alert_channels_from_env",
    "configure_runtime_logging",
    "oanda_secret_sources",
    "parse_alert_channel_names",
    "redact_alert_status",
    "render_launchd_plist",
    "render_logrotate_config",
    "render_systemd_unit",
    "resolve_runtime_secrets",
    "run_practice_startup_monitoring",
    "validate_alert_channels",
    "write_alert_validation_report",
    "write_incident_report_bundle",
    "write_logrotate_config",
    "write_secret_resolution_report",
    "write_supervisor_artifacts",
]
