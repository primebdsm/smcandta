# Runtime Configuration

The repository includes runtime configuration and live-mode guardrails for Forex bot integration.

Main APIs:

- `RuntimeConfig`
- `validate_runtime_config`
- `assert_runtime_ready`
- `build_oanda_config`
- `build_tradingeconomics_config`
- `ConfigValidationReport`

## Purpose

The config layer separates three concerns:

- Load settings from environment, `.env`-style files, or JSON.
- Validate whether the selected mode and broker are safe to run.
- Redact secrets before logging or reporting.

It does not place trades and does not instantiate a live broker by itself.

## Modes

Supported modes:

- `research`
- `backtest`
- `paper`
- `demo`
- `live`

Live mode is intentionally hard to arm. It requires:

```text
SMC_TA_MODE=live
SMC_TA_ALLOW_LIVE_TRADING=true
SMC_TA_LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_FOREX_RISK
```

For OANDA live mode, it also requires:

```text
SMC_TA_BROKER=oanda
OANDA_ACCOUNT_ID=...
OANDA_TOKEN=...
SMC_TA_OANDA_PRACTICE=false
```

OANDA demo mode must use the practice endpoint:

```text
SMC_TA_MODE=demo
SMC_TA_BROKER=oanda
SMC_TA_OANDA_PRACTICE=true
```

## Environment Variables

The loader accepts `SMC_TA_` prefixed variables and common unprefixed credential variables.

Common settings:

```text
SMC_TA_MODE=paper
SMC_TA_BROKER=paper
SMC_TA_SYMBOLS=EURUSD,GBPUSD,USDJPY
SMC_TA_TIMEFRAMES=M15,H1
SMC_TA_ACCOUNT_CURRENCY=USD
SMC_TA_MAX_TRADE_RISK_PERCENT=1.0
SMC_TA_REQUIRE_NEWS_FILTER=true
SMC_TA_JOURNAL_PATH=journal.csv
SMC_TA_LIFECYCLE_DB_PATH=trade_lifecycle.sqlite
```

Provider credentials:

```text
OANDA_ACCOUNT_ID=...
OANDA_TOKEN=...
TRADING_ECONOMICS_API_KEY=...
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=...
MT5_PATH=...
```

## Validation

```python
from smc_ta.config import RuntimeConfig, assert_runtime_ready

config = RuntimeConfig.from_env()
report = config.validate()
print(report.summary())

assert_runtime_ready(config)
```

`assert_runtime_ready` raises `ConfigValidationError` when errors exist. Warnings do not block startup, but they should be reviewed before demo or live mode.

## Adapter Config Builders

```python
from smc_ta.config import RuntimeConfig, build_oanda_config, build_tradingeconomics_config

runtime = RuntimeConfig.from_env().assert_ready()
oanda_config = build_oanda_config(runtime)
news_config = build_tradingeconomics_config(runtime, importance=(3,))
```

`build_oanda_config` refuses to build from an unsafe live config.

## Redacted Reporting

```python
safe = config.to_safe_dict()
```

Secret fields are redacted:

- `oanda_token`
- `mt5_password`
- `trading_economics_api_key`

Use `to_safe_dict()` in logs, dashboards, and support reports.

## CLI Check

Environment:

```bash
python examples/check_runtime_config.py
```

Env file:

```bash
python examples/check_runtime_config.py --env-file .env.local
```

JSON:

```bash
python examples/check_runtime_config.py --json runtime.json
```

The command exits with code `0` when validation has no errors and `2` when startup should be blocked.
