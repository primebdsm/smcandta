"""Trade lifecycle persistence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from smc_ta.lifecycle.models import TradeLifecycleRecord


class TradeLifecycleStore(Protocol):
    """Persistence contract for lifecycle records."""

    def save(self, record: TradeLifecycleRecord) -> None:
        """Insert or update a lifecycle record."""

    def get(self, trade_id: str) -> TradeLifecycleRecord | None:
        """Return one lifecycle record by ID."""

    def list_records(
        self,
        *,
        symbol: str | None = None,
        state: str | None = None,
    ) -> list[TradeLifecycleRecord]:
        """Return lifecycle records ordered by creation time."""


class MemoryTradeLifecycleStore:
    """In-memory lifecycle store for tests and single-process demo loops."""

    def __init__(self) -> None:
        self._records: dict[str, TradeLifecycleRecord] = {}

    def save(self, record: TradeLifecycleRecord) -> None:
        self._records[record.trade_id] = record

    def get(self, trade_id: str) -> TradeLifecycleRecord | None:
        return self._records.get(trade_id)

    def list_records(
        self,
        *,
        symbol: str | None = None,
        state: str | None = None,
    ) -> list[TradeLifecycleRecord]:
        records = sorted(self._records.values(), key=lambda record: record.created_at)
        if symbol is not None:
            records = [record for record in records if record.symbol == symbol.upper()]
        if state is not None:
            records = [record for record in records if record.state == state]
        return records


class SQLiteTradeLifecycleStore:
    """SQLite lifecycle store for demo/live audit trails."""

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
                CREATE TABLE IF NOT EXISTS trade_lifecycles (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    signal_timestamp TEXT,
                    setup_name TEXT,
                    confidence REAL,
                    client_order_id TEXT,
                    broker_order_id TEXT,
                    position_id TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    units REAL,
                    filled_units REAL,
                    closed_units REAL,
                    average_entry_price REAL,
                    exit_price REAL,
                    realized_pnl REAL,
                    reasons TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    history TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_lifecycles_symbol ON trade_lifecycles(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_lifecycles_state ON trade_lifecycles(state)")

    def save(self, record: TradeLifecycleRecord) -> None:
        payload = record.to_dict()
        row = {
            **payload,
            "reasons": json.dumps(payload["reasons"]),
            "metadata": json.dumps(payload["metadata"], default=str),
            "history": json.dumps(payload["history"], default=str),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_lifecycles (
                    trade_id, symbol, side, state, created_at, updated_at,
                    signal_timestamp, setup_name, confidence, client_order_id,
                    broker_order_id, position_id, entry_price, stop_loss,
                    take_profit, units, filled_units, closed_units,
                    average_entry_price, exit_price, realized_pnl, reasons,
                    metadata, history
                ) VALUES (
                    :trade_id, :symbol, :side, :state, :created_at, :updated_at,
                    :signal_timestamp, :setup_name, :confidence, :client_order_id,
                    :broker_order_id, :position_id, :entry_price, :stop_loss,
                    :take_profit, :units, :filled_units, :closed_units,
                    :average_entry_price, :exit_price, :realized_pnl, :reasons,
                    :metadata, :history
                )
                ON CONFLICT(trade_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    side=excluded.side,
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    signal_timestamp=excluded.signal_timestamp,
                    setup_name=excluded.setup_name,
                    confidence=excluded.confidence,
                    client_order_id=excluded.client_order_id,
                    broker_order_id=excluded.broker_order_id,
                    position_id=excluded.position_id,
                    entry_price=excluded.entry_price,
                    stop_loss=excluded.stop_loss,
                    take_profit=excluded.take_profit,
                    units=excluded.units,
                    filled_units=excluded.filled_units,
                    closed_units=excluded.closed_units,
                    average_entry_price=excluded.average_entry_price,
                    exit_price=excluded.exit_price,
                    realized_pnl=excluded.realized_pnl,
                    reasons=excluded.reasons,
                    metadata=excluded.metadata,
                    history=excluded.history
                """,
                row,
            )

    def get(self, trade_id: str) -> TradeLifecycleRecord | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trade_lifecycles WHERE trade_id = ?", (trade_id,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_records(
        self,
        *,
        symbol: str | None = None,
        state: str | None = None,
    ) -> list[TradeLifecycleRecord]:
        query = "SELECT * FROM trade_lifecycles"
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if symbol is not None:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol.upper()
        if state is not None:
            clauses.append("state = :state")
            params["state"] = state
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def to_frame(self, *, symbol: str | None = None, state: str | None = None) -> pd.DataFrame:
        """Return current lifecycle rows as a DataFrame."""

        records = self.list_records(symbol=symbol, state=state)
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "trade_id": record.trade_id,
                    "symbol": record.symbol,
                    "side": record.side,
                    "state": record.state,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "setup_name": record.setup_name,
                    "confidence": record.confidence,
                    "units": record.units,
                    "filled_units": record.filled_units,
                    "closed_units": record.closed_units,
                    "realized_pnl": record.realized_pnl,
                    "reasons": ";".join(record.reasons),
                }
                for record in records
            ]
        )


def _row_to_record(row: sqlite3.Row) -> TradeLifecycleRecord:
    payload = dict(row)
    payload["reasons"] = json.loads(payload["reasons"])
    payload["metadata"] = json.loads(payload["metadata"])
    payload["history"] = json.loads(payload["history"])
    return TradeLifecycleRecord.from_dict(payload)
