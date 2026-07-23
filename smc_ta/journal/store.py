"""CSV-backed trading journal."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class JournalEntry:
    """One strategy or execution journal event."""

    timestamp: pd.Timestamp
    symbol: str
    event_type: str
    side: str | None = None
    price: float | None = None
    units: float | None = None
    pnl: float | None = None
    confidence: float | None = None
    reasons: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TradeJournal:
    """Append-only CSV journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: JournalEntry) -> None:
        """Append one entry."""

        record = asdict(entry)
        record["timestamp"] = pd.Timestamp(record["timestamp"]).isoformat()
        record["metadata"] = repr(record["metadata"])
        frame = pd.DataFrame([record])
        header = not self.path.exists()
        frame.to_csv(self.path, mode="a", header=header, index=False)

    def append_signal(self, symbol: str, timestamp: pd.Timestamp, signal: pd.Series, *, notes: str | None = None) -> None:
        """Append a confluence signal snapshot."""

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

    def read(self) -> pd.DataFrame:
        """Read the journal file."""

        if not self.path.exists():
            return pd.DataFrame()
        return pd.read_csv(self.path, parse_dates=["timestamp"])

