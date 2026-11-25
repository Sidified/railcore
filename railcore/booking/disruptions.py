"""Things that happen to a booking or a run after it is sold.

Boarding-point change, cancelling a whole train run, delay reports, and
TDR claims (filing and settling). Mixed into BookingService, so `self` is
the service and every method runs in one repository transaction.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from ..domain.booking import Booking, PassengerRecord, SeatStatus, StatusKind
from ..domain.quota import Quota
from ..errors import BookingClosed, InvalidState, NotFound, ValidationError
from ..events import (BOARDING_CHANGED, DELAY_REPORTED, PASSENGER_CANCELLED, TDR_FILED, TDR_SETTLED,
                      TRAIN_CANCELLED)
from ..inventory import ChartState
from ..refunds import RefundQuote
from ..tdr import TDR, TDRPolicy, TDRReason, TDRStatus
from ..tdr.policy import AC_FALLBACK
from .changes import Changes


class DisruptionsMixin:
    tdr_policy = TDRPolicy()

    # ------------------------------------------------------- boarding change
    def change_boarding(self, pnr: str, station: str) -> Booking:
        """Move the boarding point later along the route. Allowed once, before the first chart.

        The fare is not changed and nothing is refunded for the unused part.
        """
        with self.repo.transaction():
            booking = self._active_booking(pnr)
            train = self.network.train(booking.train_number)
            meta = self._ensure_charts(train, booking.start_date)
            if meta.cancelled:
                raise InvalidState("the train run was cancelled")
            if booking.boarding_station is not None:
                raise InvalidState("the boarding point can be changed only once")
            if meta.chart is not ChartState.OPEN:
                raise BookingClosed("the boarding point cannot be changed after charting")
            a, b = train.check_segment(booking.from_station, booking.to_station)
            new = train.stop_index(station)
            if not a < new < b:
                raise ValidationError(f"{station} must be after {booking.from_station} and before "
                                      f"{booking.to_station}")
            old = booking.boards_at
            booking.boarding_station = station
            self.repo.save_booking(booking)
            self._publish(BOARDING_CHANGED, pnr=pnr, old=old, new=station)
            return booking

    # ------------------------------------------------------------ run events
    def cancel_run(self, train_number: str, start_date: date, reason: str = "") -> int:
        """Cancel a whole train run: every active passenger gets the full fare back.

        Returns the number of passengers refunded.
        """
        train = self.network.train(train_number)
        with self.repo.transaction():
            meta = self.repo.get_run(train_number, start_date)
            if meta.cancelled:
                raise InvalidState("this train run is already cancelled")
            if self.clock.now() >= train.arrival(train.destination, start_date):
                raise InvalidState("the run has already finished")
            refunded = 0
            for booking in self.repo.bookings_for_run(train_number, start_date):
                for p in booking.passengers:
                    if not p.is_active:
                        continue
                    quote = RefundQuote(p.fare, Decimal("0.00"), Decimal("0.00"), p.fare,
                                        "train run cancelled: full refund")
                    self._cancel_record(p, quote)
                    refunded += 1
                    self._publish(PASSENGER_CANCELLED, pnr=booking.pnr, passenger=p.index,
                                  refund=str(p.fare), rule=quote.rule)
                self.repo.save_booking(booking)
            meta.cancelled = True
            self.repo.save_run(meta)
            self._publish(TRAIN_CANCELLED, train=train_number, start_date=start_date.isoformat(),
                          reason=reason, refunded=refunded)
            return refunded

    def report_delay(self, train_number: str, start_date: date, station: str,
                     arrival_delay: int = 0, departure_delay: int = 0) -> None:
        train = self.network.train(train_number)
        train.stop_index(station)
        if arrival_delay < 0 or departure_delay < 0:
            raise ValidationError("delays cannot be negative")
        with self.repo.transaction():
            meta = self.repo.get_run(train_number, start_date)
            if meta.cancelled:
                raise InvalidState("the train run was cancelled")
            meta.delays[station] = (int(arrival_delay), int(departure_delay))
            self.repo.save_run(meta)
            self._publish(DELAY_REPORTED, train=train_number, start_date=start_date.isoformat(),
                          station=station, arrival_delay=arrival_delay, departure_delay=departure_delay)

    def actual_departure(self, train_number: str, start_date: date, station: str) -> datetime:
        train = self.network.train(train_number)
        delay = self.repo.get_run(train_number, start_date).delays.get(station, (0, 0))[1]
        return train.departure(station, start_date) + timedelta(minutes=delay)

    def actual_arrival(self, train_number: str, start_date: date, station: str) -> datetime:
        train = self.network.train(train_number)
        delay = self.repo.get_run(train_number, start_date).delays.get(station, (0, 0))[0]
        return train.arrival(station, start_date) + timedelta(minutes=delay)

    # ------------------------------------------------------------------ TDRs
    def file_tdr(self, pnr: str, reason: str, passengers: list[int] | None = None) -> TDR:
        try:
            why = TDRReason(reason)
        except ValueError:
            raise ValidationError(f"unknown TDR reason {reason!r}") from None
        now = self.clock.now()
        with self.repo.transaction():
            existing = self.repo.get_booking(pnr)
            if existing is None:
                raise NotFound(f"no booking with PNR {pnr}")
            train = self.network.train(existing.train_number)
            meta = self._ensure_charts(train, existing.start_date)
            if meta.cancelled:
                raise InvalidState("the train run was cancelled; the refund was automatic")
            changes = Changes(self.repo)
            booking = changes.get(pnr)
            records = self._tdr_records(booking, passengers, why)
            claimed: dict[int, Decimal] = {}
            if why is TDRReason.TRAIN_LATE_NOT_TRAVELLED:
                delay = meta.delays.get(booking.boards_at, (0, 0))[1]
                self.tdr_policy.check_late(delay, self.actual_departure(train.number, booking.start_date,
                                                                        booking.boards_at), now)
                claimed = {p.index: p.fare for p in records}
            elif why is TDRReason.AC_FAILURE:
                arrival = self.actual_arrival(train.number, booking.start_date, booking.to_station)
                self.tdr_policy.check_ac_failure(booking.travel_class, arrival, now)
                lower = self.fares.fare(train, booking.from_station, booking.to_station,
                                        AC_FALLBACK[booking.travel_class], Quota.GENERAL).total
                claimed = {p.index: self.tdr_policy.difference(p.fare, lower) for p in records}
            else:  # RAC_NOT_TRAVELLED
                if meta.chart is ChartState.OPEN:
                    raise InvalidState("RAC TDR can be filed only after charting")
                self.tdr_policy.check_rac(train.departure(booking.boards_at, booking.start_date), now)
                claimed = {p.index: self.cancellation.clerkage_refund(p.fare, booking.travel_class).refund
                           for p in records}
            if why is not TDRReason.AC_FAILURE:
                # the passengers did not travel: free their places (partners on RAC move up)
                inv = self._inventory(train, booking.start_date, booking.travel_class, meta.chart)
                moves = inv.release(booking.ref(p.index) for p in records)
                for p in records:
                    p.current_status = SeatStatus.cancelled()
                    p.cancelled_at = now
                    p.pool = None
                self._apply_moves(changes, moves)
            changes.save()
            tdr = TDR(self.repo.next_tdr_id(), pnr, [p.index for p in records], why, now, claimed=claimed)
            self.repo.save_tdr(tdr)
            self._publish(TDR_FILED, tdr=tdr.id, pnr=pnr, reason=why.value, passengers=tdr.passengers,
                          claimed=str(tdr.total))
            return tdr

    def _tdr_records(self, booking: Booking, passengers: list[int] | None,
                     why: TDRReason) -> list[PassengerRecord]:
        already = {i for t in self.repo.tdrs_for_pnr(booking.pnr) if t.status is not TDRStatus.REJECTED
                   for i in t.passengers}
        if passengers is None:
            chosen = [p for p in booking.passengers if p.is_active and p.index not in already]
        else:
            try:
                chosen = [booking.record(i) for i in passengers]
            except KeyError as exc:
                raise ValidationError(f"no passenger {exc.args[0]} on PNR {booking.pnr}") from None
        if not chosen:
            raise InvalidState("no passenger on this PNR can file this TDR")
        for p in chosen:
            if p.index in already:
                raise InvalidState(f"passenger {p.index} already has a TDR")
            if not p.is_active:
                raise InvalidState(f"passenger {p.index} is cancelled")
            if why is TDRReason.RAC_NOT_TRAVELLED and p.current_status.kind is not StatusKind.RAC:
                raise ValidationError(f"passenger {p.index} is not on RAC")
            if why is TDRReason.AC_FAILURE and p.current_status.kind is StatusKind.WAITLISTED:
                raise ValidationError(f"passenger {p.index} was waitlisted and did not travel")
        return chosen

    def settle_tdr(self, tdr_id: int, approve: bool, note: str = "") -> TDR:
        with self.repo.transaction():
            tdr = self.repo.get_tdr(tdr_id)
            if tdr is None:
                raise NotFound(f"no TDR {tdr_id}")
            if tdr.status is not TDRStatus.FILED:
                raise InvalidState(f"TDR {tdr_id} is already {tdr.status.value}")
            tdr.status = TDRStatus.REFUNDED if approve else TDRStatus.REJECTED
            tdr.note = note
            tdr.settled_at = self.clock.now()
            booking = self.repo.get_booking(tdr.pnr)
            for index in tdr.passengers:
                record = booking.record(index)
                if approve:
                    record.refund = (record.refund or Decimal("0.00")) + tdr.claimed[index]
                elif record.refund is None and not record.is_active:
                    record.refund = Decimal("0.00")
            self.repo.save_booking(booking)
            self.repo.save_tdr(tdr)
            self._publish(TDR_SETTLED, tdr=tdr.id, pnr=tdr.pnr, status=tdr.status.value,
                          refund=str(tdr.total if approve else Decimal("0.00")))
            return tdr

    def tdrs(self, pnr: str) -> list[TDR]:
        if self.repo.get_booking(pnr) is None:
            raise NotFound(f"no booking with PNR {pnr}")
        return self.repo.tdrs_for_pnr(pnr)

    # --------------------------------------------------------------- helpers
    def _active_booking(self, pnr: str) -> Booking:
        booking = self.repo.get_booking(pnr)
        if booking is None:
            raise NotFound(f"no booking with PNR {pnr}")
        if not any(p.is_active for p in booking.passengers):
            raise InvalidState("every passenger on this PNR is cancelled")
        return booking
