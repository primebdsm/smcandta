"""Redaction-safe validation for real alert channels."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from smc_ta.alerts import AlertChannel, DiscordWebhookAlert, EmailAlert, TelegramAlert
from smc_ta.config import load_env_file, redact_secret
from smc_ta.monitoring import AlertDeliveryStatus, alert_delivery_frame, probe_alert_channel

SUPPORTED_ALERT_CHANNELS = ("telegram", "discord", "email")
DEFAULT_ALERT_PROBE_MESSAGE = "SMC TA alert delivery validation probe"


@dataclass(frozen=True)
class AlertChannelValidationConfig:
    """Settings for a real alert channel validation run."""

    env_file: str | Path | None = None
    channel_names: tuple[str, ...] = SUPPORTED_ALERT_CHANNELS
    probe_message: str = DEFAULT_ALERT_PROBE_MESSAGE
    include_memory: bool = False
    require_configured: bool = True
    blocking_on_failure: bool = True
    timeout: float = 10.0
    output_report: str | Path | None = None


@dataclass(frozen=True)
class AlertChannelBuildResult:
    """Configured alert channels plus redaction-safe configuration evidence."""

    channels: tuple[tuple[str, AlertChannel], ...] = ()
    configured_channels: tuple[str, ...] = ()
    missing_channels: tuple[str, ...] = ()
    partial_channels: tuple[str, ...] = ()
    invalid_channels: tuple[str, ...] = ()
    missing_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    configured_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    config_errors: Mapping[str, str] = field(default_factory=dict)
    secret_values: tuple[str, ...] = ()

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "configured_channels": list(self.configured_channels),
            "missing_channels": list(self.missing_channels),
            "partial_channels": list(self.partial_channels),
            "invalid_channels": list(self.invalid_channels),
            "missing_keys": {name: list(keys) for name, keys in sorted(self.missing_keys.items())},
            "configured_keys": {name: list(keys) for name, keys in sorted(self.configured_keys.items())},
            "config_errors": dict(sorted(self.config_errors.items())),
        }


@dataclass(frozen=True)
class AlertChannelValidationResult:
    """Result of alert channel discovery and probe delivery."""

    config: AlertChannelValidationConfig
    build: AlertChannelBuildResult
    statuses: tuple[AlertDeliveryStatus, ...] = ()
    checked_at: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.now(tz="UTC"))
    output_report: Path | None = None

    @property
    def ok(self) -> bool:
        return not any(status.blocking for status in self.statuses)

    @property
    def warning(self) -> bool:
        return any(status.warning for status in self.statuses)

    def summary(self) -> str:
        if self.ok and not self.warning:
            return "alert_validation_ok"
        parts = [f"{status.channel_name}:{status.message}" for status in self.statuses if status.blocking]
        parts.extend(f"warning:{status.channel_name}:{status.message}" for status in self.statuses if status.warning)
        return ";".join(parts or ("alert_validation_warning",))

    def to_frame(self) -> pd.DataFrame:
        return alert_delivery_frame(self.statuses)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "checked_at": _utc_timestamp(self.checked_at).isoformat(),
            "configured_channels": list(self.build.configured_channels),
            "missing_channels": list(self.build.missing_channels),
            "partial_channels": list(self.build.partial_channels),
            "invalid_channels": list(self.build.invalid_channels),
            "config": {
                "channel_names": list(self.config.channel_names),
                "include_memory": self.config.include_memory,
                "require_configured": self.config.require_configured,
                "blocking_on_failure": self.config.blocking_on_failure,
                "timeout": self.config.timeout,
                "env_file": str(self.config.env_file) if self.config.env_file is not None else None,
            },
            "configuration": self.build.to_safe_dict(),
            "alert_delivery": [status.to_dict() for status in self.statuses],
            "output_report": str(self.output_report) if self.output_report is not None else None,
        }


def configured_alert_channels_from_env(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = None,
    channel_names: Iterable[str] = SUPPORTED_ALERT_CHANNELS,
    timeout: float = 10.0,
) -> AlertChannelBuildResult:
    """Build real alert channels from env or `.env` without exposing secret values."""

    source = _merged_env(env, env_file)
    normalized_names = _normalize_channel_names(channel_names)
    channels: list[tuple[str, AlertChannel]] = []
    configured_channels: list[str] = []
    missing_channels: list[str] = []
    partial_channels: list[str] = []
    invalid_channels: list[str] = []
    missing_keys: dict[str, tuple[str, ...]] = {}
    configured_keys: dict[str, tuple[str, ...]] = {}
    config_errors: dict[str, str] = {}
    secret_values: list[str] = []

    for name in normalized_names:
        if name == "telegram":
            values = {
                "TELEGRAM_BOT_TOKEN": _secret(source, "TELEGRAM_BOT_TOKEN"),
                "TELEGRAM_CHAT_ID": _secret(source, "TELEGRAM_CHAT_ID"),
            }
            channel = (
                TelegramAlert(values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_CHAT_ID"], timeout=timeout)
                if all(values.values())
                else None
            )
        elif name == "discord":
            values = {"DISCORD_WEBHOOK_URL": _secret(source, "DISCORD_WEBHOOK_URL")}
            channel = (
                DiscordWebhookAlert(values["DISCORD_WEBHOOK_URL"], timeout=timeout)
                if all(values.values())
                else None
            )
        elif name == "email":
            values = {
                "SMTP_HOST": _secret(source, "SMTP_HOST"),
                "SMTP_PORT": _secret(source, "SMTP_PORT"),
                "SMTP_USERNAME": _secret(source, "SMTP_USERNAME"),
                "SMTP_PASSWORD": _secret(source, "SMTP_PASSWORD"),
                "EMAIL_FROM": _secret(source, "EMAIL_FROM"),
                "EMAIL_TO": _secret(source, "EMAIL_TO"),
            }
            if all(values.values()):
                try:
                    channel = _build_email(values, source)
                except Exception as exc:
                    channel = None
                    invalid_channels.append(name)
                    config_errors[name] = f"invalid_email_config:{type(exc).__name__}"
                else:
                    config_errors.pop(name, None)
            else:
                channel = None
        else:
            raise ValueError(f"unsupported alert channel: {name}")

        present = tuple(key for key, value in values.items() if value)
        missing = tuple(key for key, value in values.items() if not value)
        if channel is not None:
            channels.append((name, channel))
            configured_channels.append(name)
            configured_keys[name] = present
            for value in values.values():
                secret_values.extend(_secret_fragments(value))
        elif name in invalid_channels:
            configured_keys[name] = present
            for value in values.values():
                secret_values.extend(_secret_fragments(value))
        elif present:
            partial_channels.append(name)
            configured_keys[name] = present
            missing_keys[name] = missing
            for value in values.values():
                secret_values.extend(_secret_fragments(value))
        else:
            missing_channels.append(name)
            missing_keys[name] = missing

    return AlertChannelBuildResult(
        channels=tuple(channels),
        configured_channels=tuple(configured_channels),
        missing_channels=tuple(missing_channels),
        partial_channels=tuple(partial_channels),
        invalid_channels=tuple(invalid_channels),
        missing_keys=missing_keys,
        configured_keys=configured_keys,
        config_errors=config_errors,
        secret_values=tuple(dict.fromkeys(secret_values)),
    )


def validate_alert_channels(
    config: AlertChannelValidationConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
    channels: Iterable[tuple[str, AlertChannel]] | None = None,
) -> AlertChannelValidationResult:
    """Probe configured alert channels and return a redaction-safe report."""

    cfg = config or AlertChannelValidationConfig()
    build = configured_alert_channels_from_env(
        env,
        env_file=cfg.env_file,
        channel_names=cfg.channel_names,
        timeout=cfg.timeout,
    )
    discovered_channels = list(build.channels)
    if channels is not None:
        discovered_channels.extend((str(name), channel) for name, channel in channels)

    statuses: list[AlertDeliveryStatus] = []
    for name, channel in discovered_channels:
        status = probe_alert_channel(
            channel,
            channel_name=name,
            message=cfg.probe_message,
            blocking_on_failure=cfg.blocking_on_failure,
        )
        statuses.append(redact_alert_status(status, build.secret_values))

    for name in build.partial_channels:
        statuses.append(
            _config_status(
                name,
                "alert_channel_partially_configured",
                build.missing_keys.get(name, ()),
                blocking=cfg.blocking_on_failure,
            )
        )
    for name in build.invalid_channels:
        statuses.append(
            AlertDeliveryStatus(
                channel_name=name,
                status="blocking" if cfg.blocking_on_failure else "warning",
                checked_at=_utc_timestamp(),
                latency_ms=0.0,
                delivered=False,
                message=build.config_errors.get(name, "alert_channel_invalid_config"),
            )
        )

    if cfg.include_memory:
        statuses.append(
            probe_alert_channel(
                _MemoryAlert(),
                channel_name="memory",
                message=cfg.probe_message,
                blocking_on_failure=cfg.blocking_on_failure,
            )
        )

    real_channels = tuple(name for name, _ in discovered_channels if name != "memory")
    if cfg.require_configured and not real_channels:
        statuses.append(
            _config_status(
                "alert_channels",
                "alert_validation_no_real_channels_configured",
                (),
                blocking=True,
            )
        )

    result = AlertChannelValidationResult(
        config=cfg,
        build=build,
        statuses=tuple(statuses),
        checked_at=_utc_timestamp(),
    )
    if cfg.output_report is not None:
        output = Path(cfg.output_report)
        result = replace(result, output_report=output)
        write_alert_validation_report(result, output)
    return result


def write_alert_validation_report(result: AlertChannelValidationResult, path: str | Path) -> Path:
    """Write a redaction-safe alert validation report."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_safe_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output


def redact_alert_status(status: AlertDeliveryStatus, secrets: Iterable[str]) -> AlertDeliveryStatus:
    """Return an alert status with known secret values removed from text fields."""

    secret_values = tuple(value for value in secrets if value)
    return replace(
        status,
        message=_redact_text(status.message, secret_values),
        details=_redact_details(status.details, secret_values),
    )


def parse_alert_channel_names(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Parse comma-separated alert channel names."""

    if value is None:
        return SUPPORTED_ALERT_CHANNELS
    if isinstance(value, str):
        return _normalize_channel_names(item.strip() for item in value.split(",") if item.strip())
    return _normalize_channel_names(value)


def _build_email(values: Mapping[str, str | None], env: Mapping[str, str]) -> EmailAlert:
    return EmailAlert(
        smtp_host=str(values["SMTP_HOST"]),
        smtp_port=int(str(values["SMTP_PORT"])),
        username=str(values["SMTP_USERNAME"]),
        password=str(values["SMTP_PASSWORD"]),
        from_address=str(values["EMAIL_FROM"]),
        to_address=str(values["EMAIL_TO"]),
        use_tls=_to_bool(_secret(env, "SMTP_USE_TLS") or "true"),
    )


def _config_status(
    channel_name: str,
    message: str,
    missing_keys: tuple[str, ...],
    *,
    blocking: bool,
) -> AlertDeliveryStatus:
    return AlertDeliveryStatus(
        channel_name=channel_name,
        status="blocking" if blocking else "warning",
        checked_at=_utc_timestamp(),
        latency_ms=0.0,
        delivered=False,
        message=message,
        details={"missing_keys": list(missing_keys)},
    )


def _merged_env(env: Mapping[str, str] | None, env_file: str | Path | None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    if env_file is not None:
        merged.update(load_env_file(env_file))
    return merged


def _secret(env: Mapping[str, str], key: str) -> str | None:
    for candidate in (f"SMC_TA_{key}", key):
        value = env.get(candidate)
        if value is None:
            continue
        text = str(value).strip()
        if text and not _looks_like_placeholder(text):
            return text
    return None


def _looks_like_placeholder(value: str) -> bool:
    normalized = str(value).strip().lower()
    return (
        normalized in {"...", "changeme", "change-me", "your-token", "your-webhook", "your-chat-id"}
        or normalized.startswith("replace_with")
        or normalized.startswith("your_")
        or normalized.startswith("example_")
    )


def _normalize_channel_names(names: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(name).strip().lower() for name in names if str(name).strip()))
    return normalized or SUPPORTED_ALERT_CHANNELS


def _redact_text(value: object, secrets: Iterable[str]) -> str:
    text = str(value)
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, redact_secret(secret) or "***")
    return text


def _secret_fragments(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    text = str(value).strip()
    if not text:
        return ()
    fragments = [text]
    fragments.extend(part for part in text.replace(":", "/").split("/") if len(part) >= 6)
    return tuple(dict.fromkeys(fragments))


def _redact_details(value: Mapping[str, Any], secrets: Iterable[str]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            redacted[key] = _redact_text(item, secrets)
        elif isinstance(item, (list, tuple)):
            redacted[key] = [_redact_text(part, secrets) if isinstance(part, str) else part for part in item]
        else:
            redacted[key] = item
    return redacted


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _utc_timestamp(value: object | None = None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class _MemoryAlert:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)
