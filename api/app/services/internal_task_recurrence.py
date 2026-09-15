from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RECURRENCE_DAILY = "daily"
RECURRENCE_SELECTED_WEEKDAYS = "selected_weekdays"
RECURRENCE_WEEKLY = "weekly"
RECURRENCE_MONTHLY = "monthly"
RECURRENCE_EVERY_N_DAYS = "every_n_days"

CYCLE_END_NEVER = "never"
CYCLE_END_AT = "at"
CYCLE_END_COUNT = "count"

_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def parse_recurrence_time(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid recurrence time: empty")
    raw = value.strip()
    match = _TIME_RE.match(raw)
    if match is None:
        raise ValueError(f"Invalid recurrence time: {raw}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid recurrence time: {raw}")
    return f"{hour:02d}:{minute:02d}"


def parse_daily_times(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError("Daily recurrence times must be a string or list")

    if not raw_items:
        raise ValueError("Daily recurrence requires at least one time")

    times = set()
    for item in raw_items:
        parsed = parse_recurrence_time(item)
        times.add(parsed)
    if not times:
        raise ValueError("Daily recurrence requires at least one time")
    return sorted(times)


def normalize_recurrence_config(recurrence_mode: str, raw_config: object) -> dict:
    mode = recurrence_mode
    if mode == RECURRENCE_DAILY:
        if isinstance(raw_config, str) or isinstance(raw_config, list):
            return {"times": parse_daily_times(raw_config)}
        config = _require_config(raw_config, {"times"}, mode)
        return {"times": parse_daily_times(config["times"])}

    if mode == RECURRENCE_SELECTED_WEEKDAYS:
        config = _require_config(raw_config, {"weekdays", "time"}, mode)
        return {
            "weekdays": _normalize_weekdays(config["weekdays"]),
            "time": parse_recurrence_time(config["time"]),
        }

    if mode == RECURRENCE_WEEKLY:
        config = _require_config(raw_config, {"time"}, mode)
        return {"time": parse_recurrence_time(config["time"])}

    if mode == RECURRENCE_MONTHLY:
        config = _require_config(raw_config, {"day", "time"}, mode)
        return {
            "day": _normalize_month_day(config["day"]),
            "time": parse_recurrence_time(config["time"]),
        }

    if mode == RECURRENCE_EVERY_N_DAYS:
        config = _require_config(raw_config, {"interval_days", "time"}, mode)
        return {
            "interval_days": _positive_int(config["interval_days"], "interval_days"),
            "time": parse_recurrence_time(config["time"]),
        }

    raise ValueError(f"Unsupported recurrence mode: {recurrence_mode}")


def validate_recurrence_end_rule(
    *,
    cycle_end_mode: str,
    cycle_end_at_utc: datetime | None = None,
    cycle_max_occurrences: int | None = None,
) -> None:
    if cycle_end_mode == CYCLE_END_NEVER:
        if cycle_end_at_utc is not None:
            raise ValueError("cycle_end_at_utc must be empty for never end mode")
        if cycle_max_occurrences is not None:
            raise ValueError("cycle_max_occurrences must be empty for never end mode")
        return

    if cycle_end_mode == CYCLE_END_AT:
        if cycle_end_at_utc is None:
            raise ValueError("cycle_end_at_utc is required for at end mode")
        if cycle_max_occurrences is not None:
            raise ValueError("cycle_max_occurrences must be empty for at end mode")
        return

    if cycle_end_mode == CYCLE_END_COUNT:
        if cycle_end_at_utc is not None:
            raise ValueError("cycle_end_at_utc must be empty for count end mode")
        _positive_int(cycle_max_occurrences, "cycle_max_occurrences")
        return

    raise ValueError(f"Unsupported cycle end mode: {cycle_end_mode}")


def candidate_allowed_by_end_rule(
    candidate_at_utc: datetime,
    *,
    cycle_end_mode: str | None = None,
    cycle_end_at_utc: datetime | None = None,
) -> bool:
    if cycle_end_mode is None or cycle_end_mode == CYCLE_END_NEVER:
        return True
    if cycle_end_mode == CYCLE_END_AT:
        if cycle_end_at_utc is None:
            raise ValueError("cycle_end_at_utc is required for at end mode")
        return _as_utc_aware(candidate_at_utc) <= _as_utc_aware(cycle_end_at_utc)
    if cycle_end_mode == CYCLE_END_COUNT:
        return True
    raise ValueError(f"Unsupported cycle end mode: {cycle_end_mode}")


def has_recurrence_capacity(
    *,
    cycle_end_mode: str,
    cycle_max_occurrences: int | None = None,
    successful_occurrence_count: int = 0,
) -> bool:
    if cycle_end_mode in {CYCLE_END_NEVER, CYCLE_END_AT}:
        return True
    if cycle_end_mode == CYCLE_END_COUNT:
        maximum = _positive_int(cycle_max_occurrences, "cycle_max_occurrences")
        if successful_occurrence_count < 0:
            raise ValueError("successful_occurrence_count must be >= 0")
        return successful_occurrence_count < maximum
    raise ValueError(f"Unsupported cycle end mode: {cycle_end_mode}")


def calculate_recurrence_occurrences(
    *,
    recurrence_mode: str,
    recurrence_config: dict,
    timezone_name: str,
    cycle_start_at_utc: datetime,
    window_start_utc: datetime,
    window_end_utc: datetime,
    cycle_end_mode: str | None = None,
    cycle_end_at_utc: datetime | None = None,
) -> list[datetime]:
    tz = _timezone(timezone_name)
    config = normalize_recurrence_config(recurrence_mode, recurrence_config)
    cycle_start = _as_utc_aware(cycle_start_at_utc)
    window_start = _as_utc_aware(window_start_utc)
    window_end = _as_utc_aware(window_end_utc)
    if window_end < window_start:
        raise ValueError("window_end_utc must be >= window_start_utc")

    local_start_date = cycle_start.astimezone(tz).date()
    local_window_end_date = window_end.astimezone(tz).date()
    candidates: list[datetime] = []

    if recurrence_mode == RECURRENCE_DAILY:
        current = local_start_date
        while current <= local_window_end_date:
            for hhmm in config["times"]:
                candidates.append(_local_candidate(current, hhmm, tz))
            current += timedelta(days=1)

    elif recurrence_mode == RECURRENCE_SELECTED_WEEKDAYS:
        current = local_start_date
        weekdays = set(config["weekdays"])
        while current <= local_window_end_date:
            if current.isoweekday() in weekdays:
                candidates.append(_local_candidate(current, config["time"], tz))
            current += timedelta(days=1)

    elif recurrence_mode == RECURRENCE_WEEKLY:
        current = local_start_date
        while current <= local_window_end_date:
            candidates.append(_local_candidate(current, config["time"], tz))
            current += timedelta(days=7)

    elif recurrence_mode == RECURRENCE_MONTHLY:
        year = local_start_date.year
        month = local_start_date.month
        while (year, month) <= (local_window_end_date.year, local_window_end_date.month):
            day = _month_day(year, month, config["day"])
            local_date = date(year, month, day)
            if local_date >= local_start_date:
                candidates.append(_local_candidate(local_date, config["time"], tz))
            year, month = _next_month(year, month)

    elif recurrence_mode == RECURRENCE_EVERY_N_DAYS:
        current = local_start_date
        step = timedelta(days=config["interval_days"])
        while current <= local_window_end_date:
            candidates.append(_local_candidate(current, config["time"], tz))
            current += step

    else:
        raise ValueError(f"Unsupported recurrence mode: {recurrence_mode}")

    result = {
        _to_naive_utc(candidate)
        for candidate in candidates
        if candidate >= cycle_start
        and candidate >= window_start
        and candidate <= window_end
        and candidate_allowed_by_end_rule(
            candidate,
            cycle_end_mode=cycle_end_mode,
            cycle_end_at_utc=cycle_end_at_utc,
        )
    }
    return sorted(result)


def _require_config(raw_config: object, allowed_keys: set[str], mode: str) -> dict:
    if not isinstance(raw_config, dict):
        raise ValueError(f"{mode} recurrence config must be an object")
    unknown = set(raw_config) - allowed_keys
    missing = allowed_keys - set(raw_config)
    if unknown:
        raise ValueError(f"Unsupported recurrence config field: {sorted(unknown)[0]}")
    if missing:
        raise ValueError(f"Missing recurrence config field: {sorted(missing)[0]}")
    return raw_config


def _normalize_weekdays(value: object) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("weekdays must be a list")
    weekdays: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1 or item > 7:
            raise ValueError(f"Invalid recurrence weekday: {item}")
        weekdays.add(item)
    if not weekdays:
        raise ValueError("weekdays must not be empty")
    return sorted(weekdays)


def _normalize_month_day(value: object) -> int | str:
    if value == "last":
        return value
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 31:
        raise ValueError(f"Invalid recurrence month day: {value}")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _local_candidate(local_date: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    return datetime.combine(local_date, time(hour, minute), tzinfo=tz).astimezone(timezone.utc)


def _month_day(year: int, month: int, configured_day: int | str) -> int:
    last_day = calendar.monthrange(year, month)[1]
    if configured_day == "last":
        return last_day
    return min(configured_day, last_day)


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1
