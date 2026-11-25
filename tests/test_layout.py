from collections import Counter

import pytest

from railcore.config import default_config
from railcore.domain.classes import BerthType, TravelClass, layout_for
from railcore.domain.quota import Quota
from railcore.inventory.layout import build_berths


@pytest.mark.parametrize("cls", list(TravelClass))
def test_every_berth_gets_exactly_one_pool(cls):
    rules = default_config().quotas[cls.value]
    berths = build_berths(cls, 3, rules)
    assert len(berths) == 3 * layout_for(cls).berths
    rac = [b for b in berths.values() if b.is_rac]
    assert all(b.type is BerthType.SIDE_LOWER for b in rac)
    counts = Counter(b.pool for b in berths.values() if not b.is_rac)
    assert counts[Quota.TATKAL] == 3 * rules["TQ"]
    assert counts[Quota.PREMIUM_TATKAL] == 3 * rules["PT"]
    assert counts[Quota.SENIOR_LOWER] == 3 * rules["SS"]
    assert counts[Quota.LADIES] == rules["LD"]  # first coach only
    if layout_for(cls).has_rac:
        assert len(rac) == 3 * rules["RAC"]
    else:
        assert not rac


def test_senior_quota_is_lower_berths():
    berths = build_berths(TravelClass.SLEEPER, 2, default_config().quotas["SL"])
    seniors = [b for b in berths.values() if b.pool is Quota.SENIOR_LOWER]
    assert seniors and all(b.type is BerthType.LOWER for b in seniors)


def test_tatkal_takes_highest_numbers():
    berths = build_berths(TravelClass.SLEEPER, 1, default_config().quotas["SL"])
    tatkal = sorted(n for (_, n), b in berths.items() if b.pool is Quota.TATKAL)
    assert tatkal[-1] == 72 and len(tatkal) == 16


def test_split_is_deterministic():
    rules = default_config().quotas["3A"]
    a = build_berths(TravelClass.THIRD_AC, 2, rules)
    b = build_berths(TravelClass.THIRD_AC, 2, rules)
    assert {k: (v.pool, v.is_rac) for k, v in a.items()} == {k: (v.pool, v.is_rac) for k, v in b.items()}


def test_berth_type_cycle():
    sl = layout_for(TravelClass.SLEEPER)
    assert [sl.berth_type(n).value for n in range(1, 9)] == ["LB", "MB", "UB", "LB", "MB", "UB", "SL", "SU"]
    with pytest.raises(ValueError):
        sl.berth_type(73)
