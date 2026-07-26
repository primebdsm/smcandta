from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from smc_ta.broker import (
    MetaTrader5Broker,
    MetaTrader5CandleDataSource,
    Mt5Config,
    Mt5OrderRejected,
    Mt5PriceValidationError,
    Mt5SymbolValidationError,
    Mt5TerminalValidationError,
    OrderRequest,
)


class FakeMt5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    SYMBOL_TRADE_MODE_FULL = 4
    TIMEFRAME_M15 = 15

    def __init__(self) -> None:
        self.initialized = False
        self.calls: list[tuple[str, object]] = []
        self.connected = True
        self.trade_allowed = True
        self.trade_mode = self.ACCOUNT_TRADE_MODE_DEMO
        self.spread_points = 12
        self.symbol_visible = True
        self.symbol_select_result = True
        self.symbol_select_updates_visibility = True
        self.order_check_retcode = 0
        self.order_send_retcode = self.TRADE_RETCODE_DONE
        self.positions = [
            SimpleNamespace(
                ticket=44,
                symbol="EURUSD.m",
                type=self.POSITION_TYPE_BUY,
                volume=0.01,
                price_open=1.1,
                time=1_704_067_200,
                sl=1.095,
                tp=1.11,
                profit=2.5,
            )
        ]
        self.orders = [
            SimpleNamespace(
                ticket=55,
                symbol="EURUSD.m",
                type=self.ORDER_TYPE_BUY_LIMIT,
                volume_current=0.01,
                price_open=1.095,
                sl=1.09,
                tp=1.11,
                position_id=44,
                position_by_id=0,
                time_setup=1_704_067_100,
                comment="entry-client",
            )
        ]

    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        self.initialized = True
        return True

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected, trade_allowed=self.trade_allowed)

    def account_info(self):
        return SimpleNamespace(
            balance=10_000,
            equity=10_025,
            margin=100,
            margin_free=9_900,
            currency="USD",
            trade_mode=self.trade_mode,
        )

    def symbol_info(self, symbol):
        if symbol != "EURUSD.m":
            return None
        return SimpleNamespace(
            name=symbol,
            visible=self.symbol_visible,
            digits=5,
            point=0.00001,
            spread=self.spread_points,
            trade_mode=self.SYMBOL_TRADE_MODE_FULL,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000,
            trade_stops_level=10,
            trade_freeze_level=0,
            currency_profit="USD",
        )

    def symbol_select(self, symbol, selected):
        self.calls.append(("symbol_select", (symbol, selected)))
        if self.symbol_select_updates_visibility:
            self.symbol_visible = selected
        return self.symbol_select_result

    def symbol_info_tick(self, symbol):
        assert symbol == "EURUSD.m"
        now = pd.Timestamp.now(tz="UTC")
        bid = 1.10000
        ask = bid + self.spread_points * 0.00001
        return SimpleNamespace(
            time=int(now.timestamp()),
            time_msc=int(now.timestamp() * 1000),
            bid=bid,
            ask=ask,
            last=ask,
            flags=0,
        )

    def positions_get(self, symbol=None, ticket=None):
        if ticket is not None:
            return [position for position in self.positions if position.ticket == ticket]
        if symbol is None:
            return list(self.positions)
        return [position for position in self.positions if position.symbol == symbol]

    def orders_get(self, symbol=None):
        if symbol is None:
            return list(self.orders)
        return [order for order in self.orders if order.symbol == symbol]

    def order_check(self, request):
        self.calls.append(("order_check", dict(request)))
        return SimpleNamespace(retcode=self.order_check_retcode, comment="check")

    def order_send(self, request):
        self.calls.append(("order_send", dict(request)))
        return SimpleNamespace(
            retcode=self.order_send_retcode,
            order=101,
            deal=202,
            price=request["price"],
            comment="done",
        )

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        assert symbol == "EURUSD.m"
        assert timeframe == self.TIMEFRAME_M15
        return [
            {
                "time": 1_704_067_200,
                "open": 1.1,
                "high": 1.101,
                "low": 1.099,
                "close": 1.1005,
                "tick_volume": 100,
                "spread": 12,
            }
        ][:count]


def fake_broker(**config_overrides) -> tuple[MetaTrader5Broker, FakeMt5]:
    mt5 = FakeMt5()
    config_values = {"symbol_aliases": {"EURUSD": "EURUSD.m"}, "max_spread_points": 20}
    config_values.update(config_overrides)
    config = Mt5Config(**config_values)
    return MetaTrader5Broker(config=config, mt5_module=mt5), mt5


def codes(report) -> set[str]:
    return {check.code for check in report.checks}


def test_mt5_readiness_checks_terminal_account_symbol_and_tick() -> None:
    broker, _ = fake_broker()

    report = broker.practice_readiness(("EURUSD",))

    assert report.ok
    assert report.summary() == "mt5_terminal_ready"
    assert {"terminal_connected", "terminal_trade_allowed", "account_probe_ok", "symbol_ready", "tick_ready"}.issubset(
        codes(report)
    )
    assert report.account is not None
    assert report.symbols[0].broker_symbol == "EURUSD.m"
    assert report.ticks[0].spread_points == pytest.approx(12)
    assert not report.to_frame().empty


def test_mt5_order_uses_alias_volume_step_order_check_and_metadata() -> None:
    broker, mt5 = fake_broker()

    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000, stop_loss=1.0995, take_profit=1.1015),
        market_price=1.1,
    )

    order_check = [call for call in mt5.calls if call[0] == "order_check"][0][1]
    order_send = [call for call in mt5.calls if call[0] == "order_send"][0][1]
    assert order_check["symbol"] == "EURUSD.m"
    assert order_send["volume"] == 0.01
    assert order_send["type_filling"] == mt5.ORDER_FILLING_RETURN
    assert fill.symbol == "EURUSD"
    assert fill.metadata["mt5_symbol"] == "EURUSD.m"
    assert fill.metadata["mt5_order_check_retcode"] == 0


def test_mt5_blocks_wide_spread_invalid_volume_real_account_and_order_check_failure() -> None:
    broker, mt5 = fake_broker(max_spread_points=5)
    mt5.spread_points = 30
    with pytest.raises(Mt5PriceValidationError, match="spread"):
        broker.get_tick("EURUSD")

    broker, _ = fake_broker()
    with pytest.raises(Mt5SymbolValidationError, match="volume_step"):
        broker.place_order(OrderRequest(symbol="EURUSD", side="buy", units=1_500), market_price=1.1)

    broker, mt5 = fake_broker()
    mt5.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(Mt5TerminalValidationError, match="real account"):
        broker.place_order(OrderRequest(symbol="EURUSD", side="buy", units=1_000), market_price=1.1)

    broker, mt5 = fake_broker()
    mt5.order_check_retcode = 10013
    with pytest.raises(Mt5OrderRejected, match="order_check"):
        broker.place_order(OrderRequest(symbol="EURUSD", side="buy", units=1_000), market_price=1.1)


def test_mt5_blocks_symbol_that_cannot_be_made_visible() -> None:
    broker, mt5 = fake_broker()
    mt5.symbol_visible = False
    mt5.symbol_select_result = False
    with pytest.raises(Mt5SymbolValidationError, match="failed to select"):
        broker.get_symbol_spec("EURUSD")

    broker, mt5 = fake_broker()
    mt5.symbol_visible = False
    mt5.symbol_select_updates_visibility = False
    with pytest.raises(Mt5SymbolValidationError, match="not visible"):
        broker.get_symbol_spec("EURUSD")


def test_mt5_pending_orders_positions_and_candles_are_normalized() -> None:
    broker, _ = fake_broker()

    positions = broker.get_open_positions("EURUSD")
    orders = broker.get_pending_orders("EURUSD")
    candles = MetaTrader5CandleDataSource(broker).get_candles("EURUSD", "M15", limit=1)

    assert positions[0].symbol == "EURUSD"
    assert positions[0].units == 1_000
    assert positions[0].metadata["mt5_symbol"] == "EURUSD.m"
    assert orders[0].symbol == "EURUSD"
    assert orders[0].units == 1_000
    assert orders[0].side == "buy"
    assert list(candles.columns) == ["open", "high", "low", "close", "tick_volume", "spread"]
    assert candles.index.tz is not None
