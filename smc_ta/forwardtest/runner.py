"""Demo-forward replay and reporting package."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import AccountState, OrderFill, Position
from smc_ta.broker.paper import PaperBroker
from smc_ta.engine.confluence import ConfluenceConfig
from smc_ta.forex.pairs import infer_pip_size, spread_to_pips
from smc_ta.forex.sessions import session_labels
from smc_ta.journal.store import JournalEntry, TradeJournal
from smc_ta.lifecycle import TradeLifecycleStateMachine, TradeLifecycleStore
from smc_ta.live import CycleResult, DemoTradingBot
from smc_ta.monitoring.metrics import health_check, performance_summary
from smc_ta.news.calendar import NewsFilter
from smc_ta.reconciliation import BrokerReconciler, MemoryPositionLedger
from smc_ta.risk import PortfolioRiskManager
from smc_ta.risk.manager import RiskConfig, RiskManager
from smc_ta.safety import EmergencyStopController
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class DemoForwardConfig:
    """Settings for a closed-candle demo-forward replay."""

    symbol: str = "EURUSD"
    initial_balance: float = 10_000.0
    warmup_candles: int = 120
    max_cycles: int | None = None
    default_spread_pips: float = 1.2
    slippage_pips: float = 0.1
    commission_per_order: float = 0.0
    use_candle_spread: bool = True
    manage_paper_positions: bool = True
    close_open_positions_at_end: bool = True
    confluence: ConfluenceConfig = ConfluenceConfig()
    risk: RiskConfig = RiskConfig()


@dataclass(frozen=True)
class DemoForwardReportBundle:
    """Paths written by `write_demo_forward_report_bundle`."""

    output_dir: Path
    summary_json: Path
    html_report: Path
    cycles_csv: Path
    equity_csv: Path
    trades_csv: Path
    fills_csv: Path
    setup_report_csv: Path
    session_report_csv: Path
    daily_report_csv: Path
    blocked_reasons_csv: Path
    position_events_csv: Path


@dataclass(frozen=True)
class DemoForwardResult:
    """Complete demo-forward replay result."""

    symbol: str
    cycles: tuple[CycleResult, ...]
    cycle_report: pd.DataFrame
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    fills: pd.DataFrame
    setup_report: pd.DataFrame
    session_report: pd.DataFrame
    daily_report: pd.DataFrame
    blocked_reasons: pd.DataFrame
    position_events: pd.DataFrame
    summary: dict[str, Any]
    final_account: AccountState
    config: DemoForwardConfig
    artifacts: DemoForwardReportBundle | None = None

    @property
    def ok(self) -> bool:
        return bool(self.summary.get("health_ok", False))

    def with_artifacts(self, artifacts: DemoForwardReportBundle) -> "DemoForwardResult":
        return DemoForwardResult(
            symbol=self.symbol,
            cycles=self.cycles,
            cycle_report=self.cycle_report,
            equity_curve=self.equity_curve,
            trades=self.trades,
            fills=self.fills,
            setup_report=self.setup_report,
            session_report=self.session_report,
            daily_report=self.daily_report,
            blocked_reasons=self.blocked_reasons,
            position_events=self.position_events,
            summary=self.summary,
            final_account=self.final_account,
            config=self.config,
            artifacts=artifacts,
        )


def run_demo_forward_test(
    candles: pd.DataFrame,
    *,
    config: DemoForwardConfig | None = None,
    broker: BrokerAdapter | None = None,
    bot: DemoTradingBot | None = None,
    risk_manager: RiskManager | None = None,
    portfolio_risk_manager: PortfolioRiskManager | None = None,
    news_filter: NewsFilter | None = None,
    journal: TradeJournal | None = None,
    lifecycle_store: TradeLifecycleStore | None = None,
    reconciler: BrokerReconciler | None = None,
    emergency_stop: EmergencyStopController | None = None,
) -> DemoForwardResult:
    """Replay closed candles through `DemoTradingBot` and build reports.

    This is meant for paper/demo-forward evidence. With the default `PaperBroker`
    it can simulate broker-side SL/TP closes from candle high/low data. If a real
    demo broker is passed, position management should be handled by that broker.
    """

    cfg = config or DemoForwardConfig()
    symbol = cfg.symbol.upper()
    data = normalize_ohlcv(candles)
    if len(data) <= cfg.warmup_candles:
        raise ValueError("not enough candles for warmup_candles plus one forward cycle")

    active_broker = broker or PaperBroker(
        initial_balance=cfg.initial_balance,
        default_spread_pips=cfg.default_spread_pips,
        slippage_pips=cfg.slippage_pips,
        commission_per_order=cfg.commission_per_order,
    )
    active_reconciler = reconciler
    if active_reconciler is None and isinstance(active_broker, PaperBroker):
        active_reconciler = BrokerReconciler(MemoryPositionLedger())
    active_lifecycle_store = lifecycle_store
    active_bot = bot or DemoTradingBot(
        symbol=symbol,
        broker=active_broker,
        risk_manager=risk_manager or RiskManager(cfg.risk),
        portfolio_risk_manager=portfolio_risk_manager,
        confluence_config=cfg.confluence,
        news_filter=news_filter,
        journal=journal,
        reconciler=active_reconciler,
        emergency_stop=emergency_stop,
        trade_lifecycle=TradeLifecycleStateMachine() if active_lifecycle_store is not None else None,
        trade_lifecycle_store=active_lifecycle_store,
    )

    cycle_results: list[CycleResult] = []
    cycle_records: list[dict[str, Any]] = []
    equity_records: list[dict[str, Any]] = []
    position_events: list[dict[str, Any]] = []
    fill_setup: dict[str, dict[str, Any]] = {}
    pip_size = infer_pip_size(symbol)
    cycle_index = 0
    for timestamp, row in _iter_forward_rows(data, cfg):
        cycle_index += 1
        market_price = float(row["close"])
        _mark_price(active_broker, symbol, market_price)
        _apply_candle_spread(active_broker, row, cfg, pip_size)
        if cfg.manage_paper_positions and isinstance(active_broker, PaperBroker):
            position_events.extend(
                _manage_paper_positions(
                    active_broker,
                    row,
                    symbol=symbol,
                    timestamp=pd.Timestamp(timestamp),
                    reconciler=active_reconciler,
                    bot=active_bot,
                    journal=journal,
                )
            )

        cycle = active_bot.run_cycle(data.loc[:timestamp])
        cycle_results.append(cycle)
        if cycle.fill is not None:
            fill_setup[cycle.fill.order_id] = {
                "setup_name": cycle.setup_name,
                "cycle_timestamp": pd.Timestamp(cycle.timestamp).isoformat(),
                "cycle_action": cycle.action,
                "signal_side": cycle.side,
                "reasons": cycle.reasons,
            }
        account = active_broker.get_account()
        open_positions = active_broker.get_open_positions(symbol)
        cycle_record = _cycle_record(
            cycle,
            account,
            open_positions,
            symbol=symbol,
            market_price=market_price,
            cycle_index=cycle_index,
        )
        cycle_records.append(cycle_record)
        equity_records.append(
            {
                "time": pd.Timestamp(timestamp),
                "balance": account.balance,
                "equity": account.equity,
                "open_positions": len(open_positions),
            }
        )

    final_timestamp = pd.Timestamp(data.index[-1])
    final_price = float(data["close"].iloc[-1])
    if cfg.close_open_positions_at_end and isinstance(active_broker, PaperBroker):
        _mark_price(active_broker, symbol, final_price)
        position_events.extend(
            _close_remaining_paper_positions(
                active_broker,
                symbol=symbol,
                market_price=final_price,
                timestamp=final_timestamp,
                reconciler=active_reconciler,
                bot=active_bot,
                journal=journal,
                reason="final_forward_close",
            )
        )
        final_account = active_broker.get_account()
        if equity_records:
            equity_records[-1]["balance"] = final_account.balance
            equity_records[-1]["equity"] = final_account.equity
            equity_records[-1]["open_positions"] = len(active_broker.get_open_positions(symbol))
    else:
        final_account = active_broker.get_account()

    cycles = pd.DataFrame.from_records(cycle_records)
    equity = pd.DataFrame.from_records(equity_records).set_index("time") if equity_records else pd.DataFrame()
    fills = _fills_frame(active_broker)
    trades = _trades_frame(active_broker, fill_setup)
    events = pd.DataFrame.from_records(position_events)
    setup_report = _setup_report(cycles, trades)
    session_report = _session_report(cycles, trades)
    daily_report = _daily_report(equity, cycles, trades)
    blocked_reasons = _blocked_reasons_report(cycles)
    summary = _summary(
        symbol=symbol,
        config=cfg,
        equity=equity,
        trades=trades,
        cycles=cycles,
        position_events=events,
        final_account=final_account,
    )
    return DemoForwardResult(
        symbol=symbol,
        cycles=tuple(cycle_results),
        cycle_report=cycles,
        equity_curve=equity,
        trades=trades,
        fills=fills,
        setup_report=setup_report,
        session_report=session_report,
        daily_report=daily_report,
        blocked_reasons=blocked_reasons,
        position_events=events,
        summary=summary,
        final_account=final_account,
        config=cfg,
    )


def write_demo_forward_report_bundle(result: DemoForwardResult, output_dir: str | Path) -> DemoForwardResult:
    """Write JSON, CSV, and HTML demo-forward report artifacts."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    summary_json = root / "summary.json"
    html_report = root / "report.html"
    cycles_csv = root / "cycles.csv"
    equity_csv = root / "equity_curve.csv"
    trades_csv = root / "trades.csv"
    fills_csv = root / "fills.csv"
    setup_report_csv = root / "setup_report.csv"
    session_report_csv = root / "session_report.csv"
    daily_report_csv = root / "daily_report.csv"
    blocked_reasons_csv = root / "blocked_reasons.csv"
    position_events_csv = root / "position_events.csv"

    summary_json.write_text(json.dumps(_jsonable(result.summary), indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(result.cycle_report, cycles_csv)
    _write_csv(result.equity_curve.reset_index(), equity_csv)
    _write_csv(result.trades, trades_csv)
    _write_csv(result.fills, fills_csv)
    _write_csv(result.setup_report, setup_report_csv)
    _write_csv(result.session_report, session_report_csv)
    _write_csv(result.daily_report, daily_report_csv)
    _write_csv(result.blocked_reasons, blocked_reasons_csv)
    _write_csv(result.position_events, position_events_csv)
    html_report.write_text(render_demo_forward_html_report(result), encoding="utf-8")
    bundle = DemoForwardReportBundle(
        output_dir=root,
        summary_json=summary_json,
        html_report=html_report,
        cycles_csv=cycles_csv,
        equity_csv=equity_csv,
        trades_csv=trades_csv,
        fills_csv=fills_csv,
        setup_report_csv=setup_report_csv,
        session_report_csv=session_report_csv,
        daily_report_csv=daily_report_csv,
        blocked_reasons_csv=blocked_reasons_csv,
        position_events_csv=position_events_csv,
    )
    return result.with_artifacts(bundle)


def render_demo_forward_html_report(result: DemoForwardResult) -> str:
    """Render a dependency-free HTML report for a demo-forward run."""

    summary = result.summary
    status = "OK" if result.ok else "CHECK"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Demo Forward Report - {result.symbol}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f8fa; color: #18202a; }}
    header {{ background: #17202a; color: white; padding: 24px 32px; }}
    main {{ padding: 24px 32px; display: grid; gap: 18px; }}
    section {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 18px; overflow-x: auto; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 4px; background: {"#d9f5e7" if result.ok else "#ffe8d6"}; color: #17202a; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
    .metric {{ border: 1px solid #e1e6ef; border-radius: 6px; padding: 10px; }}
    .metric span {{ display: block; color: #657084; font-size: 12px; text-transform: uppercase; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e7ebf2; padding: 7px 8px; text-align: left; white-space: nowrap; }}
    th {{ background: #f1f4f8; }}
  </style>
</head>
<body>
  <header>
    <h1>{result.symbol} Demo Forward Report <span class="status">{status}</span></h1>
    <div>{summary.get("start_time", "")} to {summary.get("end_time", "")}</div>
  </header>
  <main>
    <section>
      <h2>Summary</h2>
      <div class="grid">{_summary_cards(summary)}</div>
    </section>
    <section>
      <h2>Equity Curve</h2>
      {_equity_svg(result.equity_curve)}
    </section>
    <section><h2>Setup Report</h2>{_frame_to_html(result.setup_report)}</section>
    <section><h2>Session Report</h2>{_frame_to_html(result.session_report)}</section>
    <section><h2>Daily Report</h2>{_frame_to_html(result.daily_report)}</section>
    <section><h2>Blocked Reasons</h2>{_frame_to_html(result.blocked_reasons)}</section>
    <section><h2>Trades</h2>{_frame_to_html(result.trades.tail(25))}</section>
    <section><h2>Recent Cycles</h2>{_frame_to_html(result.cycle_report.tail(50))}</section>
  </main>
</body>
</html>
"""


def _iter_forward_rows(data: pd.DataFrame, cfg: DemoForwardConfig):
    rows = data.iloc[cfg.warmup_candles :]
    if cfg.max_cycles is not None:
        rows = rows.iloc[: cfg.max_cycles]
    return rows.iterrows()


def _mark_price(broker: BrokerAdapter, symbol: str, market_price: float) -> None:
    if hasattr(broker, "mark_price"):
        broker.mark_price(symbol, market_price)


def _apply_candle_spread(broker: BrokerAdapter, row: pd.Series, cfg: DemoForwardConfig, pip_size: float) -> None:
    if not cfg.use_candle_spread or not isinstance(broker, PaperBroker):
        return
    spread = row.get("spread")
    if spread is not None and pd.notna(spread):
        broker.default_spread_pips = float(spread_to_pips(float(spread), pip_size=pip_size))


def _manage_paper_positions(
    broker: PaperBroker,
    row: pd.Series,
    *,
    symbol: str,
    timestamp: pd.Timestamp,
    reconciler: BrokerReconciler | None,
    bot: DemoTradingBot,
    journal: TradeJournal | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for position in list(broker.get_open_positions(symbol)):
        reason, exit_price = _stop_or_target(position, row)
        if reason is None or exit_price is None:
            continue
        fill = _close_position(broker, position.position_id, market_price=exit_price, timestamp=timestamp)
        _record_close_side_effects(position, fill, reason, reconciler=reconciler, bot=bot, journal=journal)
        records.append(_position_event(position, fill, reason, timestamp))
    return records


def _close_remaining_paper_positions(
    broker: PaperBroker,
    *,
    symbol: str,
    market_price: float,
    timestamp: pd.Timestamp,
    reconciler: BrokerReconciler | None,
    bot: DemoTradingBot,
    journal: TradeJournal | None,
    reason: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for position in list(broker.get_open_positions(symbol)):
        fill = _close_position(broker, position.position_id, market_price=market_price, timestamp=timestamp)
        _record_close_side_effects(position, fill, reason, reconciler=reconciler, bot=bot, journal=journal)
        records.append(_position_event(position, fill, reason, timestamp))
    return records


def _stop_or_target(position: Position, row: pd.Series) -> tuple[str | None, float | None]:
    if position.side == "long":
        if position.stop_loss is not None and float(row["low"]) <= position.stop_loss:
            return "stop_loss", float(position.stop_loss)
        if position.take_profit is not None and float(row["high"]) >= position.take_profit:
            return "take_profit", float(position.take_profit)
    else:
        if position.stop_loss is not None and float(row["high"]) >= position.stop_loss:
            return "stop_loss", float(position.stop_loss)
        if position.take_profit is not None and float(row["low"]) <= position.take_profit:
            return "take_profit", float(position.take_profit)
    return None, None


def _close_position(broker: PaperBroker, position_id: str, *, market_price: float, timestamp: pd.Timestamp) -> OrderFill:
    return broker.close_position(position_id, market_price=market_price, timestamp=timestamp.to_pydatetime())


def _record_close_side_effects(
    position: Position,
    fill: OrderFill,
    reason: str,
    *,
    reconciler: BrokerReconciler | None,
    bot: DemoTradingBot,
    journal: TradeJournal | None,
) -> None:
    if reconciler is not None:
        reconciler.record_closed_position(position.position_id, exit_price=fill.price, closed_at=fill.timestamp)
    _record_lifecycle_close(position, fill, reason, bot=bot)
    if journal is not None and hasattr(journal, "append_fill"):
        journal.append_fill(fill, event_type=reason)
    elif journal is not None and hasattr(journal, "append"):
        journal.append(
            JournalEntry(
                timestamp=pd.Timestamp(fill.timestamp),
                symbol=fill.symbol,
                event_type=reason,
                side=fill.side,
                price=fill.price,
                units=fill.units,
                notes=reason,
                metadata={"order_id": fill.order_id, "position_id": position.position_id},
            )
        )


def _record_lifecycle_close(position: Position, fill: OrderFill, reason: str, *, bot: DemoTradingBot) -> None:
    store = bot.trade_lifecycle_store
    machine = bot.trade_lifecycle
    if store is None or machine is None:
        return
    matches = [
        record
        for record in store.list_records(symbol=position.symbol)
        if record.position_id == position.position_id and record.is_active
    ]
    if not matches:
        return
    record = matches[-1]
    updated = machine.record_close(
        record,
        fill=fill,
        pnl=position.realized_pnl,
        metadata={"source": "demo_forward", "reason": reason},
    )
    store.save(updated)


def _position_event(position: Position, fill: OrderFill, reason: str, timestamp: pd.Timestamp) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "symbol": position.symbol,
        "position_id": position.position_id,
        "event_type": reason,
        "side": position.side,
        "price": fill.price,
        "units": fill.units,
        "realized_pnl": position.realized_pnl,
        "fill_order_id": fill.order_id,
    }


def _cycle_record(
    cycle: CycleResult,
    account: AccountState,
    open_positions: list[Position],
    *,
    symbol: str,
    market_price: float,
    cycle_index: int,
) -> dict[str, Any]:
    risk_reasons = ";".join(cycle.risk_decision.reasons) if cycle.risk_decision is not None else ""
    portfolio_reasons = (
        ";".join(cycle.portfolio_risk_decision.reasons) if cycle.portfolio_risk_decision is not None else ""
    )
    reconciliation_reasons = (
        ";".join(cycle.reconciliation_result.blocking_reasons) if cycle.reconciliation_result is not None else ""
    )
    emergency_summary = cycle.emergency_stop_result.summary() if cycle.emergency_stop_result is not None else ""
    fill = cycle.fill
    return {
        "cycle": cycle_index,
        "timestamp": pd.Timestamp(cycle.timestamp),
        "symbol": fill.symbol if fill is not None else symbol,
        "side": cycle.side,
        "action": cycle.action,
        "setup_name": cycle.setup_name,
        "reasons": cycle.reasons,
        "risk_reasons": risk_reasons,
        "portfolio_reasons": portfolio_reasons,
        "reconciliation_reasons": reconciliation_reasons,
        "emergency_stop": emergency_summary,
        "market_price": market_price,
        "balance": account.balance,
        "equity": account.equity,
        "open_positions": len(open_positions),
        "fill_order_id": fill.order_id if fill is not None else None,
        "fill_side": fill.side if fill is not None else None,
        "fill_units": fill.units if fill is not None else None,
        "fill_price": fill.price if fill is not None else None,
        "fill_spread": fill.spread if fill is not None else None,
        "fill_slippage": fill.slippage if fill is not None else None,
        "lifecycle_state": cycle.trade_lifecycle.state if cycle.trade_lifecycle is not None else None,
        "lifecycle_trade_id": cycle.trade_lifecycle.trade_id if cycle.trade_lifecycle is not None else None,
    }


def _fills_frame(broker: BrokerAdapter) -> pd.DataFrame:
    fills = getattr(broker, "fills", None)
    if not fills:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(fill.timestamp),
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "units": fill.units,
                "price": fill.price,
                "spread": fill.spread,
                "slippage": fill.slippage,
                "commission": fill.commission,
                "client_order_id": fill.client_order_id,
                "metadata": dict(fill.metadata),
            }
            for fill in fills
        ]
    )


def _trades_frame(broker: BrokerAdapter, fill_setup: dict[str, dict[str, Any]]) -> pd.DataFrame:
    positions = getattr(broker, "positions", None)
    if not positions:
        return pd.DataFrame()
    fills = {fill.order_id: fill for fill in getattr(broker, "fills", [])}
    records: list[dict[str, Any]] = []
    for position in positions.values():
        open_fill = fills.get(position.position_id)
        setup = fill_setup.get(position.position_id, {})
        records.append(
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "units": open_fill.units if open_fill is not None else position.units,
                "entry_price": position.entry_price,
                "exit_price": position.exit_price,
                "opened_at": position.opened_at,
                "closed_at": position.closed_at,
                "realized_pnl": position.realized_pnl,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "setup_name": setup.get("setup_name", "unknown"),
                "cycle_timestamp": setup.get("cycle_timestamp"),
                "signal_side": setup.get("signal_side"),
                "reasons": setup.get("reasons"),
            }
        )
    return pd.DataFrame.from_records(records)


def _setup_report(cycles: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    cycle_counts = _group_counts(cycles, "setup_name", prefix="cycles")
    if trades.empty or "setup_name" not in trades.columns:
        return cycle_counts
    trade_metrics = (
        trades.assign(realized_pnl=trades["realized_pnl"].fillna(0.0))
        .groupby("setup_name", dropna=False)
        .agg(
            trades=("position_id", "count"),
            net_pnl=("realized_pnl", "sum"),
            average_pnl=("realized_pnl", "mean"),
            win_rate_percent=("realized_pnl", lambda pnl: float((pnl > 0).mean() * 100.0) if len(pnl) else 0.0),
        )
        .reset_index()
    )
    return _merge_reports(cycle_counts, trade_metrics, "setup_name")


def _session_report(cycles: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if cycles.empty or "timestamp" not in cycles.columns:
        return pd.DataFrame()
    sessions = session_labels(pd.DatetimeIndex(pd.to_datetime(cycles["timestamp"], utc=True)))
    rows: list[dict[str, Any]] = []
    for column in sessions.columns:
        mask = sessions[column].to_numpy()
        selected = cycles.loc[mask]
        rows.append(
            {
                "session": column,
                "cycles": int(len(selected)),
                "orders": int((selected["action"] == "order_placed").sum()) if not selected.empty else 0,
                "blocks": int(selected["action"].astype(str).str.startswith("blocked").sum()) if not selected.empty else 0,
            }
        )
    report = pd.DataFrame(rows)
    if trades.empty or "opened_at" not in trades.columns:
        return report
    trade_sessions = session_labels(pd.DatetimeIndex(pd.to_datetime(trades["opened_at"], utc=True)))
    trade_rows: list[dict[str, Any]] = []
    for column in trade_sessions.columns:
        mask = trade_sessions[column].to_numpy()
        selected = trades.loc[mask]
        trade_rows.append(
            {
                "session": column,
                "trades": int(len(selected)),
                "net_pnl": float(selected["realized_pnl"].fillna(0.0).sum()) if not selected.empty else 0.0,
            }
        )
    return _merge_reports(report, pd.DataFrame(trade_rows), "session")


def _daily_report(equity: pd.DataFrame, cycles: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame()
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame.index, utc=True).date
    daily = (
        frame.groupby("date")
        .agg(
            start_equity=("equity", "first"),
            end_equity=("equity", "last"),
            min_equity=("equity", "min"),
            max_equity=("equity", "max"),
        )
        .reset_index()
    )
    daily["return_percent"] = (daily["end_equity"] / daily["start_equity"] - 1.0) * 100.0
    if not cycles.empty:
        cycle_counts = cycles.assign(date=pd.to_datetime(cycles["timestamp"], utc=True).dt.date).groupby("date").agg(
            cycles=("cycle", "count"),
            orders=("action", lambda values: int((values == "order_placed").sum())),
            blocks=("action", lambda values: int(values.astype(str).str.startswith("blocked").sum())),
        )
        daily = daily.merge(cycle_counts.reset_index(), on="date", how="left")
    if not trades.empty:
        trade_counts = trades.assign(date=pd.to_datetime(trades["opened_at"], utc=True).dt.date).groupby("date").agg(
            trades=("position_id", "count"),
            net_pnl=("realized_pnl", "sum"),
        )
        daily = daily.merge(trade_counts.reset_index(), on="date", how="left")
    return daily.fillna(0)


def _blocked_reasons_report(cycles: pd.DataFrame) -> pd.DataFrame:
    if cycles.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in cycles.iterrows():
        if not str(row.get("action", "")).startswith("blocked") and row.get("action") != "emergency_stop_active":
            continue
        reasons = ";".join(
            str(row.get(column) or "")
            for column in ("reasons", "risk_reasons", "portfolio_reasons", "reconciliation_reasons", "emergency_stop")
        )
        for reason in [part for part in reasons.split(";") if part]:
            rows.append(
                {
                    "reason": reason,
                    "action": row.get("action"),
                    "setup_name": row.get("setup_name"),
                    "timestamp": row.get("timestamp"),
                }
            )
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .groupby(["reason", "action", "setup_name"], dropna=False)
        .agg(count=("timestamp", "count"), first_seen=("timestamp", "min"), last_seen=("timestamp", "max"))
        .reset_index()
        .sort_values(["count", "reason"], ascending=[False, True])
    )


def _summary(
    *,
    symbol: str,
    config: DemoForwardConfig,
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    cycles: pd.DataFrame,
    position_events: pd.DataFrame,
    final_account: AccountState,
) -> dict[str, Any]:
    metrics = performance_summary(equity, trades if not trades.empty else None) if not equity.empty else {}
    health = health_check(equity) if not equity.empty else None
    order_count = int((cycles["action"] == "order_placed").sum()) if not cycles.empty else 0
    blocked_count = int(cycles["action"].astype(str).str.startswith("blocked").sum()) if not cycles.empty else 0
    summary = {
        **metrics,
        "symbol": symbol,
        "cycles": int(len(cycles)),
        "orders": order_count,
        "blocked_cycles": blocked_count,
        "position_events": int(len(position_events)),
        "final_balance": final_account.balance,
        "final_equity": final_account.equity,
        "health_ok": bool(health.ok) if health is not None else False,
        "health_messages": tuple(health.messages) if health is not None else ("missing_equity_curve",),
        "start_time": pd.Timestamp(equity.index[0]).isoformat() if not equity.empty else None,
        "end_time": pd.Timestamp(equity.index[-1]).isoformat() if not equity.empty else None,
        "warmup_candles": config.warmup_candles,
        "manage_paper_positions": config.manage_paper_positions,
    }
    return summary


def _group_counts(frame: pd.DataFrame, column: str, *, prefix: str) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=[column, prefix])
    return frame.groupby(column, dropna=False).size().rename(prefix).reset_index()


def _merge_reports(left: pd.DataFrame, right: pd.DataFrame, on: str) -> pd.DataFrame:
    if left.empty:
        return right
    if right.empty:
        return left
    return left.merge(right, on=on, how="outer").fillna(0)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty:
        pd.DataFrame().to_csv(path, index=False)
    else:
        frame.to_csv(path, index=False)


def _summary_cards(summary: dict[str, Any]) -> str:
    keys = [
        "start_equity",
        "end_equity",
        "total_return_percent",
        "max_drawdown_percent",
        "trades",
        "win_rate_percent",
        "profit_factor",
        "net_pnl",
        "orders",
        "blocked_cycles",
    ]
    cards = []
    for key in keys:
        value = summary.get(key, "")
        cards.append(f'<div class="metric"><span>{key}</span>{_format_value(value)}</div>')
    return "\n".join(cards)


def _equity_svg(equity: pd.DataFrame) -> str:
    if equity.empty or "equity" not in equity.columns:
        return "<p>No equity data.</p>"
    values = equity["equity"].astype(float)
    width, height, pad = 760, 180, 12
    low, high = float(values.min()), float(values.max())
    span = high - low if high != low else 1.0
    points = []
    for index, value in enumerate(values):
        x = pad + (index / max(1, len(values) - 1)) * (width - pad * 2)
        y = height - pad - ((float(value) - low) / span) * (height - pad * 2)
        points.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
        f'aria-label="equity curve"><polyline fill="none" stroke="#1f7a8c" stroke-width="2" '
        f'points="{" ".join(points)}"/></svg>'
    )


def _frame_to_html(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p>No rows.</p>"
    return frame.to_html(index=False, escape=True, border=0)


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        return f"{value:.4g}"
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if math.isnan(value):
            return None
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None:
        return None
    return value
