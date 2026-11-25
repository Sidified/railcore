"""Bookings (PNRs), passengers and their reservation status."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .classes import BerthType, TravelClass
from .quota import Quota, WaitlistType


class StatusKind(str, Enum):
    CONFIRMED = "CNF"
    RAC = "RAC"
    WAITLISTED = "WL"
    CANCELLED = "CAN"


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"
    TRANSGENDER = "T"


@dataclass(frozen=True)
class Passenger:
    name: str
    age: int
    gender: Gender
    berth_preference: BerthType | None = None


@dataclass(frozen=True)
class SeatStatus:
    """Where a passenger stands right now (or stood at booking time).

    CNF: coach + berth. RAC: coach + berth (a shared side lower) + rac_number.
    WL: waitlist type + number. CAN: nothing else.
    """

    kind: StatusKind
    coach: str | None = None
    berth: int | None = None
    berth_type: BerthType | None = None
    rac_number: int | None = None
    waitlist: WaitlistType | None = None
    wl_number: int | None = None

    def label(self) -> str:
        if self.kind is StatusKind.CONFIRMED:
            return f"CNF/{self.coach}/{self.berth}/{self.berth_type.value}"
        if self.kind is StatusKind.RAC:
            return f"RAC {self.rac_number}/{self.coach}/{self.berth}"
        if self.kind is StatusKind.WAITLISTED:
            return f"{self.waitlist.value} {self.wl_number}"
        return "CAN"

    @staticmethod
    def cancelled() -> "SeatStatus":
        return SeatStatus(StatusKind.CANCELLED)


@dataclass(frozen=True)
class PassengerRef:
    pnr: str
    index: int  # 1-based position in the booking

    def __str__(self) -> str:
        return f"{self.pnr}#{self.index}"


@dataclass
class PassengerRecord:
    index: int
    passenger: Passenger
    booking_status: SeatStatus
    current_status: SeatStatus
    fare: Decimal
    queue_seq: int | None = None  # order in the RAC / waitlist queues
    refund: Decimal | None = None
    cancelled_at: datetime | None = None
    pool: Quota | None = None  # quota pool of the berth held (CNF only)

    @property
    def is_active(self) -> bool:
        return self.current_status.kind is not StatusKind.CANCELLED


class BookingState(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


@dataclass
class Booking:
    pnr: str
    train_number: str
    start_date: date  # date the train leaves its origin
    from_station: str
    to_station: str
    travel_class: TravelClass
    quota: Quota
    booked_at: datetime
    passengers: list[PassengerRecord] = field(default_factory=list)

    @property
    def state(self) -> BookingState:
        if any(p.is_active for p in self.passengers):
            return BookingState.ACTIVE
        return BookingState.CANCELLED

    @property
    def total_fare(self) -> Decimal:
        return sum((p.fare for p in self.passengers), Decimal("0.00"))

    @property
    def total_refund(self) -> Decimal:
        return sum((p.refund or Decimal("0.00") for p in self.passengers), Decimal("0.00"))

    def record(self, index: int) -> PassengerRecord:
        for p in self.passengers:
            if p.index == index:
                return p
        raise KeyError(index)

    def ref(self, index: int) -> PassengerRef:
        return PassengerRef(self.pnr, index)


def with_status(record: PassengerRecord, status: SeatStatus) -> PassengerRecord:
    return replace(record, current_status=status)
