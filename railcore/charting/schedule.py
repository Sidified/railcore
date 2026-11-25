"""When charts are prepared for a train run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..config import Config, default_config
from ..domain.network import Train


@dataclass(frozen=True)
class ChartSchedule:
    first_hours_before: int
    final_minutes_before: int

    @classmethod
    def from_config(cls, config: Config | None = None) -> "ChartSchedule":
        policy = (config or default_config()).policy
        return cls(int(policy["first_chart_hours_before"]), int(policy["final_chart_minutes_before"]))

    def first_chart_at(self, train: Train, start_date: date) -> datetime:
        return train.departure(train.origin, start_date) - timedelta(hours=self.first_hours_before)

    def final_chart_at(self, train: Train, start_date: date) -> datetime:
        return train.departure(train.origin, start_date) - timedelta(minutes=self.final_minutes_before)
