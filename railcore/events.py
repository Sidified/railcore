"""Domain events and a tiny synchronous event bus.

Every state change in the booking service publishes an event. The storage
layer subscribes and appends each one to the event log, which the API
exposes for auditing.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    type: str
    at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "at": self.at.isoformat(), "payload": self.payload}


BOOKING_CREATED = "booking.created"
PASSENGER_CANCELLED = "passenger.cancelled"
STATUS_CHANGED = "passenger.status_changed"
WAITLIST_DROPPED = "passenger.waitlist_dropped"
CHART_PREPARED = "chart.prepared"
QUOTA_RELEASED = "quota.released"
BOARDING_CHANGED = "booking.boarding_changed"
TRAIN_CANCELLED = "run.cancelled"
DELAY_REPORTED = "run.delay_reported"
TDR_FILED = "tdr.filed"
TDR_SETTLED = "tdr.settled"

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []

    def subscribe(self, event_type: str | None, handler: Handler) -> None:
        if event_type is None:
            self._all.append(handler)
        else:
            self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in list(self._all) + list(self._handlers.get(event.type, [])):
            handler(event)
