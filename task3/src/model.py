"""Validated, source-attributed trading calendar records."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

FIELDS = [
    "exchange", "date", "holiday", "status", "close_time_local", "timezone",
    "window_start", "window_end", "source_url", "source_basis", "notes",
]


@dataclass(frozen=True)
class Holiday:
    exchange: str
    date: str
    holiday: str
    status: str
    close_time_local: str = ""
    timezone: str = ""
    window_start: str = ""
    window_end: str = ""
    source_url: str = ""
    source_basis: str = "OFFICIAL"
    notes: str = ""

    def validate(self) -> None:
        if self.exchange not in {"NASDAQ", "NYSE", "CME", "OPRA"}:
            raise ValueError(f"Unknown exchange: {self.exchange}")
        date.fromisoformat(self.date)
        for edge in (self.window_start, self.window_end):
            if edge:
                date.fromisoformat(edge)
        if self.status not in {"CLOSED", "EARLY_CLOSE", "HOLIDAY_HOURS"}:
            raise ValueError(f"Invalid status: {self.status}")
        if not self.holiday or not self.source_url:
            raise ValueError("Holiday and source_url are required")
        if self.status == "EARLY_CLOSE" and not self.close_time_local:
            raise ValueError("Early close requires close_time_local")
        if self.status == "HOLIDAY_HOURS" and not (self.window_start and self.window_end):
            raise ValueError("CME holiday hours require a date window")
        if self.exchange == "CME" and self.status == "CLOSED":
            raise ValueError("CME Globex holiday windows must not imply universal closure")
        if self.exchange == "OPRA" and self.source_basis == "OFFICIAL":
            raise ValueError("OPRA 2026 requires verified direct source, not an OCC proxy")

    def row(self) -> dict[str, str]:
        self.validate()
        return asdict(self)
