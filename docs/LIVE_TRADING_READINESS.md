# Live Trading Readiness

This repository now contains the main components needed before connecting a real broker:

- Broker interface: `smc_ta.broker.BrokerAdapter`
- Paper/demo execution: `smc_ta.broker.PaperBroker`
- Historical CSV source: `smc_ta.data.CsvCandleDataSource`
- Backtester with spread/slippage: `smc_ta.backtest.run_backtest`
- Economic news filter: `smc_ta.news.NewsFilter`
- Position/risk manager: `smc_ta.risk.RiskManager`
- Demo forward bot: `smc_ta.live.DemoTradingBot`
- CSV journal: `smc_ta.journal.TradeJournal`
- Monitoring metrics: `smc_ta.monitoring.performance_summary`

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
4. Connect one broker adapter in demo mode.
5. Reconcile positions and balances after every cycle.
6. Add emergency stop controls.
7. Only then consider small live size.

## Still Broker-Specific

The repository does not ship credentials or a direct MetaTrader/OANDA/cTrader/FIX connector. That part must be selected for your broker and tested in demo mode first.

