"""Secret resolution helpers for deployment-safe runtime config."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

import pandas as pd

from smc_ta.config import load_env_file, redact_secret

IssueSeverity = Literal["info", "warning", "blocking"]
CommandOutputFormat = Literal["json", "env"]


class SecretSource(Protocol):
    """Protocol for a local or external secret source."""

    name: str

    def load(self, *, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        """Load secret values."""


@dataclass(frozen=True)
class EnvSecretSource:
    """Read secrets from process environment."""

    keys: tuple[str, ...] = ()
    prefix: str = ""
    name: str = "env"

    def load(self, *, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        source = os.environ if env is None else env
        values: dict[str, str] = {}
        candidate_keys = self.keys or tuple(source.keys())
        for key in candidate_keys:
            full_key = f"{self.prefix}{key}" if self.prefix and not key.startswith(self.prefix) else key
            value = source.get(full_key)
            if value is not None and str(value).strip() != "":
                values[full_key] = str(value)
        return values


@dataclass(frozen=True)
class EnvFileSecretSource:
    """Read secrets from a `.env`-style file."""

    path: str | Path
    name: str = "env_file"

    def load(self, *, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        del env
        return load_env_file(self.path)


@dataclass(frozen=True)
class JsonSecretSource:
    """Read secrets from a flat JSON object."""

    path: str | Path
    name: str = "json_file"

    def load(self, *, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        del env
        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("secret JSON file must contain an object")
        return {str(key): str(value) for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class CommandSecretSource:
    """Read secrets from an external command.

    The command should print either a JSON object or `.env` style `KEY=value`
    lines. This makes the repository compatible with tools such as 1Password,
    AWS Secrets Manager, GCP Secret Manager, macOS Keychain, or any internal
    secret broker without adding provider SDK dependencies.
    """

    command: tuple[str, ...]
    output_format: CommandOutputFormat = "json"
    timeout_seconds: float = 10.0
    name: str = "command"

    def load(self, *, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        if not self.command:
            raise ValueError("command secret source requires at least one command argument")
        completed = subprocess.run(
            list(self.command),
            capture_output=True,
            check=False,
            env=dict(env) if env is not None else None,
            text=True,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(f"secret command failed with exit code {completed.returncode}: {stderr}")
        output = completed.stdout.strip()
        if self.output_format == "json":
            payload = json.loads(output or "{}")
            if not isinstance(payload, dict):
                raise ValueError("secret command JSON output must contain an object")
            return {str(key): str(value) for key, value in payload.items() if value is not None}
        return _parse_env_text(output)


@dataclass(frozen=True)
class SecretResolutionIssue:
    """One secret resolution issue."""

    severity: IssueSeverity
    code: str
    message: str
    source: str | None = None
    key: str | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class SecretResolutionConfig:
    """Controls how deployment secrets are loaded and validated."""

    sources: tuple[SecretSource, ...] = ()
    required_keys: tuple[str, ...] = ()
    allow_empty: bool = False
    redact_visible: int = 4


@dataclass(frozen=True)
class SecretResolutionReport:
    """Redaction-safe result of loading runtime secrets."""

    values: Mapping[str, str]
    used_sources: Mapping[str, str]
    issues: tuple[SecretResolutionIssue, ...] = ()
    checked_at: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.now(tz="UTC"))
    redact_visible: int = 4

    @property
    def ok(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_issues(self) -> tuple[SecretResolutionIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(issue.code for issue in self.blocking_issues))

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(issue.key for issue in self.issues if issue.code == "missing_required_secret" and issue.key)

    def summary(self) -> str:
        if self.ok:
            return "secrets_ok"
        return ";".join(self.blocking_reasons)

    def safe_values(self) -> dict[str, str | None]:
        """Return secret values redacted for logs and reports."""

        return {
            key: redact_secret(value, visible=self.redact_visible)
            for key, value in sorted(self.values.items())
        }

    def to_frame(self) -> pd.DataFrame:
        """Return secret resolution issues as a DataFrame."""

        return pd.DataFrame([asdict(issue) for issue in self.issues])

    def to_safe_dict(self) -> dict[str, Any]:
        """Return a redaction-safe JSON dictionary."""

        return {
            "ok": self.ok,
            "summary": self.summary(),
            "checked_at": self.checked_at.isoformat(),
            "values": self.safe_values(),
            "used_sources": dict(sorted(self.used_sources.items())),
            "issues": [asdict(issue) for issue in self.issues],
        }


def resolve_runtime_secrets(
    config: SecretResolutionConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> SecretResolutionReport:
    """Load secrets from configured sources and validate required keys."""

    cfg = config or SecretResolutionConfig(sources=(EnvSecretSource(),))
    values: dict[str, str] = {}
    used_sources: dict[str, str] = {}
    issues: list[SecretResolutionIssue] = []

    for source in cfg.sources:
        try:
            loaded = dict(source.load(env=env))
        except Exception as exc:
            issues.append(
                SecretResolutionIssue(
                    severity="blocking",
                    code="secret_source_failed",
                    message=str(exc),
                    source=source.name,
                )
            )
            continue
        if not loaded:
            issues.append(
                SecretResolutionIssue(
                    severity="warning",
                    code="secret_source_empty",
                    message="secret source returned no values",
                    source=source.name,
                )
            )
        for key, value in loaded.items():
            if not cfg.allow_empty and str(value).strip() == "":
                issues.append(
                    SecretResolutionIssue(
                        severity="blocking",
                        code="empty_secret_value",
                        message="secret value is empty",
                        source=source.name,
                        key=str(key),
                    )
                )
                continue
            values[str(key)] = str(value)
            used_sources[str(key)] = source.name

    for key in cfg.required_keys:
        if _required_key_missing(key, values):
            issues.append(
                SecretResolutionIssue(
                    severity="blocking",
                    code="missing_required_secret",
                    message="required secret was not resolved",
                    key=key,
                )
            )

    if not issues:
        issues.append(
            SecretResolutionIssue(
                severity="info",
                code="secrets_loaded",
                message="secret resolution completed",
            )
        )

    return SecretResolutionReport(
        values=values,
        used_sources=used_sources,
        issues=tuple(issues),
        redact_visible=cfg.redact_visible,
    )


def write_secret_resolution_report(report: SecretResolutionReport, path: str | Path) -> Path:
    """Write a redacted secret resolution report to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_safe_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return output


def _required_key_missing(key: str, values: Mapping[str, str]) -> bool:
    if key in values and values[key].strip():
        return False
    prefixed = f"SMC_TA_{key}"
    return prefixed not in values or not values[prefixed].strip()


def _parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
