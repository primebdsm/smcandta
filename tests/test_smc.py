from __future__ import annotations

import pandas as pd

from smc_ta.smc.gaps import fair_value_gaps
from smc_ta.smc.liquidity import liquidity_sweeps, premium_discount_zones
from smc_ta.smc.order_blocks import detect_order_blocks
from smc_ta.smc.structure import market_structure, swing_points


def structure_candles() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=9, freq="15min", tz="UTC")
    data = [
        (1.0, 1.1, 0.9, 1.0),
        (1.1, 2.1, 1.0, 2.0),
        (2.0, 3.1, 1.9, 3.0),
        (3.0, 3.05, 1.9, 2.0),
        (1.2, 1.3, 0.9, 1.0),
        (1.0, 2.1, 0.95, 2.0),
        (2.0, 3.6, 1.95, 3.4),
        (3.4, 3.5, 2.7, 2.9),
        (2.9, 3.0, 2.6, 2.8),
    ]
    return pd.DataFrame(data, columns=["open", "high", "low", "close"], index=index)


def test_swing_confirmation_and_structure_break() -> None:
    candles = structure_candles()
    swings = swing_points(candles, left=1, right=1)
    structure = market_structure(candles, left=1, right=1, break_by="close")

    assert swings["swing_high"].any()
    assert swings["confirmed_swing_high"].any()
    assert (structure["structure_event"] == "BOS").any()
    assert (structure["structure_direction"] == "bullish").any()


def test_fair_value_gap_detection() -> None:
    index = pd.date_range("2024-01-01", periods=5, freq="15min", tz="UTC")
    candles = pd.DataFrame(
        [
            (1.0995, 1.1000, 1.0990, 1.0997),
            (1.0997, 1.1005, 1.0995, 1.1002),
            (1.1012, 1.1020, 1.1010, 1.1018),
            (1.1018, 1.1022, 1.1008, 1.1010),
            (1.1010, 1.1015, 1.0998, 1.1000),
        ],
        columns=["open", "high", "low", "close"],
        index=index,
    )
    gaps = fair_value_gaps(candles, min_atr_multiple=None)

    assert not gaps.empty
    first = gaps.iloc[0]
    assert first["direction"] == "bullish"
    assert first["lower"] == 1.1000
    assert first["upper"] == 1.1010


def test_order_block_detection_from_structure_event() -> None:
    candles = structure_candles()
    structure = market_structure(candles, left=1, right=1)
    order_blocks = detect_order_blocks(
        candles,
        structure,
        atr_period=2,
        min_displacement_atr=0.1,
        search_lookback=5,
    )

    assert not order_blocks.empty
    assert "bullish" in set(order_blocks["direction"])


def test_liquidity_and_premium_discount_outputs_align() -> None:
    candles = structure_candles()
    sweeps = liquidity_sweeps(candles, left=1, right=1)
    pd_zones = premium_discount_zones(candles, lookback=4)

    assert len(sweeps) == len(candles)
    assert len(pd_zones) == len(candles)
    assert {"dealing_range_low", "dealing_range_high", "pd_zone"}.issubset(pd_zones.columns)

