from __future__ import annotations

import copy
import io
import json
from urllib.error import HTTPError

import pandas as pd
import pytest

import smc_ta.broker.oanda as oanda_module
from smc_ta.broker import (
    OandaBroker,
    OandaClient,
    OandaConfig,
    OandaInstrumentSpec,
    OandaInstrumentValidationError,
    OandaOrderRejected,
    OandaPriceSnapshot,
    OandaPriceValidationError,
    OandaRateLimitError,
    OrderRequest,
)


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
        "maximumPositionSize": "0",
        "marginRate": "0.0333",
    }
    payload.update(overrides)
    return payload


def price_payload(*, bid="1.10000", ask="1.10020", tradeable=True, status="tradeable"):
    return {
        "instrument": "EUR_USD",
        "time": pd.Timestamp.now(tz="UTC").isoformat(),
        "status": status,
        "tradeable": tradeable,
        "bids": [{"price": bid, "liquidity": 10_000_000}],
        "asks": [{"price": ask, "liquidity": 10_000_000}],
        "closeoutBid": bid,
        "closeoutAsk": ask,
    }


class FakeOandaClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, *, params=None, payload=None):
        key = (method.upper(), path)
        self.calls.append({"method": method.upper(), "path": path, "params": params, "payload": payload})
        value = self.responses[key]
        if isinstance(value, list):
            item = value.pop(0)
        else:
            item = value
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)


def broker_with_fake_client(config: OandaConfig, responses) -> OandaBroker:
    broker = OandaBroker(config)
    broker.client = FakeOandaClient(responses)
    return broker


def test_oanda_instrument_metadata_validates_units() -> None:
    spec = OandaInstrumentSpec.from_api(instrument_payload())

    assert spec.symbol == "EURUSD"
    assert spec.pip_size == 0.0001
    assert spec.format_price(1.1) == "1.10000"
    assert spec.format_signed_units(1000, "buy") == "1000"
    assert spec.format_signed_units(1000, "sell") == "-1000"

    with pytest.raises(OandaInstrumentValidationError):
        spec.validate_units(0)
    with pytest.raises(OandaInstrumentValidationError):
        spec.validate_units(0.5)
    with pytest.raises(OandaInstrumentValidationError):
        spec.validate_units(1000.25)
    with pytest.raises(OandaInstrumentValidationError):
        spec.validate_units(2_000_000)


def test_oanda_price_validation_blocks_untradeable_stale_and_wide_prices() -> None:
    spec = OandaInstrumentSpec.from_api(instrument_payload())
    config = OandaConfig(
        account_id="acct",
        token="token",
        max_price_age_seconds=5,
        max_spread_pips=1.0,
    )
    broker = OandaBroker(config)

    wide = OandaPriceSnapshot.from_api(price_payload(bid="1.10000", ask="1.10030"), spec)
    with pytest.raises(OandaPriceValidationError, match="spread"):
        broker.validate_price_snapshot(wide)

    stale = OandaPriceSnapshot.from_api(
        {**price_payload(bid="1.10000", ask="1.10005"), "time": "2024-01-01T00:00:00Z"},
        spec,
    )
    with pytest.raises(OandaPriceValidationError, match="stale"):
        broker.validate_price_snapshot(stale, now=pd.Timestamp("2024-01-01T00:00:10Z"))

    closed = OandaPriceSnapshot.from_api(price_payload(tradeable=False, status="non-tradeable"), spec)
    with pytest.raises(OandaPriceValidationError, match="not tradeable"):
        broker.validate_price_snapshot(closed)


def test_oanda_place_order_uses_metadata_pricing_and_price_bound() -> None:
    config = OandaConfig(
        account_id="acct",
        token="token",
        max_spread_pips=5.0,
        max_order_slippage_pips=1.0,
    )
    responses = {
        ("GET", "/accounts/acct/instruments"): {"instruments": [instrument_payload()]},
        ("GET", "/accounts/acct/pricing"): {"prices": [price_payload()]},
        ("POST", "/accounts/acct/orders"): {
            "orderFillTransaction": {
                "id": "fill-1",
                "instrument": "EUR_USD",
                "units": "1000",
                "price": "1.10021",
                "commission": "0.0",
                "time": pd.Timestamp.now(tz="UTC").isoformat(),
                "tradeOpened": {"tradeID": "trade-1"},
            }
        },
    }
    broker = broker_with_fake_client(config, responses)

    fill = broker.place_order(
        OrderRequest(
            symbol="EURUSD",
            side="buy",
            units=1000,
            stop_loss=1.095,
            take_profit=1.11,
            client_order_id="client-1",
        ),
        market_price=1.1,
    )

    order_call = broker.client.calls[-1]
    order = order_call["payload"]["order"]
    assert fill.order_id == "fill-1"
    assert fill.metadata["oanda_trade_opened_id"] == "trade-1"
    assert fill.spread == pytest.approx(0.0002)
    assert order["instrument"] == "EUR_USD"
    assert order["units"] == "1000"
    assert order["timeInForce"] == "FOK"
    assert order["clientExtensions"]["id"] == "client-1"
    assert order["stopLossOnFill"]["price"] == "1.09500"
    assert order["takeProfitOnFill"]["price"] == "1.11000"
    assert order["priceBound"] == "1.10030"


def test_oanda_place_order_raises_when_response_has_no_fill() -> None:
    config = OandaConfig(account_id="acct", token="token", price_check_before_order=False)
    responses = {
        ("GET", "/accounts/acct/instruments"): {"instruments": [instrument_payload()]},
        ("POST", "/accounts/acct/orders"): {
            "orderCancelTransaction": {
                "id": "cancel-1",
                "reason": "MARKET_HALTED",
            }
        },
    }
    broker = broker_with_fake_client(config, responses)

    with pytest.raises(OandaOrderRejected, match="MARKET_HALTED"):
        broker.place_order(OrderRequest(symbol="EURUSD", side="buy", units=1000), market_price=1.1)


def test_oanda_client_retries_get_rate_limit(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                429,
                "too many requests",
                {"Retry-After": "0"},
                io.BytesIO(b'{"errorCode":"RATE_LIMIT","errorMessage":"slow down"}'),
            )
        return FakeResponse()

    monkeypatch.setattr(oanda_module, "urlopen", fake_urlopen)
    client = OandaClient(OandaConfig(account_id="acct", token="token", retry_backoff_seconds=0.0))

    assert client.request("GET", "/test") == {"ok": True}
    assert len(calls) == 2


def test_oanda_client_classifies_rate_limit_and_order_rejection(monkeypatch) -> None:
    def rate_limited(request, timeout):
        raise HTTPError(
            request.full_url,
            429,
            "too many requests",
            {},
            io.BytesIO(b'{"errorCode":"RATE_LIMIT","errorMessage":"slow down"}'),
        )

    monkeypatch.setattr(oanda_module, "urlopen", rate_limited)
    client = OandaClient(OandaConfig(account_id="acct", token="token", max_retries=0))
    with pytest.raises(OandaRateLimitError) as rate_error:
        client.request("GET", "/test")
    assert rate_error.value.error_code == "RATE_LIMIT"

    def rejected(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "bad request",
            {},
            io.BytesIO(b'{"orderRejectTransaction":{"reason":"UNITS_INVALID"}}'),
        )

    monkeypatch.setattr(oanda_module, "urlopen", rejected)
    with pytest.raises(OandaOrderRejected) as order_error:
        client.request("POST", "/orders")
    assert order_error.value.error_code == "UNITS_INVALID"


def test_oanda_practice_readiness_report_probes_without_trading() -> None:
    config = OandaConfig(account_id="acct", token="token", max_spread_pips=5.0)
    responses = {
        ("GET", "/accounts/acct/summary"): {
            "account": {
                "balance": "10000",
                "NAV": "10010",
                "marginUsed": "100",
                "marginAvailable": "9910",
                "currency": "USD",
            }
        },
        ("GET", "/accounts/acct/instruments"): {"instruments": [instrument_payload()]},
        ("GET", "/accounts/acct/pricing"): {"prices": [price_payload()]},
    }
    broker = broker_with_fake_client(config, responses)

    report = broker.practice_readiness(("EURUSD",))

    assert report.ok
    assert report.summary() == "oanda_practice_ready"
    assert report.account is not None
    assert report.instruments[0].symbol == "EURUSD"
    assert report.prices[0].tradeable
    assert not report.to_frame().empty
