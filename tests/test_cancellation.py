from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from railcore.domain.booking import StatusKind
from railcore.domain.classes import TravelClass
from railcore.domain.quota import Quota
from railcore.refunds import CancellationPolicy

D = Decimal
DEP = datetime(2026, 12, 10, 20, 0)


@pytest.fixture(scope="module")
def policy():
    return CancellationPolicy()


def q(policy, hours, *, fare="1595.00", status=StatusKind.CONFIRMED, cls=TravelClass.THIRD_AC,
      quota=Quota.GENERAL, charted=False):
    return policy.quote(fare=D(fare), status=status, travel_class=cls, quota=quota,
                        departure=DEP, now=DEP - timedelta(hours=hours), charted=charted)


def test_flat_charge_more_than_48_hours(policy):
    r = q(policy, 49)
    assert (r.charge, r.gst_on_charge, r.refund) == (D("180.00"), D("9.00"), D("1406.00"))


def test_sleeper_flat_charge_has_no_gst(policy):
    r = q(policy, 72, fare="410.00", cls=TravelClass.SLEEPER)
    assert (r.charge, r.gst_on_charge, r.refund) == (D("120.00"), D("0.00"), D("290.00"))


def test_exactly_48_hours_is_the_25_percent_window(policy):
    r = q(policy, 48)
    assert r.charge == D("398.75") and r.gst_on_charge == D("19.94")
    assert r.refund == D("1176.31")


def test_percent_charge_never_below_flat(policy):
    r = q(policy, 20, fare="410.00", cls=TravelClass.SLEEPER)
    assert r.charge == D("120.00")  # 25% would be 102.50


def test_exactly_12_hours_is_still_25_percent(policy):
    assert q(policy, 12).charge == D("398.75")


def test_just_under_12_hours_is_50_percent(policy):
    r = q(policy, 11.99)
    assert r.charge == D("797.50") and r.refund == D("757.62")


def test_exactly_4_hours_is_50_percent_and_under_is_nothing(policy):
    assert q(policy, 4).charge == D("797.50")
    assert q(policy, 3.99).refund == D("0.00")


def test_rac_and_waitlist_pay_clerkage(policy):
    for status in (StatusKind.RAC, StatusKind.WAITLISTED):
        r = q(policy, 1, status=status)
        assert (r.charge, r.gst_on_charge, r.refund) == (D("60.00"), D("3.00"), D("1532.00"))


def test_rac_cutoff_30_minutes(policy):
    assert q(policy, 0.5, status=StatusKind.RAC).refund == D("1532.00")
    assert q(policy, 29 / 60, status=StatusKind.RAC).refund == D("0.00")


def test_confirmed_tatkal_and_premium_tatkal_no_refund(policy):
    assert q(policy, 100, quota=Quota.TATKAL).refund == D("0.00")
    assert q(policy, 100, quota=Quota.PREMIUM_TATKAL).refund == D("0.00")


def test_waitlisted_tatkal_still_gets_clerkage_refund(policy):
    assert q(policy, 10, status=StatusKind.WAITLISTED, quota=Quota.TATKAL).refund == D("1532.00")


def test_confirmed_after_chart_no_refund(policy):
    assert q(policy, 6, charted=True).refund == D("0.00")


def test_clerkage_refund_at_charting(policy):
    assert policy.clerkage_refund(D("410.00"), TravelClass.SLEEPER).refund == D("350.00")


def test_charge_capped_at_fare(policy):
    r = q(policy, 100, fare="50.00", cls=TravelClass.SECOND_SITTING)
    assert r.charge == D("50.00") and r.refund == D("0.00")


def test_cancelled_status_cannot_be_quoted(policy):
    with pytest.raises(ValueError):
        q(policy, 10, status=StatusKind.CANCELLED)
