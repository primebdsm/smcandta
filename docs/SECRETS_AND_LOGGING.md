# Secrets And Runtime Logging

This document covers deployment-safe secret loading and bot runtime logging.

The repository never needs broker credentials committed to Git. Secrets should come from environment variables, local protected files, or external secret-manager commands.

## Secret Resolution APIs

```python
from smc_ta import (
    CommandSecretSource,
    EnvFileSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    SecretResolutionConfig,
    resolve_runtime_secrets,
    write_secret_resolution_report,
)
```

## CLI Check

Environment only:

```bash
python examples/check_secrets.py --required OANDA_ACCOUNT_ID,OANDA_TOKEN
```

`.env` file plus redacted report:

```bash
python examples/check_secrets.py \
  --env-file .env.demo \
  --required OANDA_ACCOUNT_ID,OANDA_TOKEN \
  --output reports/startup/secrets.json
```

External command that prints JSON:

```bash
python examples/check_secrets.py \
  --command "python scripts/read_secret_json.py" \
  --command-format json \
  --required OANDA_ACCOUNT_ID,OANDA_TOKEN
```

External command that prints `.env` style lines:

```bash
python examples/check_secrets.py \
  --command "security-tool export smc-ta-demo" \
  --command-format env \
  --required OANDA_ACCOUNT_ID,OANDA_TOKEN
```

The CLI prints only redacted values. It exits with `0` when required secrets are present and `2` when startup should stay blocked.

## Python Usage

```python
from smc_ta import EnvSecretSource, SecretResolutionConfig, RuntimeConfig, resolve_runtime_secrets

report = resolve_runtime_secrets(
    SecretResolutionConfig(
        sources=(EnvSecretSource(keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN")),),
        required_keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"),
    )
)

if not report.ok:
    raise RuntimeError(report.summary())

runtime = RuntimeConfig.from_env({**report.values})
```

In a real deployment, merge `report.values` with non-secret runtime variables such as mode, broker, symbols, and timeframes.

## Source Priority

Secret sources are evaluated in order. Later sources override earlier sources.

This allows a deployment to use:

1. environment defaults
2. local `.env` overrides for demo
3. external secret-manager command overrides for production

## Supported Sources

`EnvSecretSource`

Reads current process environment or a supplied environment mapping.

`EnvFileSecretSource`

Reads a `.env`-style file using the same parser as `RuntimeConfig.from_env_file`.

`JsonSecretSource`

Reads a flat JSON object:

```json
{
  "OANDA_ACCOUNT_ID": "...",
  "OANDA_TOKEN": "..."
}
```

`CommandSecretSource`

Runs a command and parses JSON or `.env` style output. This is the integration point for external secret managers without adding provider SDK dependencies.

The command should run locally with the process user's permissions. Do not log raw command output.

## Redacted Reports

`SecretResolutionReport.safe_values()` and `write_secret_resolution_report()` redact secret values before writing logs or JSON.

The raw values remain available in `report.values` for constructing runtime config or process environment.

## Runtime Logging

Use `configure_runtime_logging` at the start of a bot entrypoint:

```python
from smc_ta import RuntimeLogConfig, configure_runtime_logging

logger = configure_runtime_logging(
    RuntimeLogConfig(
        log_dir="logs",
        logger_name="smc_ta.live",
        file_name="bot.log",
        json_lines=True,
    )
)
```

This creates a rotating log file and optional console output.

Recommended event fields:

- `symbol`
- `timeframe`
- `cycle_id`
- `action`
- `signal_side`
- `confidence`
- `blocked_reason`
- `position_id`
- `broker_order_id`
- `restart_sync_summary`
- `lifecycle_recovery_summary`
- `preflight_summary`

Do not log raw tokens, account passwords, or full secret-manager payloads.

## Startup Placement

Recommended startup order:

1. Configure runtime logging.
2. Resolve secrets and write a redacted secret report.
3. Build `RuntimeConfig`.
4. Run broker restart sync.
5. Run lifecycle restart recovery.
6. Run preflight readiness.
7. Start the bot loop only when every report is OK.

If secret resolution fails, do not construct broker adapters.
