from decimal import Decimal

import pytest

from railcore.ids import is_valid_pnr, luhn_digit, make_pnr
from railcore.money import money, percent, round_up_to


def test_money_rounds_half_up():
    assert money("10.005") == Decimal("10.01")
    assert money(7) == Decimal("7.00")


@pytest.mark.parametrize("value,expected", [("400.00", "400.00"), ("400.01", "405.00"), ("1592.75", "1595.00")])
def test_round_up_to_five(value, expected):
    assert round_up_to(Decimal(value), 5) == Decimal(expected)


def test_round_up_rejects_bad_step():
    with pytest.raises(ValueError):
        round_up_to(Decimal("1"), 0)


def test_percent():
    assert percent(Decimal("200"), "25") == Decimal("50.00")


def test_pnr_has_valid_check_digit():
    pnr = make_pnr(1)
    assert len(pnr) == 10 and is_valid_pnr(pnr)
    assert not is_valid_pnr(pnr[:-1] + str((int(pnr[-1]) + 1) % 10))


def test_pnr_sequence_bounds():
    with pytest.raises(ValueError):
        make_pnr(0)
    with pytest.raises(ValueError):
        make_pnr(10**8)


def test_luhn_known_value():
    assert luhn_digit("7992739871") == 3
