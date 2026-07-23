"""Shared data types for SMC TA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["bullish", "bearish", "neutral"]
SignalSide = Literal["long", "short", "flat"]


@dataclass(frozen=True)
class ForexPairSpec:
    """Metadata needed for pip and risk calculations."""

    symbol: str
    pip_size: float
    base_currency: str
    quote_currency: str
    lot_size: int = 100_000


@dataclass(frozen=True)
class PriceZone:
    """A directional price area such as an FVG or order block."""

    direction: Direction
    lower: float
    upper: float
    midpoint: float


@dataclass(frozen=True)
class RiskPlan:
    """Basic risk plan produced by helper calculations."""

    entry: float
    stop: float
    target: float | None
    risk_pips: float
    reward_pips: float | None
    reward_to_risk: float | None

