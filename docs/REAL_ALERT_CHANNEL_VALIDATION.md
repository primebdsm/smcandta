# Real Alert Channel Validation

This workflow proves that configured operator alert channels can receive a real probe message before a repeated demo/live-style bot loop starts.

It supports:

- Telegram Bot API
- Discord webhook
- SMTP email

The validator sends an explicit probe. It does not generate trading signals, place orders, close positions, or change broker state.

## Environment Keys

Every key accepts either bare names or the `SMC_TA_` prefix.

Telegram:

```bash
SMC_TA_TELEGRAM_BOT_TOKEN=...
SMC_TA_TELEGRAM_CHAT_ID=...
```

Discord:

```bash
SMC_TA_DISCORD_WEBHOOK_URL=...
```

Email:

```bash
SMC_TA_SMTP_HOST=smtp.example.com
SMC_TA_SMTP_PORT=587
SMC_TA_SMTP_USERNAME=...
SMC_TA_SMTP_PASSWORD=...
SMC_TA_EMAIL_FROM=bot@example.com
SMC_TA_EMAIL_TO=operator@example.com
SMC_TA_SMTP_USE_TLS=true
```

Placeholder values such as `replace_with_...`, `your_...`, and `...` are treated as missing.

## Standalone Validation

```bash
python examples/validate_alert_channels.py \
  --env-file .env.demo \
  --channels telegram,discord,email \
  --output reports/startup/alert_validation.json
```

Default behavior:

- at least one real channel must be fully configured
- delivery failure is `blocking`
- JSON output is redaction-safe
- printed output contains channel names and status only, not tokens or webhook URLs

Use this for a non-blocking visibility check:

```bash
python examples/validate_alert_channels.py \
  --env-file .env.demo \
  --warning-on-failure \
  --allow-unconfigured
```

## Startup Drill Integration

```bash
python examples/oanda_practice_startup_monitor.py \
  --broker oanda \
  --symbol EURUSD \
  --max-spread-pips 2 \
  --env-file .env.demo \
  --output-dir reports/practice_startup/oanda \
  --validate-alerts \
  --require-real-alert \
  --alert-blocking
```

This writes:

- `startup/alert_delivery.csv`
- `startup/alert_validation.json`
- `dashboard/snapshot.json`
- dashboard Alert Delivery panel

If `--require-real-alert` is enabled and no real channel is configured, startup is blocked with `alert_validation_no_real_channels_configured`.

If `--alert-blocking` is enabled and the delivery probe fails, startup is blocked. Without `--alert-blocking`, delivery failure is a warning.

## Python API

```python
from smc_ta import AlertChannelValidationConfig, validate_alert_channels

result = validate_alert_channels(
    AlertChannelValidationConfig(
        env_file=".env.demo",
        channel_names=("telegram", "discord", "email"),
        require_configured=True,
        blocking_on_failure=True,
        output_report="reports/startup/alert_validation.json",
    )
)

if not result.ok:
    raise RuntimeError(result.summary())
```

## Operational Use

Run this after credential onboarding and before the practice startup monitoring drill. A real alert probe increases safety by proving the operator can be notified when the bot blocks, hits an emergency stop, loses broker connectivity, or records an incident.

The validator is a visibility control, not a profit source. It can improve realized outcomes indirectly by reducing unattended failure time, making stop conditions visible, and preserving incident evidence.
