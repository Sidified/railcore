import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest

from railcore.domain.booking import (Booking, Gender, Passenger, PassengerRecord, SeatStatus,
                                     StatusKind)
from railcore.domain.classes import BerthType, TravelClass
from railcore.domain.quota import Quota
from railcore.events import Event
from railcore.inventory import ChartState
from railcore.storage import InMemoryRepository, RunMeta, SQLiteRepository
from railcore.storage.sqlite import MIGRATIONS


def sample_booking(pnr="2000000014"):
    s = SeatStatus(StatusKind.CONFIRMED, "S1", 25, BerthType.LOWER)
    return Booking(pnr, "22401", date(2026, 12, 10), "NDLS", "BPL", TravelClass.SLEEPER, Quota.GENERAL,
                   datetime(2026, 12, 1, 9, 0),
                   [PassengerRecord(1, Passenger("Asha", 30, Gender.FEMALE, BerthType.LOWER), s, s,
                                    Decimal("410.00"), pool=Quota.GENERAL)])


def test_fresh_database_is_at_latest_version(tmp_path):
    repo = SQLiteRepository(tmp_path / "a.db")
    assert repo.schema_version() == MIGRATIONS[-1][0]
    assert repo.migrate() == []  # nothing left to apply


def test_old_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
    for version, sql in MIGRATIONS[:3]:
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations VALUES (?)", (version,))
    conn.execute("INSERT INTO bookings VALUES ('2000000014','22401','2026-12-10','NDLS','BPL','SL','GN',"
                 "'2026-12-01T09:00:00')")
    conn.execute("INSERT INTO runs VALUES ('22401','2026-12-10','FIRST')")
    conn.commit()
    conn.close()
    repo = SQLiteRepository(path)
    assert repo.schema_version() == 5
    booking = repo.get_booking("2000000014")
    assert booking.boarding_station is None and booking.boards_at == "NDLS"
    run = repo.get_run("22401", date(2026, 12, 10))
    assert run.chart is ChartState.FIRST and not run.cancelled and run.delays == {}


@pytest.mark.parametrize("make", [InMemoryRepository, lambda: SQLiteRepository(":memory:")])
def test_round_trip_and_rollback(make):
    repo = make()
    repo.save_booking(sample_booking())
    assert repo.get_booking("2000000014").passengers[0].fare == Decimal("410.00")
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.save_booking(sample_booking("2000000022"))
            repo.append_event(Event("x", datetime(2026, 12, 1)))
            raise RuntimeError("boom")
    assert repo.get_booking("2000000022") is None
    assert repo.events() == []


@pytest.mark.parametrize("make", [InMemoryRepository, lambda: SQLiteRepository(":memory:")])
def test_nested_transaction_rolls_back_whole(make):
    repo = make()
    with pytest.raises(RuntimeError):
        with repo.transaction():
            with repo.transaction():
                repo.save_booking(sample_booking())
            raise RuntimeError("outer fails after inner finished")
    assert repo.get_booking("2000000014") is None


@pytest.mark.parametrize("make", [InMemoryRepository, lambda: SQLiteRepository(":memory:")])
def test_run_meta_and_delays(make):
    repo = make()
    meta = RunMeta("22401", date(2026, 12, 10), ChartState.FIRST, False, {"BPL": (10, 15)})
    repo.save_run(meta)
    assert repo.get_run("22401", date(2026, 12, 10)) == meta
    meta.delays = {"ET": (0, 5)}
    repo.save_run(meta)
    assert repo.get_run("22401", date(2026, 12, 10)).delays == {"ET": (0, 5)}


@pytest.mark.parametrize("make", [InMemoryRepository, lambda: SQLiteRepository(":memory:")])
def test_events_are_numbered(make):
    repo = make()
    for i in range(3):
        repo.append_event(Event(f"e{i}", datetime(2026, 12, 1), {"i": i}))
    assert [e.seq for e in repo.events()] == [1, 2, 3]
    assert [e.payload["i"] for e in repo.events(after_seq=1, limit=1)] == [1]


@pytest.mark.parametrize("make", [InMemoryRepository, lambda: SQLiteRepository(":memory:")])
def test_idempotency_key_unique(make):
    repo = make()
    repo.save_booking(sample_booking())
    repo.remember_idempotent("k", "2000000014")
    assert repo.idempotent_pnr("k") == "2000000014" and repo.idempotent_pnr("other") is None
    with pytest.raises((KeyError, sqlite3.IntegrityError)):
        repo.remember_idempotent("k", "2000000014")
