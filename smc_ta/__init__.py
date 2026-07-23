"""Forex-focused Smart Money Concept and technical-analysis toolkit."""

from smc_ta.engine.confluence import (
    AnalysisResult,
    ConfluenceConfig,
    analyze_forex,
    build_smc_ta_features,
    generate_confluence_signals,
)
from smc_ta.engine.multitimeframe import MultiTimeframeConfig, MultiTimeframeResult, analyze_multi_timeframe
from smc_ta.backtest import BacktestConfig, BacktestResult, run_backtest
from smc_ta.broker import PaperBroker
from smc_ta.live import DemoTradingBot
from smc_ta.reconciliation import (
    BrokerReconciler,
    MemoryPositionLedger,
    ReconciliationConfig,
    ReconciliationResult,
    SQLitePositionLedger,
)
from smc_ta.risk import RiskConfig, RiskDecision, RiskManager
from smc_ta.strategy import StrategyProfile, get_strategy_profile, list_strategy_profiles

__all__ = [
    "AnalysisResult",
    "BacktestConfig",
    "BacktestResult",
    "BrokerReconciler",
    "ConfluenceConfig",
    "DemoTradingBot",
    "MemoryPositionLedger",
    "MultiTimeframeConfig",
    "MultiTimeframeResult",
    "PaperBroker",
    "ReconciliationConfig",
    "ReconciliationResult",
    "RiskConfig",
    "RiskDecision",
    "RiskManager",
    "SQLitePositionLedger",
    "StrategyProfile",
    "analyze_forex",
    "analyze_multi_timeframe",
    "build_smc_ta_features",
    "generate_confluence_signals",
    "get_strategy_profile",
    "list_strategy_profiles",
    "run_backtest",
]
