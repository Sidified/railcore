"""Ticket Deposit Receipts: refund claims filed after the cancellation window."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class TDRReason(str, Enum):
    TRAIN_LATE_NOT_TRAVELLED = "TRAIN_LATE_NOT_TRAVELLED"
    AC_FAILURE = "AC_FAILURE"
    RAC_NOT_TRAVELLED = "RAC_NOT_TRAVELLED"


class TDRStatus(str, Enum):
    FILED = "FILED"
    REFUNDED = "REFUNDED"
    REJECTED = "REJECTED"


@dataclass
class TDR:
    id: int
    pnr: str
    passengers: list[int]
    reason: TDRReason
    filed_at: datetime
    status: TDRStatus = TDRStatus.FILED
    claimed: dict[int, Decimal] = field(default_factory=dict)  # passenger -> amount
    note: str = ""
    settled_at: datetime | None = None

    @property
    def total(self) -> Decimal:
        return sum(self.claimed.values(), Decimal("0.00"))
