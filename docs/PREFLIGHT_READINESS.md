# Preflight Readiness

The repository includes a preflight checker for demo/live startup safety.

Main APIs:

- `run_preflight`
- `assert_preflight_ready`
- `PreflightConfig`
- `PreflightReport`
- `PreflightCheck`

## Purpose

Preflight runs the checks that should happen before the bot enters a repeated execution loop.

It can check:

- Runtime config validity
- Candle data quality
- Broker account connectivity
- Broker open-position connectivity
- Broker reconciliation against the expected ledger
- Emergency-stop status
- Required news-filter presence
- Journal path writability
- Lifecycle database path writability
- Lifecycle store connectivity

Preflight does not generate signals, optimize settings, place orders, or close positions.

## Usage

```python
from smc_ta import RuntimeConfig, assert_preflight_ready

runtime = RuntimeConfig.from_env()
report = assert_preflight_ready(
    runtime_config=runtime,
    candles_by_symbol={"EURUSD": candles},
    broker=broker,
    news_filter=news_filter,
    emergency_stop=emergency_stop,
    reconciler=reconciler,
    lifecycle_store=lifecycle_store,
)
```

If any blocking check exists, `assert_preflight_ready` raises `PreflightValidationError`.

For non-throwing behavior:

```python
from smc_ta import run_preflight

report = run_preflight(runtime_config=runtime, candles_by_symbol={"EURUSD": candles})
print(report.summary())
print(report.to_frame())
```

## CLI

Paper/default config:

```bash
python examples/run_preflight.py
```

With a candle sample:

```bash
python examples/run_preflight.py --csv EURUSD_M15.csv --symbol EURUSD
```

With a runtime env file:

```bash
python examples/run_preflight.py --env-file .env.local --csv EURUSD_M15.csv
```

The command exits with `0` when there are no blocking checks and `2` when startup should be blocked.

## Report Shape

Each check has:

- `component`
- `code`
- `severity`
- `message`
- `details`

Severity values:

- `info`
- `warning`
- `blocking`

Only `blocking` checks make `report.ok` false.

## How It Works In Demo/Live

Recommended startup flow:

1. Load `RuntimeConfig`.
2. Build broker/news/journal/lifecycle objects.
3. Load the latest candle sample for each active symbol.
4. Run `assert_preflight_ready`.
5. Start the bot loop only if preflight passes.

This creates one final gate before execution, using the real modules already in the project.
