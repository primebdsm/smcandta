"""Economic calendar API sources."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smc_ta.news.calendar import EconomicEvent


class EconomicCalendarSource(Protocol):
    """Protocol for calendar event downloaders."""

    def get_events(
        self,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        currencies: set[str] | None = None,
    ) -> list[EconomicEvent]:
        """Return normalized economic events."""


@dataclass(frozen=True)
class JsonEconomicCalendarSource:
    """Generic JSON economic-calendar source.

    Configure `field_map` to map provider fields to timestamp, currency,
    impact, title, and source. The response can be either a JSON list or a dict
    containing `events_key`.
    """

    url: str
    field_map: dict[str, str]
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    events_key: str | None = None
    timeout: float = 20.0

    def get_events(
        self,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        currencies: set[str] | None = None,
    ) -> list[EconomicEvent]:
        query = dict(self.query)
        if start is not None:
            query.setdefault("start", pd.Timestamp(start).isoformat())
        if end is not None:
            query.setdefault("end", pd.Timestamp(end).isoformat())
        if currencies:
            query.setdefault("currencies", ",".join(sorted(currency.upper() for currency in currencies)))
        url = f"{self.url}?{urlencode(query)}" if query else self.url
        request = Request(url, headers=self.headers)
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get(self.events_key, []) if self.events_key else payload
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: dict[str, Any]) -> EconomicEvent:
        return EconomicEvent(
            timestamp=pd.Timestamp(row[self.field_map["timestamp"]]),
            currency=str(row[self.field_map["currency"]]).upper(),
            impact=str(row[self.field_map["impact"]]).lower(),  # type: ignore[arg-type]
            title=str(row[self.field_map["title"]]),
            source=str(row.get(self.field_map.get("source", ""), "")) or None,
        )


@dataclass(frozen=True)
class StaticEconomicCalendarSource:
    """In-memory calendar source for tests and manual event lists."""

    events: list[EconomicEvent]

    def get_events(
        self,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        currencies: set[str] | None = None,
    ) -> list[EconomicEvent]:
        out = self.events
        if start is not None:
            out = [event for event in out if pd.Timestamp(event.timestamp) >= pd.Timestamp(start)]
        if end is not None:
            out = [event for event in out if pd.Timestamp(event.timestamp) <= pd.Timestamp(end)]
        if currencies:
            allowed = {currency.upper() for currency in currencies}
            out = [event for event in out if event.currency.upper() in allowed]
        return out


def news_filter_from_source(
    source: EconomicCalendarSource,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    currencies: set[str] | None = None,
    **kwargs,
):
    """Build `NewsFilter` from a calendar source."""

    from smc_ta.news.calendar import NewsFilter

    return NewsFilter(source.get_events(start=start, end=end, currencies=currencies), **kwargs)

