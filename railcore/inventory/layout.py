"""Splits each coach's berths into quota pools and RAC berths.

The split is deterministic so that an inventory rebuilt from storage puts
every berth in the same pool it had when it was sold.

Per coach, in this order:
  RAC  the first N side-lower berths (classes that have side lowers)
  SS   the first N lower berths that are left
  LD   the next N berths in number order, first coach only
  TQ   the N highest-numbered berths that are left
  PT   the next N highest-numbered berths below the Tatkal ones
  GN   everything else
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.classes import BerthType, CoachLayout, TravelClass, layout_for
from ..domain.quota import Quota


@dataclass
class Berth:
    coach: str
    number: int
    type: BerthType
    pool: Quota
    is_rac: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return (self.coach, self.number)


def coach_names(travel_class: TravelClass, count: int) -> list[str]:
    prefix = layout_for(travel_class).prefix
    return [f"{prefix}{i}" for i in range(1, count + 1)]


def _take(numbers: list[int], count: int, *, from_end: bool = False) -> list[int]:
    chosen = numbers[-count:] if from_end and count else numbers[:count]
    for n in chosen:
        numbers.remove(n)
    return chosen


def build_berths(travel_class: TravelClass, coaches: int, rules: dict) -> dict[tuple[str, int], Berth]:
    layout: CoachLayout = layout_for(travel_class)
    berths: dict[tuple[str, int], Berth] = {}
    for c_index, coach in enumerate(coach_names(travel_class, coaches)):
        remaining = list(range(1, layout.berths + 1))
        pools: dict[int, Quota] = {}
        rac: set[int] = set()
        if layout.has_rac and rules.get("RAC", 0):
            side_lowers = [n for n in remaining if layout.berth_type(n) is BerthType.SIDE_LOWER]
            for n in side_lowers[: rules["RAC"]]:
                rac.add(n)
                remaining.remove(n)
        lowers = [n for n in remaining if layout.berth_type(n) is BerthType.LOWER]
        for n in lowers[: rules.get("SS", 0)]:
            pools[n] = Quota.SENIOR_LOWER
            remaining.remove(n)
        if c_index == 0:
            for n in _take(remaining, rules.get("LD", 0)):
                pools[n] = Quota.LADIES
        for n in _take(remaining, rules.get("TQ", 0), from_end=True):
            pools[n] = Quota.TATKAL
        for n in _take(remaining, rules.get("PT", 0), from_end=True):
            pools[n] = Quota.PREMIUM_TATKAL
        for n in remaining:
            pools[n] = Quota.GENERAL
        for n in range(1, layout.berths + 1):
            if n in rac:
                berths[(coach, n)] = Berth(coach, n, layout.berth_type(n), Quota.GENERAL, is_rac=True)
            else:
                berths[(coach, n)] = Berth(coach, n, layout.berth_type(n), pools[n])
    return berths
