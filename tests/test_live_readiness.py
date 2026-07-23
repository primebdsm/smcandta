from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from smc_ta.backtest import BacktestConfig, run_backtest
from smc_ta.broker import OrderRequest, PaperBroker
from smc_ta.data import CsvCandleDataSource, load_csv_candles
from smc_ta.journal import TradeJournal
from smc_ta.live import DemoTradingBot
from smc_ta.monitoring import health_check, performance_summary
from smc_ta.news import EconomicEvent, NewsFilter
from smc_ta.risk import RiskConfig, RiskManager


def make_candles(n: int = 180) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    wave = np.sin(np.arange(n) / 5) * 0.001
    drift = np.arange(n) * 0.00002
    close = pd.Series(1.1000 + wave + drift, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 25),
            "spread": 0.00012,
        },
        index=index,
    )


def test_paper_broker_order_lifecycle() -> None:
    broker = PaperBroker(initial_balance=10_000, default_spread_pips=1.0, slippage_pips=0.0)
    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=10_000, stop_loss=1.0950, take_profit=1.1100),
        market_price=1.1000,
    )
    assert fill.price > 1.1000
    assert len(broker.get_open_positions("EURUSD")) == 1

    broker.close_position(fill.order_id, market_price=1.1020)
    assert not broker.get_open_positions("EURUSD")
    assert broker.get_account().balance > 10_000


def test_news_filter_blocks_pair_currency() -> None:
    event = EconomicEvent(
        timestamp=pd.Timestamp("2024-01-01 12:00", tz="UTC"),
        currency="USD",
        impact="high",
        title="FOMC",
    )
    news = NewsFilter([event], block_before=timedelta(minutes=30), block_after=timedelta(minutes=30))

    assert not news.allow_trading("EURUSD", pd.Timestamp("2024-01-01 12:10", tz="UTC"))
    assert news.allow_trading("EURUSD", pd.Timestamp("2024-01-01 13:00", tz="UTC"))


def test_risk_manager_approves_and_blocks() -> None:
    manager = RiskManager(RiskConfig(risk_percent_per_trade=1, max_units=50_000))
    account = PaperBroker(initial_balance=10_000).get_account()
    signal = pd.Series(
        {
            "side": "long",
            "confidence": 0.8,
            "reference_rr": 2.0,
            "entry_reference": 1.1000,
            "stop_reference": 1.0950,
            "target_reference": 1.1100,
            "reasons": "test",
        }
    )
    decision = manager.evaluate_signal(
        signal,
        symbol="EURUSD",
        account=account,
        open_positions=[],
        timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
    )

    assert decision.approved
    assert decision.order is not None
    assert decision.units <= 50_000

    low_confidence_signal = signal.copy()
    low_confidence_signal["confidence"] = 0.1
    blocked = manager.evaluate_signal(
        low_confidence_signal,
        symbol="EURUSD",
        account=account,
        open_positions=[],
        timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
    )
    assert not blocked.approved
    assert "confidence_below_minimum" in blocked.reasons


def test_csv_source_journal_and_monitoring(tmp_path) -> None:
    candles = make_candles(40)
    csv_path = tmp_path / "EURUSD_M15.csv"
    candles.reset_index(names="time").to_csv(csv_path, index=False)

    loaded = load_csv_candles(csv_path)
    source = CsvCandleDataSource(tmp_path)
    sourced = source.get_candles("EURUSD", "M15", limit=10)

    assert len(loaded) == len(candles)
    assert len(sourced) == 10

    journal = TradeJournal(tmp_path / "journal.csv")
    fake_signal = pd.Series({"side": "flat", "confidence": 0.0, "entry_reference": np.nan, "reasons": "test"})
    journal.append_signal("EURUSD", loaded.index[-1], fake_signal)
    assert len(journal.read()) == 1

    equity = pd.DataFrame({"equity": [10_000, 10_100, 10_050]}, index=loaded.index[:3])
    summary = performance_summary(equity)
    assert summary["end_equity"] == 10_050
    assert health_check(equity).ok


def test_backtest_and_demo_bot_contract() -> None:
    candles = make_candles()
    result = run_backtest(
        candles,
        config=BacktestConfig(
            symbol="EURUSD",
            spread_pips=1.0,
            slippage_pips=0.1,
            risk=RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0, max_units=10_000),
        ),
    )
    assert len(result.equity_curve) == len(candles)
    assert {"equity", "balance", "open_positions"}.issubset(result.equity_curve.columns)
    assert result.signals["side"].isin(["long", "short", "flat"]).all()

    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=PaperBroker(initial_balance=10_000),
        risk_manager=RiskManager(RiskConfig(min_confidence=2.0)),
    )
    cycle = bot.run_cycle(candles)
    assert cycle.action == "blocked_by_risk"
