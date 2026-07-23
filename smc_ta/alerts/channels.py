"""Telegram, Discord, and email alert channels."""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol
from urllib.request import Request, urlopen

import pandas as pd


class AlertChannel(Protocol):
    """Alert channel protocol."""

    def send(self, message: str) -> None:
        """Send an alert message."""


@dataclass(frozen=True)
class TelegramAlert:
    """Telegram Bot API alert channel."""

    bot_token: str
    chat_id: str
    timeout: float = 10.0

    def send(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": message}).encode("utf-8")
        request = Request(url, data=payload, method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout):
            return


@dataclass(frozen=True)
class DiscordWebhookAlert:
    """Discord webhook alert channel."""

    webhook_url: str
    timeout: float = 10.0

    def send(self, message: str) -> None:
        payload = json.dumps({"content": message}).encode("utf-8")
        request = Request(self.webhook_url, data=payload, method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout):
            return


@dataclass(frozen=True)
class EmailAlert:
    """SMTP email alert channel."""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    to_address: str
    use_tls: bool = True

    def send(self, message: str) -> None:
        email = EmailMessage()
        email["Subject"] = "SMC TA Alert"
        email["From"] = self.from_address
        email["To"] = self.to_address
        email.set_content(message)
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(email)


@dataclass(frozen=True)
class CompositeAlertChannel:
    """Send alerts to multiple channels."""

    channels: tuple[AlertChannel, ...]

    def send(self, message: str) -> None:
        for channel in self.channels:
            channel.send(message)


def format_signal_alert(symbol: str, signal: pd.Series, *, setup_name: str | None = None) -> str:
    """Format an analysis signal for alert delivery."""

    parts = [
        f"{symbol.upper()} signal: {signal.get('side', 'flat')}",
        f"confidence: {float(signal.get('confidence', 0.0) or 0.0):.2f}",
        f"entry: {signal.get('entry_reference')}",
        f"stop: {signal.get('stop_reference')}",
        f"target: {signal.get('target_reference')}",
        f"reasons: {signal.get('reasons', '')}",
    ]
    if setup_name:
        parts.insert(1, f"setup: {setup_name}")
    return "\n".join(parts)

