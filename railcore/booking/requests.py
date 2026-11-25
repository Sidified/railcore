"""Plain input objects for the booking service (built by the API and CLI)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..domain.booking import Gender, Passenger
from ..domain.classes import BerthType, TravelClass
from ..domain.quota import Quota
from ..errors import ValidationError


@dataclass(frozen=True)
class PassengerInput:
    name: str
    age: int
    gender: str
    berth_preference: str | None = None

    def to_passenger(self) -> Passenger:
        try:
            gender = Gender(self.gender)
        except ValueError:
            raise ValidationError(f"gender must be M, F or T, not {self.gender!r}") from None
        pref = None
        if self.berth_preference:
            try:
                pref = BerthType(self.berth_preference)
            except ValueError:
                raise ValidationError(f"unknown berth preference {self.berth_preference!r}") from None
        return Passenger(self.name.strip(), int(self.age), gender, pref)


@dataclass(frozen=True)
class BookingRequest:
    train_number: str
    start_date: date
    from_station: str
    to_station: str
    travel_class: str
    quota: str = "GN"
    passengers: list[PassengerInput] = field(default_factory=list)

    def parsed_class(self) -> TravelClass:
        try:
            return TravelClass.parse(self.travel_class)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None

    def parsed_quota(self) -> Quota:
        try:
            return Quota.parse(self.quota)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
