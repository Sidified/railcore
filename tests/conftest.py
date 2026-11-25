from __future__ import annotations

from datetime import date, datetime

import pytest

from railcore.booking import BookingRequest, BookingService, PassengerInput
from railcore.clock import FixedClock
from railcore.storage import InMemoryRepository, SQLiteRepository

# 22401 Satpura Superfast leaves NDLS at 20:00. 2026-12-10 is a Thursday.
RUN_DATE = date(2026, 12, 10)
NOW = datetime(2026, 12, 1, 9, 0)


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        return InMemoryRepository()
    return SQLiteRepository(tmp_path / "railcore.db")


@pytest.fixture
def service(repo, clock):
    return BookingService(repo, clock=clock)


def pax(name="Asha", age=30, gender="F", pref=None):
    return PassengerInput(name, age, gender, pref)


def request_for(passengers=None, *, train="22401", start=RUN_DATE, frm="NDLS", to="BPL",
                cls="SL", quota="GN"):
    return BookingRequest(train, start, frm, to, cls, quota, passengers or [pax()])


def statuses(booking):
    return [p.current_status.label() for p in booking.passengers]
