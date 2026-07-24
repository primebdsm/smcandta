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

## Live Dashboard

```python
from smc_ta import build_live_monitoring_snapshot, write_live_dashboard
```

Use `build_live_monitoring_snapshot` after each bot cycle and `write_live_dashboard` to render the latest local monitoring state. See `docs/LIVE_DASHBOARD_MONITORING.md`.

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
