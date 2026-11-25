"""Read-only reports built from the inventory: the coach-wise reservation chart
and an occupancy summary. Mixed into BookingService."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from ..domain.booking import PassengerRef
from ..domain.classes import TravelClass
from ..errors import ValidationError


class ReportsMixin:
    def chart_report(self, train_number: str, start_date: date, travel_class: str) -> dict[str, Any]:
        """Every berth of every coach with who holds it, like the chart pasted on a coach door."""
        train = self.network.train(train_number)
        try:
            cls = TravelClass.parse(travel_class)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        if not train.carries(cls):
            raise ValidationError(f"train {train_number} has no {cls.value} coaches")
        with self.repo.transaction():
            meta = self._ensure_charts(train, start_date)
            inv = self._inventory(train, start_date, cls, meta.chart)
            people: dict[PassengerRef, dict[str, Any]] = {}
            for booking in self.repo.bookings_for_run(train_number, start_date, cls):
                for p in booking.passengers:
                    if p.is_active:
                        people[booking.ref(p.index)] = {
                            "pnr": booking.pnr, "passenger": p.index, "name": p.passenger.name,
                            "age": p.passenger.age, "gender": p.passenger.gender.value,
                            "boards": booking.boards_at, "alights": booking.to_station,
                        }
        coaches: dict[str, list[dict[str, Any]]] = {c: [] for c in inv.coaches}
        counts: Counter[str] = Counter()
        for (coach, number), berth in sorted(inv.berths.items(), key=lambda kv: inv._sort_key(kv[0])):
            row: dict[str, Any] = {"berth": number, "type": berth.type.value, "pool": berth.pool.value}
            if (coach, number) in inv.occupant:
                row["status"] = "CNF"
                row["passengers"] = [people[inv.occupant[(coach, number)]]]
            elif berth.is_rac and inv.rac_slots.get((coach, number)):
                row["status"] = "RAC"
                row["passengers"] = [people[r] for r in inv.rac_slots[(coach, number)]]
            else:
                row["status"] = "VACANT"
                row["passengers"] = []
            counts[row["status"]] += 1
            coaches[coach].append(row)
        waiting = {wl.value: [people[r] for r in queue] for wl, queue in inv.waitlists.items() if queue}
        return {
            "train": train_number, "start_date": start_date.isoformat(), "class": cls.value,
            "chart": meta.chart.value, "cancelled": meta.cancelled,
            "summary": {"berths": len(inv.berths), "confirmed": counts["CNF"], "rac_berths": counts["RAC"],
                        "vacant": counts["VACANT"], "rac_passengers": len(inv.rac_queue),
                        "waitlisted": sum(len(q) for q in inv.waitlists.values())},
            "coaches": coaches,
            "waitlist": waiting,
        }

    def occupancy(self, train_number: str, start_date: date) -> dict[str, dict[str, int]]:
        """Per class: berths, confirmed, RAC passengers and waitlisted passengers."""
        train = self.network.train(train_number)
        out: dict[str, dict[str, int]] = {}
        for cls in train.composition:
            summary = self.chart_report(train_number, start_date, cls.value)["summary"]
            out[cls.value] = {"berths": summary["berths"], "confirmed": summary["confirmed"],
                              "rac": summary["rac_passengers"], "waitlisted": summary["waitlisted"]}
        return out
