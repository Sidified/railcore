"""End-to-end behaviour of the booking service, on both storage backends."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from railcore.booking import BookingService
from railcore.domain.booking import BookingState, StatusKind
from railcore.errors import BookingClosed, InvalidState, NoAvailability, NotFound, ValidationError
from railcore.events import (BOOKING_CREATED, CHART_PREPARED, PASSENGER_CANCELLED, QUOTA_RELEASED,
                             STATUS_CHANGED, WAITLIST_DROPPED)
from railcore.ids import is_valid_pnr
from railcore.storage import SQLiteRepository

from .conftest import RUN_DATE, pax, request_for, statuses

D = Decimal


def group(n, start=0, gender="M", age=30):
    return [pax(f"P{start + i}", age, gender) for i in range(n)]


def fill(service, count, cls="1A", **kw):
    """Book `count` general passengers in groups of up to 6."""
    done = 0
    while done < count:
        n = min(6, count - done)
        service.book(request_for(group(n, done), cls=cls, **kw))
        done += n


# ------------------------------------------------------------------- booking
def test_book_confirmed(service):
    b = service.book(request_for([pax("Asha", 30, "F", "LB"), pax("Ravi", 32, "M")]))
    assert is_valid_pnr(b.pnr)
    assert statuses(b) == ["CNF/S1/25/LB", "CNF/S1/11/UB"]
    assert [p.fare for p in b.passengers] == [D("410.00"), D("410.00")]
    assert b.total_fare == D("820.00")
    assert service.get_booking(b.pnr).passengers == b.passengers


def test_booking_status_kept_while_current_changes(service):
    fill(service, 22)
    wl = service.book(request_for(group(1), cls="1A"))
    assert statuses(wl) == ["GNWL 1"]
    first = service.repo.bookings_for_run("22401", RUN_DATE)[0]
    service.cancel(first.pnr, [1])
    after = service.get_booking(wl.pnr).passengers[0]
    assert after.booking_status.label() == "GNWL 1"
    assert after.current_status.kind is StatusKind.CONFIRMED


@pytest.mark.parametrize("kwargs,error", [
    (dict(passengers=[pax(age=4)]), ValidationError),
    (dict(passengers=group(7)), ValidationError),
    (dict(passengers=[pax(gender="M")], quota="LD"), ValidationError),
    (dict(passengers=[pax(age=50, gender="M")], quota="SS"), ValidationError),
    (dict(passengers=[pax(gender="X")]), ValidationError),
    (dict(passengers=[pax(pref="ZZ")]), ValidationError),
    (dict(passengers=[]), ValidationError),
    (dict(cls="1A", quota="TQ"), ValidationError),
    (dict(cls="XX"), ValidationError),
    (dict(quota="ZZ"), ValidationError),
    (dict(frm="BPL", to="NDLS"), ValidationError),
    (dict(train="12099", cls="SL", start=date(2026, 12, 7)), ValidationError),
    (dict(train="12099", cls="3A", start=date(2026, 12, 8)), ValidationError),
    (dict(start=date(2027, 2, 5)), BookingClosed),
    (dict(cls="1A", quota="PT"), ValidationError),
    (dict(train="99999"), NotFound),
])
def test_booking_validation(service, kwargs, error):
    kwargs = dict(kwargs)  # parametrize shares the dict between backends
    passengers = kwargs.pop("passengers", [pax()])
    base = request_for(**kwargs)
    req = type(base)(base.train_number, base.start_date, base.from_station, base.to_station,
                     base.travel_class, base.quota, passengers)
    with pytest.raises(error):
        service.book(req)


def test_ladies_and_senior_quotas(service):
    ld = service.book(request_for([pax("Meera", 28, "F")], quota="LD"))
    assert ld.passengers[0].pool.value == "LD"
    ss = service.book(request_for([pax("Kamla", 46, "F"), pax("Ram", 61, "M")], quota="SS"))
    assert all(p.current_status.berth_type.value == "LB" for p in ss.passengers)


def test_cannot_book_after_departure_from_boarding_station(service, clock):
    clock.set(datetime(2026, 12, 11, 6, 30))  # train left BPL at 06:20
    with pytest.raises(BookingClosed):
        service.book(request_for(frm="BPL", to="NGP"))


def test_tatkal_opening_times(service, clock):
    clock.set(datetime(2026, 12, 9, 9, 59))
    with pytest.raises(BookingClosed):
        service.book(request_for(cls="3A", quota="TQ"))
    clock.set(datetime(2026, 12, 9, 10, 0))
    assert statuses(service.book(request_for(cls="3A", quota="TQ")))[0].startswith("CNF")
    clock.set(datetime(2026, 12, 9, 10, 30))
    with pytest.raises(BookingClosed):  # non-AC Tatkal opens at 11:00
        service.book(request_for(cls="SL", quota="TQ"))
    clock.set(datetime(2026, 12, 9, 11, 0))
    b = service.book(request_for(cls="SL", quota="TQ"))
    assert b.passengers[0].fare > D("410.00")


def test_premium_tatkal_price_rises_inside_one_booking(service, clock):
    clock.set(datetime(2026, 12, 9, 11, 0))
    for i in range(2):
        service.book(request_for(group(4, i * 4), quota="PT"))
    b = service.book(request_for(group(2, 8), quota="PT"))
    first, second = (p.fare for p in b.passengers)
    assert second > first  # 8 sold before passenger 1 (x1.00), 9 before passenger 2 (x1.25)
    quote = service.quote("22401", RUN_DATE, "NDLS", "BPL", "SL", "PT", 1)[0]
    assert quote.total == second


def test_regret_is_atomic(service):
    fill(service, 22)
    for _ in range(5):
        service.book(request_for(group(6), cls="1A"))     # GNWL 1..30
    events_before = len(service.repo.events())
    with pytest.raises(NoAvailability):
        service.book(request_for(group(1), cls="1A"))
    assert len(service.repo.events()) == events_before
    b = service.book(request_for(group(1), cls="2A"))
    assert b.pnr.endswith(b.pnr[-1]) and int(b.pnr[1:9]) == 10  # PNR sequence was not used up


# --------------------------------------------------------------- cancellation
def test_cancel_refund_and_state(service):
    b = service.book(request_for(group(2)))
    result = service.cancel(b.pnr, [1])
    assert result.total_refund == D("290.00")
    stored = service.get_booking(b.pnr)
    assert stored.passengers[0].current_status.kind is StatusKind.CANCELLED
    assert stored.passengers[0].refund == D("290.00")
    assert stored.state is BookingState.ACTIVE
    service.cancel(b.pnr)
    assert service.get_booking(b.pnr).state is BookingState.CANCELLED


def test_cancel_promotes_rac_and_waitlist_on_other_pnrs(service):
    fill(service, 66, cls="2A")
    rac = service.book(request_for(group(4), cls="2A"))
    wl = service.book(request_for(group(1), cls="2A"))
    assert statuses(rac) == ["RAC 1/A1/5", "RAC 2/A1/5", "RAC 3/A2/5", "RAC 4/A2/5"]
    assert statuses(wl) == ["GNWL 1"]
    first = service.repo.bookings_for_run("22401", RUN_DATE)[0]
    result = service.cancel(first.pnr, [1])
    assert {(m.ref.pnr, m.ref.index) for m in result.moves} == {
        (rac.pnr, 1), (rac.pnr, 2), (rac.pnr, 3), (rac.pnr, 4), (wl.pnr, 1)}
    assert statuses(service.get_booking(rac.pnr))[0].startswith("CNF")
    assert statuses(service.get_booking(rac.pnr))[1:] == ["RAC 1/A1/5", "RAC 2/A2/5", "RAC 3/A2/5"]
    assert statuses(service.get_booking(wl.pnr)) == ["RAC 4/A1/5"]
    changed = [e for e in service.repo.events() if e.type == STATUS_CHANGED]
    assert len(changed) == 5


def test_cancel_errors(service, clock):
    b = service.book(request_for(group(2)))
    with pytest.raises(ValidationError):
        service.cancel(b.pnr, [3])
    with pytest.raises(ValidationError):
        service.cancel(b.pnr, [1, 1])
    service.cancel(b.pnr, [1])
    with pytest.raises(InvalidState):
        service.cancel(b.pnr, [1])
    with pytest.raises(NotFound):
        service.cancel("2999999999")
    clock.set(datetime(2026, 12, 10, 20, 1))
    with pytest.raises(BookingClosed):
        service.cancel(b.pnr)


def test_refund_quote_does_not_cancel(service):
    b = service.book(request_for(group(1)))
    quotes = service.refund_quote(b.pnr)
    assert quotes[1].refund == D("290.00")
    assert service.get_booking(b.pnr).passengers[0].is_active


# ------------------------------------------------------------------- charting
def test_lazy_first_chart_drops_waitlist_with_refund(service, clock):
    fill(service, 22)
    service.book(request_for([pax("Old", 70, "M"), pax("Older", 72, "F")], cls="1A", quota="SS"))
    wl = service.book(request_for(group(2), cls="1A"))
    assert statuses(wl) == ["GNWL 1", "GNWL 2"]
    clock.set(datetime(2026, 12, 10, 12, 0))  # 8 hours before 20:00
    after = service.get_booking(wl.pnr)
    assert statuses(after) == ["CAN", "CAN"]
    assert [p.refund for p in after.passengers] == [D("2487.00"), D("2487.00")]
    types = [e.type for e in service.repo.events()]
    assert types.count(WAITLIST_DROPPED) == 2
    assert CHART_PREPARED in types
    assert service.repo.get_run("22401", RUN_DATE).chart.value == "FIRST"


def test_chart_releases_unused_quota_to_waitlist(service, clock):
    fill(service, 22)
    wl = service.book(request_for(group(2), cls="1A"))  # 2 senior berths are still free
    clock.set(datetime(2026, 12, 10, 12, 0))
    assert statuses(service.get_booking(wl.pnr))[0].startswith("CNF")
    released = [e for e in service.repo.events() if e.type == QUOTA_RELEASED]
    assert any(e.payload["travel_class"] == "1A" and e.payload["released"] == {"SS": 2} for e in released)


def test_after_first_chart_current_booking_only(service, clock):
    clock.set(datetime(2026, 12, 10, 13, 0))
    with pytest.raises(BookingClosed):
        service.book(request_for(cls="3A", quota="TQ"))
    b = service.book(request_for())
    assert statuses(b)[0].startswith("CNF")
    assert service.availability("22401", RUN_DATE, "SL").startswith("CURRENT AVAILABLE-")


def test_confirmed_cancel_after_chart_has_no_refund(service, clock):
    b = service.book(request_for())
    clock.set(datetime(2026, 12, 10, 13, 0))
    assert service.cancel(b.pnr).total_refund == D("0.00")


def test_final_chart_closes_booking(service, clock):
    clock.set(datetime(2026, 12, 10, 19, 30))
    assert service.availability("22401", RUN_DATE, "SL") == "CHART PREPARED"
    with pytest.raises(BookingClosed):
        service.book(request_for())


def test_manual_chart_preparation(service):
    with pytest.raises(InvalidState):
        service.prepare_chart("22401", RUN_DATE, "final")
    result = service.prepare_chart("22401", RUN_DATE, "first")
    assert result.kind == "first" and "SL" in result.released
    with pytest.raises(InvalidState):
        service.prepare_chart("22401", RUN_DATE, "first")
    service.prepare_chart("22401", RUN_DATE, "final")
    with pytest.raises(InvalidState):
        service.prepare_chart("22401", RUN_DATE, "final")
    with pytest.raises(ValidationError):
        service.prepare_chart("22401", date(2026, 12, 11), "other")


# ---------------------------------------------------------- queries & events
def test_availability_strings(service):
    assert service.availability("22401", RUN_DATE, "1A") == "AVAILABLE-0022"
    fill(service, 22)
    assert service.availability("22401", RUN_DATE, "1A") == "GNWL1/WL1"
    assert service.availability("22401", RUN_DATE, "1A", "SS") == "AVAILABLE-0002"
    assert service.availability("22401", RUN_DATE, "1A", "PT") == "NOT AVAILABLE"
    with pytest.raises(ValidationError):
        service.availability("12099", date(2026, 12, 7), "SL")


def test_event_log_order(service):
    b = service.book(request_for())
    service.cancel(b.pnr)
    events = service.repo.events()
    assert [e.type for e in events] == [BOOKING_CREATED, PASSENGER_CANCELLED]
    assert [e.seq for e in events] == [1, 2]
    assert events[0].payload["pnr"] == b.pnr
    assert service.repo.events(after_seq=1)[0].type == PASSENGER_CANCELLED


# ----------------------------------------------------------------- persistence
def test_state_survives_restart(tmp_path, clock):
    path = tmp_path / "rc.db"
    first = BookingService(SQLiteRepository(path), clock=clock)
    fill(first, 22)
    w1 = first.book(request_for(group(1), cls="1A"))
    second = BookingService(SQLiteRepository(path), clock=clock)
    w2 = second.book(request_for(group(1), cls="1A"))
    assert (statuses(w1), statuses(w2)) == (["GNWL 1"], ["GNWL 2"])
    second.cancel(w1.pnr)
    w3 = BookingService(SQLiteRepository(path), clock=clock).book(request_for(group(1), cls="1A"))
    assert w3.passengers[0].booking_status.label() == "GNWL 3"  # issued numbers keep counting
    assert statuses(w3) == ["GNWL 2"]                           # but it is second in the queue
    assert statuses(second.get_booking(w2.pnr)) == ["GNWL 1"]  # current position moved up
