"""Bookings touched during one service operation, saved together at the end."""
from __future__ import annotations

from ..domain.booking import Booking
from ..errors import NotFound
from ..storage.base import Repository


class Changes:
    """Bookings touched during one operation, saved together at the end."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self.bookings: dict[str, Booking] = {}

    def get(self, pnr: str) -> Booking:
        if pnr not in self.bookings:
            found = self.repo.get_booking(pnr)
            if found is None:
                raise NotFound(f"no booking with PNR {pnr}")
            self.bookings[pnr] = found
        return self.bookings[pnr]

    def add(self, booking: Booking) -> None:
        self.bookings[booking.pnr] = booking

    def save(self) -> None:
        for booking in self.bookings.values():
            self.repo.save_booking(booking)
