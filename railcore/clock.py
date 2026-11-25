"""Clocks. Services never call datetime.now() directly so tests can fix time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(IST).replace(tzinfo=None, microsecond=0)


class FixedClock:
    """A clock that only moves when told to. Times are naive IST."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at

    def advance(self, **kwargs) -> datetime:
        self._at = self._at + timedelta(**kwargs)
        return self._at
