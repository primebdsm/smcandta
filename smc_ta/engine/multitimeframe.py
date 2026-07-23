"""Multi-timeframe SMC + TA analysis."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smc_ta.engine.confluence import AnalysisResult, ConfluenceConfig, analyze_forex
from smc_ta.smc.setups import classify_smc_setups
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class MultiTimeframeConfig:
    """Configuration for higher-timeframe/entry-timeframe confluence."""

    entry_timeframe: str
    higher_timeframes: tuple[str, ...]
    confluence: ConfluenceConfig = ConfluenceConfig()
    require_higher_timeframe_alignment: bool = True
    higher_timeframe_score_bonus: int = 2


@dataclass(frozen=True)
class MultiTimeframeResult:
    """Multi-timeframe analysis output."""

    entry_timeframe: str
    analyses: dict[str, AnalysisResult]
    projected_context: pd.DataFrame
    signals: pd.DataFrame
    setup_classification: pd.DataFrame


def analyze_multi_timeframe(
    candles_by_timeframe: dict[str, pd.DataFrame],
    *,
    symbol: str = "EURUSD",
    config: MultiTimeframeConfig,
) -> MultiTimeframeResult:
    """Analyze higher timeframes and project their context onto entry candles."""

    if config.entry_timeframe not in candles_by_timeframe:
        raise KeyError(f"missing entry timeframe candles: {config.entry_timeframe}")
    missing_htf = [tf for tf in config.higher_timeframes if tf not in candles_by_timeframe]
    if missing_htf:
        raise KeyError(f"missing higher timeframe candles: {missing_htf}")

    analyses = {
        timeframe: analyze_forex(normalize_ohlcv(candles), symbol=symbol, config=config.confluence)
        for timeframe, candles in candles_by_timeframe.items()
    }
    entry = analyses[config.entry_timeframe]
    projected = _project_higher_timeframes(
        entry.features.index,
        {timeframe: analyses[timeframe] for timeframe in config.higher_timeframes},
    )
    signals = _apply_higher_timeframe_bias(
        entry.signals.copy(),
        projected,
        require_alignment=config.require_higher_timeframe_alignment,
        score_bonus=config.higher_timeframe_score_bonus,
    )
    setups = classify_smc_setups(entry.features.join(projected, how="left"), signals)
    return MultiTimeframeResult(
        entry_timeframe=config.entry_timeframe,
        analyses=analyses,
        projected_context=projected,
        signals=signals,
        setup_classification=setups,
    )


def _project_higher_timeframes(entry_index: pd.Index, analyses: dict[str, AnalysisResult]) -> pd.DataFrame:
    if not isinstance(entry_index, pd.DatetimeIndex):
        raise TypeError("multi-timeframe projection requires a DateTimeIndex")
    projected = pd.DataFrame(index=entry_index)
    for timeframe, analysis in analyses.items():
        features = analysis.features
        subset = features[
            [
                "structure_trend",
                "pd_zone",
                "active_bull_fvg_distance",
                "active_bear_fvg_distance",
                "active_bull_ob_distance",
                "active_bear_ob_distance",
            ]
        ].copy()
        subset = subset.add_prefix(f"{timeframe}_")
        projected = projected.join(subset.reindex(entry_index, method="ffill"))
    projected["higher_timeframe_bias"] = projected.apply(_bias_from_row, axis=1)
    return projected


def _bias_from_row(row: pd.Series) -> str:
    bullish = 0
    bearish = 0
    for key, value in row.items():
        if key.endswith("_structure_trend"):
            bullish += int(value == "bullish")
            bearish += int(value == "bearish")
        elif key.endswith("_pd_zone"):
            bullish += int(value == "discount")
            bearish += int(value == "premium")
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _apply_higher_timeframe_bias(
    signals: pd.DataFrame,
    projected: pd.DataFrame,
    *,
    require_alignment: bool,
    score_bonus: int,
) -> pd.DataFrame:
    out = signals.copy()
    out["higher_timeframe_bias"] = projected["higher_timeframe_bias"]
    out["mtf_aligned"] = (
        ((out["side"] == "long") & (out["higher_timeframe_bias"] == "bullish"))
        | ((out["side"] == "short") & (out["higher_timeframe_bias"] == "bearish"))
        | (out["side"] == "flat")
    )
    long_mask = (out["side"] == "long") & (out["higher_timeframe_bias"] == "bullish")
    short_mask = (out["side"] == "short") & (out["higher_timeframe_bias"] == "bearish")
    out.loc[long_mask, "long_score"] = out.loc[long_mask, "long_score"] + score_bonus
    out.loc[short_mask, "short_score"] = out.loc[short_mask, "short_score"] + score_bonus
    if require_alignment:
        blocked = (out["side"].isin(["long", "short"])) & ~out["mtf_aligned"]
        out.loc[blocked, "reasons"] = out.loc[blocked, "reasons"].astype(str) + ";blocked_by_higher_timeframe_bias"
        out.loc[blocked, "side"] = "flat"
    return out

