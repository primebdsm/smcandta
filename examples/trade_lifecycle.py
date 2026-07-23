"""Track a trade attempt through the lifecycle state machine."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from smc_ta.broker import OrderFill, OrderRequest
from smc_ta.lifecycle import SQLiteTradeLifecycleStore, TradeLifecycleStateMachine


def main() -> None:
    machine = TradeLifecycleStateMachine()
    store = SQLiteTradeLifecycleStore("trade_lifecycle.sqlite")
    timestamp = pd.Timestamp("2024-01-01T12:00:00Z")
    signal = pd.Series(
        {
            "side": "long",
            "confidence": 0.76,
            "entry_reference": 1.1000,
            "stop_reference": 1.0950,
            "target_reference": 1.1100,
            "reference_rr": 2.0,
            "long_score": 8,
            "short_score": 2,
            "reasons": "discount_zone;near_bullish_order_block",
        }
    )

    record = machine.create_from_signal(symbol="EURUSD", timestamp=timestamp, signal=signal, setup_name="ob_mitigation")
    order = OrderRequest(symbol="EURUSD", side="buy", units=20_000, stop_loss=1.0950, take_profit=1.1100)
    record = machine.approve(record, order=order)
    record = machine.submit(record, order)
    record = machine.record_fill(
        record,
        OrderFill(
            order_id="broker-order-1",
            symbol="EURUSD",
            side="buy",
            units=20_000,
            price=1.1001,
            spread=0.0001,
            slippage=0.00002,
            commission=0.0,
            timestamp=datetime.now(timezone.utc),
            client_order_id=order.client_order_id,
        ),
    )
    store.save(record)

    print(store.to_frame())


if __name__ == "__main__":
    main()
