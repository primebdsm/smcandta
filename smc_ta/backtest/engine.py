"""Simple event-driven backtester for SMC TA signals."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smc_ta.broker.models import OrderRequest, Position
from smc_ta.broker.paper import PaperBroker
from smc_ta.engine.confluence import ConfluenceConfig, analyze_forex
from smc_ta.news.calendar import NewsFilter
from smc_ta.risk.manager import RiskConfig, RiskManager
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest settings."""

    symbol: str = "EURUSD"
    initial_balance: float = 10_000.0
    spread_pips: float = 1.2
    slippage_pips: float = 0.1
    commission_per_order: float = 0.0
    close_on_opposite_signal: bool = True
    confluence: ConfluenceConfig = ConfluenceConfig()
    risk: RiskConfig = RiskConfig()


@dataclass(frozen=True)
class BacktestResult:
    """Backtest outputs."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame
    features: pd.DataFrame
    final_balance: float
    final_equity: float


def _should_stop_or_target(position: Position, row: pd.Series) -> tuple[bool, float | None]:
    if position.side == "long":
        if position.stop_loss is not None and row["low"] <= position.stop_loss:
            return True, position.stop_loss
        if position.take_profit is not None and row["high"] >= position.take_profit:
            return True, position.take_profit
    else:
        if position.stop_loss is not None and row["high"] >= position.stop_loss:
            return True, position.stop_loss
        if position.take_profit is not None and row["low"] <= position.take_profit:
            return True, position.take_profit
    return False, None


def _opposite_signal(position: Position, side: str) -> bool:
    return (position.side == "long" and side == "short") or (position.side == "short" and side == "long")


def _trade_table(positions: list[Position]) -> pd.DataFrame:
    records = []
    for position in positions:
        records.append(
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "units": position.units,
                "entry_price": position.entry_price,
                "exit_price": position.exit_price,
                "opened_at": position.opened_at,
                "closed_at": position.closed_at,
                "realized_pnl": position.realized_pnl,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
            }
        )
    return pd.DataFrame.from_records(records)


def run_backtest(
    candles: pd.DataFrame,
    *,
    config: BacktestConfig | None = None,
    news_filter: NewsFilter | None = None,
) -> BacktestResult:
    """Run an event-driven backtest with spread/slippage and risk controls."""

    cfg = config or BacktestConfig()
    data = normalize_ohlcv(candles)
    analysis = analyze_forex(data, symbol=cfg.symbol, config=cfg.confluence)
    broker = PaperBroker(
        initial_balance=cfg.initial_balance,
        default_spread_pips=cfg.spread_pips,
        slippage_pips=cfg.slippage_pips,
        commission_per_order=cfg.commission_per_order,
    )
    risk = RiskManager(cfg.risk)
    equity_records: list[dict[str, object]] = []

    for timestamp, row in data.iterrows():
        mid = float(row["close"])
        broker.mark_price(cfg.symbol, mid)

        for position in list(broker.get_open_positions(cfg.symbol)):
            should_close, exit_price = _should_stop_or_target(position, row)
            if should_close and exit_price is not None:
                broker.close_position(position.position_id, market_price=float(exit_price), timestamp=timestamp.to_pydatetime())

        signal = analysis.signals.loc[timestamp]
        if cfg.close_on_opposite_signal:
            for position in list(broker.get_open_positions(cfg.symbol)):
                if _opposite_signal(position, str(signal["side"])):
                    broker.close_position(position.position_id, market_price=mid, timestamp=timestamp.to_pydatetime())

        news_allows = news_filter.allow_trading(cfg.symbol, timestamp) if news_filter else True
        if news_allows and signal["side"] in {"long", "short"} and not broker.get_open_positions(cfg.symbol):
            decision = risk.evaluate_signal(
                signal,
                symbol=cfg.symbol,
                account=broker.get_account(),
                open_positions=broker.get_open_positions(),
                timestamp=timestamp,
            )
            if decision.approved and decision.order is not None:
                broker.place_order(decision.order, market_price=mid, timestamp=timestamp.to_pydatetime())

        account = broker.get_account()
        equity_records.append(
            {
                "time": timestamp,
                "balance": account.balance,
                "equity": account.equity,
                "open_positions": len(broker.get_open_positions()),
            }
        )

    final_price = float(data["close"].iloc[-1])
    final_time = data.index[-1]
    for position in list(broker.get_open_positions(cfg.symbol)):
        broker.close_position(position.position_id, market_price=final_price, timestamp=final_time.to_pydatetime())

    final_account = broker.get_account()
    equity = pd.DataFrame.from_records(equity_records).set_index("time")
    trades = _trade_table(list(broker.positions.values()))
    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        signals=analysis.signals,
        features=analysis.features,
        final_balance=final_account.balance,
        final_equity=final_account.equity,
    )


def order_from_signal(signal: pd.Series, symbol: str, units: float) -> OrderRequest:
    """Build an order request from a confluence signal."""

    side = signal.get("side")
    if side not in {"long", "short"}:
        raise ValueError("signal side must be long or short")
    return OrderRequest(
        symbol=symbol,
        side="buy" if side == "long" else "sell",
        units=units,
        stop_loss=float(signal["stop_reference"]),
        take_profit=float(signal["target_reference"]),
    )

