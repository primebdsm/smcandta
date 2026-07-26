from __future__ import annotations

import json

from smc_ta import AlertChannelValidationConfig, PracticeStartupRunConfig, run_practice_startup_monitoring
import smc_ta.ops.alert_validation as alert_validation


def test_alert_validation_blocks_when_no_real_channel_is_configured(tmp_path) -> None:
    report_path = tmp_path / "alert_validation.json"

    result = alert_validation.validate_alert_channels(
        AlertChannelValidationConfig(
            channel_names=("telegram", "discord"),
            output_report=report_path,
        ),
        env={},
    )

    assert not result.ok
    assert result.summary() == "alert_channels:alert_validation_no_real_channels_configured"
    assert result.build.configured_channels == ()
    assert set(result.build.missing_channels) == {"telegram", "discord"}
    assert result.statuses[-1].blocking
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["configured_channels"] == []
    assert payload["output_report"] == str(report_path)


def test_alert_validation_redacts_secrets_from_failed_delivery(monkeypatch, tmp_path) -> None:
    class FailingTelegramAlert:
        def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0) -> None:
            self.bot_token = bot_token
            self.chat_id = chat_id

        def send(self, message: str) -> None:
            raise RuntimeError(f"token {self.bot_token} chat {self.chat_id} failed")

    monkeypatch.setattr(alert_validation, "TelegramAlert", FailingTelegramAlert)
    report_path = tmp_path / "telegram_validation.json"

    result = alert_validation.validate_alert_channels(
        AlertChannelValidationConfig(channel_names=("telegram",), output_report=report_path),
        env={
            "SMC_TA_TELEGRAM_BOT_TOKEN": "secret-telegram-token",
            "SMC_TA_TELEGRAM_CHAT_ID": "123456789",
        },
    )

    report_text = report_path.read_text(encoding="utf-8")
    status = result.statuses[0]

    assert not result.ok
    assert status.blocking
    assert result.build.configured_channels == ("telegram",)
    assert "secret-telegram-token" not in status.message
    assert "123456789" not in status.message
    assert "secret-telegram-token" not in report_text
    assert "123456789" not in report_text
    assert "TELEGRAM_BOT_TOKEN" in report_text


def test_alert_validation_reports_invalid_email_config_without_crashing() -> None:
    result = alert_validation.validate_alert_channels(
        AlertChannelValidationConfig(channel_names=("email",), require_configured=False, blocking_on_failure=False),
        env={
            "SMC_TA_SMTP_HOST": "smtp.example.com",
            "SMC_TA_SMTP_PORT": "not-a-port",
            "SMC_TA_SMTP_USERNAME": "operator",
            "SMC_TA_SMTP_PASSWORD": "secret-password",
            "SMC_TA_EMAIL_FROM": "bot@example.com",
            "SMC_TA_EMAIL_TO": "operator@example.com",
        },
    )

    assert result.ok
    assert result.warning
    assert result.build.invalid_channels == ("email",)
    assert result.statuses[0].warning
    assert result.statuses[0].message == "invalid_email_config:ValueError"


def test_practice_startup_monitoring_validates_real_alerts_from_env(monkeypatch, tmp_path) -> None:
    class FakeDiscordWebhookAlert:
        messages: list[tuple[str, str]] = []

        def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
            self.webhook_url = webhook_url

        def send(self, message: str) -> None:
            type(self).messages.append((self.webhook_url, message))

    monkeypatch.setattr(alert_validation, "DiscordWebhookAlert", FakeDiscordWebhookAlert)

    result = run_practice_startup_monitoring(
        PracticeStartupRunConfig(
            broker="paper",
            output_dir=tmp_path / "practice_alerts",
            candle_limit=120,
            validate_real_alerts=True,
            require_real_alert_channel=True,
            real_alert_channels=("discord",),
            probe_memory_alert=False,
        ),
        env={"SMC_TA_DISCORD_WEBHOOK_URL": "https://discord.example/webhook/secret-token"},
    )

    assert result.ok
    assert result.alert_delivery[0].channel_name == "discord"
    assert result.alert_delivery[0].ok
    assert "alert_validation" in result.artifacts
    assert FakeDiscordWebhookAlert.messages

    report_text = result.artifacts["alert_validation"].read_text(encoding="utf-8")
    assert "secret-token" not in report_text
    assert "DISCORD_WEBHOOK_URL" in report_text
