"""In-memory repository, used by tests and the CLI's --memory mode."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
from datetime import date

from ..domain.booking import Booking
from ..domain.classes import TravelClass
from ..events import Event
from .base import RunMeta


class InMemoryRepository:
    def __init__(self) -> None:
        self._bookings: dict[str, Booking] = {}
        self._runs: dict[tuple[str, date], RunMeta] = {}
        self._events: list[Event] = []
        self._pnr_seq = 0

    @contextmanager
    def transaction(self):
        saved = (copy.deepcopy(self._bookings), copy.deepcopy(self._runs), list(self._events), self._pnr_seq)
        try:
            yield
        except BaseException:
            self._bookings, self._runs, self._events, self._pnr_seq = saved
            raise

    def next_pnr_sequence(self) -> int:
        self._pnr_seq += 1
        return self._pnr_seq

    def save_booking(self, booking: Booking) -> None:
        self._bookings[booking.pnr] = copy.deepcopy(booking)

    def get_booking(self, pnr: str) -> Booking | None:
        found = self._bookings.get(pnr)
        return copy.deepcopy(found) if found else None

    def bookings_for_run(self, train_number: str, start_date: date,
                         travel_class: TravelClass | None = None) -> list[Booking]:
        out = [copy.deepcopy(b) for b in self._bookings.values()
               if b.train_number == train_number and b.start_date == start_date
               and (travel_class is None or b.travel_class is travel_class)]
        return sorted(out, key=lambda b: (b.booked_at, b.pnr))

    def get_run(self, train_number: str, start_date: date) -> RunMeta:
        return copy.deepcopy(self._runs.get((train_number, start_date), RunMeta(train_number, start_date)))

    def save_run(self, meta: RunMeta) -> None:
        self._runs[(meta.train_number, meta.start_date)] = copy.deepcopy(meta)

    def append_event(self, event: Event) -> Event:
        stored = replace(event, seq=len(self._events) + 1)
        self._events.append(stored)
        return stored

    def events(self, after_seq: int = 0, limit: int = 1000) -> list[Event]:
        return [e for e in self._events if e.seq > after_seq][:limit]
