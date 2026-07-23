# Live Trading Readiness

This repository now contains the main components needed before connecting a real broker:

- Broker interface: `smc_ta.broker.BrokerAdapter`
- OANDA REST adapter: `smc_ta.broker.OandaBroker`
- OANDA candle downloader: `smc_ta.broker.OandaCandleDataSource`
- Optional MetaTrader 5 adapter: `smc_ta.broker.MetaTrader5Broker`
- Paper/demo execution: `smc_ta.broker.PaperBroker`
- Historical CSV source: `smc_ta.data.CsvCandleDataSource`
- Data quality validator: `smc_ta.data.validate_candle_quality`
- Backtester with spread/slippage: `smc_ta.backtest.run_backtest`
- Economic news filter and JSON API source: `smc_ta.news.NewsFilter`, `smc_ta.news.JsonEconomicCalendarSource`
- Position/risk manager: `smc_ta.risk.RiskManager`
- Portfolio/correlation risk: `smc_ta.risk.PortfolioRiskManager`
- Broker reconciliation: `smc_ta.reconciliation.BrokerReconciler`
- Expected-position ledgers: `MemoryPositionLedger`, `SQLitePositionLedger`
- Emergency stop / kill switch: `smc_ta.safety.EmergencyStopController`
- Demo forward bot: `smc_ta.live.DemoTradingBot`
- CSV and SQLite journals: `smc_ta.journal.TradeJournal`, `smc_ta.journal.SQLiteTradeJournal`
- Monitoring metrics: `smc_ta.monitoring.performance_summary`
- Static dashboard: `smc_ta.dashboard.write_dashboard`
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
3. Forward test through `PaperBroker`.
4. Add `BrokerReconciler` with a persistent expected-position ledger.
5. Connect one broker adapter in demo mode.
6. Reconcile positions and balances after every cycle.
7. Add portfolio/correlation limits for multi-pair trading.
8. Enable `EmergencyStopController` with manual stop, equity, drawdown, position, runtime-error, and reconciliation-failure limits.
9. Only then consider small live size.

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

## Still Broker-Specific

The repository ships OANDA REST and optional MetaTrader 5 adapter implementations. cTrader/FIX and any broker-specific production controls still need to be implemented for the selected venue. Credentials are never stored in the repository.

## API References

- OANDA v20 REST documents candle granularities and the `/v3/accounts/{accountID}/orders` order endpoint.
- MetaTrader 5 Python integration documents `initialize`, `positions_get`, and `order_send`.
