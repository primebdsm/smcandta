from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from smc_ta.news import (
    TradingEconomicsApiError,
    TradingEconomicsCalendarSource,
    TradingEconomicsConfig,
    countries_for_currencies,
    importance_to_impact,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def patch_calendar_response(monkeypatch, payload: object, captured: dict[str, str] | None = None) -> None:
    def fake_urlopen(request, timeout=20.0):
        if captured is not None:
            captured["url"] = request.full_url
            captured["timeout"] = str(timeout)
        return FakeResponse(payload)

    monkeypatch.setattr("smc_ta.news.tradingeconomics.urlopen", fake_urlopen)


def test_countries_for_currencies_and_importance_mapping() -> None:
    assert countries_for_currencies({"usd", "eur", "JPY"}) == (
        "Euro Area",
        "Germany",
        "France",
        "Italy",
        "Japan",
        "United States",
    )
    assert importance_to_impact(3) == "high"
    assert importance_to_impact("2") == "medium"
    assert importance_to_impact(None) == "low"


def test_tradingeconomics_source_builds_url_and_normalizes_events(monkeypatch) -> None:
    captured: dict[str, str] = {}
    patch_calendar_response(
        monkeypatch,
        [
            {
                "CalendarId": "319275",
                "Date": "2024-01-05T13:30:00",
                "Country": "United States",
                "Category": "Non Farm Payrolls",
                "Event": "Non Farm Payrolls",
                "Importance": 3,
                "Source": "Bureau of Labor Statistics",
            }
        ],
        captured,
    )
    source = TradingEconomicsCalendarSource(
        TradingEconomicsConfig(
            api_key="demo:key",
            base_url="https://api.example.test",
            timeout=7.0,
            importance=(3,),
            values=True,
        )
    )

    events = source.get_events(
        start=pd.Timestamp("2024-01-01"),
        end=pd.Timestamp("2024-01-10"),
        currencies={"USD"},
    )

    parsed = urlparse(captured["url"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/calendar/country/united%20states/2024-01-01/2024-01-10"
    assert query["c"] == ["demo:key"]
    assert query["f"] == ["json"]
    assert query["importance"] == ["3"]
    assert query["values"] == ["true"]
    assert captured["timeout"] == "7.0"
    assert len(events) == 1
    assert events[0].timestamp == pd.Timestamp("2024-01-05T13:30:00Z")
    assert events[0].currency == "USD"
    assert events[0].impact == "high"
    assert events[0].title == "Non Farm Payrolls"
    assert events[0].source == "Bureau of Labor Statistics | TE:319275"


def test_tradingeconomics_source_filters_requested_currencies(monkeypatch) -> None:
    patch_calendar_response(
        monkeypatch,
        [
            {
                "Date": "2024-01-05T13:30:00Z",
                "Country": "United States",
                "Event": "Payrolls",
                "Importance": 3,
            },
            {
                "Date": "2024-01-05T23:30:00Z",
                "Country": "Japan",
                "Event": "Tokyo CPI",
                "Importance": 2,
            },
        ],
    )
    source = TradingEconomicsCalendarSource(TradingEconomicsConfig(api_key="demo"))

    events = source.get_events(countries=("United States", "Japan"), currencies={"JPY"})

    assert len(events) == 1
    assert events[0].currency == "JPY"
    assert events[0].impact == "medium"
    assert events[0].title == "Tokyo CPI"


def test_tradingeconomics_source_builds_news_filter(monkeypatch) -> None:
    patch_calendar_response(
        monkeypatch,
        [
            {
                "Date": "2024-06-06T12:15:00Z",
                "Country": "Euro Area",
                "Event": "ECB Rate Decision",
                "Importance": 3,
            }
        ],
    )
    source = TradingEconomicsCalendarSource(TradingEconomicsConfig(api_key="demo"))

    news_filter = source.build_news_filter(
        start=pd.Timestamp("2024-06-06"),
        end=pd.Timestamp("2024-06-07"),
        currencies={"EUR"},
        block_before=timedelta(minutes=45),
        block_after=timedelta(minutes=30),
    )

    assert not news_filter.allow_trading("EURUSD", pd.Timestamp("2024-06-06T12:30:00Z"))
    assert news_filter.allow_trading("EURUSD", pd.Timestamp("2024-06-06T13:00:00Z"))


def test_tradingeconomics_source_raises_on_provider_error_payload(monkeypatch) -> None:
    patch_calendar_response(monkeypatch, {"Message": "invalid credentials"})
    source = TradingEconomicsCalendarSource(TradingEconomicsConfig(api_key="bad"))

    with pytest.raises(TradingEconomicsApiError):
        source.get_events()
