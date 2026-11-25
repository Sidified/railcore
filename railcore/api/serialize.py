"""Turns domain objects into JSON-ready dicts (shared by the API and CLI)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain.booking import Booking, PassengerRecord, SeatStatus
from ..domain.network import Train
from ..fares import FareBreakdown
from ..refunds import RefundQuote


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def status(s: SeatStatus) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": s.kind.value, "label": s.label()}
    for name in ("coach", "berth", "rac_number", "wl_number"):
        value = getattr(s, name)
        if value is not None:
            out[name] = value
    if s.berth_type is not None:
        out["berth_type"] = s.berth_type.value
    if s.waitlist is not None:
        out["waitlist"] = s.waitlist.value
    return out


def passenger(p: PassengerRecord) -> dict[str, Any]:
    return {
        "index": p.index,
        "name": p.passenger.name,
        "age": p.passenger.age,
        "gender": p.passenger.gender.value,
        "booking_status": status(p.booking_status),
        "current_status": status(p.current_status),
        "fare": money(p.fare),
        "refund": money(p.refund),
        "cancelled_at": p.cancelled_at.isoformat() if p.cancelled_at else None,
    }


def booking(b: Booking) -> dict[str, Any]:
    return {
        "pnr": b.pnr,
        "train_number": b.train_number,
        "start_date": b.start_date.isoformat(),
        "from": b.from_station,
        "to": b.to_station,
        "class": b.travel_class.value,
        "quota": b.quota.value,
        "booked_at": b.booked_at.isoformat(),
        "state": b.state.value,
        "total_fare": money(b.total_fare),
        "total_refund": money(b.total_refund),
        "passengers": [passenger(p) for p in b.passengers],
    }


def fare(f: FareBreakdown) -> dict[str, Any]:
    return f.as_dict()


def refund(q: RefundQuote) -> dict[str, Any]:
    return {"fare": money(q.fare), "charge": money(q.charge), "gst_on_charge": money(q.gst_on_charge),
            "refund": money(q.refund), "rule": q.rule}


def train(t: Train) -> dict[str, Any]:
    return {
        "number": t.number,
        "name": t.name,
        "type": t.type,
        "runs_on": list(t.runs_on),
        "classes": {c.value: n for c, n in t.composition.items()},
        "stops": [{"station": s.station, "arr": s.arr.strftime("%H:%M") if s.arr else None,
                   "dep": s.dep.strftime("%H:%M") if s.dep else None, "day": s.day, "km": s.km}
                  for s in t.stops],
    }
