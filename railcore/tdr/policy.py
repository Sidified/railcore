"""When a TDR may be filed and how much it claims.

TRAIN_LATE_NOT_TRAVELLED  the train left the boarding station 3 hours or more
                          late; must be filed before the actual departure.
                          Claims the full fare of each passenger.
AC_FAILURE                AC classes only; filed within 20 hours of the
                          actual arrival at the destination. Claims the
                          difference between the fare paid and the fare of
                          the matching non-AC class for the same journey.
RAC_NOT_TRAVELLED         passengers still on RAC after charting who did not
                          travel; filed before 30 minutes ahead of departure.
                          Claims the fare less clerkage.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from ..domain.classes import TravelClass, layout_for
from ..errors import BookingClosed, ValidationError
from ..money import ZERO

LATE_MINUTES = 180
AC_FAILURE_HOURS = 20
RAC_CUTOFF_MINUTES = 30

# Rule 9 shape: the class an AC fare is compared against when AC fails.
AC_FALLBACK: dict[TravelClass, TravelClass] = {
    TravelClass.FIRST_AC: TravelClass.FIRST_CLASS,
    TravelClass.SECOND_AC: TravelClass.SLEEPER,
    TravelClass.THIRD_AC: TravelClass.SLEEPER,
    TravelClass.THIRD_AC_ECONOMY: TravelClass.SLEEPER,
    TravelClass.CHAIR_CAR: TravelClass.SECOND_SITTING,
    TravelClass.EXECUTIVE_CHAIR: TravelClass.FIRST_CLASS,
}


class TDRPolicy:
    def check_late(self, delay_minutes: int, actual_departure: datetime, now: datetime) -> None:
        if delay_minutes < LATE_MINUTES:
            raise ValidationError(f"train was {delay_minutes} min late at boarding; 180 min needed for this TDR")
        if now >= actual_departure:
            raise BookingClosed("TDR for a late train must be filed before it actually departs")

    def check_ac_failure(self, travel_class: TravelClass, actual_arrival: datetime, now: datetime) -> None:
        if not layout_for(travel_class).is_ac:
            raise ValidationError(f"class {travel_class.value} is not air-conditioned")
        if now < actual_arrival - timedelta(days=2):
            raise ValidationError("AC failure TDR can only be filed around the journey")
        if now > actual_arrival + timedelta(hours=AC_FAILURE_HOURS):
            raise BookingClosed("AC failure TDR must be filed within 20 hours of actual arrival")

    def check_rac(self, departure: datetime, now: datetime) -> None:
        if now > departure - timedelta(minutes=RAC_CUTOFF_MINUTES):
            raise BookingClosed("RAC TDR must be filed up to 30 minutes before departure")

    @staticmethod
    def difference(paid: Decimal, lower: Decimal) -> Decimal:
        return max(paid - lower, ZERO)
