"""Inventory behaviour on a small 2A class: 1 coach of 48 berths.

Pools with these rules: RAC 1 side lower (2 places), SS 1, LD 1, TQ 2, PT 1,
so 42 general berths.
"""
import pytest

from railcore.domain.booking import Gender, Passenger, PassengerRef, StatusKind
from railcore.domain.classes import BerthType, TravelClass
from railcore.domain.quota import Quota, WaitlistType
from railcore.errors import BookingClosed, InvalidState, NoAvailability
from railcore.inventory import ChartState, ClassInventory

RULES = {"TQ": 2, "PT": 1, "SS": 1, "LD": 1, "RAC": 1, "WL_LIMIT": 3, "TQWL_LIMIT": 2}
GN = 42


def P(name="p", age=30, gender=Gender.MALE, pref=None):
    return Passenger(name, age, gender, pref)


class Seq:
    def __init__(self):
        self.n = 0

    def refs(self, k):
        out = []
        for _ in range(k):
            self.n += 1
            out.append(PassengerRef(f"{self.n:010d}", 1))
        return out


@pytest.fixture
def inv():
    return ClassInventory("22401", TravelClass.SECOND_AC, 1, RULES)


@pytest.fixture
def seq():
    return Seq()


def book(inv, seq, quota=Quota.GENERAL, n=1, **kw):
    refs = seq.refs(n)
    return refs, inv.allocate(refs, [P(**kw) for _ in range(n)], quota)


def fill_general(inv, seq):
    for _ in range(GN):
        book(inv, seq)


def test_pool_sizes(inv):
    assert inv.pool_size(Quota.GENERAL) == GN
    assert inv.rac_capacity_left() == 2
    assert inv.availability(Quota.GENERAL) == "AVAILABLE-0042"


def test_confirmed_with_preference(inv, seq):
    _, (a,) = book(inv, seq, pref=BerthType.UPPER)
    assert a.status.kind is StatusKind.CONFIRMED
    assert a.status.berth_type is BerthType.UPPER
    assert a.pool is Quota.GENERAL


def test_group_kept_in_one_coach():
    inv = ClassInventory("22401", TravelClass.SECOND_AC, 2, RULES)
    seq = Seq()
    for _ in range(GN - 2):  # leave 2 free in A1
        book(inv, seq)
    _, allocs = book(inv, seq, n=3)
    assert {a.status.coach for a in allocs} == {"A2"}


def test_rac_then_waitlist_then_regret(inv, seq):
    fill_general(inv, seq)
    assert inv.availability(Quota.GENERAL) == "RAC 1"
    _, (r1,) = book(inv, seq)
    _, (r2,) = book(inv, seq)
    assert (r1.status.kind, r1.status.rac_number, r2.status.rac_number) == (StatusKind.RAC, 1, 2)
    assert r1.status.coach == r2.status.coach and r1.status.berth == r2.status.berth
    assert inv.availability(Quota.GENERAL) == "GNWL1/WL1"
    for i in range(3):
        _, (w,) = book(inv, seq)
        assert w.status.waitlist is WaitlistType.GNWL and w.status.wl_number == i + 1
    assert inv.availability(Quota.GENERAL) == "REGRET"
    with pytest.raises(NoAvailability):
        book(inv, seq)


def test_failed_booking_changes_nothing(inv, seq):
    fill_general(inv, seq)
    book(inv, seq, n=2)  # both RAC places
    book(inv, seq, n=2)  # GNWL 1, 2
    before = inv.all_statuses()
    with pytest.raises(NoAvailability):
        book(inv, seq, n=2)  # one more WL fits, the second does not
    assert inv.all_statuses() == before
    assert inv.wl_issued[WaitlistType.GNWL] == 2


def test_cancel_confirmed_promotes_rac_and_waitlist(inv, seq):
    fill_general(inv, seq)
    first = PassengerRef("0000000001", 1)
    (rac1, rac2), _ = book(inv, seq, n=2)
    (wl1,), _ = book(inv, seq)
    moves = {m.ref: m for m in inv.release([first])}
    assert moves[rac1].status.kind is StatusKind.CONFIRMED
    assert moves[rac2].status.rac_number == 1           # renumbered
    assert moves[wl1].status.kind is StatusKind.RAC
    assert moves[wl1].status.rac_number == 2


def test_cancel_rac_moves_waitlist_up(inv, seq):
    fill_general(inv, seq)
    (rac1, _), _ = book(inv, seq, n=2)
    (wl1, wl2), _ = book(inv, seq, n=2)
    moves = {m.ref: m.status for m in inv.release([rac1])}
    assert moves[wl1].kind is StatusKind.RAC
    assert moves[wl2].wl_number == 1


def test_cancel_waitlisted_renumbers(inv, seq):
    fill_general(inv, seq)
    book(inv, seq, n=2)
    (w1, w2, w3), _ = book(inv, seq, n=3)
    moves = {m.ref: m.status for m in inv.release([w1])}
    assert (moves[w2].wl_number, moves[w3].wl_number) == (1, 2)
    assert inv.wl_issued[WaitlistType.GNWL] == 3  # issued numbers never go back
    _, (w4,) = book(inv, seq)
    assert (w4.booking_status.wl_number, w4.status.wl_number) == (4, 3)


def test_tatkal_waitlist_gets_freed_tatkal_berth(inv, seq):
    (t1, _), _ = book(inv, seq, Quota.TATKAL, n=2)
    (tw,), (a,) = book(inv, seq, Quota.TATKAL)
    assert a.status.waitlist is WaitlistType.TQWL
    moves = {m.ref: m.status for m in inv.release([t1])}
    assert moves[tw].kind is StatusKind.CONFIRMED


def test_premium_tatkal_has_no_waitlist(inv, seq):
    book(inv, seq, Quota.PREMIUM_TATKAL)
    assert inv.pt_sold == 1
    assert inv.availability(Quota.PREMIUM_TATKAL) == "NOT AVAILABLE"
    with pytest.raises(NoAvailability):
        book(inv, seq, Quota.PREMIUM_TATKAL)


def test_senior_quota_falls_back_to_general(inv, seq):
    _, (a,) = book(inv, seq, Quota.SENIOR_LOWER, age=65)
    assert a.pool is Quota.SENIOR_LOWER and a.status.berth_type is BerthType.LOWER
    _, (b,) = book(inv, seq, Quota.SENIOR_LOWER, age=65)
    assert b.pool is Quota.GENERAL


def test_first_chart_releases_quota_and_clears_queues(inv, seq):
    fill_general(inv, seq)
    (r1, r2), _ = book(inv, seq, n=2)
    (w1, w2, w3), _ = book(inv, seq, n=3)
    moves, dropped, released = inv.prepare_first_chart()
    # 5 unused quota berths (SS 1, LD 1, TQ 2, PT 1) go to RAC 1, RAC 2, WL 1, WL 2, WL 3
    assert released == {"TQ": 2, "PT": 1, "LD": 1, "SS": 1}
    got = {m.ref: m.status.kind for m in moves}
    assert got == {r1: StatusKind.CONFIRMED, r2: StatusKind.CONFIRMED, w1: StatusKind.CONFIRMED,
                   w2: StatusKind.CONFIRMED, w3: StatusKind.CONFIRMED}
    assert dropped == []
    assert inv.chart is ChartState.FIRST
    # the RAC berth is empty after charting and becomes the one free berth
    assert inv.availability(Quota.GENERAL) == "CURRENT AVAILABLE-0001"


def test_lone_rac_passenger_confirmed_at_chart(inv, seq):
    fill_general(inv, seq)
    for q in (Quota.TATKAL, Quota.TATKAL, Quota.PREMIUM_TATKAL):
        book(inv, seq, q)
    book(inv, seq, Quota.LADIES, gender=Gender.FEMALE)
    book(inv, seq, Quota.SENIOR_LOWER, age=70)
    (r1,), _ = book(inv, seq)
    moves, dropped, _ = inv.prepare_first_chart()
    status = {m.ref: m.status for m in moves}[r1]
    assert status.kind is StatusKind.CONFIRMED and status.berth_type is BerthType.SIDE_LOWER


def test_leftover_waitlist_dropped_at_chart(inv, seq):
    fill_general(inv, seq)
    for q in (Quota.TATKAL, Quota.TATKAL, Quota.PREMIUM_TATKAL):
        book(inv, seq, q)
    book(inv, seq, Quota.LADIES, gender=Gender.FEMALE)
    book(inv, seq, Quota.SENIOR_LOWER, age=70)
    book(inv, seq, n=2)
    (w1, w2), _ = book(inv, seq, n=2)
    (tw,), _ = book(inv, seq, Quota.TATKAL)
    _, dropped, _ = inv.prepare_first_chart()
    assert set(dropped) == {w1, w2, tw}


def test_tatkal_waitlist_after_general_at_chart(inv, seq):
    fill_general(inv, seq)
    book(inv, seq, Quota.TATKAL, n=2)
    (tw,), _ = book(inv, seq, Quota.TATKAL)
    (w1,), _ = book(inv, seq)          # RAC 1
    moves, dropped, _ = inv.prepare_first_chart()
    kinds = {m.ref: m.status.kind for m in moves}
    assert kinds[w1] is StatusKind.CONFIRMED
    assert kinds[tw] is StatusKind.CONFIRMED  # free LD/SS/PT berths remain after GN queue


def test_after_first_chart_only_current_general_booking(inv, seq):
    inv.prepare_first_chart()
    with pytest.raises(BookingClosed):
        book(inv, seq, Quota.TATKAL)
    _, (a,) = book(inv, seq)
    assert a.status.kind is StatusKind.CONFIRMED


def test_current_booking_needs_room_for_everyone(inv, seq):
    fill_general(inv, seq)
    inv.prepare_first_chart()  # 5 released quota berths + the empty RAC berth = 6 free
    book(inv, seq)
    before = inv.all_statuses()
    with pytest.raises(NoAvailability):
        book(inv, seq, n=6)
    assert inv.all_statuses() == before


def test_after_chart_cancellation_frees_berth_without_promotion(inv, seq):
    (a,), _ = book(inv, seq)
    inv.prepare_first_chart()
    free_before = len(inv.free_berths(Quota.GENERAL))
    assert inv.release([a]) == []
    assert len(inv.free_berths(Quota.GENERAL)) == free_before + 1


def test_rac_partner_confirmed_when_other_cancels_after_chart(inv, seq):
    fill_general(inv, seq)
    for q in (Quota.TATKAL, Quota.TATKAL, Quota.PREMIUM_TATKAL):
        book(inv, seq, q)
    book(inv, seq, Quota.LADIES, gender=Gender.FEMALE)
    book(inv, seq, Quota.SENIOR_LOWER, age=70)
    (r1, r2), _ = book(inv, seq, n=2)
    inv.prepare_first_chart()
    moves = {m.ref: m.status for m in inv.release([r1])}
    assert moves[r2].kind is StatusKind.CONFIRMED


def test_final_chart_rules(inv, seq):
    with pytest.raises(InvalidState):
        inv.prepare_final_chart()
    inv.prepare_first_chart()
    with pytest.raises(InvalidState):
        inv.prepare_first_chart()
    inv.prepare_final_chart()
    assert inv.availability(Quota.GENERAL) == "CHART PREPARED"
    with pytest.raises(BookingClosed):
        book(inv, seq)
    with pytest.raises(InvalidState):
        inv.prepare_final_chart()


def test_release_unknown_passenger(inv):
    with pytest.raises(InvalidState):
        inv.release([PassengerRef("9999999999", 1)])
