from __future__ import annotations

import copy

import pandas as pd

from smc_ta.broker import (
    OandaBroker,
    OandaConfig,
    OandaExecutionValidationConfig,
    OandaOrderRejected,
    run_oanda_practice_execution_validation,
)
from smc_ta.reconciliation import SQLitePositionLedger


def instrument_payload(**overrides):
    payload = {
        "name": "EUR_USD",
        "displayName": "EUR/USD",
        "type": "CURRENCY",
        "displayPrecision": 5,
        "tradeUnitsPrecision": 0,
        "pipLocation": -4,
        "minimumTradeSize": "1",
        "maximumOrderUnits": "1000000",
        "marginRate": "0.0333",
    }
    payload.update(overrides)
    return payload


def price_payload(*, bid="1.10000", ask="1.10020"):
    return {
        "instrument": "EUR_USD",
        "time": pd.Timestamp.now(tz="UTC").isoformat(),
        "status": "tradeable",
        "tradeable": True,
        "bids": [{"price": bid, "liquidity": 10_000_000}],
        "asks": [{"price": ask, "liquidity": 10_000_000}],
        "closeoutBid": bid,
        "closeoutAsk": ask,
    }


class StatefulOandaClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.positions: dict[str, dict] = {}
        self.order_counter = 0

    def request(self, method, path, *, params=None, payload=None):
        method = method.upper()
        self.calls.append({"method": method, "path": path, "params": params, "payload": copy.deepcopy(payload)})
        if method == "GET" and path.endswith("/summary"):
            return {
                "account": {
                    "balance": "10000",
                    "NAV": "10000",
                    "marginUsed": "0",
                    "marginAvailable": "10000",
                    "currency": "USD",
                }
            }
        if method == "GET" and path.endswith("/instruments"):
            return {"instruments": [instrument_payload()]}
        if method == "GET" and path.endswith("/pricing"):
            return {"prices": [price_payload()]}
        if method == "GET" and path.endswith("/openPositions"):
            return self._open_positions_response()
        if method == "POST" and path.endswith("/orders"):
            return self._order_response(payload)
        if method == "PUT" and "/trades/" in path and path.endswith("/close"):
            trade_id = path.split("/trades/", 1)[1].split("/", 1)[0]
            return self._close_response(trade_id)
        raise AssertionError(f"unexpected OANDA fake call: {method} {path}")

    def _open_positions_response(self):
        longs = [position for position in self.positions.values() if position["side"] == "long"]
        shorts = [position for position in self.positions.values() if position["side"] == "short"]
        if not longs and not shorts:
            return {"positions": []}
        return {
            "positions": [
                {
                    "instrument": "EUR_USD",
                    "long": self._side_payload(longs),
                    "short": self._side_payload(shorts),
                }
            ]
        }

    @staticmethod
    def _side_payload(positions):
        if not positions:
            return {"units": "0"}
        units = sum(position["units"] for position in positions)
        average = sum(position["entry_price"] * position["units"] for position in positions) / units
        return {
            "units": str(units),
            "averagePrice": f"{average:.5f}",
            "tradeIDs": [position["trade_id"] for position in positions],
        }

    def _order_response(self, payload):
        order = payload["order"]
        units = float(order["units"])
        if units == 0:
            raise OandaOrderRejected(
                "UNITS_INVALID",
                error_code="UNITS_INVALID",
                error_message="units must be non-zero",
                response={"orderRejectTransaction": {"reason": "UNITS_INVALID"}},
            )
        self.order_counter += 1
        trade_id = f"trade-{self.order_counter}"
        fill_id = f"fill-{self.order_counter}"
        side = "long" if units > 0 else "short"
        price = 1.1002 if units > 0 else 1.1000
        self.positions[trade_id] = {
            "trade_id": trade_id,
            "side": side,
            "units": abs(units),
            "entry_price": price,
            "stop_loss": order.get("stopLossOnFill", {}).get("price"),
            "take_profit": order.get("takeProfitOnFill", {}).get("price"),
        }
        return {
            "orderCreateTransaction": {"id": f"order-{self.order_counter}"},
            "orderFillTransaction": {
                "id": fill_id,
                "instrument": "EUR_USD",
                "units": str(int(units)),
                "price": f"{price:.5f}",
                "commission": "0.0",
                "time": pd.Timestamp.now(tz="UTC").isoformat(),
                "tradeOpened": {"tradeID": trade_id},
            },
            "relatedTransactionIDs": [fill_id, trade_id],
        }

    def _close_response(self, trade_id: str):
        position = self.positions.pop(trade_id)
        close_units = -position["units"] if position["side"] == "long" else position["units"]
        return {
            "orderFillTransaction": {
                "id": f"close-{trade_id}",
                "instrument": "EUR_USD",
                "units": str(int(close_units)),
                "price": "1.10000",
                "commission": "0.0",
                "time": pd.Timestamp.now(tz="UTC").isoformat(),
                "tradesClosed": [{"tradeID": trade_id}],
            },
            "relatedTransactionIDs": [f"close-{trade_id}", trade_id],
        }


def fake_broker(*, practice: bool = True) -> tuple[OandaBroker, StatefulOandaClient]:
    broker = OandaBroker(OandaConfig(account_id="acct", token="token", practice=practice, max_spread_pips=5.0))
    client = StatefulOandaClient()
    broker.client = client
    return broker, client


def codes(report) -> set[str]:
    return {check.code for check in report.checks}


def test_oanda_execution_validation_dry_run_does_not_place_orders() -> None:
    broker, client = fake_broker()

    report = run_oanda_practice_execution_validation(broker, execute=False)

    assert report.ok
    assert not report.executed
    assert "dry_run_execution_not_requested" in codes(report)
    assert not [call for call in client.calls if call["method"] == "POST"]
    assert report.instrument is not None
    assert report.price_before is not None


def test_oanda_execution_validation_executes_and_reconciles(tmp_path) -> None:
    broker, client = fake_broker()
    ledger_path = tmp_path / "oanda_validation.sqlite"

    report = run_oanda_practice_execution_validation(
        broker,
        config=OandaExecutionValidationConfig(ledger_path=ledger_path),
        execute=True,
    )

    assert report.ok
    assert report.executed
    assert {
        "minimum_unit_order_opened",
        "minimum_unit_order_closed",
        "sl_tp_order_opened",
        "restart_reconciliation_ok",
        "sl_tp_order_closed",
        "rejected_order_probe_ok",
        "final_reconciliation_ok",
        "spread_slippage_report_ready",
    }.issubset(codes(report))
    assert len(report.samples) == 4
    assert not report.execution_frame().empty
    assert not client.positions
    assert not SQLitePositionLedger(ledger_path).open_positions("EURUSD")
    order_payloads = [call["payload"]["order"] for call in client.calls if call["method"] == "POST"]
    assert len(order_payloads) == 3
    sltp_order = [order for order in order_payloads if "stopLossOnFill" in order][0]
    assert sltp_order["takeProfitOnFill"]["price"]


def test_oanda_execution_validation_blocks_live_endpoint() -> None:
    broker, _ = fake_broker(practice=False)

    report = run_oanda_practice_execution_validation(broker, execute=True)

    assert not report.ok
    assert "not_practice_endpoint" in codes(report)


def test_oanda_execution_validation_blocks_existing_positions() -> None:
    broker, client = fake_broker()
    client.positions["existing-trade"] = {
        "trade_id": "existing-trade",
        "side": "long",
        "units": 1,
        "entry_price": 1.1,
    }

    report = run_oanda_practice_execution_validation(broker, execute=True)

    assert not report.ok
    assert not report.executed
    assert "existing_positions_block_execution" in codes(report)
    assert not [call for call in client.calls if call["method"] == "POST"]
