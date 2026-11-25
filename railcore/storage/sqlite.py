"""SQLite repository.

Schema changes are applied as numbered migrations, recorded in
`schema_migrations`, so an existing database is upgraded in place.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ..domain.booking import Booking, Gender, Passenger, PassengerRecord, SeatStatus, StatusKind
from ..domain.classes import BerthType, TravelClass
from ..domain.quota import Quota, WaitlistType
from ..events import Event
from ..inventory import ChartState
from ..tdr.models import TDR, TDRReason, TDRStatus
from .base import RunMeta

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        CREATE TABLE bookings (
            pnr            TEXT PRIMARY KEY,
            train_number   TEXT NOT NULL,
            start_date     TEXT NOT NULL,
            from_station   TEXT NOT NULL,
            to_station     TEXT NOT NULL,
            travel_class   TEXT NOT NULL,
            quota          TEXT NOT NULL,
            booked_at      TEXT NOT NULL
        );
        CREATE INDEX bookings_run ON bookings (train_number, start_date, travel_class);
        CREATE TABLE passengers (
            pnr            TEXT NOT NULL REFERENCES bookings (pnr),
            idx            INTEGER NOT NULL,
            name           TEXT NOT NULL,
            age            INTEGER NOT NULL,
            gender         TEXT NOT NULL,
            berth_pref     TEXT,
            fare           TEXT NOT NULL,
            refund         TEXT,
            cancelled_at   TEXT,
            pool           TEXT,
            b_kind TEXT NOT NULL, b_coach TEXT, b_berth INTEGER, b_type TEXT,
            b_rac INTEGER, b_wl TEXT, b_wl_no INTEGER,
            c_kind TEXT NOT NULL, c_coach TEXT, c_berth INTEGER, c_type TEXT,
            c_rac INTEGER, c_wl TEXT, c_wl_no INTEGER,
            PRIMARY KEY (pnr, idx)
        );
        CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT INTO counters VALUES ('pnr', 0);
    """),
    (2, """
        CREATE TABLE runs (
            train_number TEXT NOT NULL,
            start_date   TEXT NOT NULL,
            chart        TEXT NOT NULL,
            PRIMARY KEY (train_number, start_date)
        );
    """),
    (3, """
        CREATE TABLE events (
            seq      INTEGER PRIMARY KEY AUTOINCREMENT,
            type     TEXT NOT NULL,
            at       TEXT NOT NULL,
            payload  TEXT NOT NULL
        );
        CREATE INDEX events_type ON events (type);
    """),
    (4, """
        ALTER TABLE bookings ADD COLUMN boarding_station TEXT;
        ALTER TABLE runs ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0;
        CREATE TABLE run_delays (
            train_number TEXT NOT NULL,
            start_date   TEXT NOT NULL,
            station      TEXT NOT NULL,
            arr_delay    INTEGER NOT NULL,
            dep_delay    INTEGER NOT NULL,
            PRIMARY KEY (train_number, start_date, station)
        );
    """),
    (5, """
        CREATE TABLE tdrs (
            id          INTEGER PRIMARY KEY,
            pnr         TEXT NOT NULL REFERENCES bookings (pnr),
            passengers  TEXT NOT NULL,
            reason      TEXT NOT NULL,
            filed_at    TEXT NOT NULL,
            status      TEXT NOT NULL,
            claimed     TEXT NOT NULL,
            note        TEXT NOT NULL,
            settled_at  TEXT
        );
        CREATE INDEX tdrs_pnr ON tdrs (pnr);
        CREATE TABLE idempotency_keys (key TEXT PRIMARY KEY, pnr TEXT NOT NULL REFERENCES bookings (pnr));
        INSERT INTO counters VALUES ('tdr', 0);
    """),
]


def _status_cols(s: SeatStatus) -> tuple:
    return (s.kind.value, s.coach, s.berth, s.berth_type.value if s.berth_type else None,
            s.rac_number, s.waitlist.value if s.waitlist else None, s.wl_number)


def _status_from(row: sqlite3.Row, p: str) -> SeatStatus:
    return SeatStatus(
        kind=StatusKind(row[f"{p}_kind"]),
        coach=row[f"{p}_coach"],
        berth=row[f"{p}_berth"],
        berth_type=BerthType(row[f"{p}_type"]) if row[f"{p}_type"] else None,
        rac_number=row[f"{p}_rac"],
        waitlist=WaitlistType(row[f"{p}_wl"]) if row[f"{p}_wl"] else None,
        wl_number=row[f"{p}_wl_no"],
    )


class SQLiteRepository:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._depth = 0
        self.migrate()

    # ----------------------------------------------------------------- schema
    def migrate(self) -> list[int]:
        self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
        done = {r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            self._conn.execute("BEGIN")
            try:
                for statement in sql.split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute("INSERT INTO schema_migrations VALUES (?)", (version,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            applied.append(version)
        return applied

    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self):
        """Nested-safe: only the outermost block commits or rolls back."""
        outer = self._depth == 0
        if outer:
            self._conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if outer:
                self._conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def next_pnr_sequence(self) -> int:
        with self.transaction():
            self._conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'pnr'")
            return self._conn.execute("SELECT value FROM counters WHERE name = 'pnr'").fetchone()[0]

    # ----------------------------------------------------------------- bookings
    def save_booking(self, booking: Booking) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO bookings (pnr, train_number, start_date, from_station, to_station, travel_class,"
                " quota, booked_at, boarding_station) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(pnr) DO UPDATE SET boarding_station = excluded.boarding_station",
                (booking.pnr, booking.train_number, booking.start_date.isoformat(), booking.from_station,
                 booking.to_station, booking.travel_class.value, booking.quota.value,
                 booking.booked_at.isoformat(), booking.boarding_station),
            )
            for p in booking.passengers:
                pax = p.passenger
                self._conn.execute(
                    "INSERT OR REPLACE INTO passengers VALUES (?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?,?, ?,?,?,?,?,?,?)",
                    (booking.pnr, p.index, pax.name, pax.age, pax.gender.value,
                     pax.berth_preference.value if pax.berth_preference else None,
                     str(p.fare), str(p.refund) if p.refund is not None else None,
                     p.cancelled_at.isoformat() if p.cancelled_at else None,
                     p.pool.value if p.pool else None,
                     *_status_cols(p.booking_status), *_status_cols(p.current_status)),
                )

    def _load(self, row: sqlite3.Row) -> Booking:
        booking = Booking(
            pnr=row["pnr"], train_number=row["train_number"],
            start_date=date.fromisoformat(row["start_date"]),
            from_station=row["from_station"], to_station=row["to_station"],
            travel_class=TravelClass(row["travel_class"]), quota=Quota(row["quota"]),
            booked_at=datetime.fromisoformat(row["booked_at"]),
            boarding_station=row["boarding_station"],
        )
        for p in self._conn.execute("SELECT * FROM passengers WHERE pnr = ? ORDER BY idx", (row["pnr"],)):
            booking.passengers.append(PassengerRecord(
                index=p["idx"],
                passenger=Passenger(p["name"], p["age"], Gender(p["gender"]),
                                    BerthType(p["berth_pref"]) if p["berth_pref"] else None),
                booking_status=_status_from(p, "b"),
                current_status=_status_from(p, "c"),
                fare=Decimal(p["fare"]),
                refund=Decimal(p["refund"]) if p["refund"] is not None else None,
                cancelled_at=datetime.fromisoformat(p["cancelled_at"]) if p["cancelled_at"] else None,
                pool=Quota(p["pool"]) if p["pool"] else None,
            ))
        return booking

    def get_booking(self, pnr: str) -> Booking | None:
        row = self._conn.execute("SELECT * FROM bookings WHERE pnr = ?", (pnr,)).fetchone()
        return self._load(row) if row else None

    def bookings_for_run(self, train_number: str, start_date: date,
                         travel_class: TravelClass | None = None) -> list[Booking]:
        sql = "SELECT * FROM bookings WHERE train_number = ? AND start_date = ?"
        args: list = [train_number, start_date.isoformat()]
        if travel_class is not None:
            sql += " AND travel_class = ?"
            args.append(travel_class.value)
        sql += " ORDER BY booked_at, pnr"
        return [self._load(r) for r in self._conn.execute(sql, args).fetchall()]

    # --------------------------------------------------------------------- runs
    def get_run(self, train_number: str, start_date: date) -> RunMeta:
        row = self._conn.execute("SELECT * FROM runs WHERE train_number = ? AND start_date = ?",
                                 (train_number, start_date.isoformat())).fetchone()
        delays = {
            r["station"]: (r["arr_delay"], r["dep_delay"])
            for r in self._conn.execute("SELECT * FROM run_delays WHERE train_number = ? AND start_date = ?",
                                        (train_number, start_date.isoformat()))
        }
        if row is None:
            return RunMeta(train_number, start_date, delays=delays)
        return RunMeta(train_number, start_date, ChartState(row["chart"]), bool(row["cancelled"]), delays)

    def save_run(self, meta: RunMeta) -> None:
        day = meta.start_date.isoformat()
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO runs (train_number, start_date, chart, cancelled) VALUES (?,?,?,?)",
                (meta.train_number, day, meta.chart.value, int(meta.cancelled)))
            self._conn.execute("DELETE FROM run_delays WHERE train_number = ? AND start_date = ?",
                               (meta.train_number, day))
            for station, (arr, dep) in meta.delays.items():
                self._conn.execute("INSERT INTO run_delays VALUES (?,?,?,?,?)",
                                   (meta.train_number, day, station, arr, dep))

    # ------------------------------------------------------------------- events
    def append_event(self, event: Event) -> Event:
        with self.transaction():
            cur = self._conn.execute("INSERT INTO events (type, at, payload) VALUES (?,?,?)",
                                     (event.type, event.at.isoformat(), json.dumps(event.payload, sort_keys=True)))
            return Event(event.type, event.at, event.payload, cur.lastrowid)

    def events(self, after_seq: int = 0, limit: int = 1000) -> list[Event]:
        rows = self._conn.execute("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after_seq, limit))
        return [Event(r["type"], datetime.fromisoformat(r["at"]), json.loads(r["payload"]), r["seq"]) for r in rows]

    # --------------------------------------------------------------------- TDRs
    def next_tdr_id(self) -> int:
        with self.transaction():
            self._conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'tdr'")
            return self._conn.execute("SELECT value FROM counters WHERE name = 'tdr'").fetchone()[0]

    def save_tdr(self, tdr: TDR) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO tdrs VALUES (?,?,?,?,?,?,?,?,?)",
                (tdr.id, tdr.pnr, json.dumps(tdr.passengers), tdr.reason.value, tdr.filed_at.isoformat(),
                 tdr.status.value, json.dumps({str(k): str(v) for k, v in tdr.claimed.items()}), tdr.note,
                 tdr.settled_at.isoformat() if tdr.settled_at else None))

    @staticmethod
    def _tdr(row: sqlite3.Row) -> TDR:
        return TDR(
            id=row["id"], pnr=row["pnr"], passengers=json.loads(row["passengers"]),
            reason=TDRReason(row["reason"]), filed_at=datetime.fromisoformat(row["filed_at"]),
            status=TDRStatus(row["status"]),
            claimed={int(k): Decimal(v) for k, v in json.loads(row["claimed"]).items()},
            note=row["note"],
            settled_at=datetime.fromisoformat(row["settled_at"]) if row["settled_at"] else None)

    def get_tdr(self, tdr_id: int) -> TDR | None:
        row = self._conn.execute("SELECT * FROM tdrs WHERE id = ?", (tdr_id,)).fetchone()
        return self._tdr(row) if row else None

    def tdrs_for_pnr(self, pnr: str) -> list[TDR]:
        return [self._tdr(r) for r in self._conn.execute("SELECT * FROM tdrs WHERE pnr = ? ORDER BY id", (pnr,))]

    # -------------------------------------------------------------- idempotency
    def idempotent_pnr(self, key: str) -> str | None:
        row = self._conn.execute("SELECT pnr FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def remember_idempotent(self, key: str, pnr: str) -> None:
        with self.transaction():
            self._conn.execute("INSERT INTO idempotency_keys VALUES (?,?)", (key, pnr))
