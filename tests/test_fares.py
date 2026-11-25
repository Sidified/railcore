from decimal import Decimal

import pytest

from railcore.domain.classes import TravelClass
from railcore.domain.network import Network
from railcore.domain.quota import Quota
from railcore.errors import ValidationError
from railcore.fares import FareCalculator

D = Decimal


@pytest.fixture(scope="module")
def calc():
    return FareCalculator()


@pytest.fixture(scope="module")
def net():
    return Network.from_config()


def test_telescopic_base_fare(calc):
    # 300 km at 0.55 + 401 km at 0.48
    assert calc.base_fare(TravelClass.SLEEPER, 701) == D("357.48")
    # crosses three slabs: 300 x 1.45 + 700 x 1.28 + 90 x 1.10
    assert calc.base_fare(TravelClass.THIRD_AC, 1090) == D("1430.00")


def test_base_fare_on_slab_edge(calc):
    assert calc.base_fare(TravelClass.SLEEPER, 300) == D("165.00")
    assert calc.base_fare(TravelClass.SLEEPER, 301) == D("165.48")


def test_base_fare_needs_positive_distance(calc):
    with pytest.raises(ValidationError):
        calc.base_fare(TravelClass.SLEEPER, 0)


def test_sleeper_superfast_fare(calc, net):
    f = calc.fare(net.train("22401"), "NDLS", "BPL", TravelClass.SLEEPER, Quota.GENERAL)
    assert (f.base, f.reservation, f.superfast, f.gst) == (D("357.48"), D("20.00"), D("30.00"), D("0.00"))
    assert f.total == D("410.00")
    assert f.rounding == D("2.52")


def test_minimum_distance_applies(calc, net):
    f = calc.fare(net.train("22401"), "NDLS", "AGC", TravelClass.SLEEPER, Quota.GENERAL)
    assert (f.distance_km, f.chargeable_km) == (195, 200)
    assert f.total == D("160.00")


def test_no_superfast_charge_on_mail_express(calc, net):
    f = calc.fare(net.train("11071"), "CSMT", "MMR", TravelClass.SLEEPER, Quota.GENERAL)
    assert f.superfast == D("0.00")
    assert f.total == D("165.00")


def test_gst_only_on_ac(calc, net):
    ac = calc.fare(net.train("12001"), "NDLS", "AGC", TravelClass.EXECUTIVE_CHAIR, Quota.GENERAL)
    assert ac.gst == D("32.10")
    assert ac.total == D("675.00")
    non_ac = calc.fare(net.train("22401"), "NDLS", "BPL", TravelClass.SECOND_SITTING, Quota.GENERAL)
    assert non_ac.gst == D("0.00")


def test_tatkal_percent_inside_limits(calc, net):
    f = calc.fare(net.train("11071"), "CSMT", "ET", TravelClass.SLEEPER, Quota.TATKAL)
    assert f.tatkal == D("113.44")
    assert f.total == D("515.00")


def test_tatkal_minimum_charge(calc, net):
    f = calc.fare(net.train("11071"), "CSMT", "MMR", TravelClass.SLEEPER, Quota.TATKAL)
    assert f.tatkal == D("100.00")
    assert f.total == D("265.00")


def test_tatkal_maximum_charge(calc, net):
    assert calc.tatkal_charge(TravelClass.SECOND_AC, 1090) == D("500.00")


def test_no_tatkal_in_first_ac(calc, net):
    with pytest.raises(ValidationError):
        calc.fare(net.train("22401"), "NDLS", "BPL", TravelClass.FIRST_AC, Quota.TATKAL)


@pytest.mark.parametrize("sold,expected", [(0, "1.00"), (8, "1.00"), (9, "1.25"), (17, "1.25"),
                                           (18, "1.50"), (27, "2.00"), (36, "2.00"), (40, "2.00")])
def test_premium_tatkal_bands(calc, sold, expected):
    assert calc.premium_multiplier(sold, 36) == D(expected)


def test_premium_tatkal_needs_a_pool(calc):
    with pytest.raises(ValidationError):
        calc.premium_multiplier(0, 0)


def test_premium_tatkal_surge_adds_dynamic_part(calc, net):
    train = net.train("22401")
    cheap = calc.fare(train, "NDLS", "BPL", TravelClass.SLEEPER, Quota.PREMIUM_TATKAL, 0, 36)
    dear = calc.fare(train, "NDLS", "BPL", TravelClass.SLEEPER, Quota.PREMIUM_TATKAL, 18, 36)
    assert cheap.dynamic == D("0.00")
    assert dear.dynamic == ((dear.base + dear.tatkal) * D("0.5")).quantize(D("0.01"))
    assert dear.total > cheap.total


def test_totals_are_multiples_of_five(calc, net):
    for train in net.trains.values():
        for cls in train.composition:
            f = calc.fare(train, train.origin, train.destination, cls, Quota.GENERAL)
            assert f.total % 5 == 0
            assert f.total == f.base + f.reservation + f.superfast + f.tatkal + f.dynamic + f.gst + f.rounding
