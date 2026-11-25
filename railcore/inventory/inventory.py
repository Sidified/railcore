"""Seat inventory for one class of one train run.

Holds who sits where (CNF), who shares a side lower (RAC), and who is
waiting (GNWL / TQWL). Every change returns the list of passengers whose
status changed, so the booking service can update their records.

Important limitation (by design for now): a berth is held for the WHOLE
run of the train. A passenger going NDLS -> AGC blocks their berth all the
way to the destination, even though the berth is empty after AGC.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from ..domain.booking import Passenger, PassengerRef, SeatStatus, StatusKind
from ..domain.classes import BerthType, TravelClass
from ..domain.quota import WAITLIST_FOR, Quota, WaitlistType
from ..errors import BookingClosed, InvalidState, NoAvailability
from .layout import Berth, build_berths, coach_names

Key = tuple[str, int]


class ChartState(str, Enum):
    OPEN = "OPEN"
    FIRST = "FIRST"
    FINAL = "FINAL"


@dataclass(frozen=True)
class Move:
    """A passenger's new status after an inventory change."""

    ref: PassengerRef
    status: SeatStatus
    pool: Quota | None
    reason: str


@dataclass(frozen=True)
class Allocation:
    status: SeatStatus  # current status (RAC/WL number = position in the queue)
    pool: Quota | None
    booked_as: SeatStatus | None = None  # status printed on the ticket (issued RAC/WL number)

    @property
    def booking_status(self) -> SeatStatus:
        return self.booked_as or self.status


class ClassInventory:
    def __init__(self, train_number: str, travel_class: TravelClass, coaches: int, rules: dict):
        self.train_number = train_number
        self.travel_class = travel_class
        self.rules = rules
        self.coaches = coach_names(travel_class, coaches)
        self._coach_order = {c: i for i, c in enumerate(self.coaches)}
        self.berths: dict[Key, Berth] = build_berths(travel_class, coaches, rules)
        self.occupant: dict[Key, PassengerRef] = {}
        self.rac_slots: dict[Key, list[PassengerRef]] = {k: [] for k, b in self.berths.items() if b.is_rac}
        self.rac_queue: list[PassengerRef] = []
        self.waitlists: dict[WaitlistType, list[PassengerRef]] = {w: [] for w in WaitlistType}
        self.wl_issued: dict[WaitlistType, int] = {w: 0 for w in WaitlistType}
        self.rac_issued = 0
        self.pt_sold = 0
        self.chart = ChartState.OPEN

    # ------------------------------------------------------------------ queries
    def _sort_key(self, key: Key) -> tuple[int, int]:
        return (self._coach_order[key[0]], key[1])

    def free_berths(self, pool: Quota) -> list[Key]:
        keys = [k for k, b in self.berths.items()
                if not b.is_rac and b.pool is pool and k not in self.occupant]
        return sorted(keys, key=self._sort_key)

    def pool_size(self, pool: Quota) -> int:
        return sum(1 for b in self.berths.values() if not b.is_rac and b.pool is pool)

    def free_rac_slots(self) -> list[Key]:
        keys = [k for k, occ in self.rac_slots.items() if len(occ) < 2]
        return sorted(keys, key=self._sort_key)

    def rac_capacity_left(self) -> int:
        return sum(2 - len(occ) for occ in self.rac_slots.values())

    def wl_limit(self, wl: WaitlistType) -> int:
        return int(self.rules["WL_LIMIT" if wl is WaitlistType.GNWL else "TQWL_LIMIT"])

    def status_of(self, ref: PassengerRef) -> Allocation | None:
        for key, occ in self.occupant.items():
            if occ == ref:
                berth = self.berths[key]
                return Allocation(SeatStatus(StatusKind.CONFIRMED, key[0], key[1], berth.type), berth.pool)
        for key, occ in self.rac_slots.items():
            if ref in occ:
                return Allocation(SeatStatus(StatusKind.RAC, key[0], key[1], self.berths[key].type,
                                             rac_number=self.rac_queue.index(ref) + 1), None)
        for wl, queue in self.waitlists.items():
            if ref in queue:
                return Allocation(SeatStatus(StatusKind.WAITLISTED, waitlist=wl,
                                             wl_number=queue.index(ref) + 1), None)
        return None

    def all_statuses(self) -> dict[PassengerRef, Allocation]:
        out: dict[PassengerRef, Allocation] = {}
        for key, ref in self.occupant.items():
            berth = self.berths[key]
            out[ref] = Allocation(SeatStatus(StatusKind.CONFIRMED, key[0], key[1], berth.type), berth.pool)
        rac_pos = {ref: i + 1 for i, ref in enumerate(self.rac_queue)}
        for key, occ in self.rac_slots.items():
            for ref in occ:
                out[ref] = Allocation(SeatStatus(StatusKind.RAC, key[0], key[1], self.berths[key].type,
                                                 rac_number=rac_pos[ref]), None)
        for wl, queue in self.waitlists.items():
            for i, ref in enumerate(queue):
                out[ref] = Allocation(SeatStatus(StatusKind.WAITLISTED, waitlist=wl, wl_number=i + 1), None)
        return out

    def availability(self, quota: Quota) -> str:
        if self.chart is ChartState.FINAL:
            return "CHART PREPARED"
        if self.chart is ChartState.FIRST:
            free = len(self.free_berths(Quota.GENERAL))
            return f"CURRENT AVAILABLE-{free:04d}" if free else "NOT AVAILABLE"
        if quota is Quota.SENIOR_LOWER:
            free = len(self.free_berths(Quota.SENIOR_LOWER))
            return f"AVAILABLE-{free:04d}" if free else self.availability(Quota.GENERAL)
        free = len(self.free_berths(quota))
        if free:
            return f"AVAILABLE-{free:04d}"
        if quota is Quota.GENERAL and self.rac_capacity_left():
            return f"RAC {self.rac_issued + 1}"
        wl = WAITLIST_FOR[quota]
        if wl is None:
            return "NOT AVAILABLE"
        if len(self.waitlists[wl]) >= self.wl_limit(wl):
            return "REGRET"
        return f"{wl.value}{self.wl_issued[wl] + 1}/WL{len(self.waitlists[wl]) + 1}"

    # --------------------------------------------------------------- allocation
    def allocate(self, refs: list[PassengerRef], passengers: list[Passenger], quota: Quota) -> list[Allocation]:
        """Place every passenger of one booking, or raise and change nothing."""
        if len(refs) != len(passengers):
            raise ValueError("refs and passengers differ in length")
        if self.chart is ChartState.FINAL:
            raise BookingClosed("chart is final; booking is closed")
        snapshot = self._snapshot()
        try:
            if self.chart is ChartState.FIRST:
                return self._allocate_current(refs, passengers, quota)
            return self._allocate_open(refs, passengers, quota)
        except Exception:
            self._restore(snapshot)
            raise

    def _allocate_current(self, refs, passengers, quota) -> list[Allocation]:
        if quota is not Quota.GENERAL:
            raise BookingClosed("after charting only current (general) booking is open")
        keys = self._pick_berths(Quota.GENERAL, passengers)
        if len(keys) < len(passengers):
            raise NoAvailability("not enough current availability for all passengers")
        return [self._seat(ref, key) for ref, key in zip(refs, keys)]

    def _allocate_open(self, refs, passengers, quota) -> list[Allocation]:
        pool = quota
        if quota is Quota.SENIOR_LOWER:
            keys = self._pick_berths(Quota.SENIOR_LOWER, passengers)
            if len(keys) < len(passengers):
                # not enough senior lower berths for everyone: book under general rules
                pool = Quota.GENERAL
        if pool is not Quota.SENIOR_LOWER:
            keys = self._pick_berths(pool, passengers)
        out: list[Allocation] = []
        for i, ref in enumerate(refs):
            if i < len(keys):
                out.append(self._seat(ref, keys[i]))
                if quota is Quota.PREMIUM_TATKAL:
                    self.pt_sold += 1
                continue
            if pool is Quota.GENERAL and self.free_rac_slots():
                out.append(self._rac(ref, self.free_rac_slots()[0]))
                continue
            wl = WAITLIST_FOR[pool]
            if wl is None:
                raise NoAvailability(f"no berths left in quota {quota.value}")
            if len(self.waitlists[wl]) >= self.wl_limit(wl):
                raise NoAvailability(f"waitlist {wl.value} is full (REGRET)")
            out.append(self._waitlist(ref, wl))
        return out

    def _pick_berths(self, pool: Quota, passengers: list[Passenger]) -> list[Key]:
        """Choose berths for as many passengers as possible.

        Keeps a group in one coach when one coach can take all of them, and
        honours berth preferences inside the chosen coaches.
        """
        free = self.free_berths(pool)
        if not free:
            return []
        by_coach: dict[str, list[Key]] = {}
        for key in free:
            by_coach.setdefault(key[0], []).append(key)
        whole = [c for c in self.coaches if len(by_coach.get(c, [])) >= len(passengers)]
        order = ([whole[0]] if whole else []) + [c for c in self.coaches if not whole or c != whole[0]]
        candidates = [k for c in order for k in by_coach.get(c, [])]
        count = min(len(passengers), len(candidates))
        chosen: list[Key | None] = [None] * count
        taken: set[Key] = set()
        for i in range(count):
            pref = passengers[i].berth_preference
            if pref is None:
                continue
            for key in candidates:
                if key not in taken and self.berths[key].type is pref:
                    chosen[i] = key
                    taken.add(key)
                    break
        rest = iter(k for k in candidates if k not in taken)
        for i in range(count):
            if chosen[i] is None:
                key = next(rest)
                chosen[i] = key
                taken.add(key)
        return [k for k in chosen if k is not None]

    def _seat(self, ref: PassengerRef, key: Key) -> Allocation:
        if key in self.occupant:
            raise InvalidState(f"berth {key} already taken")
        self.occupant[key] = ref
        berth = self.berths[key]
        return Allocation(SeatStatus(StatusKind.CONFIRMED, key[0], key[1], berth.type), berth.pool)

    def _rac(self, ref: PassengerRef, key: Key) -> Allocation:
        self.rac_slots[key].append(ref)
        self.rac_queue.append(ref)
        self.rac_issued += 1
        btype = self.berths[key].type
        return Allocation(
            SeatStatus(StatusKind.RAC, key[0], key[1], btype, rac_number=len(self.rac_queue)), None,
            SeatStatus(StatusKind.RAC, key[0], key[1], btype, rac_number=self.rac_issued))

    def _waitlist(self, ref: PassengerRef, wl: WaitlistType) -> Allocation:
        self.waitlists[wl].append(ref)
        self.wl_issued[wl] += 1
        return Allocation(
            SeatStatus(StatusKind.WAITLISTED, waitlist=wl, wl_number=len(self.waitlists[wl])), None,
            SeatStatus(StatusKind.WAITLISTED, waitlist=wl, wl_number=self.wl_issued[wl]))

    # ----------------------------------------------------- cancellation/promotion
    def release(self, refs: Iterable[PassengerRef]) -> list[Move]:
        """Remove passengers and promote others. Returns moves for OTHER passengers."""
        refs = list(refs)
        before = self.all_statuses()
        for ref in refs:
            self._release_one(ref)
        return self._diff(before, exclude=set(refs), reason="promoted after cancellation")

    def _release_one(self, ref: PassengerRef) -> None:
        for key, occ in list(self.occupant.items()):
            if occ == ref:
                del self.occupant[key]
                if self.chart is ChartState.OPEN:
                    self._fill_berth(key)
                return
        for key, occ in self.rac_slots.items():
            if ref in occ:
                occ.remove(ref)
                self.rac_queue.remove(ref)
                if self.chart is ChartState.OPEN:
                    self._fill_rac_slot(key)
                elif len(occ) == 1:
                    self._convert_rac_berth(key)
                return
        for queue in self.waitlists.values():
            if ref in queue:
                queue.remove(ref)
                return
        raise InvalidState(f"passenger {ref} holds nothing in this inventory")

    def _fill_berth(self, key: Key, *, charting: bool = False) -> bool:
        berth = self.berths[key]
        if berth.pool is Quota.GENERAL:
            if self.rac_queue:
                promoted = self.rac_queue.pop(0)
                slot = next(k for k, occ in self.rac_slots.items() if promoted in occ)
                self.rac_slots[slot].remove(promoted)
                self.occupant[key] = promoted
                self._fill_rac_slot(slot)
                return True
            if self.waitlists[WaitlistType.GNWL]:
                self.occupant[key] = self.waitlists[WaitlistType.GNWL].pop(0)
                return True
            if charting and self.waitlists[WaitlistType.TQWL]:
                self.occupant[key] = self.waitlists[WaitlistType.TQWL].pop(0)
                return True
            return False
        if berth.pool is Quota.TATKAL and self.waitlists[WaitlistType.TQWL]:
            self.occupant[key] = self.waitlists[WaitlistType.TQWL].pop(0)
            return True
        return False

    def _fill_rac_slot(self, key: Key) -> None:
        if self.waitlists[WaitlistType.GNWL] and len(self.rac_slots[key]) < 2:
            promoted = self.waitlists[WaitlistType.GNWL].pop(0)
            self.rac_slots[key].append(promoted)
            self.rac_queue.append(promoted)

    def _convert_rac_berth(self, key: Key) -> None:
        """A RAC berth with one person left after charting becomes their CNF berth."""
        occ = self.rac_slots.pop(key)
        berth = self.berths[key]
        berth.is_rac = False
        berth.pool = Quota.GENERAL
        if occ:
            ref = occ[0]
            self.rac_queue.remove(ref)
            self.occupant[key] = ref

    # ------------------------------------------------------------------- charting
    def prepare_first_chart(self) -> tuple[list[Move], list[PassengerRef], dict[str, int]]:
        """Release unused quota berths, clear queues, drop the rest of the waitlist.

        Returns (moves, dropped waitlisted passengers, released berth counts).
        """
        if self.chart is not ChartState.OPEN:
            raise InvalidState("first chart already prepared")
        before = self.all_statuses()
        for key in self.free_berths(Quota.TATKAL):
            self._fill_berth(key)
        released: dict[str, int] = {}
        for pool in (Quota.TATKAL, Quota.PREMIUM_TATKAL, Quota.LADIES, Quota.SENIOR_LOWER):
            free = self.free_berths(pool)
            if free:
                released[pool.value] = len(free)
            for berth in self.berths.values():
                if not berth.is_rac and berth.pool is pool:
                    berth.pool = Quota.GENERAL
        for key in self.free_berths(Quota.GENERAL):
            if not self._fill_berth(key, charting=True):
                break
        for key in list(self.rac_slots):
            if len(self.rac_slots[key]) < 2:
                self._convert_rac_berth(key)
        dropped = [ref for queue in self.waitlists.values() for ref in queue]
        for queue in self.waitlists.values():
            queue.clear()
        self.chart = ChartState.FIRST
        moves = self._diff(before, exclude=set(dropped), reason="charting")
        return moves, dropped, released

    def prepare_final_chart(self) -> None:
        if self.chart is ChartState.OPEN:
            raise InvalidState("first chart must be prepared before the final chart")
        if self.chart is ChartState.FINAL:
            raise InvalidState("final chart already prepared")
        self.chart = ChartState.FINAL

    # -------------------------------------------------------------------- helpers
    def _diff(self, before: dict[PassengerRef, Allocation], exclude: set[PassengerRef], reason: str) -> list[Move]:
        after = self.all_statuses()
        moves = []
        for ref, alloc in after.items():
            if ref in exclude:
                continue
            if before.get(ref) != alloc:
                moves.append(Move(ref, alloc.status, alloc.pool, reason))
        return sorted(moves, key=lambda m: (m.ref.pnr, m.ref.index))

    def _snapshot(self):
        return (
            dict(self.occupant),
            {k: list(v) for k, v in self.rac_slots.items()},
            list(self.rac_queue),
            {k: list(v) for k, v in self.waitlists.items()},
            dict(self.wl_issued),
            self.rac_issued,
            self.pt_sold,
            {k: (b.pool, b.is_rac) for k, b in self.berths.items()},
        )

    def _restore(self, snap) -> None:
        (self.occupant, self.rac_slots, self.rac_queue, self.waitlists,
         self.wl_issued, self.rac_issued, self.pt_sold, berth_state) = snap
        for key, (pool, is_rac) in berth_state.items():
            self.berths[key].pool = pool
            self.berths[key].is_rac = is_rac

    # ----------------------------------------------------------------- rebuilding
    def place_existing(self, ref: PassengerRef, status: SeatStatus) -> None:
        """Put back a passenger loaded from storage (no promotion, no counters)."""
        if status.kind is StatusKind.CONFIRMED:
            key = (status.coach, status.berth)
            if key in self.rac_slots:  # RAC berth converted to CNF after charting
                self.rac_slots.pop(key)
                self.berths[key].is_rac = False
                self.berths[key].pool = Quota.GENERAL
            self._seat(ref, key)
        elif status.kind is StatusKind.RAC:
            self.rac_slots[(status.coach, status.berth)].append(ref)
        elif status.kind is StatusKind.WAITLISTED:
            self.waitlists[status.waitlist].append(ref)

    def finish_restore(self, rac_order: list[PassengerRef], wl_order: dict[WaitlistType, list[PassengerRef]],
                       wl_issued: dict[WaitlistType, int], rac_issued: int, pt_sold: int,
                       chart: ChartState) -> None:
        self.rac_queue = list(rac_order)
        for wl, order in wl_order.items():
            self.waitlists[wl] = list(order)
        self.wl_issued = dict(wl_issued)
        self.rac_issued = rac_issued
        self.pt_sold = pt_sold
        self.chart = chart
        if chart is not ChartState.OPEN:
            for key in list(self.rac_slots):
                if len(self.rac_slots[key]) < 2:
                    occ = self.rac_slots.pop(key)
                    self.berths[key].is_rac = False
                    self.berths[key].pool = Quota.GENERAL
                    if occ:  # pragma: no cover - charting always converts single RAC
                        raise InvalidState("restored a lone RAC passenger after charting")
            for berth in self.berths.values():
                if not berth.is_rac:
                    berth.pool = Quota.GENERAL

    def berth_type(self, key: Key) -> BerthType:
        return self.berths[key].type
