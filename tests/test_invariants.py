"""Randomised invariant checks: hundreds of bookings and cancellations, and
after every step the whole run must still be consistent."""
import random
from collections import Counter, defaultdict
from datetime import datetime

import pytest

from railcore.booking import BookingService
from railcore.clock import FixedClock
from railcore.domain.booking import StatusKind
from railcore.domain.classes import TravelClass
from railcore.domain.quota import Quota, WaitlistType
from railcore.errors import RailCoreError
from railcore.inventory import ChartState
from railcore.storage import InMemoryRepository, SQLiteRepository

from .conftest import RUN_DATE, pax, request_for

CLASSES = ["1A", "2A"]
QUOTAS = ["GN", "GN", "GN", "TQ", "SS", "LD"]


def check_run(service):
    train = service.network.train("22401")
    meta = service.repo.get_run("22401", RUN_DATE)
    bookings = service.repo.bookings_for_run("22401", RUN_DATE)
    for cls_code in CLASSES:
        cls = TravelClass(cls_code)
        records = [(b, p) for b in bookings if b.travel_class is cls for p in b.passengers]
        active = [(b, p) for b, p in records if p.is_active]
        cnf = [(p.current_status.coach, p.current_status.berth)
               for _, p in active if p.current_status.kind is StatusKind.CONFIRMED]
        assert len(cnf) == len(set(cnf)), "a berth is sold twice"
        rac = [p.current_status for _, p in active if p.current_status.kind is StatusKind.RAC]
        assert sorted(s.rac_number for s in rac) == list(range(1, len(rac) + 1))
        assert all(n <= 2 for n in Counter((s.coach, s.berth) for s in rac).values())
        wls = defaultdict(list)
        for _, p in active:
            if p.current_status.kind is StatusKind.WAITLISTED:
                wls[p.current_status.waitlist].append(p.current_status.wl_number)
        for numbers in wls.values():
            assert sorted(numbers) == list(range(1, len(numbers) + 1))
        for _, p in records:
            assert p.refund is None or p.refund <= p.fare
        inv = service._inventory(train, RUN_DATE, cls, meta.chart)
        live = {ref: a.status for ref, a in inv.all_statuses().items()}
        stored = {b.ref(p.index): p.current_status for b, p in active}
        assert live == stored, "inventory rebuilt from storage disagrees with stored statuses"
        if meta.chart is ChartState.OPEN:
            if inv.free_berths(Quota.GENERAL):
                assert not inv.rac_queue and not inv.waitlists[WaitlistType.GNWL]
            if inv.free_rac_slots():
                assert not inv.waitlists[WaitlistType.GNWL]
            if inv.free_berths(Quota.TATKAL):
                assert not inv.waitlists[WaitlistType.TQWL]
        else:
            assert not inv.waitlists[WaitlistType.GNWL] and not inv.waitlists[WaitlistType.TQWL]


def random_passengers(rng, quota, n, start):
    out = []
    for i in range(n):
        gender = "F" if quota == "LD" else rng.choice("MF")
        age = rng.randint(65, 90) if quota == "SS" else rng.randint(5, 80)
        out.append(pax(f"R{start + i}", age, gender, rng.choice([None, "LB", "UB", "SL"])))
    return out


def run_random(service, rng, steps, chart_at=None):
    made = 0
    for step in range(steps):
        if chart_at is not None and step == chart_at:
            service.prepare_chart("22401", RUN_DATE, "first")
        live = [b for b in service.repo.bookings_for_run("22401", RUN_DATE)
                if any(p.is_active for p in b.passengers)]
        if live and rng.random() < 0.35:
            b = rng.choice(live)
            active = [p.index for p in b.passengers if p.is_active]
            chosen = rng.sample(active, rng.randint(1, len(active)))
            service.cancel(b.pnr, chosen)
        else:
            quota = rng.choice(QUOTAS)
            n = rng.randint(1, 2 if quota == "SS" else 4)
            try:
                service.book(request_for(random_passengers(rng, quota, n, made),
                                         cls=rng.choice(CLASSES), quota=quota))
                made += n
            except RailCoreError:
                pass
        check_run(service)


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_random_bookings_and_cancellations_keep_invariants(seed):
    service = BookingService(InMemoryRepository(), clock=FixedClock(datetime(2026, 12, 9, 11, 0)))
    run_random(service, random.Random(seed), 180)


@pytest.mark.parametrize("seed", [7, 8])
def test_invariants_hold_across_charting(seed):
    service = BookingService(InMemoryRepository(), clock=FixedClock(datetime(2026, 12, 9, 11, 0)))
    run_random(service, random.Random(seed), 220, chart_at=160)


def test_invariants_on_sqlite(tmp_path):
    service = BookingService(SQLiteRepository(tmp_path / "inv.db"),
                             clock=FixedClock(datetime(2026, 12, 9, 11, 0)))
    run_random(service, random.Random(11), 150, chart_at=120)
