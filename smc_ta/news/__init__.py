"""Economic calendar and news-risk filters."""

from smc_ta.news.calendar import EconomicEvent, NewsFilter
from smc_ta.news.sources import (
    EconomicCalendarSource,
    JsonEconomicCalendarSource,
    StaticEconomicCalendarSource,
    news_filter_from_source,
)

__all__ = [
    "EconomicCalendarSource",
    "EconomicEvent",
    "JsonEconomicCalendarSource",
    "NewsFilter",
    "StaticEconomicCalendarSource",
    "news_filter_from_source",
]
