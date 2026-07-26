# OANDA Credential Onboarding

This package checks OANDA practice credentials without printing raw account IDs or tokens.

It solves one specific deployment problem: Codex or the bot process can only run the real OANDA practice startup drill when credentials are visible inside that same process environment or loaded from a local env/secret source.

## Safe Env File

Copy the example file:

```bash
cp .env.demo.example .env.demo
```

Fill only the copied `.env.demo` file:

```bash
SMC_TA_OANDA_ACCOUNT_ID=your_practice_account_id
SMC_TA_OANDA_TOKEN=your_practice_token
SMC_TA_OANDA_PRACTICE=true
```

`.env.demo` is ignored by Git. `.env.demo.example` is safe to commit because it contains placeholders only.

If you run onboarding against `.env.demo.example` without replacing the placeholder values, the check blocks with `placeholder_secret_value`.

The onboarding and startup tools also accept the shorter names:

```bash
OANDA_ACCOUNT_ID=your_practice_account_id
OANDA_TOKEN=your_practice_token
```

Prefer `SMC_TA_OANDA_*` in `.env.demo` because it matches `RuntimeConfig`.

## Onboarding Check

Environment only:

```bash
python examples/onboard_oanda_credentials.py
```

Local env file:

```bash
python examples/onboard_oanda_credentials.py \
  --env-file .env.demo \
  --output reports/startup/oanda_credentials.json
```

Expected success:

```text
oanda_credentials_ok
next_command=python examples/oanda_practice_startup_monitor.py ...
redacted_report=reports/startup/oanda_credentials.json
```

Expected block when credentials are missing:

```text
oanda_credentials_blocked:missing_required_secret
missing_keys=OANDA_ACCOUNT_ID,OANDA_TOKEN
```

The redacted report never stores raw tokens.

## Then Run Practice Startup

When onboarding is OK, run the printed `next_command`, or run:

```bash
python examples/oanda_practice_startup_monitor.py \
  --broker oanda \
  --symbol EURUSD \
  --timeframe M15 \
  --max-spread-pips 2 \
  --env-file .env.demo \
  --output-dir reports/practice_startup/oanda_latest
```

This is still read-only and does not place orders. It can call OANDA account, instrument, price, candle, position, transaction, and pending-order endpoints.

## Python API

```python
from smc_ta import OandaCredentialOnboardingConfig, check_oanda_credential_onboarding

result = check_oanda_credential_onboarding(
    OandaCredentialOnboardingConfig(
        env_file=".env.demo",
        output_report="reports/startup/oanda_credentials.json",
    )
)

if not result.ok:
    raise RuntimeError(result.summary())

print(result.startup_command())
```

Use this before constructing `OandaBroker` in a demo/live bot entrypoint.
