"""Download Trading Economics calendar events into the project news format.

Set environment variable:
TRADING_ECONOMICS_API_KEY
"""

from __future__ import annotations

import argparse

import pandas as pd

from smc_ta.news import TradingEconomicsCalendarSource, TradingEconomicsConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="Start date, for example 2024-01-01")
    parser.add_argument("--end", default=None, help="End date, for example 2024-01-07")
    parser.add_argument("--currencies", nargs="+", default=["USD", "EUR", "GBP"])
    parser.add_argument("--importance", nargs="+", type=int, default=[3], help="1 low, 2 medium, 3 high")
    parser.add_argument("--values", action="store_true", help="Request actual/forecast/previous fields when available")
    args = parser.parse_args()

    start = pd.Timestamp(args.start) if args.start else pd.Timestamp.now(tz="UTC").normalize()
    end = pd.Timestamp(args.end) if args.end else start + pd.Timedelta(days=7)
    source = TradingEconomicsCalendarSource(
        TradingEconomicsConfig.from_env(
            importance=tuple(args.importance),
            values=args.values,
        )
    )

    events = source.get_events(start=start, end=end, currencies=set(args.currencies))
    if not events:
        print("No matching events found.")
        return

    rows = [
        {
            "timestamp": event.timestamp,
            "currency": event.currency,
            "impact": event.impact,
            "title": event.title,
            "source": event.source,
        }
        for event in events
    ]
    print(pd.DataFrame(rows).sort_values(["timestamp", "currency"]).to_string(index=False))


if __name__ == "__main__":
    main()
