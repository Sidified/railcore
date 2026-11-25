"""The booking service: the one place that changes reservations.

Every public method runs in one repository transaction:
  1. load the run's inventory from stored bookings (never trusted from memory)
  2. prepare any chart that is already due (charts are lazy)
  3. validate, allocate / release, price, refund
  4. save every booking whose passengers changed, and publish events
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ..charting import ChartSchedule
from ..clock import Clock, SystemClock
from ..config import Config, default_config
from ..domain.booking import Booking, PassengerRecord, PassengerRef, SeatStatus, StatusKind
from ..domain.classes import TravelClass
from ..domain.network import Network, Train
from ..domain.quota import Quota, WaitlistType
from ..errors import BookingClosed, InvalidState, NotFound, ValidationError
from ..events import (BOOKING_CREATED, CHART_PREPARED, PASSENGER_CANCELLED, QUOTA_RELEASED,
                      STATUS_CHANGED, WAITLIST_DROPPED, Event, EventBus)
from ..fares import FareBreakdown, FareCalculator
from ..ids import make_pnr
from ..inventory import ChartState, ClassInventory, Move
from ..refunds import CancellationPolicy, RefundQuote
from ..storage.base import Repository, RunMeta
from .requests import BookingRequest
from .validation import BookingRules


@dataclass
class CancellationResult:
    booking: Booking
    refunds: dict[int, RefundQuote]
    moves: list[Move] = field(default_factory=list)

    @property
    def total_refund(self) -> Decimal:
        return sum((q.refund for q in self.refunds.values()), Decimal("0.00"))


@dataclass
class ChartResult:
    train_number: str
    start_date: date
    kind: str
    moves: list[Move] = field(default_factory=list)
    dropped: list[PassengerRef] = field(default_factory=list)
    released: dict[str, dict[str, int]] = field(default_factory=dict)


class _Changes:
    """Bookings touched during one operation, saved together at the end."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self.bookings: dict[str, Booking] = {}

    def get(self, pnr: str) -> Booking:
        if pnr not in self.bookings:
            found = self.repo.get_booking(pnr)
            if found is None:
                raise NotFound(f"no booking with PNR {pnr}")
            self.bookings[pnr] = found
        return self.bookings[pnr]

    def add(self, booking: Booking) -> None:
        self.bookings[booking.pnr] = booking

    def save(self) -> None:
        for booking in self.bookings.values():
            self.repo.save_booking(booking)


class BookingService:
    def __init__(self, repo: Repository, *, network: Network | None = None, clock: Clock | None = None,
                 config: Config | None = None, bus: EventBus | None = None):
        self.config = config or default_config()
        self.repo = repo
        self.network = network or Network.from_config(self.config)
        self.clock = clock or SystemClock()
        self.fares = FareCalculator(self.config)
        self.cancellation = CancellationPolicy(self.config)
        self.charts = ChartSchedule.from_config(self.config)
        self.rules = BookingRules(self.config.policy)
        self.bus = bus or EventBus()
        self.bus.subscribe(None, self.repo.append_event)

    # ================================================================ loading
    def _inventory(self, train: Train, start_date: date, travel_class: TravelClass,
                   chart: ChartState) -> ClassInventory:
        rules = self.config.quotas[travel_class.value]
        inv = ClassInventory(train.number, travel_class, train.composition[travel_class], rules)
        rac: list[tuple[int, PassengerRef]] = []
        wls: dict[WaitlistType, list[tuple[int, PassengerRef]]] = {w: [] for w in WaitlistType}
        wl_issued = {w: 0 for w in WaitlistType}
        rac_issued = 0
        pt_sold = 0
        for booking in self.repo.bookings_for_run(train.number, start_date, travel_class):
            for p in booking.passengers:
                ref = booking.ref(p.index)
                b = p.booking_status
                if b.kind is StatusKind.WAITLISTED:
                    wl_issued[b.waitlist] = max(wl_issued[b.waitlist], b.wl_number)
                if b.kind is StatusKind.RAC:
                    rac_issued = max(rac_issued, b.rac_number)
                if booking.quota is Quota.PREMIUM_TATKAL:
                    pt_sold += 1
                cur = p.current_status
                if cur.kind is StatusKind.CANCELLED:
                    continue
                inv.place_existing(ref, cur)
                if cur.kind is StatusKind.RAC:
                    rac.append((cur.rac_number, ref))
                elif cur.kind is StatusKind.WAITLISTED:
                    wls[cur.waitlist].append((cur.wl_number, ref))
        inv.finish_restore(
            rac_order=[r for _, r in sorted(rac, key=lambda x: x[0])],
            wl_order={w: [r for _, r in sorted(v, key=lambda x: x[0])] for w, v in wls.items()},
            wl_issued=wl_issued, rac_issued=rac_issued, pt_sold=pt_sold, chart=chart,
        )
        return inv

    def _publish(self, event_type: str, **payload) -> None:
        self.bus.publish(Event(event_type, self.clock.now(), payload))

    def _apply_moves(self, changes: _Changes, moves: list[Move]) -> None:
        for move in moves:
            booking = changes.get(move.ref.pnr)
            record = booking.record(move.ref.index)
            old = record.current_status
            record.current_status = move.status
            record.pool = move.pool
            self._publish(STATUS_CHANGED, pnr=move.ref.pnr, passenger=move.ref.index,
                          old=old.label(), new=move.status.label(), reason=move.reason)

    # ================================================================ charting
    def _ensure_charts(self, train: Train, start_date: date) -> RunMeta:
        meta = self.repo.get_run(train.number, start_date)
        now = self.clock.now()
        if meta.chart is ChartState.OPEN and now >= self.charts.first_chart_at(train, start_date):
            self._first_chart(train, start_date, meta)
        if meta.chart is ChartState.FIRST and now >= self.charts.final_chart_at(train, start_date):
            self._final_chart(train, start_date, meta)
        return meta

    def _first_chart(self, train: Train, start_date: date, meta: RunMeta) -> ChartResult:
        result = ChartResult(train.number, start_date, "first")
        changes = _Changes(self.repo)
        for travel_class in train.composition:
            inv = self._inventory(train, start_date, travel_class, meta.chart)
            moves, dropped, released = inv.prepare_first_chart()
            self._apply_moves(changes, moves)
            result.moves += moves
            for ref in dropped:
                booking = changes.get(ref.pnr)
                record = booking.record(ref.index)
                quote = self.cancellation.clerkage_refund(record.fare, travel_class)
                self._cancel_record(record, quote)
                result.dropped.append(ref)
                self._publish(WAITLIST_DROPPED, pnr=ref.pnr, passenger=ref.index, refund=str(quote.refund))
            if released:
                result.released[travel_class.value] = released
                self._publish(QUOTA_RELEASED, train=train.number, start_date=start_date.isoformat(),
                              travel_class=travel_class.value, released=released)
        changes.save()
        meta.chart = ChartState.FIRST
        self.repo.save_run(meta)
        self._publish(CHART_PREPARED, train=train.number, start_date=start_date.isoformat(), kind="first")
        return result

    def _final_chart(self, train: Train, start_date: date, meta: RunMeta) -> ChartResult:
        meta.chart = ChartState.FINAL
        self.repo.save_run(meta)
        self._publish(CHART_PREPARED, train=train.number, start_date=start_date.isoformat(), kind="final")
        return ChartResult(train.number, start_date, "final")

    def prepare_chart(self, train_number: str, start_date: date, kind: str = "first") -> ChartResult:
        """Prepare a chart now, even before its scheduled time (an operator action)."""
        train = self.network.train(train_number)
        if not train.runs_on_date(start_date):
            raise ValidationError(f"train {train_number} does not run on {start_date.isoformat()}")
        with self.repo.transaction():
            meta = self.repo.get_run(train_number, start_date)
            if kind == "first":
                if meta.chart is not ChartState.OPEN:
                    raise InvalidState("first chart already prepared")
                return self._first_chart(train, start_date, meta)
            if kind == "final":
                if meta.chart is ChartState.OPEN:
                    raise InvalidState("first chart must be prepared before the final chart")
                if meta.chart is ChartState.FINAL:
                    raise InvalidState("final chart already prepared")
                return self._final_chart(train, start_date, meta)
            raise ValidationError("chart kind must be 'first' or 'final'")

    # ================================================================ queries
    @staticmethod
    def _parse(travel_class: str, quota: str) -> tuple[TravelClass, Quota]:
        try:
            return TravelClass.parse(travel_class), Quota.parse(quota)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None

    def availability(self, train_number: str, start_date: date, travel_class: str, quota: str = "GN") -> str:
        train = self.network.train(train_number)
        cls, q = self._parse(travel_class, quota)
        if not train.carries(cls):
            raise ValidationError(f"train {train_number} has no {cls.value} coaches")
        with self.repo.transaction():
            meta = self._ensure_charts(train, start_date)
            return self._inventory(train, start_date, cls, meta.chart).availability(q)

    def quote(self, train_number: str, start_date: date, from_station: str, to_station: str,
              travel_class: str, quota: str = "GN", passengers: int = 1) -> list[FareBreakdown]:
        train = self.network.train(train_number)
        cls, q = self._parse(travel_class, quota)
        if not train.carries(cls):
            raise ValidationError(f"train {train_number} has no {cls.value} coaches")
        self.rules.check_class(train, cls, q)
        pt_sold, pt_pool = 0, 0
        if q is Quota.PREMIUM_TATKAL:
            meta = self.repo.get_run(train_number, start_date)
            inv = self._inventory(train, start_date, cls, meta.chart)
            pt_sold, pt_pool = inv.pt_sold, inv.pool_size(Quota.PREMIUM_TATKAL)
        return [self.fares.fare(train, from_station, to_station, cls, q, pt_sold + i, pt_pool)
                for i in range(passengers)]

    def get_booking(self, pnr: str) -> Booking:
        booking = self.repo.get_booking(pnr)
        if booking is None:
            raise NotFound(f"no booking with PNR {pnr}")
        train = self.network.train(booking.train_number)
        with self.repo.transaction():
            self._ensure_charts(train, booking.start_date)
        return self.repo.get_booking(pnr)

    # ================================================================ booking
    def book(self, request: BookingRequest) -> Booking:
        train = self.network.train(request.train_number)
        cls, quota = request.parsed_class(), request.parsed_quota()
        passengers = [p.to_passenger() for p in request.passengers]
        train.check_segment(request.from_station, request.to_station)
        self.rules.check_passengers(quota, passengers)
        self.rules.check_class(train, cls, quota)
        now = self.clock.now()
        with self.repo.transaction():
            meta = self._ensure_charts(train, request.start_date)
            self.rules.check_dates(train, request.start_date, request.from_station, now, cls, quota)
            if meta.chart is ChartState.FINAL:
                raise BookingClosed("final chart prepared; booking is closed")
            if meta.chart is ChartState.FIRST and quota is not Quota.GENERAL:
                raise BookingClosed("after charting only current (general) booking is open")
            inv = self._inventory(train, request.start_date, cls, meta.chart)
            self.rules.check_quota_pool(cls, quota, inv.pool_size(quota))
            pnr = make_pnr(self.repo.next_pnr_sequence())
            refs = [PassengerRef(pnr, i + 1) for i in range(len(passengers))]
            pt_before, pt_pool = inv.pt_sold, inv.pool_size(Quota.PREMIUM_TATKAL)
            allocations = inv.allocate(refs, passengers, quota)
            booking = Booking(pnr, train.number, request.start_date, request.from_station,
                              request.to_station, cls, quota, now)
            for i, (pax, alloc) in enumerate(zip(passengers, allocations)):
                fare = self.fares.fare(train, request.from_station, request.to_station, cls, quota,
                                       pt_before + i, pt_pool)
                booking.passengers.append(PassengerRecord(
                    index=i + 1, passenger=pax, booking_status=alloc.booking_status,
                    current_status=alloc.status, fare=fare.total, pool=alloc.pool))
            self.repo.save_booking(booking)
            self._publish(BOOKING_CREATED, pnr=pnr, train=train.number, start_date=request.start_date.isoformat(),
                          travel_class=cls.value, quota=quota.value,
                          statuses=[p.current_status.label() for p in booking.passengers],
                          total_fare=str(booking.total_fare))
            return booking

    # ============================================================ cancellation
    def _cancel_record(self, record: PassengerRecord, quote: RefundQuote) -> None:
        record.current_status = SeatStatus.cancelled()
        record.refund = quote.refund
        record.cancelled_at = self.clock.now()
        record.pool = None

    def refund_quote(self, pnr: str, passengers: list[int] | None = None) -> dict[int, RefundQuote]:
        booking = self.get_booking(pnr)
        return self._refund_quotes(booking, self._select(booking, passengers))

    def _select(self, booking: Booking, passengers: list[int] | None) -> list[PassengerRecord]:
        if passengers is None:
            chosen = [p for p in booking.passengers if p.is_active]
        else:
            if len(set(passengers)) != len(passengers):
                raise ValidationError("a passenger is listed twice")
            try:
                chosen = [booking.record(i) for i in passengers]
            except KeyError as exc:
                raise ValidationError(f"no passenger {exc.args[0]} on PNR {booking.pnr}") from None
        if not chosen:
            raise InvalidState("nothing left to cancel on this PNR")
        for p in chosen:
            if not p.is_active:
                raise InvalidState(f"passenger {p.index} is already cancelled")
        return chosen

    def _refund_quotes(self, booking: Booking, records: list[PassengerRecord]) -> dict[int, RefundQuote]:
        train = self.network.train(booking.train_number)
        meta = self.repo.get_run(train.number, booking.start_date)
        departure = train.departure(booking.from_station, booking.start_date)
        now = self.clock.now()
        if now >= departure:
            raise BookingClosed("the train has left the boarding station; file a TDR instead")
        return {
            p.index: self.cancellation.quote(
                fare=p.fare, status=p.current_status.kind, travel_class=booking.travel_class,
                quota=booking.quota, departure=departure, now=now,
                charted=meta.chart is not ChartState.OPEN)
            for p in records
        }

    def cancel(self, pnr: str, passengers: list[int] | None = None) -> CancellationResult:
        with self.repo.transaction():
            existing = self.repo.get_booking(pnr)
            if existing is None:
                raise NotFound(f"no booking with PNR {pnr}")
            train = self.network.train(existing.train_number)
            meta = self._ensure_charts(train, existing.start_date)
            changes = _Changes(self.repo)
            booking = changes.get(pnr)
            records = self._select(booking, passengers)
            quotes = self._refund_quotes(booking, records)
            inv = self._inventory(train, booking.start_date, booking.travel_class, meta.chart)
            moves = inv.release(booking.ref(p.index) for p in records)
            for p in records:
                self._cancel_record(p, quotes[p.index])
                self._publish(PASSENGER_CANCELLED, pnr=pnr, passenger=p.index,
                              refund=str(quotes[p.index].refund), rule=quotes[p.index].rule)
            self._apply_moves(changes, moves)
            changes.save()
            return CancellationResult(changes.get(pnr), quotes, moves)
