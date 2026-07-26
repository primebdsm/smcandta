"""Credential onboarding helpers for broker practice workflows."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from smc_ta.ops.secrets import (
    CommandOutputFormat,
    CommandSecretSource,
    EnvFileSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    SecretResolutionConfig,
    SecretResolutionIssue,
    SecretResolutionReport,
    SecretSource,
    resolve_runtime_secrets,
    write_secret_resolution_report,
)

OANDA_REQUIRED_SECRET_KEYS = ("OANDA_ACCOUNT_ID", "OANDA_TOKEN")
OANDA_PREFIXED_SECRET_KEYS = tuple(f"SMC_TA_{key}" for key in OANDA_REQUIRED_SECRET_KEYS)
OANDA_ACCEPTED_SECRET_KEYS = OANDA_REQUIRED_SECRET_KEYS + OANDA_PREFIXED_SECRET_KEYS


@dataclass(frozen=True)
class OandaCredentialOnboardingConfig:
    """Settings for a redaction-safe OANDA credential onboarding check."""

    env_file: str | Path | None = None
    json_file: str | Path | None = None
    command: tuple[str, ...] = ()
    command_format: CommandOutputFormat = "json"
    output_report: str | Path | None = None
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    startup_output_dir: str | Path = "reports/practice_startup/oanda_latest"
    max_spread_pips: float | None = 2.0


@dataclass(frozen=True)
class OandaCredentialOnboardingResult:
    """Result of an OANDA credential onboarding check."""

    config: OandaCredentialOnboardingConfig
    secret_report: SecretResolutionReport
    output_report: Path | None = None

    @property
    def ok(self) -> bool:
        return self.secret_report.ok

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return self.secret_report.missing_keys

    @property
    def accepted_keys(self) -> tuple[str, ...]:
        return tuple(sorted(key for key in self.secret_report.values if key in OANDA_ACCEPTED_SECRET_KEYS))

    def summary(self) -> str:
        if self.ok:
            return "oanda_credentials_ok"
        return f"oanda_credentials_blocked:{self.secret_report.summary()}"

    def safe_values(self) -> dict[str, str | None]:
        return self.secret_report.safe_values()

    def export_templates(self, *, prefixed: bool = True) -> tuple[str, ...]:
        prefix = "SMC_TA_" if prefixed else ""
        return tuple(f"export {prefix}{key}=..." for key in OANDA_REQUIRED_SECRET_KEYS)

    def startup_command(self) -> str:
        parts = [
            "python",
            "examples/oanda_practice_startup_monitor.py",
            "--broker",
            "oanda",
            "--symbol",
            self.config.symbol.upper(),
            "--timeframe",
            self.config.timeframe,
            "--output-dir",
            str(self.config.startup_output_dir),
        ]
        if self.config.max_spread_pips is not None:
            parts.extend(["--max-spread-pips", str(self.config.max_spread_pips)])
        if self.config.env_file is not None:
            parts.extend(["--env-file", str(self.config.env_file)])
        return " ".join(shlex.quote(part) for part in parts)


def check_oanda_credential_onboarding(
    config: OandaCredentialOnboardingConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> OandaCredentialOnboardingResult:
    """Resolve OANDA practice credentials and optionally write a redacted report."""

    cfg = config or OandaCredentialOnboardingConfig()
    report = resolve_runtime_secrets(
        SecretResolutionConfig(
            sources=oanda_secret_sources(cfg),
            required_keys=OANDA_REQUIRED_SECRET_KEYS,
        ),
        env=env,
    )
    report = _with_placeholder_issues(report)
    output_report = None
    if cfg.output_report is not None:
        output_report = write_secret_resolution_report(report, cfg.output_report)
    return OandaCredentialOnboardingResult(cfg, report, output_report=output_report)


def oanda_secret_sources(config: OandaCredentialOnboardingConfig | None = None) -> tuple[SecretSource, ...]:
    """Return secret sources that accept both bare and `SMC_TA_` OANDA names."""

    cfg = config or OandaCredentialOnboardingConfig()
    sources: list[SecretSource] = [
        EnvSecretSource(keys=OANDA_REQUIRED_SECRET_KEYS),
        EnvSecretSource(keys=OANDA_REQUIRED_SECRET_KEYS, prefix="SMC_TA_", name="env_smc_ta"),
    ]
    if cfg.env_file is not None:
        sources.append(EnvFileSecretSource(cfg.env_file))
    if cfg.json_file is not None:
        sources.append(JsonSecretSource(cfg.json_file))
    if cfg.command:
        sources.append(CommandSecretSource(cfg.command, output_format=cfg.command_format))
    return tuple(sources)


def _with_placeholder_issues(report: SecretResolutionReport) -> SecretResolutionReport:
    issues = list(report.issues)
    for key, value in report.values.items():
        if key in OANDA_ACCEPTED_SECRET_KEYS and _looks_like_placeholder(value):
            issues.append(
                SecretResolutionIssue(
                    severity="blocking",
                    code="placeholder_secret_value",
                    message="secret value still contains placeholder text",
                    source=report.used_sources.get(key),
                    key=key,
                )
            )
    if len(issues) == len(report.issues):
        return report
    return SecretResolutionReport(
        values=report.values,
        used_sources=report.used_sources,
        issues=tuple(issues),
        checked_at=report.checked_at,
        redact_visible=report.redact_visible,
    )


def _looks_like_placeholder(value: str) -> bool:
    normalized = str(value).strip().lower()
    return (
        normalized in {"...", "changeme", "change-me", "your-token", "your-account-id"}
        or normalized.startswith("replace_with_")
        or normalized.startswith("your_")
    )
