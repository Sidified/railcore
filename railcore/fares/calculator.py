"""Fare calculation.

Per passenger:
    base        telescopic per-km rates over the chargeable distance
    reservation fixed per class
    superfast   fixed per class, only on superfast train types
    tatkal      percent of base with a class minimum and maximum (TQ and PT)
    dynamic     Premium Tatkal surge: (base + tatkal) x (multiplier - 1)
    gst         percent of all of the above, AC classes only
    total       rounded UP to the next 5 rupees
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..config import Config, default_config
from ..domain.classes import TravelClass, layout_for
from ..domain.network import Train
from ..domain.quota import Quota
from ..errors import ValidationError
from ..money import ZERO, money, percent, round_up_to


@dataclass(frozen=True)
class FareBreakdown:
    base: Decimal
    reservation: Decimal
    superfast: Decimal
    tatkal: Decimal
    dynamic: Decimal
    gst: Decimal
    rounding: Decimal
    total: Decimal
    distance_km: int
    chargeable_km: int

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


class FareCalculator:
    def __init__(self, config: Config | None = None):
        cfg = (config or default_config()).fares
        self._slabs: list[int] = cfg["slabs_km"]
        self._rates = {TravelClass(k): [Decimal(r) for r in v] for k, v in cfg["rate_per_km"].items()}
        self._min_km = {TravelClass(k): v for k, v in cfg["min_distance_km"].items()}
        self._reservation = {TravelClass(k): money(v) for k, v in cfg["reservation_charge"].items()}
        self._superfast = {TravelClass(k): money(v) for k, v in cfg["superfast_charge"].items()}
        self._superfast_types = set(cfg["superfast_train_types"])
        tk = cfg["tatkal"]
        self._tk_pct = {TravelClass(k): Decimal(v) for k, v in tk["percent_of_base"].items()}
        self._tk_min = {TravelClass(k): money(v) for k, v in tk["min_charge"].items()}
        self._tk_max = {TravelClass(k): money(v) for k, v in tk["max_charge"].items()}
        self._tk_min_km = {TravelClass(k): v for k, v in tk["min_distance_km"].items()}
        self._pt_bands = [(b["sold_below_percent"], Decimal(b["multiplier"])) for b in cfg["premium_tatkal_bands"]]
        self._gst_pct = Decimal(cfg["gst_percent_ac"])
        self._round_to = int(cfg["round_total_up_to"])

    # -- pieces -----------------------------------------------------------
    def base_fare(self, travel_class: TravelClass, km: int) -> Decimal:
        """Telescopic fare: each slab of distance is charged at its own rate."""
        if km <= 0:
            raise ValidationError("distance must be positive")
        rates = self._rates[travel_class]
        total = Decimal(0)
        lower = 0
        bounds = self._slabs + [None]
        for rate, upper in zip(rates, bounds):
            if upper is None or km <= upper:
                total += (km - lower) * rate
                break
            total += (upper - lower) * rate
            lower = upper
        return money(total)

    def chargeable_km(self, travel_class: TravelClass, km: int) -> int:
        return max(km, self._min_km[travel_class])

    def tatkal_charge(self, travel_class: TravelClass, km: int) -> Decimal:
        if travel_class not in self._tk_pct:
            raise ValidationError(f"Tatkal is not available in class {travel_class.value}")
        tk_km = max(self.chargeable_km(travel_class, km), self._tk_min_km[travel_class])
        raw = percent(self.base_fare(travel_class, tk_km), self._tk_pct[travel_class])
        return min(max(raw, self._tk_min[travel_class]), self._tk_max[travel_class])

    def premium_multiplier(self, sold_before: int, pool_size: int) -> Decimal:
        """Surge multiplier from how full the Premium Tatkal pool already is."""
        if pool_size <= 0:
            raise ValidationError("no Premium Tatkal berths on this class")
        sold_pct = Decimal(sold_before) * 100 / Decimal(pool_size)
        for below, multiplier in self._pt_bands:
            if sold_pct < below:
                return multiplier
        return self._pt_bands[-1][1]

    # -- full quote -------------------------------------------------------
    def fare(self, train: Train, from_station: str, to_station: str, travel_class: TravelClass,
             quota: Quota, pt_sold_before: int = 0, pt_pool_size: int = 0) -> FareBreakdown:
        km = train.distance(from_station, to_station)
        charge_km = self.chargeable_km(travel_class, km)
        base = self.base_fare(travel_class, charge_km)
        reservation = self._reservation[travel_class]
        superfast = self._superfast[travel_class] if train.type in self._superfast_types else ZERO
        tatkal = self.tatkal_charge(travel_class, km) if quota.is_tatkal else ZERO
        dynamic = ZERO
        if quota is Quota.PREMIUM_TATKAL:
            mult = self.premium_multiplier(pt_sold_before, pt_pool_size)
            dynamic = money((base + tatkal) * (mult - 1))
        subtotal = base + reservation + superfast + tatkal + dynamic
        gst = percent(subtotal, self._gst_pct) if layout_for(travel_class).is_ac else ZERO
        total = round_up_to(subtotal + gst, self._round_to)
        return FareBreakdown(base, reservation, superfast, tatkal, dynamic, gst,
                             money(total - subtotal - gst), total, km, charge_km)
