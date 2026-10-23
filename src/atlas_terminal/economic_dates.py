"""Observation periods, distinct from release dates and information availability."""

import calendar
from datetime import date
from typing import Any


def month_end(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def within(observed: str, start: str | None, end: str | None) -> bool:
    return (start is None or observed >= start) and (end is None or observed <= end)


def years(start: str | None, end: str | None) -> tuple[int, int]:
    current = date.today().year
    return (int(start[:4]), int(end[:4])) if start and end else (current - 2, current)


def window_metadata(
    records: list[dict[str, Any]], start: str | None, end: str | None
) -> dict[str, Any]:
    dates = [row["date"] for row in records if row.get("date")]
    return {
        "requested_start_date": start,
        "requested_end_date": end,
        "first_observation_date": min(dates, default=None),
        "last_observation_date": max(dates, default=None),
        "date_selection": "inclusive_observation_period_end",
        "point_in_time": False,
    }
