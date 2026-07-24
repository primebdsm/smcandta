from __future__ import annotations

import pandas as pd

from smc_ta import check_broker_connectivity, probe_alert_channel
from smc_ta.broker import PaperBroker
from smc_ta.monitoring import alert_delivery_frame, broker_connectivity_frame


def test_broker_connectivity_ok_for_paper_broker() -> None:
    status = check_broker_connectivity(
        PaperBroker(initial_balance=10_000),
        broker_name="paper",
        symbol="EURUSD",
        timestamp=pd.Timestamp("2024-01-01T00:00:00Z"),
    )
    frame = broker_connectivity_frame((status,))

    assert status.ok
    assert status.account_ok
    assert status.positions_ok
    assert status.symbol == "EURUSD"
    assert status.message == "broker_connectivity_ok"
    assert frame.iloc[0]["status"] == "ok"


def test_broker_connectivity_blocks_on_required_probe_failure() -> None:
    class BrokenBroker:
        def get_account(self):
            raise RuntimeError("account down")

        def get_open_positions(self, symbol=None):
            raise RuntimeError("positions down")

    status = check_broker_connectivity(BrokenBroker(), broker_name="broken")

    assert status.blocking
    assert not status.account_ok
    assert not status.positions_ok
    assert "account_probe_failed" in status.message
    assert "positions_probe_failed" in status.message


def test_broker_connectivity_warns_when_optional_probe_is_missing() -> None:
    status = check_broker_connectivity(
        PaperBroker(initial_balance=10_000),
        broker_name="paper",
        include_transactions=True,
        include_pending_orders=True,
    )

    assert status.warning
    assert status.transactions_ok is False
    assert status.pending_orders_ok is False
    assert "get_latest_transaction_id_not_supported" in status.message
    assert "get_pending_orders_not_supported" in status.message


def test_alert_delivery_probe_success_and_failure() -> None:
    good = _MemoryAlert()
    success = probe_alert_channel(good, channel_name="memory", message="probe")
    failure = probe_alert_channel(_BrokenAlert(), channel_name="broken")
    blocking_failure = probe_alert_channel(_BrokenAlert(), channel_name="broken", blocking_on_failure=True)
    frame = alert_delivery_frame((success, failure, blocking_failure))

    assert success.ok
    assert success.delivered
    assert good.messages == ["probe"]
    assert failure.warning
    assert not failure.delivered
    assert blocking_failure.blocking
    assert set(frame["status"]) == {"ok", "warning", "blocking"}


class _MemoryAlert:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class _BrokenAlert:
    def send(self, message: str) -> None:
        raise RuntimeError("alert down")
