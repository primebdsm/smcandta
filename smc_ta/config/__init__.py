"""Runtime configuration and live-mode guardrails."""

from smc_ta.config.runtime import (
    LIVE_CONFIRMATION_PHRASE,
    ConfigIssue,
    ConfigValidationError,
    ConfigValidationReport,
    RuntimeConfig,
    assert_runtime_ready,
    build_oanda_config,
    build_tradingeconomics_config,
    load_env_file,
    redact_secret,
    validate_runtime_config,
)

__all__ = [
    "LIVE_CONFIRMATION_PHRASE",
    "ConfigIssue",
    "ConfigValidationError",
    "ConfigValidationReport",
    "RuntimeConfig",
    "assert_runtime_ready",
    "build_oanda_config",
    "build_tradingeconomics_config",
    "load_env_file",
    "redact_secret",
    "validate_runtime_config",
]
