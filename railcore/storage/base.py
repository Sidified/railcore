"""Repository interface shared by the in-memory and SQLite stores."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from ..domain.booking import Booking
from ..domain.classes import TravelClass
from ..events import Event
from ..tdr.models import TDR
from ..inventory import ChartState


@dataclass
class RunMeta:
    train_number: str
    start_date: date
    chart: ChartState = ChartState.OPEN
    cancelled: bool = False
    # reported delay in minutes at each station: station -> (arrival delay, departure delay)
    delays: dict[str, tuple[int, int]] = field(default_factory=dict)


class Repository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def next_pnr_sequence(self) -> int: ...

    def save_booking(self, booking: Booking) -> None: ...

    def get_booking(self, pnr: str) -> Booking | None: ...

    def bookings_for_run(self, train_number: str, start_date: date,
                         travel_class: TravelClass | None = None) -> list[Booking]: ...

    def get_run(self, train_number: str, start_date: date) -> RunMeta: ...

    def save_run(self, meta: RunMeta) -> None: ...

    def append_event(self, event: Event) -> Event: ...

    def events(self, after_seq: int = 0, limit: int = 1000) -> list[Event]: ...

    def next_tdr_id(self) -> int: ...

    def save_tdr(self, tdr: TDR) -> None: ...

    def get_tdr(self, tdr_id: int) -> TDR | None: ...

    def tdrs_for_pnr(self, pnr: str) -> list[TDR]: ...

    def idempotent_pnr(self, key: str) -> str | None: ...

    def remember_idempotent(self, key: str, pnr: str) -> None: ...
