# News Providers

The repository includes two economic-calendar integration paths:

- `JsonEconomicCalendarSource` for provider APIs that can be mapped with a field map.
- `TradingEconomicsCalendarSource` for the Trading Economics calendar API.

These sources produce the same normalized objects:

```python
EconomicEvent(
    timestamp=...,  # UTC pandas Timestamp
    currency="USD",
    impact="high",
    title="Non Farm Payrolls",
    source="..."
)
```

`NewsFilter` then blocks trading when a matching base or quote currency has a configured event inside the block window.

## Trading Economics

Set the API key outside the repository:

```bash
export TRADING_ECONOMICS_API_KEY="your_key"
```

Example:

```python
from datetime import timedelta

import pandas as pd

from smc_ta.news import TradingEconomicsCalendarSource, TradingEconomicsConfig

source = TradingEconomicsCalendarSource(
    TradingEconomicsConfig.from_env(importance=(3,))
)
news_filter = source.build_news_filter(
    start=pd.Timestamp("2024-01-01"),
    end=pd.Timestamp("2024-01-07"),
    currencies={"USD", "EUR", "GBP"},
    block_before=timedelta(minutes=30),
    block_after=timedelta(minutes=30),
)

allowed = news_filter.allow_trading("EURUSD", pd.Timestamp("2024-01-05T13:00:00Z"))
```

Command-line check:

```bash
python examples/tradingeconomics_news.py --currencies USD EUR GBP --start 2024-01-01 --end 2024-01-07
```

## How It Works

1. The bot asks for currencies and a date range.
2. The connector maps Forex currencies to provider country names, for example `USD -> United States`.
3. The connector downloads calendar rows from Trading Economics.
4. Provider importance is mapped to project impact: `1 -> low`, `2 -> medium`, `3 -> high`.
5. Calendar timestamps are normalized to UTC.
6. `NewsFilter` blocks pairs where the event currency matches the base or quote currency.

For example, a high-impact USD event at `12:30 UTC` can block `EURUSD`, `GBPUSD`, and `USDJPY` from `12:00` to `13:00` when the before/after windows are 30 minutes.

## Notes For Live Use

Economic-calendar filtering is a risk control, not a prediction tool. It prevents or reduces exposure around scheduled events where spreads, slippage, and price gaps can become abnormal.

The connector does not store credentials, does not place trades, and does not change strategy logic. It only feeds real provider events into the existing broker-neutral `NewsFilter`.

## Official References

- Trading Economics calendar API: https://tradingeconomics.com/api/calendar.aspx
- Trading Economics country calendar endpoint: https://docs.tradingeconomics.com/economic_calendar/country/
- Trading Economics date-range endpoint: https://docs.tradingeconomics.com/economic_calendar/point-in-time/
- Trading Economics response fields: https://docs.tradingeconomics.com/economic_calendar/schema/
