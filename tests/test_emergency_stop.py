from __future__ import annotations

import pandas as pd

from smc_ta.broker import AccountState, OrderRequest, PaperBroker
from smc_ta.live import DemoTradingBot
from smc_ta.reconciliation import BrokerReconciler, MemoryPositionLedger
from smc_ta.risk import RiskConfig, RiskManager
from smc_ta.safety import EmergencyStopConfig, EmergencyStopController


class RecordingAlert:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


def make_candles(n: int = 160) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + pd.RangeIndex(n).to_series().to_numpy() * 0.00001, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100,
            "spread": 0.0001,
        },
        index=index,
    )


def account(equity: float) -> AccountState:
    return AccountState(balance=equity, equity=equity, currency="USD")


def test_emergency_stop_manual_and_file_triggers(tmp_path) -> None:
    controller = EmergencyStopController()
    manual = controller.activate("operator_stop")

    assert manual.active
    assert manual.reasons == ("operator_stop",)
    assert controller.evaluate(account=account(10_000), open_positions=[], timestamp=pd.Timestamp("2024-01-01", tz="UTC")).active

    stop_file = tmp_path / "STOP_TRADING"
    stop_file.write_text("stop", encoding="utf-8")
    file_controller = EmergencyStopController(EmergencyStopConfig(manual_stop_file=stop_file))
    result = file_controller.evaluate(
        account=account(10_000),
        open_positions=[],
        timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
    )

    assert result.active
    assert "manual_stop_file_present" in result.reasons


def test_emergency_stop_equity_and_drawdown_limits_latch() -> None:
    controller = EmergencyStopController(
        EmergencyStopConfig(min_equity=9_500, max_daily_loss_percent=4.0, max_total_drawdown_percent=5.0)
    )
    first = controller.evaluate(
        account=account(10_000),
        open_positions=[],
        timestamp=pd.Timestamp("2024-01-01 09:00", tz="UTC"),
    )
    assert first.ok

    second = controller.evaluate(
        account=account(9_400),
        open_positions=[],
        timestamp=pd.Timestamp("2024-01-01 10:00", tz="UTC"),
    )

    assert second.active
    assert "min_equity_reached" in second.reasons
    assert "max_daily_loss_reached" in second.reasons
    assert "max_total_drawdown_reached" in second.reasons


def test_runtime_error_limit_triggers_stop() -> None:
    controller = EmergencyStopController(EmergencyStopConfig(max_runtime_errors=1))
    result = controller.record_runtime_error(RuntimeError("broker disconnected"))

    assert result is not None
    assert result.active
    assert result.reasons == ("runtime_error_limit_reached",)


def test_demo_bot_emergency_stop_blocks_and_closes_positions() -> None:
    broker = PaperBroker(initial_balance=10_000, default_spread_pips=0.0, slippage_pips=0.0)
    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=10_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )
    ledger = MemoryPositionLedger(broker.get_open_positions("EURUSD"))
    alert = RecordingAlert()
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=broker,
        risk_manager=RiskManager(RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0)),
        reconciler=BrokerReconciler(ledger),
        emergency_stop=EmergencyStopController(
            EmergencyStopConfig(max_open_positions=1, close_positions_on_trigger=True)
        ),
        alert_channel=alert,
    )

    result = bot.run_cycle(make_candles())

    assert result.action == "emergency_stop_active"
    assert result.emergency_stop_result is not None
    assert "max_open_positions_reached" in result.emergency_stop_result.reasons
    assert len(result.emergency_close_fills) == 1
    assert result.emergency_close_fills[0].side == "sell"
    assert not broker.get_open_positions("EURUSD")
    assert not ledger.open_positions("EURUSD")
    assert alert.messages and "emergency stop active" in alert.messages[-1]
    assert fill.order_id != result.emergency_close_fills[0].order_id


def test_reconciliation_failure_can_trigger_emergency_stop_before_reconciliation_block() -> None:
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=10_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=broker,
        risk_manager=RiskManager(RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0)),
        reconciler=BrokerReconciler(MemoryPositionLedger()),
        emergency_stop=EmergencyStopController(EmergencyStopConfig(block_on_reconciliation_failure=True)),
    )

    result = bot.run_cycle(make_candles())

    assert result.action == "emergency_stop_active"
    assert result.emergency_stop_result is not None
    assert "reconciliation_failed" in result.emergency_stop_result.reasons

