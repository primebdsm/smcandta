"""Forex pair metadata and pip helpers."""

from __future__ import annotations

import re

import pandas as pd

from smc_ta.types import ForexPairSpec

JPY_QUOTES = {"JPY"}
METAL_SYMBOLS = {"XAUUSD": 0.1, "XAGUSD": 0.01}


def normalize_symbol(symbol: str) -> str:
    """Normalize broker symbols such as EUR/USD or EURUSD.pro."""

    clean = re.sub(r"[^A-Za-z]", "", symbol).upper()
    if len(clean) < 6:
        raise ValueError(f"cannot infer pair from symbol: {symbol}")
    return clean[:6]


def infer_pip_size(symbol: str) -> float:
    """Infer standard pip size for a Forex symbol."""

    pair = normalize_symbol(symbol)
    if pair in METAL_SYMBOLS:
        return METAL_SYMBOLS[pair]
    quote = pair[3:6]
    return 0.01 if quote in JPY_QUOTES else 0.0001


def forex_pair_spec(symbol: str, *, lot_size: int = 100_000) -> ForexPairSpec:
    """Build a pair spec from a Forex symbol."""

    pair = normalize_symbol(symbol)
    return ForexPairSpec(
        symbol=pair,
        pip_size=infer_pip_size(pair),
        base_currency=pair[:3],
        quote_currency=pair[3:6],
        lot_size=lot_size,
    )


def spread_to_pips(spread: pd.Series | float, symbol: str | None = None, pip_size: float | None = None):
    """Convert a raw price spread into pips."""

    resolved_pip_size = pip_size if pip_size is not None else infer_pip_size(symbol or "EURUSD")
    return spread / resolved_pip_size

