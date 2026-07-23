"""Forex-focused Smart Money Concept and technical-analysis toolkit."""

from smc_ta.engine.confluence import (
    AnalysisResult,
    ConfluenceConfig,
    analyze_forex,
    build_smc_ta_features,
    generate_confluence_signals,
)
from smc_ta.backtest import BacktestConfig, BacktestResult, run_backtest
from smc_ta.broker import PaperBroker
from smc_ta.live import DemoTradingBot
from smc_ta.risk import RiskConfig, RiskDecision, RiskManager

__all__ = [
    "AnalysisResult",
    "BacktestConfig",
    "BacktestResult",
    "ConfluenceConfig",
    "DemoTradingBot",
    "PaperBroker",
    "RiskConfig",
    "RiskDecision",
    "RiskManager",
    "analyze_forex",
    "build_smc_ta_features",
    "generate_confluence_signals",
    "run_backtest",
]
