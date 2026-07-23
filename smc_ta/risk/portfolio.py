"""Portfolio and correlation risk controls for Forex exposure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from smc_ta.broker.models import OrderRequest, Position
from smc_ta.validation import normalize_ohlcv

DecisionStatus = Literal["approved", "blocked"]


@dataclass(frozen=True)
class PortfolioRiskConfig:
    """Portfolio-level limits checked before order placement."""

    max_total_open_positions: int | None = None
    max_symbol_positions: int | None = 1
    max_currency_gross_exposure: float | None = None
    max_currency_net_exposure: float | None = None
    max_same_currency_direction_positions: int | None = None
    max_correlated_positions: int | None = None
    correlation_threshold: float = 0.8
    use_absolute_correlation: bool = True
    block_opposite_same_symbol: bool = True


@dataclass(frozen=True)
class PortfolioRiskDecision:
    """Portfolio-level risk decision."""

    status: DecisionStatus
    reasons: tuple[str, ...]
    currency_exposure: dict[str, float] = field(default_factory=dict)
    correlated_symbols: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class PortfolioRiskManager:
    """Evaluate proposed orders against portfolio concentration limits."""

    def __init__(
        self,
        config: PortfolioRiskConfig | None = None,
        *,
        correlation_matrix: pd.DataFrame | None = None,
    ) -> None:
        self.config = config or PortfolioRiskConfig()
        self.correlation_matrix = _normalize_correlation_matrix(correlation_matrix) if correlation_matrix is not None else None

    def evaluate_order(
        self,
        order: OrderRequest,
        *,
        open_positions: list[Position],
        market_price: float,
        correlation_matrix: pd.DataFrame | None = None,
    ) -> PortfolioRiskDecision:
        """Return whether the proposed order keeps portfolio risk inside limits."""

        cfg = self.config
        reasons: list[str] = []
        symbol = normalize_symbol(order.symbol)
        proposed_exposure = order_currency_exposure(order, market_price)
        open_exposure = aggregate_currency_exposure(open_positions)
        combined_exposure = _combine_exposures(open_exposure, proposed_exposure)
        projected_positions = _projected_positions(open_positions, order, market_price)

        if cfg.max_total_open_positions is not None and len(projected_positions) > cfg.max_total_open_positions:
            reasons.append("max_total_open_positions_reached")

        if cfg.max_symbol_positions is not None:
            symbol_count = sum(1 for position in projected_positions if normalize_symbol(position.symbol) == symbol)
            if symbol_count > cfg.max_symbol_positions:
                reasons.append("max_symbol_positions_reached")

        if cfg.block_opposite_same_symbol:
            proposed_side = "long" if order.side == "buy" else "short"
            if any(normalize_symbol(position.symbol) == symbol and position.side != proposed_side for position in open_positions):
                reasons.append("opposite_same_symbol_exposure")

        if cfg.max_currency_gross_exposure is not None:
            gross = aggregate_currency_gross_exposure(projected_positions)
            if any(value > cfg.max_currency_gross_exposure for value in gross.values()):
                reasons.append("max_currency_gross_exposure_reached")

        if cfg.max_currency_net_exposure is not None:
            if any(abs(value) > cfg.max_currency_net_exposure for value in combined_exposure.values()):
                reasons.append("max_currency_net_exposure_reached")

        if cfg.max_same_currency_direction_positions is not None:
            direction_counts = currency_direction_counts(projected_positions)
            if any(count > cfg.max_same_currency_direction_positions for counts in direction_counts.values() for count in counts.values()):
                reasons.append("max_same_currency_direction_positions_reached")

        corr_matrix = _normalize_correlation_matrix(correlation_matrix) if correlation_matrix is not None else self.correlation_matrix
        correlated = correlated_open_symbols(
            symbol,
            open_positions,
            corr_matrix,
            threshold=cfg.correlation_threshold,
            use_absolute=cfg.use_absolute_correlation,
        )
        if cfg.max_correlated_positions is not None and len(correlated) + 1 > cfg.max_correlated_positions:
            reasons.append("max_correlated_positions_reached")

        return PortfolioRiskDecision(
            status="blocked" if reasons else "approved",
            reasons=tuple(dict.fromkeys(reasons or ["approved"])),
            currency_exposure=combined_exposure,
            correlated_symbols=tuple(correlated),
        )


def normalize_symbol(symbol: str) -> str:
    """Normalize a Forex symbol into six-letter pair format."""

    clean = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(clean) < 6:
        raise ValueError(f"cannot infer Forex pair from symbol: {symbol}")
    return clean[:6]


def split_symbol(symbol: str) -> tuple[str, str]:
    """Return base and quote currencies from a Forex symbol."""

    pair = normalize_symbol(symbol)
    return pair[:3], pair[3:6]


def position_currency_exposure(position: Position, *, price: float | None = None) -> dict[str, float]:
    """Return signed base/quote currency exposure for one open position."""

    base, quote = split_symbol(position.symbol)
    mark = float(price if price is not None else position.entry_price)
    quote_value = position.units * mark
    if position.side == "long":
        return {base: position.units, quote: -quote_value}
    return {base: -position.units, quote: quote_value}


def order_currency_exposure(order: OrderRequest, price: float) -> dict[str, float]:
    """Return signed base/quote currency exposure for a proposed order."""

    base, quote = split_symbol(order.symbol)
    quote_value = order.units * price
    if order.side == "buy":
        return {base: order.units, quote: -quote_value}
    return {base: -order.units, quote: quote_value}


def aggregate_currency_exposure(positions: list[Position]) -> dict[str, float]:
    """Aggregate signed currency exposure across open positions."""

    exposure: dict[str, float] = {}
    for position in positions:
        exposure = _combine_exposures(exposure, position_currency_exposure(position))
    return exposure


def aggregate_currency_gross_exposure(positions: list[Position]) -> dict[str, float]:
    """Aggregate absolute currency exposure before netting."""

    exposure: dict[str, float] = {}
    for position in positions:
        for currency, value in position_currency_exposure(position).items():
            exposure[currency] = exposure.get(currency, 0.0) + abs(value)
    return exposure


def currency_direction_counts(positions: list[Position]) -> dict[str, dict[str, int]]:
    """Count long/short currency legs across positions."""

    counts: dict[str, dict[str, int]] = {}
    for position in positions:
        for currency, value in position_currency_exposure(position).items():
            side = "long" if value > 0 else "short"
            counts.setdefault(currency, {"long": 0, "short": 0})
            counts[currency][side] += 1
    return counts


def compute_return_correlations(
    candles_by_symbol: dict[str, pd.DataFrame],
    *,
    lookback: int | None = None,
    method: str = "pearson",
) -> pd.DataFrame:
    """Compute close-to-close return correlations across symbols."""

    returns = {}
    for symbol, candles in candles_by_symbol.items():
        data = normalize_ohlcv(candles)
        close = data["close"].tail(lookback) if lookback is not None else data["close"]
        returns[normalize_symbol(symbol)] = close.pct_change()
    frame = pd.DataFrame(returns).dropna(how="all")
    return frame.corr(method=method)


def correlated_open_symbols(
    symbol: str,
    open_positions: list[Position],
    correlation_matrix: pd.DataFrame | None,
    *,
    threshold: float,
    use_absolute: bool = True,
) -> list[str]:
    """Return open-position symbols correlated with a proposed symbol."""

    if correlation_matrix is None or correlation_matrix.empty:
        return []
    matrix = _normalize_correlation_matrix(correlation_matrix)
    proposed_symbol = normalize_symbol(symbol)
    if proposed_symbol not in matrix.index:
        return []
    correlated: list[str] = []
    for position in open_positions:
        open_symbol = normalize_symbol(position.symbol)
        if open_symbol == proposed_symbol or open_symbol not in matrix.columns:
            continue
        value = float(matrix.at[proposed_symbol, open_symbol])
        comparable = abs(value) if use_absolute else value
        if comparable >= threshold and open_symbol not in correlated:
            correlated.append(open_symbol)
    return correlated


def _combine_exposures(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    combined = dict(left)
    for currency, value in right.items():
        combined[currency] = combined.get(currency, 0.0) + float(value)
    return combined


def _projected_positions(open_positions: list[Position], order: OrderRequest, market_price: float) -> list[Position]:
    side = "long" if order.side == "buy" else "short"
    projected = list(open_positions)
    projected.append(
        Position(
            position_id=f"proposed_{order.client_order_id}",
            symbol=normalize_symbol(order.symbol),
            side=side,
            units=float(order.units),
            entry_price=float(market_price),
            opened_at=pd.Timestamp.now(tz="UTC").to_pydatetime(),
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            metadata={"proposed": True},
        )
    )
    return projected


def _normalize_correlation_matrix(matrix: pd.DataFrame | None) -> pd.DataFrame:
    if matrix is None:
        return pd.DataFrame()
    out = matrix.copy()
    out.index = [normalize_symbol(symbol) for symbol in out.index]
    out.columns = [normalize_symbol(symbol) for symbol in out.columns]
    return out
