"""Trading Economics economic calendar connector."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smc_ta.news.calendar import EconomicEvent, Impact, NewsFilter

TRADING_ECONOMICS_BASE_URL = "https://api.tradingeconomics.com"

COUNTRY_TO_CURRENCY = {
    "Australia": "AUD",
    "Canada": "CAD",
    "China": "CNY",
    "Euro Area": "EUR",
    "Germany": "EUR",
    "France": "EUR",
    "Italy": "EUR",
    "Japan": "JPY",
    "New Zealand": "NZD",
    "Switzerland": "CHF",
    "United Kingdom": "GBP",
    "United States": "USD",
}

CURRENCY_TO_COUNTRIES = {
    "AUD": ("Australia",),
    "CAD": ("Canada",),
    "CHF": ("Switzerland",),
    "CNY": ("China",),
    "EUR": ("Euro Area", "Germany", "France", "Italy"),
    "GBP": ("United Kingdom",),
    "JPY": ("Japan",),
    "NZD": ("New Zealand",),
    "USD": ("United States",),
}

IMPORTANCE_TO_IMPACT: dict[int, Impact] = {
    1: "low",
    2: "medium",
    3: "high",
}


class TradingEconomicsApiError(RuntimeError):
    """Raised when Trading Economics returns an API error."""


@dataclass(frozen=True)
class TradingEconomicsConfig:
    """Trading Economics API settings."""

    api_key: str
    base_url: str = TRADING_ECONOMICS_BASE_URL
    timeout: float = 20.0
    importance: tuple[int, ...] | None = None
    values: bool = False
    country_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env_var: str = "TRADING_ECONOMICS_API_KEY", **kwargs) -> "TradingEconomicsConfig":
        """Build config from an environment variable."""

        api_key = os.environ.get(env_var)
        if not api_key:
            raise ValueError(f"{env_var} is required")
        return cls(api_key=api_key, **kwargs)


class TradingEconomicsCalendarSource:
    """EconomicCalendarSource implementation for Trading Economics."""

    def __init__(self, config: TradingEconomicsConfig) -> None:
        self.config = config

    def get_events(
        self,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        currencies: set[str] | None = None,
        countries: tuple[str, ...] | None = None,
    ) -> list[EconomicEvent]:
        """Return normalized calendar events from Trading Economics."""

        country_names = countries or countries_for_currencies(currencies or set())
        rows = self._request_calendar(start=start, end=end, countries=country_names)
        events = [self._row_to_event(row) for row in rows]
        if currencies:
            allowed = {currency.upper() for currency in currencies}
            events = [event for event in events if event.currency.upper() in allowed]
        return events

    def build_news_filter(
        self,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        currencies: set[str] | None = None,
        countries: tuple[str, ...] | None = None,
        **kwargs,
    ) -> NewsFilter:
        """Build a NewsFilter from provider events."""

        return NewsFilter(
            self.get_events(start=start, end=end, currencies=currencies, countries=countries),
            **kwargs,
        )

    def _request_calendar(
        self,
        *,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
        countries: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        path = self._calendar_path(start=start, end=end, countries=countries)
        params: dict[str, str] = {
            "c": self.config.api_key,
            "f": "json",
        }
        if self.config.importance:
            params["importance"] = ",".join(str(value) for value in self.config.importance)
        if self.config.values:
            params["values"] = "true"
        url = f"{self.config.base_url.rstrip('/')}{path}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TradingEconomicsApiError(f"Trading Economics API error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TradingEconomicsApiError(f"Trading Economics API request failed: {exc.reason}") from exc
        data = json.loads(payload) if payload else []
        if isinstance(data, dict):
            if "Message" in data or "error" in data:
                raise TradingEconomicsApiError(str(data))
            if "Date" in data and ("Country" in data or "Event" in data):
                return [data]
            data = data.get("data", [])
        if not isinstance(data, list):
            raise TradingEconomicsApiError(f"unexpected response payload: {type(data).__name__}")
        return data

    @staticmethod
    def _calendar_path(
        *,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
        countries: tuple[str, ...],
    ) -> str:
        date_part = ""
        if start is not None and end is not None:
            date_part = f"/{_te_date(start)}/{_te_date(end)}"
        if countries:
            country_part = ",".join(quote(country.lower(), safe="") for country in countries)
            return f"/calendar/country/{country_part}{date_part}"
        return f"/calendar{date_part}"

    def _row_to_event(self, row: dict[str, Any]) -> EconomicEvent:
        country = str(row.get("Country") or "")
        currency = self.config.country_overrides.get(country) or COUNTRY_TO_CURRENCY.get(country) or _currency_from_row(row)
        impact = importance_to_impact(row.get("Importance"))
        title = str(row.get("Event") or row.get("Category") or "Economic Event")
        source_name = str(row.get("Source") or "Trading Economics")
        calendar_id = row.get("CalendarId") or row.get("CalendarID")
        if calendar_id:
            source_name = f"{source_name} | TE:{calendar_id}"
        return EconomicEvent(
            timestamp=_utc_timestamp(row["Date"]),
            currency=currency.upper(),
            impact=impact,
            title=title,
            source=source_name,
        )


def countries_for_currencies(currencies: set[str]) -> tuple[str, ...]:
    """Return Trading Economics country names for Forex currency codes."""

    countries: list[str] = []
    for currency in sorted(currency.upper() for currency in currencies):
        countries.extend(CURRENCY_TO_COUNTRIES.get(currency, ()))
    return tuple(dict.fromkeys(countries))


def importance_to_impact(value: object) -> Impact:
    """Map Trading Economics importance number to low/medium/high impact."""

    try:
        importance = int(float(value))
    except (TypeError, ValueError):
        importance = 1
    return IMPORTANCE_TO_IMPACT.get(importance, "low")


def _te_date(value: pd.Timestamp) -> str:
    return _utc_timestamp(value).strftime("%Y-%m-%d")


def _utc_timestamp(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _currency_from_row(row: dict[str, Any]) -> str:
    raw = str(row.get("Currency") or "").upper()
    if raw in {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY"}:
        return raw
    return "XXX"
