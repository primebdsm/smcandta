from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from smc_ta.alerts import format_signal_alert
from smc_ta.backtest import BacktestConfig, run_pair_backtests
from smc_ta.broker.oanda import _candles_to_frame, oanda_instrument
from smc_ta.dashboard import render_dashboard_html
from smc_ta.engine import MultiTimeframeConfig, analyze_multi_timeframe
from smc_ta.journal import JournalEntry, SQLiteTradeJournal
from smc_ta.news import JsonEconomicCalendarSource, news_filter_from_source
from smc_ta.risk import RiskConfig
from smc_ta.smc import classify_smc_setups
from smc_ta.strategy import get_strategy_profile, list_strategy_profiles


def make_candles(n: int = 180, freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = pd.Series(1.1000 + np.sin(np.arange(n) / 7) * 0.001 + np.arange(n) * 0.00002, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 20),
            "spread": 0.00012,
        },
        index=index,
    )


def test_oanda_helpers_normalize_candles() -> None:
    assert oanda_instrument("EURUSD") == "EUR_USD"
    frame = _candles_to_frame(
        [
            {
                "complete": True,
                "time": "2024-01-01T00:00:00Z",
                "mid": {"o": "1.1000", "h": "1.1010", "l": "1.0990", "c": "1.1005"},
                "bid": {"c": "1.1004"},
                "ask": {"c": "1.1006"},
                "volume": 123,
            }
        ]
    )

    assert frame.iloc[0]["close"] == 1.1005
    assert frame.iloc[0]["tick_volume"] == 123
    assert frame.iloc[0]["spread"] == pytest.approx(0.0002)


def test_multi_timeframe_contract_and_setup_classifier() -> None:
    entry = make_candles(180, "15min")
    higher = make_candles(80, "1h")
    result = analyze_multi_timeframe(
        {"M15": entry, "H1": higher},
        symbol="EURUSD",
        config=MultiTimeframeConfig(entry_timeframe="M15", higher_timeframes=("H1",)),
    )

    assert len(result.signals) == len(entry)
    assert "higher_timeframe_bias" in result.signals.columns
    assert len(result.setup_classification) == len(entry)

    features = pd.DataFrame(
        {
            "structure_trend": ["bullish"],
            "structure_direction": ["bullish"],
            "active_bull_fvg_distance": [0.0],
            "active_bear_fvg_distance": [np.nan],
            "active_bull_ob_distance": [np.nan],
            "active_bear_ob_distance": [np.nan],
            "pd_zone": ["discount"],
            "liquidity_sweep": ["sell_side"],
            "london_kill_zone": [True],
        },
        index=pd.date_range("2024-01-01", periods=1, tz="UTC"),
    )
    signals = pd.DataFrame({"side": ["long"]}, index=features.index)
    setups = classify_smc_setups(features, signals)
    assert "liquidity_sweep_choch" in setups.iloc[0]["setup_name"]
    assert setups.iloc[0]["setup_direction"] == "bullish"


def test_strategy_profile_backtest_pair_report_dashboard_and_alert() -> None:
    assert "intraday_m15" in list_strategy_profiles()
    profile = get_strategy_profile("intraday_m15")
    assert profile.mtf_config().entry_timeframe == "M15"

    candles = make_candles()
    _, report = run_pair_backtests(
        {"EURUSD": candles, "GBPUSD": candles.copy()},
        base_config=BacktestConfig(
            risk=RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0, max_units=5_000),
            max_daily_trades=1,
            trailing_stop_atr_multiple=2.0,
            partial_close_at_rr=1.0,
        ),
    )
    assert {"EURUSD", "GBPUSD"}.issubset(set(report.index))

    html = render_dashboard_html(
        symbol="EURUSD",
        signals=pd.DataFrame({"side": ["flat"], "confidence": [0.0], "long_score": [0], "short_score": [0], "reasons": ["test"]}),
        features=pd.DataFrame({"structure_trend": ["neutral"], "pd_zone": ["equilibrium"]}),
        equity_curve=pd.DataFrame({"equity": [10_000, 10_050]}),
        trades=pd.DataFrame(),
    )
    assert "SMC TA Dashboard" in html

    message = format_signal_alert(
        "EURUSD",
        pd.Series({"side": "long", "confidence": 0.75, "entry_reference": 1.1, "stop_reference": 1.09, "target_reference": 1.12, "reasons": "test"}),
        setup_name="fvg_continuation",
    )
    assert "setup: fvg_continuation" in message


def test_sqlite_journal_and_json_calendar_source(tmp_path, monkeypatch) -> None:
    journal = SQLiteTradeJournal(tmp_path / "journal.sqlite")
    journal.append(
        JournalEntry(
            timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
            symbol="EURUSD",
            event_type="note",
            notes="created",
        )
    )
    assert len(journal.read(symbol="EURUSD")) == 1

    payload = json.dumps(
        {
            "events": [
                {
                    "time": "2024-01-01T12:00:00Z",
                    "currency": "USD",
                    "impact": "high",
                    "name": "NFP",
                }
            ]
        }
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return payload

    def fake_urlopen(request, timeout=20.0):
        return FakeResponse()

    monkeypatch.setattr("smc_ta.news.sources.urlopen", fake_urlopen)
    source = JsonEconomicCalendarSource(
        "https://example.test/calendar",
        events_key="events",
        field_map={"timestamp": "time", "currency": "currency", "impact": "impact", "title": "name"},
    )
    news_filter = news_filter_from_source(source, block_before=timedelta(minutes=30), block_after=timedelta(minutes=30))
    assert not news_filter.allow_trading("EURUSD", pd.Timestamp("2024-01-01T12:10:00Z"))
