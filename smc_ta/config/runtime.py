"""Runtime configuration and live-trading guardrails."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from smc_ta.broker import OandaConfig
from smc_ta.news import TradingEconomicsConfig

RuntimeMode = Literal["research", "backtest", "paper", "demo", "live"]
BrokerName = Literal["paper", "oanda", "mt5", "custom"]
IssueSeverity = Literal["error", "warning"]

LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_LIVE_FOREX_RISK"
VALID_MODES = {"research", "backtest", "paper", "demo", "live"}
VALID_BROKERS = {"paper", "oanda", "mt5", "custom"}
VALID_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "D"}
SECRET_FIELDS = {"oanda_token", "mt5_password", "trading_economics_api_key"}


@dataclass(frozen=True)
class ConfigIssue:
    """One runtime configuration problem or warning."""

    severity: IssueSeverity
    code: str
    message: str


@dataclass(frozen=True)
class ConfigValidationReport:
    """Validation result for a runtime config."""

    issues: tuple[ConfigIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[ConfigIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ConfigIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.errors)

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "runtime_config_ok"
        parts = [issue.code for issue in self.errors] + [f"warning:{issue.code}" for issue in self.warnings]
        return ";".join(parts)

    def to_frame(self):
        """Return issues as a DataFrame without making pandas a config import dependency."""

        import pandas as pd

        return pd.DataFrame([asdict(issue) for issue in self.issues])


class ConfigValidationError(ValueError):
    """Raised when runtime config is not safe to run."""

    def __init__(self, report: ConfigValidationReport) -> None:
        super().__init__(report.summary())
        self.report = report


@dataclass(frozen=True)
class RuntimeConfig:
    """Bot runtime settings loaded from env, `.env`, or JSON."""

    mode: RuntimeMode = "paper"
    broker: BrokerName = "paper"
    symbols: tuple[str, ...] = ("EURUSD",)
    timeframes: tuple[str, ...] = ("M15",)
    account_currency: str = "USD"
    allow_live_trading: bool = False
    live_confirmation: str = ""
    require_news_filter: bool = False
    max_trade_risk_percent: float = 1.0
    oanda_account_id: str | None = None
    oanda_token: str | None = None
    oanda_practice: bool = True
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None
    trading_economics_api_key: str | None = None
    journal_path: str | None = None
    lifecycle_db_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "SMC_TA_",
    ) -> "RuntimeConfig":
        """Build config from environment variables."""

        source = dict(os.environ if env is None else env)
        return cls(
            mode=_normalize_mode(_env_get(source, prefix, "MODE", default="paper")),
            broker=_normalize_broker(_env_get(source, prefix, "BROKER", default="paper")),
            symbols=_split_csv(_env_get(source, prefix, "SYMBOLS", default="EURUSD")),
            timeframes=_split_csv(_env_get(source, prefix, "TIMEFRAMES", default="M15")),
            account_currency=_env_get(source, prefix, "ACCOUNT_CURRENCY", default="USD").upper(),
            allow_live_trading=_to_bool(_env_get(source, prefix, "ALLOW_LIVE_TRADING", default="false")),
            live_confirmation=_env_get(source, prefix, "LIVE_CONFIRMATION", default=""),
            require_news_filter=_to_bool(_env_get(source, prefix, "REQUIRE_NEWS_FILTER", default="false")),
            max_trade_risk_percent=float(_env_get(source, prefix, "MAX_TRADE_RISK_PERCENT", default="1.0")),
            oanda_account_id=_env_get_optional(source, prefix, "OANDA_ACCOUNT_ID"),
            oanda_token=_env_get_optional(source, prefix, "OANDA_TOKEN"),
            oanda_practice=_to_bool(_env_get(source, prefix, "OANDA_PRACTICE", default="true")),
            mt5_login=_optional_int(_env_get_optional(source, prefix, "MT5_LOGIN")),
            mt5_password=_env_get_optional(source, prefix, "MT5_PASSWORD"),
            mt5_server=_env_get_optional(source, prefix, "MT5_SERVER"),
            mt5_path=_env_get_optional(source, prefix, "MT5_PATH"),
            trading_economics_api_key=_env_get_optional(source, prefix, "TRADING_ECONOMICS_API_KEY"),
            journal_path=_env_get_optional(source, prefix, "JOURNAL_PATH"),
            lifecycle_db_path=_env_get_optional(source, prefix, "LIFECYCLE_DB_PATH"),
        )

    @classmethod
    def from_env_file(
        cls,
        path: str | Path,
        *,
        base_env: Mapping[str, str] | None = None,
        prefix: str = "SMC_TA_",
    ) -> "RuntimeConfig":
        """Build config from a `.env`-style file overlaid on `base_env`."""

        merged = dict(base_env or {})
        merged.update(load_env_file(path))
        return cls.from_env(merged, prefix=prefix)

    @classmethod
    def from_json(cls, path: str | Path) -> "RuntimeConfig":
        """Build config from a JSON file using the dataclass field names."""

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RuntimeConfig":
        """Build config from a dictionary-like object."""

        data = dict(payload)
        if "mode" in data:
            data["mode"] = _normalize_mode(str(data["mode"]))
        if "broker" in data:
            data["broker"] = _normalize_broker(str(data["broker"]))
        if "symbols" in data:
            data["symbols"] = _split_any(data["symbols"])
        if "timeframes" in data:
            data["timeframes"] = tuple(item.upper() for item in _split_any(data["timeframes"]))
        if "account_currency" in data:
            data["account_currency"] = str(data["account_currency"]).upper()
        for key in ("allow_live_trading", "require_news_filter", "oanda_practice"):
            if key in data:
                data[key] = _to_bool(data[key])
        if "max_trade_risk_percent" in data:
            data["max_trade_risk_percent"] = float(data["max_trade_risk_percent"])
        if "mt5_login" in data:
            data["mt5_login"] = _optional_int(data["mt5_login"])
        return cls(**data)

    def validate(self) -> ConfigValidationReport:
        """Validate this config."""

        return validate_runtime_config(self)

    def assert_ready(self) -> "RuntimeConfig":
        """Raise when this config is not safe to run."""

        assert_runtime_ready(self)
        return self

    def to_safe_dict(self) -> dict[str, Any]:
        """Return a serializable dict with secrets redacted."""

        record = asdict(self)
        for field_name in SECRET_FIELDS:
            record[field_name] = redact_secret(record.get(field_name))
        return record


def validate_runtime_config(config: RuntimeConfig) -> ConfigValidationReport:
    """Return config errors and warnings."""

    issues: list[ConfigIssue] = []

    if config.mode not in VALID_MODES:
        issues.append(_error("invalid_mode", f"unsupported mode: {config.mode}"))
    if config.broker not in VALID_BROKERS:
        issues.append(_error("invalid_broker", f"unsupported broker: {config.broker}"))
    if not config.symbols:
        issues.append(_error("missing_symbols", "at least one Forex symbol is required"))
    for symbol in config.symbols:
        if not _valid_symbol(symbol):
            issues.append(_error("invalid_symbol", f"symbol must be six alphabetic characters: {symbol}"))
    if len(set(config.symbols)) != len(config.symbols):
        issues.append(_warning("duplicate_symbols", "duplicate symbols were configured"))
    if not config.timeframes:
        issues.append(_error("missing_timeframes", "at least one timeframe is required"))
    for timeframe in config.timeframes:
        if timeframe.upper() not in VALID_TIMEFRAMES:
            issues.append(_error("invalid_timeframe", f"unsupported timeframe: {timeframe}"))
    if not _valid_currency(config.account_currency):
        issues.append(_error("invalid_account_currency", "account currency must be a three-letter code"))
    if config.max_trade_risk_percent <= 0:
        issues.append(_error("invalid_risk_percent", "max_trade_risk_percent must be positive"))
    elif config.max_trade_risk_percent > 100:
        issues.append(_error("invalid_risk_percent", "max_trade_risk_percent must be <= 100"))
    elif config.max_trade_risk_percent > 5:
        issues.append(_warning("large_risk_percent", "risk per trade is above 5%"))

    if config.mode == "live":
        if not config.allow_live_trading:
            issues.append(_error("live_not_armed", "live mode requires allow_live_trading=True"))
        if config.live_confirmation != LIVE_CONFIRMATION_PHRASE:
            issues.append(_error("missing_live_confirmation", f"live mode requires {LIVE_CONFIRMATION_PHRASE}"))
        if config.broker == "paper":
            issues.append(_error("paper_broker_in_live_mode", "paper broker cannot be used for live mode"))

    if config.mode == "demo" and config.broker == "paper":
        issues.append(_error("paper_broker_in_demo_mode", "use mode='paper' for PaperBroker or choose a demo broker"))

    if config.broker == "oanda":
        if config.mode in {"demo", "live"}:
            if not config.oanda_account_id:
                issues.append(_error("missing_oanda_account_id", "OANDA account ID is required"))
            if not config.oanda_token:
                issues.append(_error("missing_oanda_token", "OANDA token is required"))
        if config.mode == "demo" and not config.oanda_practice:
            issues.append(_error("oanda_demo_must_use_practice", "OANDA demo mode must use the practice endpoint"))
        if config.mode == "live" and config.oanda_practice:
            issues.append(_error("oanda_live_must_not_use_practice", "OANDA live mode must use practice=False"))

    if config.broker == "mt5" and config.mode == "live" and not any((config.mt5_login, config.mt5_server, config.mt5_path)):
        issues.append(_warning("mt5_terminal_session_unverified", "MT5 live mode relies on the currently initialized terminal session"))

    if config.require_news_filter and not config.trading_economics_api_key:
        issues.append(_error("missing_news_api_key", "required news filter needs TRADING_ECONOMICS_API_KEY"))
    if config.mode == "live" and not config.require_news_filter:
        issues.append(_warning("live_news_filter_not_required", "live config does not require a news filter"))

    if config.mode in {"demo", "live"}:
        if not config.lifecycle_db_path:
            issues.append(_warning("missing_lifecycle_db_path", "demo/live mode should persist lifecycle records"))
        if not config.journal_path:
            issues.append(_warning("missing_journal_path", "demo/live mode should persist a trade journal"))

    return ConfigValidationReport(tuple(issues))


def assert_runtime_ready(config: RuntimeConfig) -> ConfigValidationReport:
    """Raise `ConfigValidationError` when validation has errors."""

    report = validate_runtime_config(config)
    if not report.ok:
        raise ConfigValidationError(report)
    return report


def build_oanda_config(config: RuntimeConfig) -> OandaConfig:
    """Create an `OandaConfig` after runtime validation."""

    if config.broker != "oanda":
        raise ConfigValidationError(
            ConfigValidationReport((_error("broker_is_not_oanda", "runtime broker must be 'oanda'"),))
        )
    report = validate_runtime_config(config)
    if report.errors or not config.oanda_account_id or not config.oanda_token:
        errors = report.errors or (
            _error("missing_oanda_credentials", "OANDA account ID and token are required"),
        )
        raise ConfigValidationError(ConfigValidationReport(tuple(errors)))
    return OandaConfig(
        account_id=config.oanda_account_id,
        token=config.oanda_token,
        practice=config.oanda_practice,
    )


def build_tradingeconomics_config(
    config: RuntimeConfig,
    *,
    importance: tuple[int, ...] | None = (3,),
    values: bool = False,
) -> TradingEconomicsConfig:
    """Create a `TradingEconomicsConfig` after checking the API key exists."""

    if not config.trading_economics_api_key:
        raise ConfigValidationError(
            ConfigValidationReport((_error("missing_news_api_key", "TRADING_ECONOMICS_API_KEY is required"),))
        )
    return TradingEconomicsConfig(
        api_key=config.trading_economics_api_key,
        importance=importance,
        values=values,
    )


def load_env_file(path: str | Path) -> dict[str, str]:
    """Load a simple KEY=VALUE file without shell evaluation."""

    out: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = _strip_quotes(value.strip())
    return out


def redact_secret(value: object, *, visible: int = 4) -> str | None:
    """Redact a secret for logs or config reports."""

    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    if len(text) <= visible:
        return "*" * len(text)
    return f"{'*' * max(len(text) - visible, 4)}{text[-visible:]}"


def _env_get(env: Mapping[str, str], prefix: str, name: str, *, default: str) -> str:
    return _env_get_optional(env, prefix, name) or default


def _env_get_optional(env: Mapping[str, str], prefix: str, name: str) -> str | None:
    for candidate in (f"{prefix}{name}", name):
        value = env.get(candidate)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def _split_any(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return _split_csv(value)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip().upper() for item in value if str(item).strip())
    return (str(value).strip().upper(),) if str(value).strip() else ()


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_int(value: object | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _normalize_mode(value: str) -> RuntimeMode:
    return value.strip().lower()  # type: ignore[return-value]


def _normalize_broker(value: str) -> BrokerName:
    return value.strip().lower()  # type: ignore[return-value]


def _valid_symbol(value: str) -> bool:
    clean = "".join(ch for ch in str(value).upper() if ch.isalpha())
    return len(clean) == 6


def _valid_currency(value: str) -> bool:
    return len(str(value)) == 3 and str(value).isalpha()


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _error(code: str, message: str) -> ConfigIssue:
    return ConfigIssue("error", code, message)


def _warning(code: str, message: str) -> ConfigIssue:
    return ConfigIssue("warning", code, message)
