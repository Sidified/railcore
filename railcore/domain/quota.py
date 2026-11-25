"""Quotas and the waitlist each one feeds."""
from __future__ import annotations

from enum import Enum


class Quota(str, Enum):
    GENERAL = "GN"
    TATKAL = "TQ"
    PREMIUM_TATKAL = "PT"
    LADIES = "LD"
    SENIOR_LOWER = "SS"

    @classmethod
    def parse(cls, value: "str | Quota") -> "Quota":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"unknown quota {value!r}") from None

    @property
    def is_tatkal(self) -> bool:
        return self in (Quota.TATKAL, Quota.PREMIUM_TATKAL)


class WaitlistType(str, Enum):
    GNWL = "GNWL"
    TQWL = "TQWL"


# Which waitlist a quota falls into when its berths run out. None means the
# quota has no waitlist: the booking is refused (PT, LD) or falls back to
# general (SS, see inventory.allocator).
WAITLIST_FOR: dict[Quota, WaitlistType | None] = {
    Quota.GENERAL: WaitlistType.GNWL,
    Quota.TATKAL: WaitlistType.TQWL,
    Quota.PREMIUM_TATKAL: None,
    Quota.LADIES: None,
    Quota.SENIOR_LOWER: None,
}
