# Python Bot Integration

This package is designed to sit between market data ingestion and broker execution.

```mermaid
flowchart LR
  A["Broker/Data Vendor"] --> B["OHLCV DataFrame"]
  B --> C["smc_ta.analyze_forex"]
  C --> D["Feature Table"]
  C --> E["Signal Table"]
  D --> F["Backtester or Research Notebook"]
  E --> G["Risk Manager"]
  G --> H["Broker Adapter"]
  G --> J["Trade Lifecycle Store"]
  C --> I["Chart / Journal Snapshot"]
  C --> N["Demo-Forward Reports"]
  K["Runtime Config"] --> G
  K --> H
  K --> L["Preflight Readiness"]
  H --> M["Restart Sync"]
  M --> L
  J --> O["Lifecycle Recovery"]
  M --> O
  O --> L
  L --> P["Deployment Runbook / Incident Bundle"]
  Q["Secrets / Logs / Supervisor"] --> K
  Q --> P
```

## Expected Candle Shape

```python
columns = ["open", "high", "low", "close", "tick_volume", "spread"]
```

The index should be time-based and sorted ascending. UTC is recommended.

## Example Live Loop

```python
from smc_ta import analyze_forex

def on_new_closed_candle(candles):
    result = analyze_forex(candles, symbol="EURUSD")
    signal = result.signals.iloc[-1]

    if signal["side"] == "flat":
        return None

    return {
        "side": signal["side"],
        "confidence": float(signal["confidence"]),
        "reasons": signal["reasons"],
    }
```

## Required Before Live Trading

- Demo-tested broker adapter for orders, positions, and account state
- Runtime config validation and explicit live-mode arming
- Broker restart sync before resuming after crashes/deploys
- Lifecycle restart recovery before preflight
- Preflight readiness check before starting the execution loop
- Economic calendar/news source such as `TradingEconomicsCalendarSource`
- Spread and slippage model selected for the target broker
- Session schedule adjusted for daylight saving time when needed
- Backtesting engine with realistic transaction costs
- Demo forward testing
- Demo-forward report bundles for cycle, equity, trade, setup, session, daily, and block review
- Deployment runbook, rollback procedure, and incident evidence capture
- Runtime logging, secret resolution, process supervision, and log rotation
- Risk limits: max daily loss, max open trades, max correlated exposure
- Trade lifecycle store for signal/block/order/fill/close audit trail
- Optional chart snapshots for review and monitoring

## Current Live-Readiness Modules

- `OandaBroker` and `OandaCandleDataSource` for OANDA v20 REST demo/live accounts
- `RuntimeConfig` and `assert_runtime_ready` for mode, broker, credential, and live guardrail validation
- `run_preflight` and `assert_preflight_ready` for startup readiness checks
- `MetaTrader5Broker` and `MetaTrader5CandleDataSource` for local MT5 terminal workflows
- `JsonEconomicCalendarSource` for provider-specific calendar APIs
- `TradingEconomicsCalendarSource` for real Trading Economics calendar events
- `SQLiteTradeJournal` for persistent local journals
- `BrokerReconciler` for blocking when broker positions differ from bot ledger state
- `sync_broker_state_after_restart` for transaction checkpoints, pending-order reporting, and explicit ledger repair before startup
- `EmergencyStopController` for hard stop, manual stop file, drawdown, equity, runtime-error, and optional close-all controls
- `TradeLifecycleStateMachine` and `SQLiteTradeLifecycleStore` for deterministic trade state tracking
- `recover_lifecycle_after_restart` for synchronizing active lifecycle records with broker-open positions after restart
- `PortfolioRiskManager` for currency exposure, symbol concentration, same-currency, and correlation limits
- `run_walk_forward` for train/test validation before demo/live use
- `run_demo_forward_test` and `write_demo_forward_report_bundle` for bot-path replay evidence before broker-demo operation
- `write_incident_report_bundle` for standardized JSON, Markdown, and CSV incident evidence
- `resolve_runtime_secrets`, `configure_runtime_logging`, and `write_supervisor_artifacts` for deployment operations
- `validate_candle_quality` for missing candles, duplicate timestamps, invalid OHLC, spread spikes, weekend candles, and range spikes
- `write_analysis_chart` for static SMC/TA chart snapshots from `analyze_forex` output
- `analyze_multi_timeframe` for higher-timeframe context
- `classify_smc_setups` for setup labels
