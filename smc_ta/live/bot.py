"""Demo forward-testing bot orchestration."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from smc_ta.alerts.channels import AlertChannel, format_signal_alert
from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import OrderFill
from smc_ta.engine.confluence import ConfluenceConfig, analyze_forex
from smc_ta.journal.store import TradeJournal
from smc_ta.news.calendar import NewsFilter
from smc_ta.risk.manager import RiskDecision, RiskManager
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
        confluence_config: ConfluenceConfig | None = None,
        news_filter: NewsFilter | None = None,
        journal: TradeJournal | None = None,
        alert_channel: AlertChannel | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.broker = broker
        self.risk_manager = risk_manager
        self.confluence_config = confluence_config or ConfluenceConfig()
        self.news_filter = news_filter
        self.journal = journal
        self.alert_channel = alert_channel

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
        if self.alert_channel and signal["side"] in {"long", "short"}:
            self.alert_channel.send(format_signal_alert(self.symbol, signal, setup_name=setup_name))

        if self.news_filter and not self.news_filter.allow_trading(self.symbol, timestamp):
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="blocked_by_news",
                risk_decision=None,
                fill=None,
                reasons="news_filter_blocked",
                setup_name=setup_name,
            )

        decision = self.risk_manager.evaluate_signal(
            signal,
            symbol=self.symbol,
            account=self.broker.get_account(),
            open_positions=self.broker.get_open_positions(),
            timestamp=timestamp,
        )
        if not decision.approved or decision.order is None:
            return CycleResult(
                timestamp=timestamp,
                side=str(signal["side"]),
                action="blocked_by_risk",
                risk_decision=decision,
                fill=None,
                reasons=";".join(decision.reasons),
                setup_name=setup_name,
            )

        fill = self.broker.place_order(decision.order, market_price=market_price)
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
        )
