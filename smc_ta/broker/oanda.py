"""OANDA v20 REST broker adapter and candle source.

The adapter uses only Python stdlib HTTP so the core package stays light. It is
intended for demo-first integration. Validate every account, instrument, and
order setting against your OANDA division before live trading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, Position, utc_now
from smc_ta.validation import normalize_ohlcv


class OandaApiError(RuntimeError):
    """Raised when OANDA returns an API error."""


@dataclass(frozen=True)
class OandaConfig:
    """OANDA connection settings."""

    account_id: str
    token: str
    practice: bool = True
    timeout: float = 20.0

    @property
    def base_url(self) -> str:
        host = "api-fxpractice.oanda.com" if self.practice else "api-fxtrade.oanda.com"
        return f"https://{host}/v3"


def oanda_instrument(symbol: str) -> str:
    """Convert EURUSD into OANDA's EUR_USD format."""

    clean = "".join(ch for ch in symbol.upper() if ch.isalpha())[:6]
    if len(clean) != 6:
        raise ValueError(f"cannot convert symbol to OANDA instrument: {symbol}")
    return f"{clean[:3]}_{clean[3:]}"


def _symbol_from_oanda(instrument: str) -> str:
    return instrument.replace("_", "").upper()


class OandaClient:
    """Small JSON client for OANDA v20 REST."""

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
        query = f"?{urlencode(params)}" if params else ""
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.config.base_url}{path}{query}",
            data=body,
            method=method.upper(),
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept-Datetime-Format": "RFC3339",
            },
        )
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                data = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OandaApiError(f"OANDA API error {exc.code}: {detail}") from exc
        return json.loads(data) if data else {}


class OandaBroker:
    """BrokerAdapter implementation for OANDA v20 REST."""

    def __init__(self, config: OandaConfig) -> None:
        self.config = config
        self.client = OandaClient(config)

    def get_account(self) -> AccountState:
        response = self.client.request("GET", f"/accounts/{self.config.account_id}/summary")
        account = response["account"]
        balance = float(account["balance"])
        nav = float(account.get("NAV", balance))
        margin_used = float(account.get("marginUsed", 0.0))
        return AccountState(
            balance=balance,
            equity=nav,
            margin_used=margin_used,
            free_margin=nav - margin_used,
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

    def place_order(self, request: OrderRequest, *, market_price: float) -> OrderFill:
        units = int(round(request.units))
        if request.side == "sell":
            units = -units
        order: dict[str, Any] = {
            "type": "MARKET",
            "instrument": oanda_instrument(request.symbol),
            "units": str(units),
            "positionFill": "DEFAULT",
            "clientExtensions": {"id": request.client_order_id},
        }
        if request.stop_loss is not None:
            order["stopLossOnFill"] = {"price": f"{request.stop_loss:.5f}"}
        if request.take_profit is not None:
            order["takeProfitOnFill"] = {"price": f"{request.take_profit:.5f}"}

        response = self.client.request(
            "POST",
            f"/accounts/{self.config.account_id}/orders",
            payload={"order": order},
        )
        fill_tx = response.get("orderFillTransaction") or response.get("orderCreateTransaction", {})
        price = float(fill_tx.get("price", market_price))
        filled_units = abs(float(fill_tx.get("units", units)))
        return OrderFill(
            order_id=str(fill_tx.get("id", request.client_order_id)),
            symbol=request.symbol.upper(),
            side=request.side,
            units=filled_units,
            price=price,
            spread=0.0,
            slippage=abs(price - market_price),
            commission=float(fill_tx.get("commission", 0.0)),
            timestamp=_parse_oanda_time(fill_tx.get("time")),
            client_order_id=request.client_order_id,
        )

    def close_position(self, position_id: str, *, market_price: float) -> OrderFill:
        response = self.client.request(
            "PUT",
            f"/accounts/{self.config.account_id}/trades/{position_id}/close",
            payload={"units": "ALL"},
        )
        fill_tx = response.get("orderFillTransaction", {})
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
            params["from"] = pd.Timestamp(start).tz_convert("UTC").isoformat()
        if end is not None:
            params["to"] = pd.Timestamp(end).tz_convert("UTC").isoformat()
        response = self.client.request(
            "GET",
            f"/instruments/{oanda_instrument(symbol)}/candles",
            params=params,
        )
        return _candles_to_frame(response.get("candles", []))


def _parse_oanda_time(value: str | None) -> datetime:
    if not value:
        return utc_now()
    return pd.Timestamp(value).to_pydatetime()


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
                "time": pd.Timestamp(candle["time"]),
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

