"""Named Smart Money Concept setup classification."""

from __future__ import annotations

import pandas as pd


def classify_smc_setups(features: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """Classify common SMC setup names from feature/signal context."""

    out = pd.DataFrame(index=features.index)
    out["setup_name"] = "none"
    out["setup_direction"] = "neutral"
    out["setup_score"] = 0
    out["setup_reasons"] = ""
    signal_side = signals["side"] if signals is not None and "side" in signals else pd.Series("flat", index=features.index)

    sweep = features.get("liquidity_sweep", pd.Series(index=features.index, dtype="object"))
    for idx, row in features.iterrows():
        labels: list[str] = []
        reasons: list[str] = []
        direction = "neutral"
        score = 0

        bullish_structure = row.get("structure_trend") == "bullish" or row.get("structure_direction") == "bullish"
        bearish_structure = row.get("structure_trend") == "bearish" or row.get("structure_direction") == "bearish"
        near_bull_fvg = pd.notna(row.get("active_bull_fvg_distance")) and float(row["active_bull_fvg_distance"]) == 0.0
        near_bear_fvg = pd.notna(row.get("active_bear_fvg_distance")) and float(row["active_bear_fvg_distance"]) == 0.0
        near_bull_ob = pd.notna(row.get("active_bull_ob_distance")) and float(row["active_bull_ob_distance"]) == 0.0
        near_bear_ob = pd.notna(row.get("active_bear_ob_distance")) and float(row["active_bear_ob_distance"]) == 0.0

        if sweep.loc[idx] == "sell_side" and bullish_structure:
            labels.append("liquidity_sweep_choch")
            reasons.append("sell_side_sweep_plus_bullish_structure")
            direction = "bullish"
            score += 3
        if sweep.loc[idx] == "buy_side" and bearish_structure:
            labels.append("liquidity_sweep_choch")
            reasons.append("buy_side_sweep_plus_bearish_structure")
            direction = "bearish"
            score += 3

        if near_bull_fvg and bullish_structure:
            labels.append("fvg_continuation")
            reasons.append("bullish_structure_inside_bullish_fvg")
            direction = "bullish"
            score += 2
        if near_bear_fvg and bearish_structure:
            labels.append("fvg_continuation")
            reasons.append("bearish_structure_inside_bearish_fvg")
            direction = "bearish"
            score += 2

        if near_bull_ob:
            labels.append("order_block_mitigation")
            reasons.append("price_inside_bullish_order_block")
            direction = "bullish" if direction == "neutral" else direction
            score += 2
        if near_bear_ob:
            labels.append("order_block_mitigation")
            reasons.append("price_inside_bearish_order_block")
            direction = "bearish" if direction == "neutral" else direction
            score += 2

        if row.get("pd_zone") == "premium" and sweep.loc[idx] == "buy_side":
            labels.append("premium_reversal")
            reasons.append("buy_side_liquidity_swept_in_premium")
            direction = "bearish"
            score += 2
        if row.get("pd_zone") == "discount" and sweep.loc[idx] == "sell_side":
            labels.append("discount_continuation")
            reasons.append("sell_side_liquidity_swept_in_discount")
            direction = "bullish"
            score += 2

        in_london = bool(row.get("london_kill_zone", False))
        if in_london and sweep.loc[idx] in {"buy_side", "sell_side"}:
            labels.append("london_sweep_reversal")
            reasons.append("liquidity_sweep_in_london_kill_zone")
            score += 1

        side = signal_side.loc[idx]
        if side == "long" and direction == "bullish":
            score += 1
            reasons.append("signal_agrees_long")
        elif side == "short" and direction == "bearish":
            score += 1
            reasons.append("signal_agrees_short")

        if labels:
            out.at[idx, "setup_name"] = "+".join(dict.fromkeys(labels))
            out.at[idx, "setup_direction"] = direction
            out.at[idx, "setup_score"] = score
            out.at[idx, "setup_reasons"] = ";".join(reasons)

    return out

