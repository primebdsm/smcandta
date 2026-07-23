"""Expected-position ledgers for reconciliation."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Protocol

import pandas as pd

from smc_ta.broker.models import Position


class PositionLedger(Protocol):
    """Expected-position ledger protocol."""

    def record_open_position(self, position: Position) -> None:
        """Record or replace an expected open position."""

    def record_closed_position(self, position_id: str, *, exit_price: float | None = None, closed_at=None) -> None:
        """Mark an expected position as closed."""

    def open_positions(self, symbol: str | None = None) -> list[Position]:
        """Return expected open positions."""


class MemoryPositionLedger:
    """In-memory expected-position ledger."""

    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions: dict[str, Position] = {}
        for position in positions or []:
            self.record_open_position(position)

    def record_open_position(self, position: Position) -> None:
        self.positions[position.position_id] = deepcopy(position)

    def record_closed_position(self, position_id: str, *, exit_price: float | None = None, closed_at=None) -> None:
        if position_id not in self.positions:
            return
        position = self.positions[position_id]
        position.exit_price = exit_price
        position.closed_at = pd.Timestamp(closed_at).to_pydatetime() if closed_at is not None else pd.Timestamp.now(tz="UTC").to_pydatetime()

    def open_positions(self, symbol: str | None = None) -> list[Position]:
        symbol_filter = symbol.upper() if symbol else None
        return [
            deepcopy(position)
            for position in self.positions.values()
            if position.is_open and (symbol_filter is None or position.symbol == symbol_filter)
        ]


class SQLitePositionLedger:
    """SQLite expected-position ledger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expected_positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    units REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    closed_at TEXT,
                    exit_price REAL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def record_open_position(self, position: Position) -> None:
        record = {
            "position_id": position.position_id,
            "symbol": position.symbol.upper(),
            "side": position.side,
            "units": position.units,
            "entry_price": position.entry_price,
            "opened_at": pd.Timestamp(position.opened_at).isoformat(),
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "closed_at": pd.Timestamp(position.closed_at).isoformat() if position.closed_at else None,
            "exit_price": position.exit_price,
            "realized_pnl": position.realized_pnl,
            "metadata": json.dumps(position.metadata),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO expected_positions (
                    position_id, symbol, side, units, entry_price, opened_at,
                    stop_loss, take_profit, closed_at, exit_price, realized_pnl, metadata
                ) VALUES (
                    :position_id, :symbol, :side, :units, :entry_price, :opened_at,
                    :stop_loss, :take_profit, :closed_at, :exit_price, :realized_pnl, :metadata
                )
                ON CONFLICT(position_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    units = excluded.units,
                    entry_price = excluded.entry_price,
                    opened_at = excluded.opened_at,
                    stop_loss = excluded.stop_loss,
                    take_profit = excluded.take_profit,
                    closed_at = excluded.closed_at,
                    exit_price = excluded.exit_price,
                    realized_pnl = excluded.realized_pnl,
                    metadata = excluded.metadata
                """,
                record,
            )

    def record_closed_position(self, position_id: str, *, exit_price: float | None = None, closed_at=None) -> None:
        closed_timestamp = pd.Timestamp(closed_at).isoformat() if closed_at is not None else pd.Timestamp.now(tz="UTC").isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE expected_positions
                SET closed_at = :closed_at, exit_price = :exit_price
                WHERE position_id = :position_id
                """,
                {
                    "position_id": position_id,
                    "closed_at": closed_timestamp,
                    "exit_price": exit_price,
                },
            )

    def open_positions(self, symbol: str | None = None) -> list[Position]:
        query = "SELECT * FROM expected_positions WHERE closed_at IS NULL"
        params: dict[str, object] = {}
        if symbol:
            query += " AND symbol = :symbol"
            params["symbol"] = symbol.upper()
        query += " ORDER BY opened_at"
        with self._connect() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
        return [_position_from_record(dict(zip(columns, row))) for row in rows]


def _position_from_record(record: dict[str, object]) -> Position:
    return Position(
        position_id=str(record["position_id"]),
        symbol=str(record["symbol"]).upper(),
        side=str(record["side"]),  # type: ignore[arg-type]
        units=float(record["units"]),
        entry_price=float(record["entry_price"]),
        opened_at=pd.Timestamp(record["opened_at"]).to_pydatetime(),
        stop_loss=float(record["stop_loss"]) if record["stop_loss"] is not None else None,
        take_profit=float(record["take_profit"]) if record["take_profit"] is not None else None,
        closed_at=pd.Timestamp(record["closed_at"]).to_pydatetime() if record["closed_at"] is not None else None,
        exit_price=float(record["exit_price"]) if record["exit_price"] is not None else None,
        realized_pnl=float(record["realized_pnl"]),
        metadata=json.loads(str(record["metadata"] or "{}")),
    )
