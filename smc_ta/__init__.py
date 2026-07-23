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
from smc_ta.config import (
    LIVE_CONFIRMATION_PHRASE,
    ConfigValidationError,
    ConfigValidationReport,
    RuntimeConfig,
    assert_runtime_ready,
    validate_runtime_config,
)
from smc_ta.data import DataQualityConfig, DataQualityReport, validate_candle_quality
from smc_ta.lifecycle import (
    MemoryTradeLifecycleStore,
    SQLiteTradeLifecycleStore,
    TradeLifecycleError,
    TradeLifecycleRecord,
    TradeLifecycleStateMachine,
)
from smc_ta.live import DemoTradingBot
from smc_ta.news import TradingEconomicsApiError, TradingEconomicsCalendarSource, TradingEconomicsConfig
from smc_ta.preflight import (
    PreflightCheck,
    PreflightConfig,
    PreflightReport,
    PreflightValidationError,
    assert_preflight_ready,
    run_preflight,
)
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
from smc_ta.visualization import ChartConfig, render_analysis_chart_html, render_analysis_chart_svg, write_analysis_chart
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
    "ChartConfig",
    "ConfluenceConfig",
    "ConfigValidationError",
    "ConfigValidationReport",
    "DataQualityConfig",
    "DataQualityReport",
    "DemoTradingBot",
    "EmergencyStopConfig",
    "EmergencyStopController",
    "EmergencyStopResult",
    "LIVE_CONFIRMATION_PHRASE",
    "MemoryPositionLedger",
    "MemoryTradeLifecycleStore",
    "MultiTimeframeConfig",
    "MultiTimeframeResult",
    "PaperBroker",
    "PortfolioRiskConfig",
    "PortfolioRiskDecision",
    "PortfolioRiskManager",
    "PreflightCheck",
    "PreflightConfig",
    "PreflightReport",
    "PreflightValidationError",
    "ReconciliationConfig",
    "ReconciliationResult",
    "RiskConfig",
    "RiskDecision",
    "RiskManager",
    "RuntimeConfig",
    "SQLitePositionLedger",
    "SQLiteTradeLifecycleStore",
    "StrategyProfile",
    "TradingEconomicsApiError",
    "TradingEconomicsCalendarSource",
    "TradingEconomicsConfig",
    "TradeLifecycleError",
    "TradeLifecycleRecord",
    "TradeLifecycleStateMachine",
    "WalkForwardCandidate",
    "WalkForwardConfig",
    "WalkForwardResult",
    "analyze_forex",
    "analyze_multi_timeframe",
    "assert_preflight_ready",
    "assert_runtime_ready",
    "build_smc_ta_features",
    "generate_confluence_signals",
    "get_strategy_profile",
    "list_strategy_profiles",
    "run_backtest",
    "run_preflight",
    "run_walk_forward",
    "render_analysis_chart_html",
    "render_analysis_chart_svg",
    "validate_candle_quality",
    "validate_runtime_config",
    "write_analysis_chart",
]
