"""Money helpers. All amounts are Decimal rupees; floats are never used."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
PAISE = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    """Convert to a 2-decimal rupee amount (half-up)."""
    if isinstance(value, float):  # pragma: no cover - guard against misuse
        raise TypeError("use Decimal or str for money, not float")
    return Decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)


def round_up_to(value: Decimal, step: int) -> Decimal:
    """Round up to the next multiple of `step` rupees (fares are charged in steps of 5)."""
    if step <= 0:
        raise ValueError("step must be positive")
    units = (value / Decimal(step)).to_integral_value(rounding=ROUND_CEILING)
    return money(units * step)


def percent(value: Decimal, pct: Decimal | str) -> Decimal:
    return money(value * Decimal(pct) / Decimal(100))
