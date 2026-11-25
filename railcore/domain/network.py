"""Stations, trains, timetables and the network that holds them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ..config import Config, default_config
from ..errors import NotFound, ValidationError
from .classes import TravelClass

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True)
class Station:
    code: str
    name: str


def _parse_hhmm(value: str | None) -> time | None:
    if value is None:
        return None
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class Stop:
    station: str
    arr: time | None
    dep: time | None
    day: int
    km: int


@dataclass(frozen=True)
class Train:
    number: str
    name: str
    type: str
    runs_on: tuple[str, ...]
    stops: tuple[Stop, ...]
    composition: dict[TravelClass, int] = field(hash=False)

    @property
    def origin(self) -> str:
        return self.stops[0].station

    @property
    def destination(self) -> str:
        return self.stops[-1].station

    def stop_index(self, station: str) -> int:
        for i, stop in enumerate(self.stops):
            if stop.station == station:
                return i
        raise ValidationError(f"train {self.number} does not stop at {station}")

    def check_segment(self, from_station: str, to_station: str) -> tuple[int, int]:
        a, b = self.stop_index(from_station), self.stop_index(to_station)
        if a >= b:
            raise ValidationError(f"{from_station} is not before {to_station} on train {self.number}")
        return a, b

    def distance(self, from_station: str, to_station: str) -> int:
        a, b = self.check_segment(from_station, to_station)
        return self.stops[b].km - self.stops[a].km

    def runs_on_date(self, start_date: date) -> bool:
        return WEEKDAYS[start_date.weekday()] in self.runs_on

    def departure(self, station: str, start_date: date) -> datetime:
        """Scheduled departure from `station` for the run starting on `start_date`."""
        stop = self.stops[self.stop_index(station)]
        if stop.dep is None:
            raise ValidationError(f"train {self.number} does not depart from {station}")
        return datetime.combine(start_date + timedelta(days=stop.day), stop.dep)

    def arrival(self, station: str, start_date: date) -> datetime:
        stop = self.stops[self.stop_index(station)]
        if stop.arr is None:
            raise ValidationError(f"train {self.number} does not arrive at {station}")
        return datetime.combine(start_date + timedelta(days=stop.day), stop.arr)

    def carries(self, travel_class: TravelClass) -> bool:
        return self.composition.get(travel_class, 0) > 0

    @property
    def is_ac_only(self) -> bool:
        from .classes import layout_for
        return all(layout_for(c).is_ac for c in self.composition)


class Network:
    def __init__(self, stations: dict[str, Station], trains: dict[str, Train]):
        self.stations = stations
        self.trains = trains

    @classmethod
    def from_config(cls, config: Config | None = None) -> "Network":
        config = config or default_config()
        stations = {s["code"]: Station(s["code"], s["name"]) for s in config.stations}
        trains: dict[str, Train] = {}
        for raw in config.trains:
            stops = tuple(
                Stop(s["station"], _parse_hhmm(s["arr"]), _parse_hhmm(s["dep"]), s["day"], s["km"])
                for s in raw["stops"]
            )
            for stop in stops:
                if stop.station not in stations:
                    raise ValueError(f"train {raw['number']} uses unknown station {stop.station}")
            kms = [s.km for s in stops]
            if kms != sorted(kms) or len(set(kms)) != len(kms):
                raise ValueError(f"train {raw['number']} distances must strictly increase")
            composition = {TravelClass.parse(k): int(v) for k, v in raw["composition"].items()}
            trains[raw["number"]] = Train(raw["number"], raw["name"], raw["type"],
                                          tuple(raw["runs_on"]), stops, composition)
        return cls(stations, trains)

    def train(self, number: str) -> Train:
        try:
            return self.trains[number]
        except KeyError:
            raise NotFound(f"no train {number}") from None

    def trains_between(self, from_station: str, to_station: str) -> list[Train]:
        found = []
        for train in self.trains.values():
            codes = [s.station for s in train.stops]
            if from_station in codes and to_station in codes and codes.index(from_station) < codes.index(to_station):
                found.append(train)
        return sorted(found, key=lambda t: t.number)
