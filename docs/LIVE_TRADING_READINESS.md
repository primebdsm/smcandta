# Live Trading Readiness

For the complete post-roadmap audit and recommended next build order, see `docs/FINAL_AUDIT_REPORT.md`.

This repository now contains the main components needed before connecting a real broker:

- Broker interface: `smc_ta.broker.BrokerAdapter`
- Runtime config and live guardrails: `smc_ta.config.RuntimeConfig`
- Preflight startup readiness: `smc_ta.preflight.run_preflight`
- OANDA REST adapter: `smc_ta.broker.OandaBroker`
- OANDA candle downloader: `smc_ta.broker.OandaCandleDataSource`
- OANDA practice hardening: `OandaBroker.practice_readiness`, instrument metadata checks, price freshness, spread checks, and order rejection classification
- Optional MetaTrader 5 adapter: `smc_ta.broker.MetaTrader5Broker`
- Paper/demo execution: `smc_ta.broker.PaperBroker`
- Historical CSV source: `smc_ta.data.CsvCandleDataSource`
- Data quality validator: `smc_ta.data.validate_candle_quality`
- Backtester with spread/slippage: `smc_ta.backtest.run_backtest`
- Economic news filter, JSON API source, and Trading Economics connector: `NewsFilter`, `JsonEconomicCalendarSource`, `TradingEconomicsCalendarSource`
- Position/risk manager: `smc_ta.risk.RiskManager`
- Portfolio/correlation risk: `smc_ta.risk.PortfolioRiskManager`
- Broker reconciliation: `smc_ta.reconciliation.BrokerReconciler`
- Expected-position ledgers: `MemoryPositionLedger`, `SQLitePositionLedger`
- Broker restart sync: `sync_broker_state_after_restart`, `RestartSyncConfig`, `SQLiteSyncCheckpointStore`
- Emergency stop / kill switch: `smc_ta.safety.EmergencyStopController`
- Trade lifecycle state machine and stores: `TradeLifecycleStateMachine`, `SQLiteTradeLifecycleStore`
- Lifecycle restart recovery: `recover_lifecycle_after_restart`, `LifecycleRecoveryConfig`
- Demo forward bot: `smc_ta.live.DemoTradingBot`
- Demo-forward report package: `run_demo_forward_test`, `write_demo_forward_report_bundle`
- CSV and SQLite journals: `smc_ta.journal.TradeJournal`, `smc_ta.journal.SQLiteTradeJournal`
- Monitoring metrics: `smc_ta.monitoring.performance_summary`
- Static/live dashboard: `smc_ta.dashboard.write_dashboard`, `smc_ta.dashboard.write_live_dashboard`
- Deployment runbook and incident bundle helper: `write_incident_report_bundle`
- Static chart visualization: `smc_ta.visualization.write_analysis_chart`
- Alerts: `smc_ta.alerts.TelegramAlert`, `smc_ta.alerts.DiscordWebhookAlert`, `smc_ta.alerts.EmailAlert`
- Strategy profiles: `smc_ta.strategy.get_strategy_profile`

## Broker Adapter Contract

Live adapters should implement:

```python
from smc_ta.broker import BrokerAdapter, OrderRequest

class OandaBroker:
    def get_account(self): ...
    def get_open_positions(self, symbol=None): ...
    def place_order(self, request: OrderRequest, *, market_price: float): ...
    def close_position(self, position_id: str, *, market_price: float): ...
```

Keep broker-specific authentication, order IDs, retry logic, and reconciliation inside that adapter. The analysis engine should stay broker-neutral.

## Safe Path To Live

1. Backtest with spread, slippage, and commission.
2. Review the trade journal and monitoring metrics.
3. Generate demo-forward reports through `run_demo_forward_test`.
4. Validate `RuntimeConfig` and keep live mode blocked unless explicitly armed.
5. Add `BrokerReconciler` with a persistent expected-position ledger.
6. Run broker restart sync before preflight whenever the process starts.
7. Run lifecycle restart recovery before preflight whenever the process starts.
8. Connect one broker adapter in demo mode.
9. Reconcile positions and balances after every cycle.
10. Add portfolio/correlation limits for multi-pair trading.
11. Enable `EmergencyStopController` with manual stop, equity, drawdown, position, runtime-error, and reconciliation-failure limits.
12. Enable `SQLiteTradeLifecycleStore` so every signal, block, submission, fill, failure, and close is auditable.
13. Add a real economic calendar source such as `TradingEconomicsCalendarSource` and verify event times against your broker/server timezone.
14. Run `assert_preflight_ready` before every demo/live process start.
15. Follow `docs/DEPLOYMENT_RUNBOOK.md` and keep `docs/INCIDENT_PROCEDURES.md` ready before any repeated bot process.
16. Only then consider small live size.

## Emergency Stop

The kill switch can stop all new trading when any configured safety rule is hit:

- manual controller activation
- manual stop file exists
- equity falls below a minimum
- daily loss reaches a configured percent
- total drawdown reaches a configured percent
- open positions reach a configured limit
- reconciliation fails
- runtime error threshold is reached

When `close_positions_on_trigger=True`, `DemoTradingBot` closes open positions for the active symbol and records them in the reconciliation ledger and journal.

## Economic Calendar Connector

`TradingEconomicsCalendarSource` downloads provider calendar rows, maps country events to Forex currencies, maps provider importance to low/medium/high impact, normalizes timestamps to UTC, and feeds the same `NewsFilter` used by the backtester and demo bot.

Credentials are read from `TRADING_ECONOMICS_API_KEY` through `TradingEconomicsConfig.from_env()`. See `docs/NEWS_PROVIDERS.md` for usage.

## Chart Visualization

`write_analysis_chart` renders a standalone HTML/SVG chart from `analyze_forex` output. It shows candles, volume, EMA/VWAP overlays, FVGs, order blocks, liquidity pools, liquidity sweeps, BOS/CHoCH, signal arrows, and latest entry/stop/target reference lines.

Use charts for review, alerts, journal snapshots, and debugging. The chart renderer does not make execution decisions.

## Trade Lifecycle

`TradeLifecycleStateMachine` tracks each trade attempt through `signal`, `approved`, `blocked`, `submitted`, `open`, `partially_closed`, `closed`, `cancelled`, and `failed` states. `SQLiteTradeLifecycleStore` persists the latest state plus event history for audit and replay.

`DemoTradingBot` can write lifecycle records automatically through `trade_lifecycle_store`. This does not replace broker reconciliation or emergency stop controls; it makes their decisions visible and durable.

`recover_lifecycle_after_restart` synchronizes active lifecycle records with broker-open positions after a restart. It can create recovery lifecycle records for broker-only positions, mark missing broker positions closed, fail stale unfilled records, and block duplicate or unsafe lifecycle state. See `docs/LIFECYCLE_RESTART_RECOVERY.md`.

## Runtime Guardrails

`RuntimeConfig` loads environment, `.env`-style files, or JSON config and validates mode, broker, symbols, timeframes, required credentials, news-filter requirements, lifecycle/journal paths, and live-mode arming.

Live mode requires `allow_live_trading=True` and `live_confirmation="I_UNDERSTAND_LIVE_FOREX_RISK"`. OANDA live mode also requires `oanda_practice=False`. See `docs/RUNTIME_CONFIG.md`.

## Preflight Readiness

`run_preflight` combines runtime config, data quality, broker account/position probes, reconciliation, emergency stop, news-filter presence, persistence paths, and lifecycle-store checks into one startup report.

Use `assert_preflight_ready` as the final gate before a repeated demo/live loop starts. See `docs/PREFLIGHT_READINESS.md`.

## Broker Restart Sync

`sync_broker_state_after_restart` compares broker-open positions with the SQLite expected-position ledger before the bot resumes. It can run report-only, adopt broker positions into the ledger, mark ledger-only positions closed, update mismatched ledger rows from broker truth, save broker transaction checkpoints, and report pending broker orders.

For OANDA, `OandaBroker` exposes account changes and pending orders so the startup report can include transactions and protective or unlinked orders. Run `python examples/broker_restart_sync.py --broker oanda --symbol EURUSD --ledger-path oanda_positions.sqlite` before preflight. See `docs/BROKER_RESTART_SYNC.md`.

Run lifecycle restart recovery immediately after broker restart sync so the lifecycle store is reconciled to the same broker truth.

## OANDA Practice Hardening

`OandaBroker` now validates account-specific instrument metadata before order placement, checks current OANDA bid/ask pricing, blocks stale or wide-spread prices, classifies rate-limit/order-rejection errors, and provides a non-trading practice readiness probe.

Run `python examples/oanda_practice_check.py --symbols EURUSD --max-spread-pips 2` before OANDA demo forward testing. See `docs/OANDA_PRACTICE_HARDENING.md`.

## OANDA Execution Validation

`run_oanda_practice_execution_validation` can place and close minimum-size OANDA practice trades, validate SL/TP-on-fill, verify rejected-order handling, simulate restart reconciliation through SQLite, and print spread/slippage samples.

Run `python examples/oanda_execution_validate.py --symbol EURUSD --max-spread-pips 2 --execute` only on a practice account. See `docs/OANDA_EXECUTION_VALIDATION.md`.

## Demo-Forward Reports

`run_demo_forward_test` replays closed candles through the `DemoTradingBot` path and writes cycle, equity, trade, fill, setup, session, daily, blocked-reason, and paper-position-event reports.

Run `python examples/demo_forward_report.py --output-dir reports/demo_forward` for a deterministic sample, or pass a Forex CSV path for recent out-of-sample candles. See `docs/DEMO_FORWARD_REPORTS.md`.

## Live Dashboard Monitoring

`build_live_monitoring_snapshot` and `write_live_dashboard` render account state, signal state, SMC/TA context, equity, preflight checks, emergency-stop state, open positions, lifecycle records, journal rows, blocked events, and execution samples into one local HTML dashboard.

Run `python examples/live_dashboard_monitor.py --output live_dashboard.html` for a paper-mode sample. See `docs/LIVE_DASHBOARD_MONITORING.md`.

## Deployment And Incident Procedures

`docs/DEPLOYMENT_RUNBOOK.md` defines the safe startup order, promotion gates, rollback process, and post-deploy monitoring checklist.

`write_incident_report_bundle` writes standardized JSON, Markdown, and CSV evidence for blocked startup, emergency stops, restart sync issues, lifecycle recovery issues, spread/slippage incidents, and monitoring failures. Run `python examples/incident_bundle.py --output-dir reports/incidents/sample --severity SEV2` for a paper-mode sample. See `docs/INCIDENT_PROCEDURES.md`.

## Still Broker-Specific

The repository ships OANDA REST and optional MetaTrader 5 adapter implementations. cTrader/FIX and any broker-specific production controls still need to be implemented for the selected venue. Credentials are never stored in the repository.

## API References

- OANDA v20 REST documents candle granularities and the `/v3/accounts/{accountID}/orders` order endpoint.
- MetaTrader 5 Python integration documents `initialize`, `positions_get`, and `order_send`.
- Trading Economics documents calendar country/date endpoints and event response fields.
