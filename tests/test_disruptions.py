"""Boarding change, train-run cancellation, delays, TDRs, idempotent booking and chart reports."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from railcore.domain.booking import StatusKind
from railcore.errors import BookingClosed, InvalidState, NotFound, ValidationError
from railcore.events import BOARDING_CHANGED, BOOKING_CREATED, TDR_FILED, TDR_SETTLED, TRAIN_CANCELLED
from railcore.tdr import TDRStatus

from .conftest import RUN_DATE, pax, request_for, statuses

D = Decimal


def group(n, start=0, gender="M", age=30):
    return [pax(f"P{start + i}", age, gender) for i in range(n)]


# --------------------------------------------------------------- boarding change
def test_change_boarding(service):
    b = service.book(request_for(to="BPL"))
    changed = service.change_boarding(b.pnr, "AGC")
    assert changed.boards_at == "AGC" and changed.from_station == "NDLS"
    assert service.get_booking(b.pnr).boards_at == "AGC"
    assert [e.type for e in service.repo.events()][-1] == BOARDING_CHANGED
    with pytest.raises(InvalidState):
        service.change_boarding(b.pnr, "GWL")


@pytest.mark.parametrize("station", ["NDLS", "BPL", "NGP", "JBP"])
def test_boarding_must_be_inside_the_journey(service, station):
    b = service.book(request_for(to="BPL"))
    with pytest.raises(ValidationError):
        service.change_boarding(b.pnr, station)


def test_no_boarding_change_after_chart(service, clock):
    b = service.book(request_for())
    clock.set(datetime(2026, 12, 10, 12, 0))
    with pytest.raises(BookingClosed):
        service.change_boarding(b.pnr, "AGC")


def test_confirmed_refund_lost_after_boarding_change(service):
    b = service.book(request_for())
    service.change_boarding(b.pnr, "AGC")
    assert service.cancel(b.pnr).total_refund == D("0.00")


def test_cancel_cutoff_follows_new_boarding_station(service, clock):
    b = service.book(request_for(to="NGP"))
    service.change_boarding(b.pnr, "BPL")
    clock.set(datetime(2026, 12, 10, 21, 0))  # left NDLS, not yet at BPL (06:20 next day)
    assert service.cancel(b.pnr).booking.passengers[0].current_status.kind is StatusKind.CANCELLED


def test_boarding_change_on_cancelled_booking(service):
    b = service.book(request_for())
    service.cancel(b.pnr)
    with pytest.raises(InvalidState):
        service.change_boarding(b.pnr, "AGC")


# ----------------------------------------------------------------- run cancelled
def test_cancel_run_refunds_everyone_in_full(service):
    for _ in range(4):
        service.book(request_for(group(6), cls="1A"))
    wl = service.repo.bookings_for_run("22401", RUN_DATE)[-1]
    sl = service.book(request_for(group(2)))
    assert service.cancel_run("22401", RUN_DATE, "flooding") == 26
    for booking in service.repo.bookings_for_run("22401", RUN_DATE):
        assert all(p.refund == p.fare and not p.is_active for p in booking.passengers)
    assert statuses(service.get_booking(wl.pnr))[-1] == "CAN"
    assert service.get_booking(sl.pnr).total_refund == D("820.00")
    assert service.availability("22401", RUN_DATE, "SL") == "TRAIN CANCELLED"
    events = [e for e in service.repo.events() if e.type == TRAIN_CANCELLED]
    assert events[0].payload == {"train": "22401", "start_date": "2026-12-10", "reason": "flooding",
                                 "refunded": 26}


def test_cancelled_run_blocks_everything(service):
    b = service.book(request_for())
    service.cancel_run("22401", RUN_DATE)
    with pytest.raises(BookingClosed):
        service.book(request_for())
    with pytest.raises(InvalidState):
        service.cancel(b.pnr)
    with pytest.raises(InvalidState):
        service.cancel_run("22401", RUN_DATE)
    with pytest.raises(InvalidState):
        service.prepare_chart("22401", RUN_DATE)
    with pytest.raises(InvalidState):
        service.report_delay("22401", RUN_DATE, "BPL", 0, 30)
    with pytest.raises(InvalidState):
        service.file_tdr(b.pnr, "AC_FAILURE")


def test_cannot_cancel_finished_run(service, clock):
    clock.set(datetime(2026, 12, 11, 12, 30))
    with pytest.raises(InvalidState):
        service.cancel_run("22401", RUN_DATE)


# ---------------------------------------------------------------- delays & TDR
def test_late_train_tdr_full_refund(service, clock):
    b = service.book(request_for(group(2), frm="BPL", to="NGP"))
    service.report_delay("22401", RUN_DATE, "BPL", 190, 200)
    assert service.actual_departure("22401", RUN_DATE, "BPL") == datetime(2026, 12, 11, 9, 40)
    clock.set(datetime(2026, 12, 11, 7, 0))
    tdr = service.file_tdr(b.pnr, "TRAIN_LATE_NOT_TRAVELLED", [1])
    fare = b.passengers[0].fare
    assert tdr.claimed == {1: fare} and tdr.status is TDRStatus.FILED
    stored = service.get_booking(b.pnr)
    assert stored.passengers[0].current_status.kind is StatusKind.CANCELLED
    assert stored.passengers[0].refund is None          # nothing paid until settled
    settled = service.settle_tdr(tdr.id, True, "verified")
    assert settled.status is TDRStatus.REFUNDED
    assert service.get_booking(b.pnr).passengers[0].refund == fare
    assert [e.type for e in service.repo.events()][-2:] == [TDR_FILED, TDR_SETTLED]
    with pytest.raises(InvalidState):
        service.settle_tdr(tdr.id, True)


def test_late_tdr_needs_three_hours_and_filing_before_departure(service, clock):
    b = service.book(request_for(frm="BPL", to="NGP"))
    service.report_delay("22401", RUN_DATE, "BPL", 0, 120)
    clock.set(datetime(2026, 12, 11, 7, 0))
    with pytest.raises(ValidationError):
        service.file_tdr(b.pnr, "TRAIN_LATE_NOT_TRAVELLED")
    service.report_delay("22401", RUN_DATE, "BPL", 0, 180)
    clock.set(datetime(2026, 12, 11, 9, 20))  # actual departure 09:20
    with pytest.raises(BookingClosed):
        service.file_tdr(b.pnr, "TRAIN_LATE_NOT_TRAVELLED")


def test_delay_validation(service):
    with pytest.raises(ValidationError):
        service.report_delay("22401", RUN_DATE, "BPL", -5, 0)
    with pytest.raises(ValidationError):
        service.report_delay("22401", RUN_DATE, "JBP", 0, 5)


def test_ac_failure_claims_fare_difference(service, clock):
    b = service.book(request_for(cls="3A", to="NGP"))
    service.report_delay("22401", RUN_DATE, "NGP", 60, 0)
    clock.set(datetime(2026, 12, 12, 9, 30))  # actual arrival 13:30 + 20 h = 09:30 next day
    tdr = service.file_tdr(b.pnr, "AC_FAILURE")
    assert tdr.claimed == {1: D("1005.00")}  # 3A 1595 - SL 590 for NDLS-NGP
    assert service.get_booking(b.pnr).passengers[0].is_active  # they travelled
    with pytest.raises(InvalidState):
        service.file_tdr(b.pnr, "AC_FAILURE")
    rejected = service.settle_tdr(tdr.id, False, "no TTE certificate")
    assert rejected.status is TDRStatus.REJECTED
    assert service.get_booking(b.pnr).passengers[0].refund is None
    service.file_tdr(b.pnr, "AC_FAILURE")  # a rejected claim may be filed again


def test_ac_failure_limits(service, clock):
    ac = service.book(request_for(cls="3A", to="NGP"))
    sl = service.book(request_for(to="NGP"))
    clock.set(datetime(2026, 12, 12, 8, 31))  # arrival 12:30 + 20 h = 08:30
    with pytest.raises(BookingClosed):
        service.file_tdr(ac.pnr, "AC_FAILURE")
    clock.set(datetime(2026, 12, 11, 13, 0))
    with pytest.raises(ValidationError):
        service.file_tdr(sl.pnr, "AC_FAILURE")


def fill_2a_to_rac(service, clock):
    """Fill every 2A pool on 22401 so RAC passengers stay on RAC through charting."""
    clock.set(datetime(2026, 12, 9, 10, 0))
    n = 0
    for size in (6,) * 11:
        service.book(request_for(group(size, n), cls="2A")); n += size
    for _ in range(4):
        service.book(request_for(group(4, n), cls="2A", quota="TQ")); n += 4
    service.book(request_for(group(4, n), cls="2A", quota="PT")); n += 4
    service.book(request_for(group(2, n, "F"), cls="2A", quota="LD")); n += 2
    for _ in range(3):
        service.book(request_for(group(2, n, "M", 70), cls="2A", quota="SS")); n += 2
    return service.book(request_for(group(4, n), cls="2A"))


def test_rac_not_travelled_tdr(service, clock):
    rac = fill_2a_to_rac(service, clock)
    assert [s.split("/")[0] for s in statuses(rac)] == ["RAC 1", "RAC 2", "RAC 3", "RAC 4"]
    with pytest.raises(InvalidState):
        service.file_tdr(rac.pnr, "RAC_NOT_TRAVELLED", [1])  # before charting
    clock.set(datetime(2026, 12, 10, 19, 0))
    tdr = service.file_tdr(rac.pnr, "RAC_NOT_TRAVELLED", [1])
    assert tdr.claimed == {1: D("1482.00")}  # 1545 less clerkage 60 + GST 3
    after = service.get_booking(rac.pnr)
    assert statuses(after)[:2] == ["CAN", "CNF/A1/5/SL"]  # the berth partner gets the whole berth


def test_rac_tdr_rules(service, clock):
    rac = fill_2a_to_rac(service, clock)
    clock.set(datetime(2026, 12, 10, 19, 31))
    with pytest.raises(BookingClosed):
        service.file_tdr(rac.pnr, "RAC_NOT_TRAVELLED", [1])
    b = service.repo.bookings_for_run("22401", RUN_DATE)[0]
    clock.set(datetime(2026, 12, 10, 19, 0))
    with pytest.raises(ValidationError):
        service.file_tdr(b.pnr, "RAC_NOT_TRAVELLED", [1])


def test_tdr_errors(service):
    b = service.book(request_for())
    with pytest.raises(ValidationError):
        service.file_tdr(b.pnr, "LOST_LUGGAGE")
    with pytest.raises(NotFound):
        service.file_tdr("2999999999", "AC_FAILURE")
    with pytest.raises(NotFound):
        service.settle_tdr(99, True)
    with pytest.raises(NotFound):
        service.tdrs("2999999999")
    assert service.tdrs(b.pnr) == []


# ----------------------------------------------------------------- idempotency
def test_idempotent_booking(service):
    req = request_for(group(2))
    first = service.book(req, idempotency_key="abc-1")
    again = service.book(req, idempotency_key="abc-1")
    assert again.pnr == first.pnr
    assert [e.type for e in service.repo.events()].count(BOOKING_CREATED) == 1
    with pytest.raises(ValidationError):
        service.book(request_for(group(3)), idempotency_key="abc-1")
    with pytest.raises(ValidationError):
        service.book(req, idempotency_key="x" * 101)
    assert service.book(req, idempotency_key="abc-2").pnr != first.pnr


def test_failed_booking_does_not_burn_the_key(service, clock):
    req = request_for(cls="3A", quota="TQ")
    with pytest.raises(BookingClosed):
        service.book(req, idempotency_key="retry-me")
    clock.set(datetime(2026, 12, 9, 10, 0))
    assert service.book(req, idempotency_key="retry-me").passengers[0].is_active


# ---------------------------------------------------------------- chart report
def test_chart_report(service, clock):
    rac = fill_2a_to_rac(service, clock)
    service.book(request_for(group(1, 100), cls="2A"))  # GNWL 1
    report = service.chart_report("22401", RUN_DATE, "2A")
    assert report["summary"] == {"berths": 96, "confirmed": 94, "rac_berths": 2, "vacant": 0,
                                 "rac_passengers": 4, "waitlisted": 1}
    a1_5 = next(r for r in report["coaches"]["A1"] if r["berth"] == 5)
    assert a1_5["status"] == "RAC" and [p["pnr"] for p in a1_5["passengers"]] == [rac.pnr, rac.pnr]
    assert report["waitlist"]["GNWL"][0]["name"] == "P100"
    occ = service.occupancy("22401", RUN_DATE)
    assert occ["2A"] == {"berths": 96, "confirmed": 94, "rac": 4, "waitlisted": 1}
    assert occ["SL"]["confirmed"] == 0


def test_chart_report_validation(service):
    with pytest.raises(ValidationError):
        service.chart_report("22401", RUN_DATE, "EC")
    with pytest.raises(ValidationError):
        service.chart_report("22401", RUN_DATE, "ZZ")
