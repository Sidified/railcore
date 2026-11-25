"""Loads the JSON data files (network, tariff, quotas, policy) once."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str, data_dir: Path) -> Any:
    with open(data_dir / name, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass(frozen=True)
class Config:
    stations: list[dict]
    trains: list[dict]
    fares: dict
    quotas: dict
    policy: dict

    @classmethod
    def from_dir(cls, data_dir: Path | str = DATA_DIR) -> "Config":
        data_dir = Path(data_dir)
        return cls(
            stations=_load("stations.json", data_dir),
            trains=_load("trains.json", data_dir),
            fares=_load("fares.json", data_dir),
            quotas=_load("quotas.json", data_dir),
            policy=_load("policy.json", data_dir),
        )


@lru_cache(maxsize=1)
def default_config() -> Config:
    return Config.from_dir(DATA_DIR)
