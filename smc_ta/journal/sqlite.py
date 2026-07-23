"""SQLite-backed trading journal."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from smc_ta.broker.models import OrderFill
from smc_ta.journal.store import JournalEntry


class SQLiteTradeJournal:
    """SQLite journal for signals, fills, risk blocks, and notes."""

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
                CREATE TABLE IF NOT EXISTS journal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    side TEXT,
                    price REAL,
                    units REAL,
                    pnl REAL,
                    confidence REAL,
                    reasons TEXT,
                    notes TEXT,
                    metadata TEXT
                )
                """
            )

    def append(self, entry: JournalEntry) -> None:
        record = {
            "timestamp": pd.Timestamp(entry.timestamp).isoformat(),
            "symbol": entry.symbol.upper(),
            "event_type": entry.event_type,
            "side": entry.side,
            "price": entry.price,
            "units": entry.units,
            "pnl": entry.pnl,
            "confidence": entry.confidence,
            "reasons": entry.reasons,
            "notes": entry.notes,
            "metadata": json.dumps(entry.metadata),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO journal_events (
                    timestamp, symbol, event_type, side, price, units, pnl,
                    confidence, reasons, notes, metadata
                ) VALUES (
                    :timestamp, :symbol, :event_type, :side, :price, :units, :pnl,
                    :confidence, :reasons, :notes, :metadata
                )
                """,
                record,
            )

    def append_signal(self, symbol: str, timestamp: pd.Timestamp, signal: pd.Series, *, notes: str | None = None) -> None:
        self.append(
            JournalEntry(
                timestamp=timestamp,
                symbol=symbol.upper(),
                event_type="signal",
                side=str(signal.get("side")),
                price=float(signal["entry_reference"]) if pd.notna(signal.get("entry_reference")) else None,
                confidence=float(signal.get("confidence", 0.0) or 0.0),
                reasons=str(signal.get("reasons", "")),
                notes=notes,
            )
        )

    def append_fill(self, fill: OrderFill, *, event_type: str = "fill", notes: str | None = None) -> None:
        self.append(
            JournalEntry(
                timestamp=pd.Timestamp(fill.timestamp),
                symbol=fill.symbol,
                event_type=event_type,
                side=fill.side,
                price=fill.price,
                units=fill.units,
                notes=notes,
                metadata={
                    "order_id": fill.order_id,
                    "spread": fill.spread,
                    "slippage": fill.slippage,
                    "commission": fill.commission,
                    "client_order_id": fill.client_order_id,
                },
            )
        )

    def read(self, *, symbol: str | None = None, event_type: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM journal_events"
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol.upper()
        if event_type:
            clauses.append("event_type = :event_type")
            params["event_type"] = event_type
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params, parse_dates=["timestamp"])

