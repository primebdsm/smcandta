"""Named strategy presets for common Forex workflows."""

from __future__ import annotations

from dataclasses import dataclass

from smc_ta.backtest.engine import BacktestConfig
from smc_ta.engine.confluence import ConfluenceConfig
from smc_ta.engine.multitimeframe import MultiTimeframeConfig
from smc_ta.risk.manager import RiskConfig


@dataclass(frozen=True)
class StrategyProfile:
    """Strategy preset containing analysis, MTF, risk, and backtest settings."""

    name: str
    description: str
    entry_timeframe: str
    higher_timeframes: tuple[str, ...]
    confluence: ConfluenceConfig
    risk: RiskConfig
    backtest: BacktestConfig

    def mtf_config(self) -> MultiTimeframeConfig:
        return MultiTimeframeConfig(
            entry_timeframe=self.entry_timeframe,
            higher_timeframes=self.higher_timeframes,
            confluence=self.confluence,
        )


def _profiles() -> dict[str, StrategyProfile]:
    scalping_confluence = ConfluenceConfig(
        swing_left=2,
        swing_right=2,
        min_signal_score=5,
        max_spread_pips=1.8,
        recent_sweep_bars=4,
    )
    intraday_confluence = ConfluenceConfig(
        swing_left=3,
        swing_right=3,
        min_signal_score=6,
        max_spread_pips=2.5,
    )
    swing_confluence = ConfluenceConfig(
        swing_left=5,
        swing_right=5,
        min_signal_score=6,
        max_spread_pips=4.0,
        premium_discount_lookback=150,
    )
    return {
        "scalping_m5": StrategyProfile(
            name="scalping_m5",
            description="M5 execution with M15/H1 context and tighter spread controls.",
            entry_timeframe="M5",
            higher_timeframes=("M15", "H1"),
            confluence=scalping_confluence,
            risk=RiskConfig(risk_percent_per_trade=0.25, max_daily_loss_percent=1.5, max_open_positions=1, min_confidence=0.45),
            backtest=BacktestConfig(confluence=scalping_confluence, max_daily_trades=4, session_filter=("london_kill_zone", "new_york_kill_zone")),
        ),
        "intraday_m15": StrategyProfile(
            name="intraday_m15",
            description="M15 execution with H1/H4 SMC bias.",
            entry_timeframe="M15",
            higher_timeframes=("H1", "H4"),
            confluence=intraday_confluence,
            risk=RiskConfig(risk_percent_per_trade=0.5, max_daily_loss_percent=2.0, max_open_positions=2, min_confidence=0.5),
            backtest=BacktestConfig(confluence=intraday_confluence, max_daily_trades=3, trailing_stop_atr_multiple=1.5),
        ),
        "swing_h4": StrategyProfile(
            name="swing_h4",
            description="H4 execution with daily context and wider spread tolerance.",
            entry_timeframe="H4",
            higher_timeframes=("D",),
            confluence=swing_confluence,
            risk=RiskConfig(risk_percent_per_trade=1.0, max_daily_loss_percent=3.0, max_open_positions=3, min_confidence=0.5),
            backtest=BacktestConfig(confluence=swing_confluence, max_daily_trades=1, trailing_stop_atr_multiple=2.0, partial_close_at_rr=1.5),
        ),
        "london_killzone": StrategyProfile(
            name="london_killzone",
            description="London kill-zone sweep/reversal profile.",
            entry_timeframe="M5",
            higher_timeframes=("M15", "H1"),
            confluence=scalping_confluence,
            risk=RiskConfig(risk_percent_per_trade=0.25, max_daily_loss_percent=1.0, max_open_positions=1, min_confidence=0.45),
            backtest=BacktestConfig(confluence=scalping_confluence, max_daily_trades=2, session_filter=("london_kill_zone",)),
        ),
        "ny_session_reversal": StrategyProfile(
            name="ny_session_reversal",
            description="New York sweep/reversal profile.",
            entry_timeframe="M5",
            higher_timeframes=("M15", "H1"),
            confluence=scalping_confluence,
            risk=RiskConfig(risk_percent_per_trade=0.25, max_daily_loss_percent=1.0, max_open_positions=1, min_confidence=0.45),
            backtest=BacktestConfig(confluence=scalping_confluence, max_daily_trades=2, session_filter=("new_york_kill_zone",)),
        ),
    }


def list_strategy_profiles() -> list[str]:
    """Return available profile names."""

    return sorted(_profiles())


def get_strategy_profile(name: str) -> StrategyProfile:
    """Return a named strategy profile."""

    profiles = _profiles()
    key = name.lower()
    if key not in profiles:
        raise KeyError(f"unknown strategy profile {name}; available: {sorted(profiles)}")
    return profiles[key]

