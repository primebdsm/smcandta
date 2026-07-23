"""SMC + technical-analysis confluence engine."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smc_ta.forex.pairs import infer_pip_size, spread_to_pips
from smc_ta.forex.sessions import session_labels
from smc_ta.smc.gaps import active_fvg_features, fair_value_gaps
from smc_ta.smc.liquidity import equal_highs_lows, liquidity_sweeps, premium_discount_zones
from smc_ta.smc.order_blocks import active_order_block_features, detect_order_blocks
from smc_ta.smc.structure import market_structure
from smc_ta.technical.summary import build_technical_snapshot
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class ConfluenceConfig:
    """Configuration for the SMC + TA signal engine."""

    swing_left: int = 3
    swing_right: int = 3
    structure_break_pips: float = 0.0
    sweep_buffer_pips: float = 0.0
    equal_level_tolerance_pips: float = 2.0
    fvg_min_atr_multiple: float | None = 0.05
    order_block_search_lookback: int = 10
    order_block_min_displacement_atr: float = 1.0
    premium_discount_lookback: int = 100
    max_poi_atr_distance: float = 0.75
    recent_sweep_bars: int = 5
    adx_threshold: float = 18.0
    min_signal_score: int = 6
    max_spread_pips: float | None = 2.5
    reference_reward_to_risk: float = 2.0


@dataclass(frozen=True)
class AnalysisResult:
    """Full analysis result returned by `analyze_forex`."""

    candles: pd.DataFrame
    features: pd.DataFrame
    signals: pd.DataFrame
    market_structure: pd.DataFrame
    fair_value_gaps: pd.DataFrame
    order_blocks: pd.DataFrame
    liquidity_pools: pd.DataFrame


@dataclass(frozen=True)
class _AnalysisParts:
    candles: pd.DataFrame
    features: pd.DataFrame
    market_structure: pd.DataFrame
    fair_value_gaps: pd.DataFrame
    order_blocks: pd.DataFrame
    liquidity_pools: pd.DataFrame


def _build_parts(
    df: pd.DataFrame,
    *,
    symbol: str = "EURUSD",
    pip_size: float | None = None,
    config: ConfluenceConfig | None = None,
) -> _AnalysisParts:
    cfg = config or ConfluenceConfig()
    data = normalize_ohlcv(df)
    resolved_pip_size = pip_size if pip_size is not None else infer_pip_size(symbol)

    technical = build_technical_snapshot(data)
    structure = market_structure(
        data,
        left=cfg.swing_left,
        right=cfg.swing_right,
        min_break=cfg.structure_break_pips * resolved_pip_size,
    )
    gaps = fair_value_gaps(
        data,
        min_atr_multiple=cfg.fvg_min_atr_multiple,
    )
    fvg_features = active_fvg_features(data, gaps)
    blocks = detect_order_blocks(
        data,
        structure,
        search_lookback=cfg.order_block_search_lookback,
        min_displacement_atr=cfg.order_block_min_displacement_atr,
    )
    block_features = active_order_block_features(data, blocks)
    liquidity = liquidity_sweeps(
        data,
        left=cfg.swing_left,
        right=cfg.swing_right,
        buffer=cfg.sweep_buffer_pips * resolved_pip_size,
    )
    pools = equal_highs_lows(
        data,
        left=cfg.swing_left,
        right=cfg.swing_right,
        tolerance=cfg.equal_level_tolerance_pips * resolved_pip_size,
    )
    pd_zones = premium_discount_zones(data, lookback=cfg.premium_discount_lookback)

    features = data.join(technical)
    features = features.join(
        structure[
            [
                "confirmed_swing_high",
                "confirmed_swing_low",
                "confirmed_swing_high_price",
                "confirmed_swing_low_price",
                "structure_event",
                "structure_direction",
                "structure_trend",
                "broken_level",
            ]
        ]
    )
    features = features.join(liquidity)
    features = features.join(pd_zones)
    features = features.join(fvg_features)
    features = features.join(block_features)

    if isinstance(features.index, pd.DatetimeIndex):
        features = features.join(session_labels(features.index))

    if "spread" in features.columns:
        features["spread_pips"] = spread_to_pips(features["spread"], pip_size=resolved_pip_size)
    else:
        features["spread_pips"] = np.nan

    features["pip_size"] = resolved_pip_size
    features["symbol"] = symbol.upper()

    return _AnalysisParts(
        candles=data,
        features=features,
        market_structure=structure,
        fair_value_gaps=gaps,
        order_blocks=blocks,
        liquidity_pools=pools,
    )


def build_smc_ta_features(
    df: pd.DataFrame,
    *,
    symbol: str = "EURUSD",
    pip_size: float | None = None,
    config: ConfluenceConfig | None = None,
) -> pd.DataFrame:
    """Return a candle-aligned feature table combining SMC and TA."""

    return _build_parts(df, symbol=symbol, pip_size=pip_size, config=config).features


def _is_near(row: pd.Series, distance_col: str, atr_col: str, multiplier: float) -> bool:
    distance = row.get(distance_col)
    atr_value = row.get(atr_col)
    return bool(pd.notna(distance) and pd.notna(atr_value) and float(distance) <= float(atr_value) * multiplier)


def _recent_equals(series: pd.Series, i: int, bars: int, value: str) -> bool:
    start = max(0, i - bars + 1)
    return bool(series.iloc[start : i + 1].eq(value).any())


def _truthy(row: pd.Series, key: str) -> bool:
    value = row.get(key)
    return bool(value) if pd.notna(value) else False


def _build_reference_levels(row: pd.Series, side: str, cfg: ConfluenceConfig) -> tuple[float, float, float, float]:
    entry = float(row["close"])
    atr_value = row.get("atr_14")
    atr_buffer = float(atr_value) * 0.25 if pd.notna(atr_value) else entry * 0.001

    if side == "long":
        candidates = [
            row.get("active_bull_ob_lower"),
            row.get("active_bull_fvg_lower"),
            row.get("low"),
        ]
        valid = [float(value) for value in candidates if pd.notna(value) and float(value) < entry]
        stop = min(valid) - atr_buffer if valid else entry - atr_buffer * 4
        risk = entry - stop
        target = entry + risk * cfg.reference_reward_to_risk
    elif side == "short":
        candidates = [
            row.get("active_bear_ob_upper"),
            row.get("active_bear_fvg_upper"),
            row.get("high"),
        ]
        valid = [float(value) for value in candidates if pd.notna(value) and float(value) > entry]
        stop = max(valid) + atr_buffer if valid else entry + atr_buffer * 4
        risk = stop - entry
        target = entry - risk * cfg.reference_reward_to_risk
    else:
        return np.nan, np.nan, np.nan, np.nan

    rr = abs(target - entry) / abs(entry - stop) if entry != stop else np.nan
    return entry, stop, target, rr


def generate_confluence_signals(
    df: pd.DataFrame,
    *,
    features: pd.DataFrame | None = None,
    symbol: str = "EURUSD",
    pip_size: float | None = None,
    config: ConfluenceConfig | None = None,
) -> pd.DataFrame:
    """Generate long/short/flat signals from combined SMC + TA features."""

    cfg = config or ConfluenceConfig()
    data = features if features is not None else build_smc_ta_features(
        df,
        symbol=symbol,
        pip_size=pip_size,
        config=cfg,
    )

    out = pd.DataFrame(index=data.index)
    out["side"] = "flat"
    out["long_score"] = 0
    out["short_score"] = 0
    out["confidence"] = 0.0
    out["smc_context"] = False
    out["ta_context"] = False
    out["entry_reference"] = np.nan
    out["stop_reference"] = np.nan
    out["target_reference"] = np.nan
    out["reference_rr"] = np.nan
    out["reasons"] = ""

    max_score = 11
    sweep_series = data.get("liquidity_sweep", pd.Series(index=data.index, dtype="object"))

    for i, idx in enumerate(data.index):
        row = data.iloc[i]
        long_reasons: list[str] = []
        short_reasons: list[str] = []
        long_score = 0
        short_score = 0

        if pd.notna(row.get("ema_20")) and pd.notna(row.get("ema_50")):
            if row["ema_20"] > row["ema_50"]:
                long_score += 1
                long_reasons.append("ema_20_above_ema_50")
            elif row["ema_20"] < row["ema_50"]:
                short_score += 1
                short_reasons.append("ema_20_below_ema_50")

        if pd.notna(row.get("ema_20")):
            if row["close"] > row["ema_20"]:
                long_score += 1
                long_reasons.append("close_above_ema_20")
            elif row["close"] < row["ema_20"]:
                short_score += 1
                short_reasons.append("close_below_ema_20")

        if pd.notna(row.get("macd_histogram")):
            if row["macd_histogram"] > 0:
                long_score += 1
                long_reasons.append("macd_positive")
            elif row["macd_histogram"] < 0:
                short_score += 1
                short_reasons.append("macd_negative")

        if pd.notna(row.get("rsi_14")):
            if 35 <= row["rsi_14"] <= 70:
                long_score += 1
                long_reasons.append("rsi_long_range")
            if 30 <= row["rsi_14"] <= 65:
                short_score += 1
                short_reasons.append("rsi_short_range")

        if pd.notna(row.get("di_adx")) and row["di_adx"] >= cfg.adx_threshold:
            if row.get("di_plus_di", 0) > row.get("di_minus_di", 0):
                long_score += 1
                long_reasons.append("adx_bullish")
            elif row.get("di_minus_di", 0) > row.get("di_plus_di", 0):
                short_score += 1
                short_reasons.append("adx_bearish")

        if row.get("structure_trend") == "bullish":
            long_score += 1
            long_reasons.append("smc_bullish_structure")
        elif row.get("structure_trend") == "bearish":
            short_score += 1
            short_reasons.append("smc_bearish_structure")

        recent_sell_sweep = _recent_equals(sweep_series, i, cfg.recent_sweep_bars, "sell_side")
        recent_buy_sweep = _recent_equals(sweep_series, i, cfg.recent_sweep_bars, "buy_side")
        if recent_sell_sweep:
            long_score += 1
            long_reasons.append("recent_sell_side_sweep")
        if recent_buy_sweep:
            short_score += 1
            short_reasons.append("recent_buy_side_sweep")

        if row.get("pd_zone") == "discount":
            long_score += 1
            long_reasons.append("discount_zone")
        elif row.get("pd_zone") == "premium":
            short_score += 1
            short_reasons.append("premium_zone")

        near_bull_fvg = _is_near(row, "active_bull_fvg_distance", "atr_14", cfg.max_poi_atr_distance)
        near_bear_fvg = _is_near(row, "active_bear_fvg_distance", "atr_14", cfg.max_poi_atr_distance)
        near_bull_ob = _is_near(row, "active_bull_ob_distance", "atr_14", cfg.max_poi_atr_distance)
        near_bear_ob = _is_near(row, "active_bear_ob_distance", "atr_14", cfg.max_poi_atr_distance)
        if near_bull_fvg:
            long_score += 1
            long_reasons.append("near_bullish_fvg")
        if near_bear_fvg:
            short_score += 1
            short_reasons.append("near_bearish_fvg")
        if near_bull_ob:
            long_score += 1
            long_reasons.append("near_bullish_order_block")
        if near_bear_ob:
            short_score += 1
            short_reasons.append("near_bearish_order_block")

        if _truthy(row, "candle_bullish_engulfing") or _truthy(row, "candle_pin_bar"):
            long_score += 1
            long_reasons.append("bullish_candle_response")
        if _truthy(row, "candle_bearish_engulfing") or _truthy(row, "candle_pin_bar"):
            short_score += 1
            short_reasons.append("bearish_candle_response")

        spread_ok = True
        if cfg.max_spread_pips is not None and pd.notna(row.get("spread_pips")):
            spread_ok = float(row["spread_pips"]) <= cfg.max_spread_pips

        long_smc = (
            row.get("structure_trend") == "bullish"
            or recent_sell_sweep
            or row.get("pd_zone") == "discount"
            or near_bull_fvg
            or near_bull_ob
        )
        short_smc = (
            row.get("structure_trend") == "bearish"
            or recent_buy_sweep
            or row.get("pd_zone") == "premium"
            or near_bear_fvg
            or near_bear_ob
        )
        long_ta = (
            (pd.notna(row.get("ema_20")) and row["close"] > row["ema_20"])
            or (pd.notna(row.get("macd_histogram")) and row["macd_histogram"] > 0)
            or (pd.notna(row.get("di_adx")) and row["di_adx"] >= cfg.adx_threshold and row.get("di_plus_di", 0) > row.get("di_minus_di", 0))
        )
        short_ta = (
            (pd.notna(row.get("ema_20")) and row["close"] < row["ema_20"])
            or (pd.notna(row.get("macd_histogram")) and row["macd_histogram"] < 0)
            or (pd.notna(row.get("di_adx")) and row["di_adx"] >= cfg.adx_threshold and row.get("di_minus_di", 0) > row.get("di_plus_di", 0))
        )

        side = "flat"
        reasons = ["spread_filter_failed"] if not spread_ok else []
        if spread_ok:
            long_is_valid = long_score >= cfg.min_signal_score and long_smc and long_ta
            short_is_valid = short_score >= cfg.min_signal_score and short_smc and short_ta
            if long_is_valid and long_score > short_score:
                side = "long"
                reasons = long_reasons
            elif short_is_valid and short_score > long_score:
                side = "short"
                reasons = short_reasons
            elif long_is_valid and short_is_valid:
                reasons = ["conflicting_confluence"]
            else:
                reasons = long_reasons if long_score >= short_score else short_reasons

        entry, stop, target, rr = _build_reference_levels(row, side, cfg)
        out.at[idx, "side"] = side
        out.at[idx, "long_score"] = long_score
        out.at[idx, "short_score"] = short_score
        out.at[idx, "confidence"] = max(long_score, short_score) / max_score
        out.at[idx, "smc_context"] = bool(long_smc or short_smc)
        out.at[idx, "ta_context"] = bool(long_ta or short_ta)
        out.at[idx, "entry_reference"] = entry
        out.at[idx, "stop_reference"] = stop
        out.at[idx, "target_reference"] = target
        out.at[idx, "reference_rr"] = rr
        out.at[idx, "reasons"] = ";".join(reasons)

    return out


def analyze_forex(
    df: pd.DataFrame,
    *,
    symbol: str = "EURUSD",
    pip_size: float | None = None,
    config: ConfluenceConfig | None = None,
) -> AnalysisResult:
    """Run the full SMC + TA analysis pipeline."""

    cfg = config or ConfluenceConfig()
    parts = _build_parts(df, symbol=symbol, pip_size=pip_size, config=cfg)
    signals = generate_confluence_signals(
        parts.candles,
        features=parts.features,
        symbol=symbol,
        pip_size=pip_size,
        config=cfg,
    )
    return AnalysisResult(
        candles=parts.candles,
        features=parts.features,
        signals=signals,
        market_structure=parts.market_structure,
        fair_value_gaps=parts.fair_value_gaps,
        order_blocks=parts.order_blocks,
        liquidity_pools=parts.liquidity_pools,
    )

