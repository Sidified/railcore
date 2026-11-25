"""Booking rules checked before any berth is allocated."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..domain.booking import Gender, Passenger
from ..domain.classes import TravelClass, layout_for
from ..domain.network import Train
from ..domain.quota import Quota
from ..errors import BookingClosed, ValidationError

MIN_BOOKED_AGE = 5  # children under 5 travel free without a berth and are not booked


class BookingRules:
    def __init__(self, policy: dict):
        self.arp_days = int(policy["advance_reservation_days"])
        self.max_passengers = {Quota(k): int(v) for k, v in policy["max_passengers"].items()}
        self.tatkal_classes = {TravelClass(c) for c in policy["tatkal_classes"]}
        self.tatkal_open = {k: time.fromisoformat(v) for k, v in policy["tatkal_open"].items()}
        self.senior_age = {Gender(k): int(v) for k, v in policy["senior_lower_berth_min_age"].items()}

    def check_passengers(self, quota: Quota, passengers: list[Passenger]) -> None:
        if not passengers:
            raise ValidationError("at least one passenger is required")
        limit = self.max_passengers[quota]
        if len(passengers) > limit:
            raise ValidationError(f"at most {limit} passengers can be booked in quota {quota.value}")
        for p in passengers:
            if not p.name:
                raise ValidationError("passenger name is required")
            if p.age < MIN_BOOKED_AGE:
                raise ValidationError("children under 5 travel free without a berth and are not booked")
            if p.age > 125:
                raise ValidationError(f"age {p.age} is not valid")
        if quota is Quota.LADIES and any(p.gender is not Gender.FEMALE for p in passengers):
            raise ValidationError("ladies quota is for female passengers only")
        if quota is Quota.SENIOR_LOWER:
            for p in passengers:
                if p.age < self.senior_age[p.gender]:
                    raise ValidationError(f"{p.name} is not eligible for the senior lower-berth quota")

    def check_class_and_quota(self, train: Train, travel_class: TravelClass, quota: Quota,
                              pool_size: int) -> None:
        if not train.carries(travel_class):
            raise ValidationError(f"train {train.number} has no {travel_class.value} coaches")
        if quota.is_tatkal and travel_class not in self.tatkal_classes:
            raise ValidationError(f"{quota.value} is not available in class {travel_class.value}")
        if quota is not Quota.GENERAL and pool_size == 0:
            raise ValidationError(f"no {quota.value} quota in class {travel_class.value} on this train")

    def tatkal_opens_at(self, travel_class: TravelClass, start_date: date) -> datetime:
        key = "ac" if layout_for(travel_class).is_ac else "non_ac"
        return datetime.combine(start_date - timedelta(days=1), self.tatkal_open[key])

    def check_dates(self, train: Train, start_date: date, from_station: str, now: datetime,
                    travel_class: TravelClass, quota: Quota) -> None:
        if not train.runs_on_date(start_date):
            raise ValidationError(f"train {train.number} does not run on {start_date.isoformat()}")
        if now >= train.departure(from_station, start_date):
            raise BookingClosed("the train has already left the boarding station")
        if (start_date - now.date()).days > self.arp_days:
            raise BookingClosed(f"booking opens {self.arp_days} days before the journey")
        if quota.is_tatkal and now < self.tatkal_opens_at(travel_class, start_date):
            opens = self.tatkal_opens_at(travel_class, start_date)
            raise BookingClosed(f"Tatkal booking opens at {opens.isoformat(sep=' ', timespec='minutes')}")
