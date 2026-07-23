"""OANDA v20 REST broker adapter and candle source.

The adapter uses only Python stdlib HTTP so the core package stays light. It is
intended for demo-first integration. Validate every account, instrument, and
order setting against your OANDA division before live trading.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, OrderSide, Position, utc_now
from smc_ta.validation import normalize_ohlcv


class OandaApiError(RuntimeError):
    """Raised when OANDA returns an API error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        self.response = response or {}


class OandaRateLimitError(OandaApiError):
    """Raised when OANDA asks the client to slow down."""


class OandaConnectionError(OandaApiError):
    """Raised when the REST request cannot reach OANDA."""


class OandaOrderRejected(OandaApiError):
    """Raised when OANDA rejects, cancels, or does not fill an order."""


class OandaInstrumentValidationError(ValueError):
    """Raised when an order violates OANDA instrument metadata."""


class OandaPriceValidationError(RuntimeError):
    """Raised when current OANDA pricing is unsafe for execution."""


@dataclass(frozen=True)
class OandaConfig:
    """OANDA connection and execution-safety settings."""

    account_id: str
    token: str
    practice: bool = True
    timeout: float = 20.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    retry_methods: tuple[str, ...] = ("GET",)
    market_order_time_in_force: str = "FOK"
    position_fill: str = "DEFAULT"
    use_client_extensions: bool = True
    price_check_before_order: bool = True
    enforce_tradeable_price: bool = True
    max_price_age_seconds: float = 15.0
    max_spread_pips: float | None = None
    max_order_slippage_pips: float | None = None

    @property
    def base_url(self) -> str:
        host = "api-fxpractice.oanda.com" if self.practice else "api-fxtrade.oanda.com"
        return f"https://{host}/v3"


@dataclass(frozen=True)
class OandaInstrumentSpec:
    """Tradable instrument metadata from OANDA account instruments."""

    name: str
    display_name: str = ""
    instrument_type: str = ""
    display_precision: int = 5
    trade_units_precision: int = 0
    pip_location: int = -4
    minimum_trade_size: float = 1.0
    maximum_order_units: float | None = None
    maximum_position_size: float | None = None
    margin_rate: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "OandaInstrumentSpec":
        """Build metadata from an OANDA instrument record."""

        return cls(
            name=str(payload["name"]).upper(),
            display_name=str(payload.get("displayName", "")),
            instrument_type=str(payload.get("type", "")),
            display_precision=int(payload.get("displayPrecision", 5)),
            trade_units_precision=int(payload.get("tradeUnitsPrecision", 0)),
            pip_location=int(payload.get("pipLocation", -4)),
            minimum_trade_size=float(payload.get("minimumTradeSize", 1.0)),
            maximum_order_units=_optional_float(payload.get("maximumOrderUnits")),
            maximum_position_size=_optional_float(payload.get("maximumPositionSize")),
            margin_rate=_optional_float(payload.get("marginRate")),
            metadata=dict(payload),
        )

    @property
    def symbol(self) -> str:
        return _symbol_from_oanda(self.name)

    @property
    def pip_size(self) -> float:
        return 10.0**self.pip_location

    @property
    def unit_step(self) -> float:
        return 10.0 ** (-self.trade_units_precision)

    def format_price(self, price: float) -> str:
        """Format a price using OANDA display precision."""

        return f"{float(price):.{self.display_precision}f}"

    def format_signed_units(self, units: float, side: OrderSide) -> str:
        """Validate and format a signed unit quantity for an order request."""

        self.validate_units(units)
        signed = -abs(float(units)) if side == "sell" else abs(float(units))
        if self.trade_units_precision == 0:
            return str(int(round(signed)))
        return f"{signed:.{self.trade_units_precision}f}"

    def validate_units(self, units: float) -> None:
        """Raise if a unit quantity violates this instrument's trade rules."""

        abs_units = abs(float(units))
        if abs_units <= 0:
            raise OandaInstrumentValidationError(f"{self.name} units must be positive")
        if abs_units < self.minimum_trade_size:
            raise OandaInstrumentValidationError(
                f"{self.name} units {abs_units:g} below minimumTradeSize {self.minimum_trade_size:g}"
            )
        if self.maximum_order_units and abs_units > self.maximum_order_units:
            raise OandaInstrumentValidationError(
                f"{self.name} units {abs_units:g} above maximumOrderUnits {self.maximum_order_units:g}"
            )
        scaled = abs_units / self.unit_step
        if abs(scaled - round(scaled)) > 1e-9:
            raise OandaInstrumentValidationError(
                f"{self.name} units {abs_units:g} do not match tradeUnitsPrecision {self.trade_units_precision}"
            )


@dataclass(frozen=True)
class OandaPriceSnapshot:
    """Current OANDA price state for an instrument."""

    instrument: str
    symbol: str
    time: pd.Timestamp
    bid: float
    ask: float
    closeout_bid: float
    closeout_ask: float
    status: str
    tradeable: bool
    spread: float
    spread_pips: float

    @classmethod
    def from_api(cls, payload: dict[str, Any], spec: OandaInstrumentSpec) -> "OandaPriceSnapshot":
        """Build a price snapshot from an OANDA pricing record."""

        bid = _first_price(payload.get("bids"))
        ask = _first_price(payload.get("asks"))
        if bid is None or ask is None:
            raise OandaPriceValidationError(f"{spec.name} price record is missing bid/ask prices")
        closeout_bid = float(payload.get("closeoutBid", bid))
        closeout_ask = float(payload.get("closeoutAsk", ask))
        spread = ask - bid
        return cls(
            instrument=spec.name,
            symbol=spec.symbol,
            time=_utc_timestamp(payload.get("time")),
            bid=bid,
            ask=ask,
            closeout_bid=closeout_bid,
            closeout_ask=closeout_ask,
            status=str(payload.get("status", "unknown")),
            tradeable=bool(payload.get("tradeable", False)),
            spread=spread,
            spread_pips=spread / spec.pip_size if spec.pip_size else 0.0,
        )

    def execution_price(self, side: OrderSide) -> float:
        return self.ask if side == "buy" else self.bid

    def age_seconds(self, now: pd.Timestamp | None = None) -> float:
        current = pd.Timestamp.now(tz="UTC") if now is None else _utc_timestamp(now)
        return max(0.0, (current - self.time).total_seconds())


@dataclass(frozen=True)
class OandaReadinessCheck:
    """One OANDA practice readiness check."""

    component: str
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class OandaPracticeReadinessReport:
    """Non-trading OANDA practice readiness report."""

    checks: tuple[OandaReadinessCheck, ...]
    account: AccountState | None = None
    instruments: tuple[OandaInstrumentSpec, ...] = ()
    prices: tuple[OandaPriceSnapshot, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(check.blocking for check in self.checks)

    def summary(self) -> str:
        if self.ok:
            warnings = [f"warning:{check.code}" for check in self.checks if check.severity == "warning"]
            return ";".join(warnings) if warnings else "oanda_practice_ready"
        return ";".join(check.code for check in self.checks if check.blocking)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(check) for check in self.checks])


def oanda_instrument(symbol: str) -> str:
    """Convert EURUSD into OANDA's EUR_USD format."""

    clean = "".join(ch for ch in symbol.upper() if ch.isalpha())[:6]
    if len(clean) != 6:
        raise ValueError(f"cannot convert symbol to OANDA instrument: {symbol}")
    return f"{clean[:3]}_{clean[3:]}"


def _symbol_from_oanda(instrument: str) -> str:
    return instrument.replace("_", "").upper()


class OandaClient:
    """Small JSON client for OANDA v20 REST with conservative retries."""

    def __init__(self, config: OandaConfig) -> None:
        self.config = config

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method_upper = method.upper()
        attempts = max(0, self.config.max_retries) + 1
        for attempt in range(attempts):
            try:
                return self._request_once(method_upper, path, params=params, payload=payload)
            except HTTPError as exc:
                error = _api_error_from_http_error(exc)
                if self._should_retry(method_upper, exc.code, attempt, attempts):
                    time.sleep(_retry_delay(exc, self.config.retry_backoff_seconds, attempt))
                    continue
                raise error from exc
            except (TimeoutError, URLError) as exc:
                if self._should_retry(method_upper, None, attempt, attempts):
                    time.sleep(self.config.retry_backoff_seconds * (2**attempt))
                    continue
                raise OandaConnectionError(f"OANDA request failed: {exc}") from exc
        raise OandaConnectionError("OANDA request failed after retries")

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.config.base_url}{path}{query}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept-Datetime-Format": "RFC3339",
            },
        )
        with urlopen(request, timeout=self.config.timeout) as response:
            data = response.read().decode("utf-8")
        return json.loads(data) if data else {}

    def _should_retry(self, method: str, status_code: int | None, attempt: int, attempts: int) -> bool:
        if attempt >= attempts - 1:
            return False
        if method not in {item.upper() for item in self.config.retry_methods}:
            return False
        return status_code is None or status_code in self.config.retry_statuses


class OandaBroker:
    """BrokerAdapter implementation for OANDA v20 REST."""

    def __init__(self, config: OandaConfig) -> None:
        self.config = config
        self.client = OandaClient(config)
        self._instrument_cache: dict[str, OandaInstrumentSpec] = {}

    def get_account(self) -> AccountState:
        response = self.client.request("GET", f"/accounts/{self.config.account_id}/summary")
        account = response["account"]
        balance = float(account["balance"])
        nav = float(account.get("NAV", balance))
        margin_used = float(account.get("marginUsed", 0.0))
        free_margin = float(account.get("marginAvailable", nav - margin_used))
        return AccountState(
            balance=balance,
            equity=nav,
            margin_used=margin_used,
            free_margin=free_margin,
            currency=account.get("currency", "USD"),
        )

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        response = self.client.request("GET", f"/accounts/{self.config.account_id}/openPositions")
        positions: list[Position] = []
        symbol_filter = symbol.upper() if symbol else None
        for raw in response.get("positions", []):
            raw_symbol = _symbol_from_oanda(raw["instrument"])
            if symbol_filter and raw_symbol != symbol_filter:
                continue
            for side_name, position_side in (("long", "long"), ("short", "short")):
                side_data = raw.get(side_name, {})
                units = abs(float(side_data.get("units", 0.0)))
                if units <= 0:
                    continue
                trade_ids = side_data.get("tradeIDs", [])
                positions.append(
                    Position(
                        position_id=str(trade_ids[0]) if trade_ids else f"{raw_symbol}_{side_name}",
                        symbol=raw_symbol,
                        side=position_side,
                        units=units,
                        entry_price=float(side_data.get("averagePrice", 0.0)),
                        opened_at=utc_now(),
                        metadata={"oanda_trade_ids": trade_ids},
                    )
                )
        return positions

    def get_instruments(self, symbols: list[str] | tuple[str, ...] | None = None) -> list[OandaInstrumentSpec]:
        """Return OANDA account instruments, optionally filtered by symbol."""

        params: dict[str, Any] | None = None
        if symbols:
            params = {"instruments": ",".join(oanda_instrument(symbol) for symbol in symbols)}
        response = self.client.request("GET", f"/accounts/{self.config.account_id}/instruments", params=params)
        specs = [OandaInstrumentSpec.from_api(raw) for raw in response.get("instruments", [])]
        for spec in specs:
            self._instrument_cache[spec.symbol] = spec
        return specs

    def get_instrument_spec(self, symbol: str) -> OandaInstrumentSpec:
        """Return account-specific OANDA instrument metadata."""

        clean = _symbol_from_oanda(oanda_instrument(symbol))
        if clean in self._instrument_cache:
            return self._instrument_cache[clean]
        specs = self.get_instruments((clean,))
        for spec in specs:
            if spec.symbol == clean:
                return spec
        raise OandaInstrumentValidationError(f"{clean} is not available for OANDA account {self.config.account_id}")

    def get_price(self, symbol: str) -> OandaPriceSnapshot:
        """Return and validate the latest OANDA bid/ask snapshot for a symbol."""

        spec = self.get_instrument_spec(symbol)
        response = self.client.request(
            "GET",
            f"/accounts/{self.config.account_id}/pricing",
            params={"instruments": spec.name},
        )
        prices = response.get("prices", [])
        if not prices:
            raise OandaPriceValidationError(f"{spec.name} pricing response was empty")
        snapshot = OandaPriceSnapshot.from_api(prices[0], spec)
        self.validate_price_snapshot(snapshot)
        return snapshot

    def validate_price_snapshot(self, snapshot: OandaPriceSnapshot, *, now: pd.Timestamp | None = None) -> None:
        """Raise when current pricing is stale, not tradeable, or too wide."""

        if self.config.enforce_tradeable_price and not snapshot.tradeable:
            raise OandaPriceValidationError(f"{snapshot.instrument} price is not tradeable: {snapshot.status}")
        if self.config.max_price_age_seconds >= 0:
            age = snapshot.age_seconds(now)
            if age > self.config.max_price_age_seconds:
                raise OandaPriceValidationError(
                    f"{snapshot.instrument} price is stale: {age:.2f}s > {self.config.max_price_age_seconds:.2f}s"
                )
        if self.config.max_spread_pips is not None and snapshot.spread_pips > self.config.max_spread_pips:
            raise OandaPriceValidationError(
                f"{snapshot.instrument} spread {snapshot.spread_pips:.2f} pips above limit {self.config.max_spread_pips:.2f}"
            )

    def practice_readiness(self, symbols: list[str] | tuple[str, ...]) -> OandaPracticeReadinessReport:
        """Run a non-trading OANDA practice readiness probe."""

        checks: list[OandaReadinessCheck] = []
        instruments: list[OandaInstrumentSpec] = []
        prices: list[OandaPriceSnapshot] = []
        if not self.config.practice:
            checks.append(
                OandaReadinessCheck(
                    "config",
                    "not_practice_endpoint",
                    "blocking",
                    "practice readiness must use OANDA practice endpoint",
                )
            )
        account: AccountState | None = None
        try:
            account = self.get_account()
            checks.append(
                OandaReadinessCheck(
                    "account",
                    "account_ok",
                    "info",
                    "OANDA account summary loaded",
                    {"currency": account.currency, "equity": account.equity, "free_margin": account.free_margin},
                )
            )
        except Exception as exc:
            checks.append(
                OandaReadinessCheck(
                    "account",
                    "account_failed",
                    "blocking",
                    str(exc),
                    {"exception_type": type(exc).__name__},
                )
            )

        for symbol in symbols:
            clean = symbol.upper()
            try:
                spec = self.get_instrument_spec(clean)
                instruments.append(spec)
                checks.append(
                    OandaReadinessCheck(
                        "instrument",
                        "instrument_ok",
                        "info",
                        f"{clean} instrument metadata loaded",
                        {
                            "instrument": spec.name,
                            "pip_size": spec.pip_size,
                            "minimum_trade_size": spec.minimum_trade_size,
                            "maximum_order_units": spec.maximum_order_units,
                        },
                    )
                )
                price = self.get_price(clean)
                prices.append(price)
                checks.append(
                    OandaReadinessCheck(
                        "pricing",
                        "price_ok",
                        "info",
                        f"{clean} pricing is tradeable",
                        {
                            "bid": price.bid,
                            "ask": price.ask,
                            "spread_pips": price.spread_pips,
                            "status": price.status,
                        },
                    )
                )
            except Exception as exc:
                checks.append(
                    OandaReadinessCheck(
                        "symbol",
                        "symbol_probe_failed",
                        "blocking",
                        str(exc),
                        {"symbol": clean, "exception_type": type(exc).__name__},
                    )
                )
        if checks and not any(check.blocking for check in checks):
            checks.append(
                OandaReadinessCheck(
                    "readiness",
                    "oanda_practice_ready",
                    "info",
                    "OANDA practice account, instruments, and prices passed readiness probes",
                )
            )
        return OandaPracticeReadinessReport(
            checks=tuple(checks),
            account=account,
            instruments=tuple(instruments),
            prices=tuple(prices),
        )

    def place_order(self, request: OrderRequest, *, market_price: float) -> OrderFill:
        spec = self.get_instrument_spec(request.symbol)
        signed_units = spec.format_signed_units(request.units, request.side)
        price_snapshot: OandaPriceSnapshot | None = None
        reference_price = market_price
        if self.config.price_check_before_order:
            price_snapshot = self.get_price(request.symbol)
            reference_price = price_snapshot.execution_price(request.side)
        order: dict[str, Any] = {
            "type": "MARKET",
            "instrument": spec.name,
            "units": signed_units,
            "timeInForce": self.config.market_order_time_in_force,
            "positionFill": self.config.position_fill,
        }
        if self.config.use_client_extensions:
            order["clientExtensions"] = {"id": request.client_order_id}
        if request.stop_loss is not None:
            order["stopLossOnFill"] = {"price": spec.format_price(request.stop_loss)}
        if request.take_profit is not None:
            order["takeProfitOnFill"] = {"price": spec.format_price(request.take_profit)}
        if price_snapshot is not None and self.config.max_order_slippage_pips is not None:
            price_bound = _price_bound(
                side=request.side,
                reference_price=reference_price,
                pip_size=spec.pip_size,
                max_slippage_pips=self.config.max_order_slippage_pips,
            )
            order["priceBound"] = spec.format_price(price_bound)

        response = self.client.request(
            "POST",
            f"/accounts/{self.config.account_id}/orders",
            payload={"order": order},
        )
        fill_tx = response.get("orderFillTransaction")
        if not fill_tx:
            raise _order_rejected_from_response(response, fallback_message="OANDA order did not fill")
        price = float(fill_tx.get("price", reference_price))
        filled_units = abs(float(fill_tx.get("units", signed_units)))
        trade_opened_id = _trade_opened_id(fill_tx)
        return OrderFill(
            order_id=str(fill_tx.get("id", request.client_order_id)),
            symbol=request.symbol.upper(),
            side=request.side,
            units=filled_units,
            price=price,
            spread=price_snapshot.spread if price_snapshot is not None else 0.0,
            slippage=abs(price - reference_price),
            commission=float(fill_tx.get("commission", 0.0)),
            timestamp=_parse_oanda_time(fill_tx.get("time")),
            client_order_id=request.client_order_id,
            metadata={
                "oanda_transaction_id": str(fill_tx.get("id", "")),
                "oanda_trade_opened_id": trade_opened_id,
                "oanda_trade_reduced_id": _trade_reduced_id(fill_tx),
                "oanda_trade_closed_ids": _trade_closed_ids(fill_tx),
                "oanda_related_transaction_ids": tuple(str(item) for item in response.get("relatedTransactionIDs", [])),
            },
        )

    def close_position(self, position_id: str, *, market_price: float) -> OrderFill:
        response = self.client.request(
            "PUT",
            f"/accounts/{self.config.account_id}/trades/{position_id}/close",
            payload={"units": "ALL"},
        )
        fill_tx = response.get("orderFillTransaction")
        if not fill_tx:
            raise _order_rejected_from_response(response, fallback_message="OANDA close request did not fill")
        instrument = fill_tx.get("instrument", "")
        side = "sell" if float(fill_tx.get("units", 0.0)) < 0 else "buy"
        price = float(fill_tx.get("price", market_price))
        return OrderFill(
            order_id=str(fill_tx.get("id", position_id)),
            symbol=_symbol_from_oanda(instrument) if instrument else "",
            side=side,
            units=abs(float(fill_tx.get("units", 0.0))),
            price=price,
            spread=0.0,
            slippage=abs(price - market_price),
            commission=float(fill_tx.get("commission", 0.0)),
            timestamp=_parse_oanda_time(fill_tx.get("time")),
            metadata={
                "oanda_transaction_id": str(fill_tx.get("id", "")),
                "oanda_trade_opened_id": _trade_opened_id(fill_tx),
                "oanda_trade_reduced_id": _trade_reduced_id(fill_tx),
                "oanda_trade_closed_ids": _trade_closed_ids(fill_tx),
                "oanda_related_transaction_ids": tuple(str(item) for item in response.get("relatedTransactionIDs", [])),
            },
        )


class OandaCandleDataSource:
    """Historical candle downloader for OANDA v20 instruments."""

    def __init__(self, config: OandaConfig) -> None:
        self.config = config
        self.client = OandaClient(config)

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {
            "granularity": timeframe,
            "price": "MBA",
        }
        if limit is not None:
            params["count"] = limit
        if start is not None:
            params["from"] = _utc_timestamp(start).isoformat()
        if end is not None:
            params["to"] = _utc_timestamp(end).isoformat()
        response = self.client.request(
            "GET",
            f"/instruments/{oanda_instrument(symbol)}/candles",
            params=params,
        )
        return _candles_to_frame(response.get("candles", []))


def _parse_oanda_time(value: str | None) -> datetime:
    if not value:
        return utc_now()
    return _utc_timestamp(value).to_pydatetime()


def _price_component(candle: dict[str, Any], component: str) -> dict[str, str]:
    if component in candle:
        return candle[component]
    return candle.get("mid") or candle.get("bid") or candle.get("ask") or {}


def _candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for candle in candles:
        if not candle.get("complete", True):
            continue
        mid = _price_component(candle, "mid")
        bid = _price_component(candle, "bid")
        ask = _price_component(candle, "ask")
        spread = None
        if bid and ask:
            spread = float(ask["c"]) - float(bid["c"])
        records.append(
            {
                "time": _utc_timestamp(candle["time"]),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "tick_volume": float(candle.get("volume", 0.0)),
                "spread": spread,
            }
        )
    if not records:
        return pd.DataFrame(columns=["open", "high", "low", "close", "tick_volume", "spread"])
    frame = pd.DataFrame.from_records(records).set_index("time")
    return normalize_ohlcv(frame)


def _api_error_from_http_error(exc: HTTPError) -> OandaApiError:
    detail = exc.read().decode("utf-8", errors="replace")
    payload = _json_payload(detail)
    error_code = _payload_error_code(payload)
    error_message = _payload_error_message(payload) or detail
    message = f"OANDA API error {exc.code}"
    if error_code:
        message += f" {error_code}"
    if error_message:
        message += f": {error_message}"
    kwargs = {
        "status_code": exc.code,
        "error_code": error_code,
        "error_message": error_message,
        "response": payload,
    }
    if exc.code == 429:
        return OandaRateLimitError(message, **kwargs)
    if _has_order_rejection(payload):
        return OandaOrderRejected(message, **kwargs)
    return OandaApiError(message, **kwargs)


def _payload_error_code(payload: dict[str, Any]) -> str | None:
    if not payload:
        return None
    for key in ("errorCode", "rejectReason", "reason"):
        if payload.get(key):
            return str(payload[key])
    for tx_key in ("orderRejectTransaction", "orderCancelTransaction"):
        tx = payload.get(tx_key)
        if isinstance(tx, dict) and tx.get("reason"):
            return str(tx["reason"])
    return None


def _payload_error_message(payload: dict[str, Any]) -> str | None:
    if not payload:
        return None
    for key in ("errorMessage", "message"):
        if payload.get(key):
            return str(payload[key])
    for tx_key in ("orderRejectTransaction", "orderCancelTransaction"):
        tx = payload.get(tx_key)
        if isinstance(tx, dict) and tx.get("reason"):
            return str(tx["reason"])
    return None


def _has_order_rejection(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("orderRejectTransaction", "orderCancelTransaction", "orderCancelRejectTransaction"))


def _order_rejected_from_response(response: dict[str, Any], *, fallback_message: str) -> OandaOrderRejected:
    reason = _payload_error_message(response) or fallback_message
    return OandaOrderRejected(reason, error_code=_payload_error_code(response), error_message=reason, response=response)


def _trade_opened_id(fill_tx: dict[str, Any]) -> str | None:
    for key in ("tradeOpenedID", "tradeID"):
        if fill_tx.get(key):
            return str(fill_tx[key])
    opened = fill_tx.get("tradeOpened")
    if isinstance(opened, dict):
        for key in ("tradeID", "id"):
            if opened.get(key):
                return str(opened[key])
    opened_list = fill_tx.get("tradesOpened")
    if isinstance(opened_list, list):
        for item in opened_list:
            if isinstance(item, dict):
                for key in ("tradeID", "id"):
                    if item.get(key):
                        return str(item[key])
    return None


def _trade_reduced_id(fill_tx: dict[str, Any]) -> str | None:
    for key in ("tradeReducedID",):
        if fill_tx.get(key):
            return str(fill_tx[key])
    reduced = fill_tx.get("tradeReduced")
    if isinstance(reduced, dict):
        for key in ("tradeID", "id"):
            if reduced.get(key):
                return str(reduced[key])
    return None


def _trade_closed_ids(fill_tx: dict[str, Any]) -> tuple[str, ...]:
    closed_ids: list[str] = []
    for key in ("tradeClosedIDs",):
        value = fill_tx.get(key)
        if isinstance(value, list):
            closed_ids.extend(str(item) for item in value)
    closed_list = fill_tx.get("tradesClosed")
    if isinstance(closed_list, list):
        for item in closed_list:
            if isinstance(item, dict):
                for key in ("tradeID", "id"):
                    if item.get(key):
                        closed_ids.append(str(item[key]))
                        break
    return tuple(dict.fromkeys(closed_ids))


def _json_payload(detail: str) -> dict[str, Any]:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _retry_delay(exc: HTTPError, backoff: float, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return max(0.0, backoff * (2**attempt))


def _price_bound(*, side: OrderSide, reference_price: float, pip_size: float, max_slippage_pips: float) -> float:
    offset = pip_size * max(0.0, max_slippage_pips)
    return reference_price + offset if side == "buy" else reference_price - offset


def _first_price(values: object) -> float | None:
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict) or "price" not in first:
        return None
    return float(first["price"])


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _utc_timestamp(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
