"""Confluence engine."""

from smc_ta.engine.confluence import (
    AnalysisResult,
    ConfluenceConfig,
    analyze_forex,
    build_smc_ta_features,
    generate_confluence_signals,
)
from smc_ta.engine.multitimeframe import MultiTimeframeConfig, MultiTimeframeResult, analyze_multi_timeframe

__all__ = [
    "AnalysisResult",
    "ConfluenceConfig",
    "MultiTimeframeConfig",
    "MultiTimeframeResult",
    "analyze_forex",
    "analyze_multi_timeframe",
    "build_smc_ta_features",
    "generate_confluence_signals",
]
