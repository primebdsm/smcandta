from __future__ import annotations

import json

import pytest

from smc_ta.config import (
    LIVE_CONFIRMATION_PHRASE,
    ConfigValidationError,
    RuntimeConfig,
    assert_runtime_ready,
    build_oanda_config,
    build_tradingeconomics_config,
    load_env_file,
    validate_runtime_config,
)


def issue_codes(config: RuntimeConfig) -> set[str]:
    return {issue.code for issue in validate_runtime_config(config).issues}


def test_default_runtime_config_is_safe_for_paper_mode() -> None:
    config = RuntimeConfig()
    report = config.validate()

    assert report.ok
    assert report.summary() == "runtime_config_ok"
    assert config.to_safe_dict()["broker"] == "paper"
    assert assert_runtime_ready(config).ok


def test_live_oanda_requires_explicit_arming_and_confirmation() -> None:
    config = RuntimeConfig(
        mode="live",
        broker="oanda",
        oanda_account_id="001-123",
        oanda_token="live-token",
        oanda_practice=False,
        require_news_filter=True,
        trading_economics_api_key="te-key",
        journal_path="journal.csv",
        lifecycle_db_path="lifecycle.sqlite",
    )

    report = config.validate()

    assert not report.ok
    assert {"live_not_armed", "missing_live_confirmation"}.issubset(issue.code for issue in report.errors)
    with pytest.raises(ConfigValidationError):
        assert_runtime_ready(config)
    with pytest.raises(ConfigValidationError):
        build_oanda_config(config)


def test_armed_live_oanda_config_builds_adapter_configs_and_redacts_secrets() -> None:
    config = RuntimeConfig(
        mode="live",
        broker="oanda",
        allow_live_trading=True,
        live_confirmation=LIVE_CONFIRMATION_PHRASE,
        oanda_account_id="001-123",
        oanda_token="super-secret-token",
        oanda_practice=False,
        require_news_filter=True,
        trading_economics_api_key="te-secret-key",
        journal_path="journal.csv",
        lifecycle_db_path="lifecycle.sqlite",
    )

    report = config.validate()
    oanda = build_oanda_config(config)
    news = build_tradingeconomics_config(config, importance=(2, 3), values=True)
    safe = config.to_safe_dict()

    assert report.ok
    assert oanda.account_id == "001-123"
    assert oanda.token == "super-secret-token"
    assert not oanda.practice
    assert news.api_key == "te-secret-key"
    assert news.importance == (2, 3)
    assert safe["oanda_token"].endswith("oken")
    assert "super-secret-token" not in json.dumps(safe)
    assert safe["trading_economics_api_key"].endswith("-key")


def test_env_file_and_env_aliases_load_runtime_config(tmp_path) -> None:
    env_file = tmp_path / ".env.runtime"
    env_file.write_text(
        """
        # demo config
        SMC_TA_MODE=demo
        SMC_TA_BROKER=oanda
        SMC_TA_SYMBOLS=EURUSD, GBPUSD
        SMC_TA_TIMEFRAMES=M15,H1
        OANDA_ACCOUNT_ID=demo-account
        OANDA_TOKEN="demo-token"
        TRADING_ECONOMICS_API_KEY=calendar-key
        SMC_TA_REQUIRE_NEWS_FILTER=true
        SMC_TA_JOURNAL_PATH=journal.csv
        SMC_TA_LIFECYCLE_DB_PATH=lifecycle.sqlite
        """,
        encoding="utf-8",
    )

    config = RuntimeConfig.from_env_file(env_file)

    assert load_env_file(env_file)["OANDA_TOKEN"] == "demo-token"
    assert config.mode == "demo"
    assert config.broker == "oanda"
    assert config.symbols == ("EURUSD", "GBPUSD")
    assert config.timeframes == ("M15", "H1")
    assert config.oanda_account_id == "demo-account"
    assert config.oanda_token == "demo-token"
    assert config.trading_economics_api_key == "calendar-key"
    assert config.validate().ok


def test_json_mapping_validation_and_required_news_key(tmp_path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "mode": "demo",
                "broker": "oanda",
                "symbols": ["EURUSD"],
                "timeframes": ["M15"],
                "oanda_account_id": "demo-account",
                "oanda_token": "demo-token",
                "oanda_practice": "true",
                "require_news_filter": "true",
                "journal_path": "journal.csv",
                "lifecycle_db_path": "lifecycle.sqlite",
            }
        ),
        encoding="utf-8",
    )

    config = RuntimeConfig.from_json(path)

    assert config.require_news_filter
    assert "missing_news_api_key" in issue_codes(config)
    with pytest.raises(ConfigValidationError):
        build_tradingeconomics_config(config)


def test_invalid_symbols_timeframes_and_risk_are_errors() -> None:
    config = RuntimeConfig(
        symbols=("EURUSD", "BAD"),
        timeframes=("M15", "M2"),
        max_trade_risk_percent=0.0,
    )

    codes = issue_codes(config)

    assert {"invalid_symbol", "invalid_timeframe", "invalid_risk_percent"}.issubset(codes)
