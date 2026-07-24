# Codex Usage

Use this repository as a structured codebase, not as loose strategy notes.

## Main Entry Point

```python
from smc_ta import analyze_forex

result = analyze_forex(candles, symbol="GBPUSD")
```

The result object contains:

- `features`: candle-aligned SMC + TA feature table
- `signals`: candle-aligned confluence signal table
- `market_structure`: BOS/CHoCH and swing context
- `fair_value_gaps`: FVG event table
- `order_blocks`: OB event table
- `liquidity_pools`: equal-high/equal-low liquidity zones

## Bot Pattern

1. Load broker candles.
2. Normalize columns with `normalize_ohlcv`.
3. Run `analyze_forex`.
4. Read only the latest closed candle.
5. Apply your risk, news, spread, execution, and account rules.
6. Send orders through a separate broker adapter.

## Runtime Guardrails

```python
from smc_ta import RuntimeConfig, assert_runtime_ready

config = RuntimeConfig.from_env()
assert_runtime_ready(config)
```

Use runtime config checks before constructing broker adapters or starting demo/live loops. See `docs/RUNTIME_CONFIG.md`.

## Preflight Check

```python
from smc_ta import assert_preflight_ready

assert_preflight_ready(
    runtime_config=config,
    candles_by_symbol={"EURUSD": candles},
    broker=broker,
)
```

Use preflight after constructing runtime objects and before starting a repeated demo/live bot loop. See `docs/PREFLIGHT_READINESS.md`.

## Broker Restart Sync

```python
from smc_ta import RestartSyncConfig, SQLiteSyncCheckpointStore

checkpoints = SQLiteSyncCheckpointStore("positions.sqlite")
report = bot.sync_after_restart(
    checkpoint_store=checkpoints,
    config=RestartSyncConfig(
        adopt_unmanaged_broker_positions=True,
        mark_missing_expected_positions_closed=True,
    ),
)
```

Use restart sync before preflight when the process resumes after a crash, deploy, or VPS restart. See `docs/BROKER_RESTART_SYNC.md`.

## Lifecycle Restart Recovery

```python
from smc_ta import LifecycleRecoveryConfig

lifecycle_report = bot.recover_lifecycle_after_restart(
    config=LifecycleRecoveryConfig(
        create_missing_lifecycles_for_broker_positions=True,
        mark_missing_broker_positions_closed=True,
        fail_unfilled_lifecycles_without_broker_position=True,
    ),
)
```

Use lifecycle recovery after broker restart sync and before preflight so active lifecycle records match broker-open positions. See `docs/LIFECYCLE_RESTART_RECOVERY.md`.

## OANDA Practice Check

```bash
python examples/oanda_practice_check.py --symbols EURUSD --max-spread-pips 2
```

Use this before OANDA demo forward testing. It probes account, instrument metadata, and current pricing without placing an order. See `docs/OANDA_PRACTICE_HARDENING.md`.

## OANDA Execution Validation

```bash
python examples/oanda_execution_validate.py --symbol EURUSD --max-spread-pips 2 --execute
```

Use this only with an OANDA practice account. It validates minimum-size order open/close, SL/TP order open/close, rejected-order handling, restart reconciliation, and spread/slippage reporting. See `docs/OANDA_EXECUTION_VALIDATION.md`.

## Demo-Forward Reports

```python
from smc_ta import DemoForwardConfig, run_demo_forward_test, write_demo_forward_report_bundle

result = run_demo_forward_test(candles, config=DemoForwardConfig(symbol="EURUSD"))
write_demo_forward_report_bundle(result, "reports/demo_forward")
```

Use this after backtesting/walk-forward and before broker-demo live loops. It exercises the bot path and writes cycle, equity, fill, trade, setup, session, daily, and blocked-reason reports. See `docs/DEMO_FORWARD_REPORTS.md`.

## Live Dashboard

```python
from smc_ta import build_live_monitoring_snapshot, write_live_dashboard
```

Use `build_live_monitoring_snapshot` after each bot cycle and `write_live_dashboard` to render the latest local monitoring state. See `docs/LIVE_DASHBOARD_MONITORING.md`.

## Deployment And Incidents

```python
from smc_ta import write_incident_report_bundle

bundle = write_incident_report_bundle(
    "reports/incidents/incident-001",
    title="startup blocked by lifecycle recovery",
    severity="SEV2",
    symbol="EURUSD",
    runtime_config=config,
    preflight_report=preflight,
    restart_sync_report=restart_sync,
    lifecycle_recovery_report=lifecycle_recovery,
    monitoring_snapshot=snapshot,
)
```

Use `docs/DEPLOYMENT_RUNBOOK.md` for deployment order and `docs/INCIDENT_PROCEDURES.md` when a startup or runtime control blocks trading.

## Supervision, Secrets, And Logs

```python
from smc_ta import (
    EnvSecretSource,
    RuntimeLogConfig,
    SecretResolutionConfig,
    SupervisorConfig,
    configure_runtime_logging,
    resolve_runtime_secrets,
    write_supervisor_artifacts,
)

logger = configure_runtime_logging(RuntimeLogConfig(log_dir="logs", json_lines=True))
secrets = resolve_runtime_secrets(
    SecretResolutionConfig(
        sources=(EnvSecretSource(keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN")),),
        required_keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"),
    )
)
artifacts = write_supervisor_artifacts(
    SupervisorConfig(service_name="smc-ta-demo", env_file=".env.demo", log_dir="logs")
)
```

Use `docs/PROCESS_SUPERVISION.md` and `docs/SECRETS_AND_LOGGING.md` for deployment-safe process/log/secret handling.

## Real News Provider

```python
from smc_ta.news import TradingEconomicsCalendarSource, TradingEconomicsConfig

calendar = TradingEconomicsCalendarSource(
    TradingEconomicsConfig.from_env(importance=(3,))
)
```

Use the provider to build the same `NewsFilter` object that the backtester and demo bot already accept. See `docs/NEWS_PROVIDERS.md`.

## Chart Snapshot

```python
from smc_ta import analyze_forex, write_analysis_chart

result = analyze_forex(candles, symbol="EURUSD")
write_analysis_chart("analysis_chart.html", result, symbol="EURUSD")
```

The chart is a review artifact for Codex, journals, alerts, or dashboard snapshots. It renders the existing analysis output and should not be used as a separate source of trading decisions.

## Trade Lifecycle

```python
from smc_ta import DemoTradingBot
from smc_ta.lifecycle import SQLiteTradeLifecycleStore

store = SQLiteTradeLifecycleStore("trade_lifecycle.sqlite")
bot = DemoTradingBot(
    symbol="EURUSD",
    broker=broker,
    risk_manager=risk_manager,
    trade_lifecycle_store=store,
)
```

Use lifecycle records to inspect whether a signal was blocked, approved, submitted, opened, closed, failed, or cancelled. See `docs/TRADE_LIFECYCLE.md`.

## No-Lookahead Notes

Swing highs/lows require right-side candles for confirmation. The SMC structure module exposes both pivot candle fields and confirmation-time fields. Use confirmation-time columns when backtesting or trading live.

FVGs form after the third candle closes. Order blocks form after a displacement or structure event. These are event-based detectors and are safe to use after the event candle closes.
