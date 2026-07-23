# Trade Lifecycle

The repository includes a deterministic trade lifecycle state machine for demo/live audit trails.

Main APIs:

- `TradeLifecycleStateMachine`
- `TradeLifecycleRecord`
- `TradeLifecycleEvent`
- `MemoryTradeLifecycleStore`
- `SQLiteTradeLifecycleStore`

## States

Allowed states:

- `created`
- `signal`
- `approved`
- `blocked`
- `submitted`
- `open`
- `partially_closed`
- `closed`
- `cancelled`
- `failed`

Terminal states:

- `blocked`
- `closed`
- `cancelled`
- `failed`

Invalid transitions raise `TradeLifecycleError`. For example, a blocked trade cannot later become open.

## Standalone Usage

```python
import pandas as pd

from smc_ta.broker import OrderRequest
from smc_ta.lifecycle import TradeLifecycleStateMachine

machine = TradeLifecycleStateMachine()
record = machine.create_from_signal(
    symbol="EURUSD",
    timestamp=pd.Timestamp("2024-01-01T12:00:00Z"),
    signal=latest_signal,
    setup_name="liquidity_sweep_choch",
)

order = OrderRequest(
    symbol="EURUSD",
    side="buy",
    units=20_000,
    stop_loss=1.0950,
    take_profit=1.1100,
)
record = machine.approve(record, order=order)
record = machine.submit(record, order)
```

When the broker returns a fill:

```python
record = machine.record_fill(record, fill)
```

When the broker closes the position:

```python
record = machine.record_close(record, fill=close_fill, pnl=42.50)
```

## Demo Bot Integration

`DemoTradingBot` accepts an optional lifecycle store:

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

cycle = bot.run_cycle(candles)
print(cycle.trade_lifecycle.state)
```

The bot records:

- signal generated
- news, risk, portfolio, reconciliation, or emergency-stop block
- risk approval
- broker submission
- broker fill/open
- broker submission failure

Existing bot behavior does not change when no lifecycle store/state machine is configured.

## SQLite Store

```python
from smc_ta.lifecycle import SQLiteTradeLifecycleStore

store = SQLiteTradeLifecycleStore("trade_lifecycle.sqlite")
open_records = store.list_records(state="open")
eurusd_records = store.list_records(symbol="EURUSD")
summary = store.to_frame()
```

The SQLite row stores the latest lifecycle state plus JSON history for each transition. This gives a replayable audit trail for research, demo forward testing, and production incident review.

## How It Works In A Real Bot

1. Signal is generated from `analyze_forex`.
2. Lifecycle moves to `signal`.
3. News/risk/safety logic either moves it to `blocked` or risk moves it to `approved`.
4. Broker submission moves it to `submitted`.
5. A broker fill moves it to `open`.
6. Partial or full closes move it to `partially_closed` or `closed`.
7. Exceptions and broker errors move it to `failed`.

This makes the execution path auditable without changing SMC/TA signal generation.
