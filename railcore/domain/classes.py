"""Travel classes and their coach layouts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TravelClass(str, Enum):
    FIRST_AC = "1A"
    SECOND_AC = "2A"
    THIRD_AC = "3A"
    THIRD_AC_ECONOMY = "3E"
    SLEEPER = "SL"
    FIRST_CLASS = "FC"
    CHAIR_CAR = "CC"
    EXECUTIVE_CHAIR = "EC"
    SECOND_SITTING = "2S"

    @classmethod
    def parse(cls, value: "str | TravelClass") -> "TravelClass":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"unknown travel class {value!r}") from None


class BerthType(str, Enum):
    LOWER = "LB"
    MIDDLE = "MB"
    UPPER = "UB"
    SIDE_LOWER = "SL"
    SIDE_UPPER = "SU"
    WINDOW = "WS"
    MIDDLE_SEAT = "MS"
    AISLE = "AS"


_THREE_TIER = (BerthType.LOWER, BerthType.MIDDLE, BerthType.UPPER,
               BerthType.LOWER, BerthType.MIDDLE, BerthType.UPPER,
               BerthType.SIDE_LOWER, BerthType.SIDE_UPPER)
_TWO_TIER = (BerthType.LOWER, BerthType.UPPER, BerthType.LOWER, BerthType.UPPER,
             BerthType.SIDE_LOWER, BerthType.SIDE_UPPER)
_CABIN = (BerthType.LOWER, BerthType.UPPER, BerthType.LOWER, BerthType.UPPER)
_THREE_SEAT = (BerthType.WINDOW, BerthType.MIDDLE_SEAT, BerthType.AISLE,
               BerthType.AISLE, BerthType.MIDDLE_SEAT, BerthType.WINDOW)
_TWO_SEAT = (BerthType.WINDOW, BerthType.AISLE, BerthType.AISLE, BerthType.WINDOW)


@dataclass(frozen=True)
class CoachLayout:
    prefix: str
    berths: int
    cycle: tuple[BerthType, ...]
    is_ac: bool
    sleeper: bool

    def berth_type(self, number: int) -> BerthType:
        if not 1 <= number <= self.berths:
            raise ValueError(f"berth {number} outside 1..{self.berths}")
        return self.cycle[(number - 1) % len(self.cycle)]

    @property
    def has_rac(self) -> bool:
        return BerthType.SIDE_LOWER in self.cycle


LAYOUTS: dict[TravelClass, CoachLayout] = {
    TravelClass.SLEEPER: CoachLayout("S", 72, _THREE_TIER, is_ac=False, sleeper=True),
    TravelClass.THIRD_AC: CoachLayout("B", 64, _THREE_TIER, is_ac=True, sleeper=True),
    TravelClass.THIRD_AC_ECONOMY: CoachLayout("M", 72, _THREE_TIER, is_ac=True, sleeper=True),
    TravelClass.SECOND_AC: CoachLayout("A", 48, _TWO_TIER, is_ac=True, sleeper=True),
    TravelClass.FIRST_AC: CoachLayout("H", 24, _CABIN, is_ac=True, sleeper=True),
    TravelClass.FIRST_CLASS: CoachLayout("F", 24, _CABIN, is_ac=False, sleeper=True),
    TravelClass.CHAIR_CAR: CoachLayout("C", 78, _THREE_SEAT, is_ac=True, sleeper=False),
    TravelClass.EXECUTIVE_CHAIR: CoachLayout("E", 56, _TWO_SEAT, is_ac=True, sleeper=False),
    TravelClass.SECOND_SITTING: CoachLayout("D", 108, _THREE_SEAT, is_ac=False, sleeper=False),
}


def layout_for(travel_class: TravelClass) -> CoachLayout:
    return LAYOUTS[travel_class]
