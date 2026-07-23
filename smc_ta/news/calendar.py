"""Economic-calendar risk filter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import pandas as pd

Impact = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class EconomicEvent:
    """Scheduled economic event."""

    timestamp: pd.Timestamp
    currency: str
    impact: Impact
    title: str
    source: str | None = None


class NewsFilter:
    """Block signals around configured economic calendar events."""

    def __init__(
        self,
        events: list[EconomicEvent] | pd.DataFrame | None = None,
        *,
        block_before: timedelta = timedelta(minutes=30),
        block_after: timedelta = timedelta(minutes=30),
        impacts: tuple[Impact, ...] = ("high",),
    ) -> None:
        self.block_before = block_before
        self.block_after = block_after
        self.impacts = set(impacts)
        self.events = self._coerce_events(events)

    @staticmethod
    def _coerce_events(events: list[EconomicEvent] | pd.DataFrame | None) -> pd.DataFrame:
        if events is None:
            return pd.DataFrame(columns=["timestamp", "currency", "impact", "title", "source"])
        if isinstance(events, pd.DataFrame):
            out = events.copy()
        else:
            out = pd.DataFrame(
                [
                    {
                        "timestamp": event.timestamp,
                        "currency": event.currency,
                        "impact": event.impact,
                        "title": event.title,
                        "source": event.source,
                    }
                    for event in events
                ]
            )
        if out.empty:
            return pd.DataFrame(columns=["timestamp", "currency", "impact", "title", "source"])
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        out["currency"] = out["currency"].str.upper()
        out["impact"] = out["impact"].str.lower()
        return out.sort_values("timestamp").reset_index(drop=True)

    @classmethod
    def from_csv(cls, path: str, **kwargs) -> "NewsFilter":
        """Load events from a CSV with timestamp, currency, impact, and title."""

        return cls(pd.read_csv(path), **kwargs)

    @staticmethod
    def currencies_for_symbol(symbol: str) -> set[str]:
        clean = "".join(ch for ch in symbol.upper() if ch.isalpha())[:6]
        if len(clean) < 6:
            raise ValueError(f"cannot infer currencies from symbol: {symbol}")
        return {clean[:3], clean[3:6]}

    def blocking_events(self, symbol: str, timestamp: pd.Timestamp) -> pd.DataFrame:
        """Return events that block trading at `timestamp`."""

        if self.events.empty:
            return self.events.copy()
        ts = pd.Timestamp(timestamp)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        currencies = self.currencies_for_symbol(symbol)
        # Convert a trade-time check into event-time bounds.
        start = ts - self.block_after
        end = ts + self.block_before
        mask = (
            self.events["currency"].isin(currencies)
            & self.events["impact"].isin(self.impacts)
            & self.events["timestamp"].between(start, end)
        )
        return self.events[mask].copy()

    def allow_trading(self, symbol: str, timestamp: pd.Timestamp) -> bool:
        """Return false when a matching event is inside the block window."""

        return self.blocking_events(symbol, timestamp).empty
