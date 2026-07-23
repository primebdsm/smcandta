"""Economic calendar and news-risk filters."""

from smc_ta.news.calendar import EconomicEvent, NewsFilter
from smc_ta.news.sources import (
    EconomicCalendarSource,
    JsonEconomicCalendarSource,
    StaticEconomicCalendarSource,
    news_filter_from_source,
)
from smc_ta.news.tradingeconomics import (
    TradingEconomicsApiError,
    TradingEconomicsCalendarSource,
    TradingEconomicsConfig,
    countries_for_currencies,
    importance_to_impact,
)

__all__ = [
    "EconomicCalendarSource",
    "EconomicEvent",
    "JsonEconomicCalendarSource",
    "NewsFilter",
    "StaticEconomicCalendarSource",
    "TradingEconomicsApiError",
    "TradingEconomicsCalendarSource",
    "TradingEconomicsConfig",
    "countries_for_currencies",
    "importance_to_impact",
    "news_filter_from_source",
]
