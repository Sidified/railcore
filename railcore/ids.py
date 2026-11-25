"""PNR numbers: 10 digits, the last one a Luhn check digit."""
from __future__ import annotations


def luhn_digit(digits: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def make_pnr(sequence: int, zone_prefix: int = 2) -> str:
    if sequence < 1 or sequence >= 10**8:
        raise ValueError("PNR sequence out of range")
    body = f"{zone_prefix}{sequence:08d}"
    return body + str(luhn_digit(body))


def is_valid_pnr(pnr: str) -> bool:
    return len(pnr) == 10 and pnr.isdigit() and luhn_digit(pnr[:-1]) == int(pnr[-1])
