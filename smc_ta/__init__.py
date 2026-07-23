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
from smc_ta.data import DataQualityConfig, DataQualityReport, validate_candle_quality
from smc_ta.live import DemoTradingBot
from smc_ta.reconciliation import (
    BrokerReconciler,
    MemoryPositionLedger,
    ReconciliationConfig,
    ReconciliationResult,
    SQLitePositionLedger,
)
from smc_ta.risk import PortfolioRiskConfig, PortfolioRiskDecision, PortfolioRiskManager, RiskConfig, RiskDecision, RiskManager
from smc_ta.safety import EmergencyStopConfig, EmergencyStopController, EmergencyStopResult
from smc_ta.strategy import StrategyProfile, get_strategy_profile, list_strategy_profiles
from smc_ta.walkforward import (
    WalkForwardCandidate,
    WalkForwardConfig,
    WalkForwardResult,
    run_walk_forward,
)

__all__ = [
    "AnalysisResult",
    "BacktestConfig",
    "BacktestResult",
    "BrokerReconciler",
    "ConfluenceConfig",
    "DataQualityConfig",
    "DataQualityReport",
    "DemoTradingBot",
    "EmergencyStopConfig",
    "EmergencyStopController",
    "EmergencyStopResult",
    "MemoryPositionLedger",
    "MultiTimeframeConfig",
    "MultiTimeframeResult",
    "PaperBroker",
    "PortfolioRiskConfig",
    "PortfolioRiskDecision",
    "PortfolioRiskManager",
    "ReconciliationConfig",
    "ReconciliationResult",
    "RiskConfig",
    "RiskDecision",
    "RiskManager",
    "SQLitePositionLedger",
    "StrategyProfile",
    "WalkForwardCandidate",
    "WalkForwardConfig",
    "WalkForwardResult",
    "analyze_forex",
    "analyze_multi_timeframe",
    "build_smc_ta_features",
    "generate_confluence_signals",
    "get_strategy_profile",
    "list_strategy_profiles",
    "run_backtest",
    "run_walk_forward",
    "validate_candle_quality",
]
