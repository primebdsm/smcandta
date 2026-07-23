"""Demo forward-testing bot orchestration."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smc_ta.alerts.channels import AlertChannel, format_signal_alert
from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import OrderFill, OrderRequest
from smc_ta.engine.confluence import ConfluenceConfig, analyze_forex
from smc_ta.journal.store import TradeJournal
from smc_ta.lifecycle import TradeLifecycleRecord, TradeLifecycleStateMachine, TradeLifecycleStore
from smc_ta.news.calendar import NewsFilter
from smc_ta.reconciliation import BrokerReconciler, ReconciliationResult
from smc_ta.risk import PortfolioRiskDecision, PortfolioRiskManager
from smc_ta.risk.manager import RiskDecision, RiskManager
from smc_ta.safety import EmergencyStopController, EmergencyStopResult
from smc_ta.smc.setups import classify_smc_setups
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class CycleResult:
    """Result of one demo/live analysis cycle."""

    timestamp: pd.Timestamp
    side: str
    action: str
    risk_decision: RiskDecision | None
    fill: OrderFill | None
    reasons: str
    setup_name: str = "none"
    portfolio_risk_decision: PortfolioRiskDecision | None = None
    reconciliation_result: ReconciliationResult | None = None
    emergency_stop_result: EmergencyStopResult | None = None
    emergency_close_fills: tuple[OrderFill, ...] = ()
    trade_lifecycle: TradeLifecycleRecord | None = None


class DemoTradingBot:
    """Run the full decision path against a broker adapter.

    This class is safe for paper/demo use. To trade live, pass a real broker
    adapter that implements `BrokerAdapter` and add production controls around
    connectivity, reconciliation, order retry, and emergency stop handling.
    """

    def __init__(
        self,
        *,
        symbol: str,
        broker: BrokerAdapter,
        risk_manager: RiskManager,
        portfolio_risk_manager: PortfolioRiskManager | None = None,
        confluence_config: ConfluenceConfig | None = None,
        news_filter: NewsFilter | None = None,
        journal: TradeJournal | None = None,
        alert_channel: AlertChannel | None = None,
        reconciler: BrokerReconciler | None = None,
        emergency_stop: EmergencyStopController | None = None,
        trade_lifecycle: TradeLifecycleStateMachine | None = None,
        trade_lifecycle_store: TradeLifecycleStore | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.broker = broker
        self.risk_manager = risk_manager
        self.portfolio_risk_manager = portfolio_risk_manager
        self.confluence_config = confluence_config or ConfluenceConfig()
        self.news_filter = news_filter
        self.journal = journal
        self.alert_channel = alert_channel
        self.reconciler = reconciler
        self.emergency_stop = emergency_stop
        self.trade_lifecycle_store = trade_lifecycle_store
        self.trade_lifecycle = trade_lifecycle or (
            TradeLifecycleStateMachine() if trade_lifecycle_store is not None else None
        )

    def run_cycle(self, candles: pd.DataFrame) -> CycleResult:
        """Analyze the latest closed candle and place a demo/paper order if approved."""

        data = normalize_ohlcv(candles)
        timestamp = pd.Timestamp(data.index[-1])
        market_price = float(data["close"].iloc[-1])
        if hasattr(self.broker, "mark_price"):
            self.broker.mark_price(self.symbol, market_price)

        analysis = analyze_forex(data, symbol=self.symbol, config=self.confluence_config)
        signal = analysis.signals.iloc[-1]
        setups = classify_smc_setups(analysis.features, analysis.signals)
        setup_name = str(setups.iloc[-1]["setup_name"])
        if self.journal:
            self.journal.append_signal(self.symbol, timestamp, signal)
        lifecycle_record = self._create_lifecycle_record(timestamp, signal, setup_name)

        if self.news_filter and not self.news_filter.allow_trading(self.symbol, timestamp):
            lifecycle_record = self._block_lifecycle_record(lifecycle_record, "news_filter_blocked", source="news")
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="blocked_by_news",
                risk_decision=None,
                fill=None,
                reasons="news_filter_blocked",
                setup_name=setup_name,
                trade_lifecycle=lifecycle_record,
            )

        reconciliation_result = None
        reconciliation_blocked = False
        if self.reconciler is not None:
            reconciliation_result = self.reconciler.reconcile_broker(self.broker, self.symbol)
            if not reconciliation_result.ok:
                reconciliation_blocked = True

        emergency_stop_result = self._evaluate_emergency_stop(
            timestamp=timestamp,
            market_price=market_price,
            reconciliation_result=reconciliation_result,
        )
        if emergency_stop_result is not None and emergency_stop_result.active:
            lifecycle_record = self._block_lifecycle_record(
                lifecycle_record,
                emergency_stop_result.summary(),
                source="emergency_stop",
            )
            close_fills = self._handle_emergency_stop(emergency_stop_result, market_price)
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="emergency_stop_active",
                risk_decision=None,
                fill=None,
                reasons=emergency_stop_result.summary(),
                setup_name=setup_name,
                portfolio_risk_decision=None,
                reconciliation_result=reconciliation_result,
                emergency_stop_result=emergency_stop_result,
                emergency_close_fills=tuple(close_fills),
                trade_lifecycle=lifecycle_record,
            )

        if reconciliation_blocked and reconciliation_result is not None:
            lifecycle_record = self._block_lifecycle_record(
                lifecycle_record,
                reconciliation_result.summary(),
                source="reconciliation",
            )
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="blocked_by_reconciliation",
                risk_decision=None,
                fill=None,
                reasons=reconciliation_result.summary(),
                setup_name=setup_name,
                portfolio_risk_decision=None,
                reconciliation_result=reconciliation_result,
                emergency_stop_result=emergency_stop_result,
                trade_lifecycle=lifecycle_record,
            )

        decision = self.risk_manager.evaluate_signal(
            signal,
            symbol=self.symbol,
            account=self.broker.get_account(),
            open_positions=self.broker.get_open_positions(),
            timestamp=timestamp,
        )
        if not decision.approved or decision.order is None:
            lifecycle_record = self._block_lifecycle_record(
                lifecycle_record,
                ";".join(decision.reasons),
                source="risk",
            )
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="blocked_by_risk",
                risk_decision=decision,
                fill=None,
                reasons=";".join(decision.reasons),
                setup_name=setup_name,
                portfolio_risk_decision=None,
                reconciliation_result=reconciliation_result,
                emergency_stop_result=emergency_stop_result,
                trade_lifecycle=lifecycle_record,
            )
        lifecycle_record = self._approve_lifecycle_record(lifecycle_record, decision)

        portfolio_decision = None
        if self.portfolio_risk_manager is not None:
            portfolio_decision = self.portfolio_risk_manager.evaluate_order(
                decision.order,
                open_positions=self.broker.get_open_positions(),
                market_price=market_price,
            )
            if not portfolio_decision.approved:
                lifecycle_record = self._block_lifecycle_record(
                    lifecycle_record,
                    ";".join(portfolio_decision.reasons),
                    source="portfolio_risk",
                )
                return CycleResult(
                    timestamp=timestamp,
                    side=str(signal["side"]),
                    action="blocked_by_portfolio_risk",
                    risk_decision=decision,
                    fill=None,
                    reasons=";".join(portfolio_decision.reasons),
                    setup_name=setup_name,
                    portfolio_risk_decision=portfolio_decision,
                    reconciliation_result=reconciliation_result,
                    emergency_stop_result=emergency_stop_result,
                    trade_lifecycle=lifecycle_record,
                )

        if self.alert_channel and signal["side"] in {"long", "short"}:
            self.alert_channel.send(format_signal_alert(self.symbol, signal, setup_name=setup_name))
        lifecycle_record = self._submit_lifecycle_record(lifecycle_record, decision.order)
        try:
            fill = self.broker.place_order(decision.order, market_price=market_price)
        except Exception as exc:
            lifecycle_record = self._fail_lifecycle_record(
                lifecycle_record,
                str(exc),
                metadata={"exception_type": type(exc).__name__},
            )
            raise
        lifecycle_record = self._fill_lifecycle_record(lifecycle_record, fill)
        if self.reconciler is not None:
            self._record_latest_broker_position(fill)
        if self.journal and hasattr(self.journal, "append_fill"):
            self.journal.append_fill(fill)
        return CycleResult(
            timestamp=timestamp,
            side=str(signal["side"]),
            action="order_placed",
            risk_decision=decision,
            fill=fill,
            reasons=str(signal.get("reasons", "")),
            setup_name=setup_name,
            portfolio_risk_decision=portfolio_decision,
            reconciliation_result=reconciliation_result,
            emergency_stop_result=emergency_stop_result,
            trade_lifecycle=lifecycle_record,
        )

    def _create_lifecycle_record(
        self,
        timestamp: pd.Timestamp,
        signal: pd.Series,
        setup_name: str,
    ) -> TradeLifecycleRecord | None:
        if self.trade_lifecycle is None:
            return None
        record = self.trade_lifecycle.create_from_signal(
            symbol=self.symbol,
            timestamp=timestamp,
            signal=signal,
            setup_name=setup_name,
        )
        self._save_lifecycle_record(record)
        return record

    def _approve_lifecycle_record(
        self,
        record: TradeLifecycleRecord | None,
        decision: RiskDecision,
    ) -> TradeLifecycleRecord | None:
        if record is None or self.trade_lifecycle is None:
            return record
        updated = self.trade_lifecycle.approve(record, decision)
        self._save_lifecycle_record(updated)
        return updated

    def _submit_lifecycle_record(
        self,
        record: TradeLifecycleRecord | None,
        order: OrderRequest,
    ) -> TradeLifecycleRecord | None:
        if record is None or self.trade_lifecycle is None:
            return record
        updated = self.trade_lifecycle.submit(record, order)
        self._save_lifecycle_record(updated)
        return updated

    def _fill_lifecycle_record(
        self,
        record: TradeLifecycleRecord | None,
        fill: OrderFill,
    ) -> TradeLifecycleRecord | None:
        if record is None or self.trade_lifecycle is None:
            return record
        updated = self.trade_lifecycle.record_fill(record, fill)
        self._save_lifecycle_record(updated)
        return updated

    def _block_lifecycle_record(
        self,
        record: TradeLifecycleRecord | None,
        reason: str,
        *,
        source: str,
    ) -> TradeLifecycleRecord | None:
        if record is None or self.trade_lifecycle is None:
            return record
        updated = self.trade_lifecycle.block(record, reason, source=source)
        self._save_lifecycle_record(updated)
        return updated

    def _fail_lifecycle_record(
        self,
        record: TradeLifecycleRecord | None,
        reason: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> TradeLifecycleRecord | None:
        if record is None or self.trade_lifecycle is None:
            return record
        updated = self.trade_lifecycle.fail(record, reason, metadata=metadata)
        self._save_lifecycle_record(updated)
        return updated

    def _save_lifecycle_record(self, record: TradeLifecycleRecord) -> None:
        if self.trade_lifecycle_store is not None:
            self.trade_lifecycle_store.save(record)

    def _record_latest_broker_position(self, fill: OrderFill) -> None:
        if self.reconciler is None:
            return
        expected_side = "long" if fill.side == "buy" else "short"
        broker_positions = [
            position
            for position in self.broker.get_open_positions(self.symbol)
            if position.symbol == fill.symbol and position.side == expected_side
        ]
        if not broker_positions:
            return
        exact = [position for position in broker_positions if position.position_id == fill.order_id]
        self.reconciler.record_expected_position((exact or broker_positions)[-1])

    def _evaluate_emergency_stop(
        self,
        *,
        timestamp: pd.Timestamp,
        market_price: float,
        reconciliation_result: ReconciliationResult | None,
    ) -> EmergencyStopResult | None:
        if self.emergency_stop is None:
            return None
        try:
            account = self.broker.get_account()
            open_positions = self.broker.get_open_positions()
        except Exception as exc:
            result = self.emergency_stop.record_runtime_error(exc)
            if result is not None:
                return result
            raise
        if hasattr(self.broker, "mark_price"):
            self.broker.mark_price(self.symbol, market_price)
        return self.emergency_stop.evaluate(
            account=account,
            open_positions=open_positions,
            timestamp=timestamp,
            reconciliation_result=reconciliation_result,
        )

    def _handle_emergency_stop(
        self,
        result: EmergencyStopResult,
        market_price: float,
    ) -> list[OrderFill]:
        if self.alert_channel:
            self.alert_channel.send(f"{self.symbol} emergency stop active: {result.summary()}")
        if self.journal:
            self._append_journal_event(
                event_type="emergency_stop",
                side=None,
                price=market_price,
                notes=result.summary(),
                metadata=result.details,
            )
        close_fills: list[OrderFill] = []
        if not result.close_positions:
            return close_fills
        for position in list(self.broker.get_open_positions(self.symbol)):
            fill = self.broker.close_position(position.position_id, market_price=market_price)
            close_fills.append(fill)
            if self.reconciler is not None:
                self.reconciler.record_closed_position(
                    position.position_id,
                    exit_price=fill.price,
                    closed_at=fill.timestamp,
                )
            if self.journal and hasattr(self.journal, "append_fill"):
                self.journal.append_fill(fill, event_type="emergency_close")
        return close_fills

    def _append_journal_event(
        self,
        *,
        event_type: str,
        side: str | None,
        price: float | None,
        notes: str | None,
        metadata: dict[str, object],
    ) -> None:
        if self.journal is None or not hasattr(self.journal, "append"):
            return
        from smc_ta.journal.store import JournalEntry

        self.journal.append(
            JournalEntry(
                timestamp=pd.Timestamp.now(tz="UTC"),
                symbol=self.symbol,
                event_type=event_type,
                side=side,
                price=price,
                notes=notes,
                metadata=metadata,
            )
        )
