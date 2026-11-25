"""Refund on cancellation, per passenger.

Confirmed (non-Tatkal) tickets, by time left before the scheduled departure
from the passenger's boarding station:
    more than 48 h      flat charge per class
    48 h down to 12 h   25% of the fare, at least the flat charge
    12 h down to 4 h    50% of the fare, at least the flat charge
    less than 4 h       no refund
Confirmed Tatkal and Premium Tatkal: no refund.
RAC and waitlisted: clerkage per passenger until 30 minutes before departure,
then no refund.
After the first chart, a confirmed ticket gets no refund on cancellation.
GST is added to the charge for AC classes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..config import Config, default_config
from ..domain.booking import StatusKind
from ..domain.classes import TravelClass, layout_for
from ..domain.quota import Quota
from ..money import ZERO, money, percent


@dataclass(frozen=True)
class RefundQuote:
    fare: Decimal
    charge: Decimal
    gst_on_charge: Decimal
    refund: Decimal
    rule: str


class CancellationPolicy:
    def __init__(self, config: Config | None = None):
        cfg = (config or default_config()).policy["cancellation"]
        self._flat = {TravelClass(k): money(v) for k, v in cfg["flat_charge"].items()}
        self._windows = sorted(cfg["windows"], key=lambda w: -w["from_hours"])
        self._clerkage = money(cfg["clerkage"])
        self._rac_wl_cutoff_min = int(cfg["rac_wl_cutoff_minutes"])
        self._gst_pct = Decimal(cfg["gst_on_charges_ac_percent"])

    def _finish(self, fare: Decimal, charge: Decimal, travel_class: TravelClass, rule: str) -> RefundQuote:
        charge = min(charge, fare)
        gst = percent(charge, self._gst_pct) if layout_for(travel_class).is_ac and charge else ZERO
        refund = max(fare - charge - gst, ZERO)
        return RefundQuote(fare, charge, gst, money(refund), rule)

    def none(self, fare: Decimal, rule: str) -> RefundQuote:
        return RefundQuote(fare, fare, ZERO, ZERO, rule)

    def quote(self, *, fare: Decimal, status: StatusKind, travel_class: TravelClass, quota: Quota,
              departure: datetime, now: datetime, charted: bool) -> RefundQuote:
        minutes_left = (departure - now).total_seconds() / 60
        if status in (StatusKind.RAC, StatusKind.WAITLISTED):
            if minutes_left < self._rac_wl_cutoff_min:
                return self.none(fare, "RAC/WL cancelled less than 30 minutes before departure")
            return self._finish(fare, self._clerkage, travel_class, "RAC/WL clerkage")
        if status is not StatusKind.CONFIRMED:
            raise ValueError(f"cannot quote a refund for status {status.value}")
        if quota.is_tatkal:
            return self.none(fare, "confirmed Tatkal/Premium Tatkal: no refund")
        if charted:
            return self.none(fare, "confirmed ticket cancelled after charting: no refund")
        hours_left = minutes_left / 60
        flat = self._flat[travel_class]
        for window in self._windows:
            if hours_left > window["from_hours"] or (window["kind"] != "flat" and hours_left >= window["from_hours"]):
                if window["kind"] == "flat":
                    return self._finish(fare, flat, travel_class, f"more than {window['from_hours']} h: flat charge")
                charge = max(percent(fare, window["percent"]), flat)
                return self._finish(fare, charge, travel_class,
                                    f"{window['percent']}% charge ({window['from_hours']} h or more left)")
        return self.none(fare, "confirmed ticket cancelled less than 4 hours before departure")

    def clerkage_refund(self, fare: Decimal, travel_class: TravelClass) -> RefundQuote:
        """Refund when a waitlisted passenger is dropped at charting."""
        return self._finish(fare, self._clerkage, travel_class, "waitlisted at charting: refund less clerkage")
