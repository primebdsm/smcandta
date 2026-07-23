"""Simple event-driven backtester for SMC TA signals."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smc_ta.broker.models import OrderRequest, Position
from smc_ta.broker.paper import PaperBroker
from smc_ta.engine.confluence import ConfluenceConfig, analyze_forex
from smc_ta.forex.pairs import infer_pip_size, spread_to_pips
from smc_ta.forex.sessions import session_labels
from smc_ta.monitoring.metrics import performance_summary
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
    use_candle_spread: bool = True
    session_filter: tuple[str, ...] | None = None
    max_daily_trades: int | None = None
    trailing_stop_atr_multiple: float | None = None
    partial_close_at_rr: float | None = None
    partial_close_fraction: float = 0.5
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
    pair_report: pd.DataFrame


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


def _session_allows(timestamp, cfg: BacktestConfig) -> bool:
    if not cfg.session_filter:
        return True
    session_frame = session_labels(pd.DatetimeIndex([timestamp]))
    return any(bool(session_frame.iloc[0].get(name, False)) for name in cfg.session_filter)


def _update_trailing_stop(position: Position, row: pd.Series, atr_value: float, multiple: float) -> None:
    distance = atr_value * multiple
    if distance <= 0:
        return
    if position.side == "long":
        candidate = float(row["close"]) - distance
        if position.stop_loss is None or candidate > position.stop_loss:
            position.stop_loss = candidate
    else:
        candidate = float(row["close"]) + distance
        if position.stop_loss is None or candidate < position.stop_loss:
            position.stop_loss = candidate


def _position_rr(position: Position, current_price: float) -> float:
    if position.stop_loss is None or position.entry_price == position.stop_loss:
        return 0.0
    if position.side == "long":
        reward = current_price - position.entry_price
        risk = position.entry_price - position.stop_loss
    else:
        reward = position.entry_price - current_price
        risk = position.stop_loss - position.entry_price
    return reward / risk if risk > 0 else 0.0


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
    daily_trades: dict[pd.Timestamp, int] = {}
    partial_closed: set[str] = set()
    pip_size = infer_pip_size(cfg.symbol)

    for timestamp, row in data.iterrows():
        mid = float(row["close"])
        broker.mark_price(cfg.symbol, mid)
        if cfg.use_candle_spread and "spread" in row and pd.notna(row["spread"]):
            broker.default_spread_pips = float(spread_to_pips(float(row["spread"]), pip_size=pip_size))

        for position in list(broker.get_open_positions(cfg.symbol)):
            if cfg.trailing_stop_atr_multiple is not None:
                atr_value = analysis.features.at[timestamp, "atr_14"]
                if pd.notna(atr_value):
                    _update_trailing_stop(position, row, float(atr_value), cfg.trailing_stop_atr_multiple)
            if (
                cfg.partial_close_at_rr is not None
                and position.position_id not in partial_closed
                and _position_rr(position, mid) >= cfg.partial_close_at_rr
            ):
                broker.close_position_units(
                    position.position_id,
                    units=position.units * cfg.partial_close_fraction,
                    market_price=mid,
                    timestamp=timestamp.to_pydatetime(),
                )
                partial_closed.add(position.position_id)
            should_close, exit_price = _should_stop_or_target(position, row)
            if should_close and exit_price is not None:
                broker.close_position(position.position_id, market_price=float(exit_price), timestamp=timestamp.to_pydatetime())

        signal = analysis.signals.loc[timestamp]
        if cfg.close_on_opposite_signal:
            for position in list(broker.get_open_positions(cfg.symbol)):
                if _opposite_signal(position, str(signal["side"])):
                    broker.close_position(position.position_id, market_price=mid, timestamp=timestamp.to_pydatetime())

        news_allows = news_filter.allow_trading(cfg.symbol, timestamp) if news_filter else True
        day = pd.Timestamp(timestamp).normalize()
        daily_trades.setdefault(day, 0)
        daily_trade_allows = cfg.max_daily_trades is None or daily_trades[day] < cfg.max_daily_trades
        session_allows = _session_allows(timestamp, cfg)
        if (
            news_allows
            and session_allows
            and daily_trade_allows
            and signal["side"] in {"long", "short"}
            and not broker.get_open_positions(cfg.symbol)
        ):
            decision = risk.evaluate_signal(
                signal,
                symbol=cfg.symbol,
                account=broker.get_account(),
                open_positions=broker.get_open_positions(),
                timestamp=timestamp,
            )
            if decision.approved and decision.order is not None:
                broker.place_order(decision.order, market_price=mid, timestamp=timestamp.to_pydatetime())
                daily_trades[day] += 1

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
    report = pair_report(cfg.symbol, equity, trades)
    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        signals=analysis.signals,
        features=analysis.features,
        final_balance=final_account.balance,
        final_equity=final_account.equity,
        pair_report=report,
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


def pair_report(symbol: str, equity_curve: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Return one-row pair performance report."""

    summary = performance_summary(equity_curve, trades)
    summary["symbol"] = symbol.upper()
    return pd.DataFrame([summary]).set_index("symbol")


def run_pair_backtests(
    candles_by_symbol: dict[str, pd.DataFrame],
    *,
    base_config: BacktestConfig | None = None,
    news_filter: NewsFilter | None = None,
) -> tuple[dict[str, BacktestResult], pd.DataFrame]:
    """Run the same backtest configuration across multiple Forex pairs."""

    cfg = base_config or BacktestConfig()
    results: dict[str, BacktestResult] = {}
    reports: list[pd.DataFrame] = []
    for symbol, candles in candles_by_symbol.items():
        pair_cfg = BacktestConfig(
            symbol=symbol.upper(),
            initial_balance=cfg.initial_balance,
            spread_pips=cfg.spread_pips,
            slippage_pips=cfg.slippage_pips,
            commission_per_order=cfg.commission_per_order,
            close_on_opposite_signal=cfg.close_on_opposite_signal,
            use_candle_spread=cfg.use_candle_spread,
            session_filter=cfg.session_filter,
            max_daily_trades=cfg.max_daily_trades,
            trailing_stop_atr_multiple=cfg.trailing_stop_atr_multiple,
            partial_close_at_rr=cfg.partial_close_at_rr,
            partial_close_fraction=cfg.partial_close_fraction,
            confluence=cfg.confluence,
            risk=cfg.risk,
        )
        result = run_backtest(candles, config=pair_cfg, news_filter=news_filter)
        results[symbol.upper()] = result
        reports.append(result.pair_report)
    return results, pd.concat(reports).sort_index() if reports else pd.DataFrame()
