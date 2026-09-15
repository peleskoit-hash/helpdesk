from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from api.app.services import internal_task_recurrence as recurrence


def test_daily_time_parser_accepts_business_input():
    assert recurrence.parse_daily_times("10:00, 11.30,16:35") == ["10:00", "11:30", "16:35"]


def test_daily_dedupes_and_sorts_times():
    assert recurrence.parse_daily_times("16:35,10:00,10.00, 09:05") == ["09:05", "10:00", "16:35"]


def test_invalid_daily_entry_rejects_whole_input_and_names_bad_member():
    with pytest.raises(ValueError, match="25:30"):
        recurrence.parse_daily_times("10:00,25:30,16:00")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9.05", "09:05"),
        ("9:05", "09:05"),
        ("10.00", "10:00"),
    ],
)
def test_single_time_normalization(raw, expected):
    assert recurrence.parse_recurrence_time(raw) == expected


@pytest.mark.parametrize("raw", ["24:00", "12:60", "", "abc", "10:00:00"])
def test_single_time_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        recurrence.parse_recurrence_time(raw)


def test_config_normalization_all_modes():
    assert recurrence.normalize_recurrence_config("daily", {"times": "10:00, 11.30"}) == {
        "times": ["10:00", "11:30"]
    }
    assert recurrence.normalize_recurrence_config("selected_weekdays", {"weekdays": [5, 1, 5], "time": "9.05"}) == {
        "weekdays": [1, 5],
        "time": "09:05",
    }
    assert recurrence.normalize_recurrence_config("weekly", {"time": "10.00"}) == {"time": "10:00"}
    assert recurrence.normalize_recurrence_config("monthly", {"day": "last", "time": "10:00"}) == {
        "day": "last",
        "time": "10:00",
    }
    assert recurrence.normalize_recurrence_config("every_n_days", {"interval_days": 3, "time": "10:00"}) == {
        "interval_days": 3,
        "time": "10:00",
    }


@pytest.mark.parametrize("weekdays", [[0], [8], ["1"], []])
def test_selected_weekdays_validation(weekdays):
    with pytest.raises(ValueError):
        recurrence.normalize_recurrence_config("selected_weekdays", {"weekdays": weekdays, "time": "10:00"})


@pytest.mark.parametrize("interval_days", [0, -1])
def test_every_n_days_validation(interval_days):
    with pytest.raises(ValueError):
        recurrence.normalize_recurrence_config("every_n_days", {"interval_days": interval_days, "time": "10:00"})


@pytest.mark.parametrize("day", [1, 15, 31, "last"])
def test_monthly_validation_accepts_valid_days(day):
    assert recurrence.normalize_recurrence_config("monthly", {"day": day, "time": "10:00"})["day"] == day


@pytest.mark.parametrize("day", [0, 32, "unknown"])
def test_monthly_validation_rejects_invalid_days(day):
    with pytest.raises(ValueError):
        recurrence.normalize_recurrence_config("monthly", {"day": day, "time": "10:00"})


def test_daily_generation_multiple_occurrences_per_day_ascending_unique():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="daily",
        recurrence_config={"times": "16:00,10:00,10.00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 16, 23, 59),
    )

    assert items == [
        datetime(2026, 9, 15, 10, 0),
        datetime(2026, 9, 15, 16, 0),
        datetime(2026, 9, 16, 10, 0),
        datetime(2026, 9, 16, 16, 0),
    ]


def test_start_day_filters_candidates_before_cycle_start():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="daily",
        recurrence_config={"times": "10:00,16:00"},
        timezone_name="Europe/Moscow",
        cycle_start_at_utc=datetime(2026, 9, 15, 11, 0),  # 14:00 local
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 16, 23, 59),
    )

    assert items == [
        datetime(2026, 9, 15, 13, 0),
        datetime(2026, 9, 16, 7, 0),
        datetime(2026, 9, 16, 13, 0),
    ]


def test_selected_weekdays_generation_uses_iso_weekdays():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="selected_weekdays",
        recurrence_config={"weekdays": [1, 3, 5], "time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 14, 0, 0),  # Monday
        window_start_utc=datetime(2026, 9, 14, 0, 0),
        window_end_utc=datetime(2026, 9, 20, 23, 59),
    )

    assert items == [
        datetime(2026, 9, 14, 10, 0),
        datetime(2026, 9, 16, 10, 0),
        datetime(2026, 9, 18, 10, 0),
    ]


def test_weekly_generation_is_anchored_to_cycle_start_local_date():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="weekly",
        recurrence_config={"time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 15, 14, 0),
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 30, 23, 59),
    )

    assert items == [
        datetime(2026, 9, 22, 10, 0),
        datetime(2026, 9, 29, 10, 0),
    ]


def test_monthly_day_31_clamps_to_last_existing_day():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="monthly",
        recurrence_config={"day": 31, "time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2027, 1, 1, 0, 0),
        window_start_utc=datetime(2027, 1, 1, 0, 0),
        window_end_utc=datetime(2027, 4, 30, 23, 59),
    )

    assert items == [
        datetime(2027, 1, 31, 10, 0),
        datetime(2027, 2, 28, 10, 0),
        datetime(2027, 3, 31, 10, 0),
        datetime(2027, 4, 30, 10, 0),
    ]


def test_monthly_last_uses_real_last_day_including_leap_february():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="monthly",
        recurrence_config={"day": "last", "time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2028, 1, 1, 0, 0),
        window_start_utc=datetime(2028, 1, 1, 0, 0),
        window_end_utc=datetime(2028, 3, 31, 23, 59),
    )

    assert items == [
        datetime(2028, 1, 31, 10, 0),
        datetime(2028, 2, 29, 10, 0),
        datetime(2028, 3, 31, 10, 0),
    ]


def test_every_n_days_uses_local_calendar_anchor_and_step():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="every_n_days",
        recurrence_config={"interval_days": 3, "time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 25, 23, 59),
    )

    assert items == [
        datetime(2026, 9, 15, 10, 0),
        datetime(2026, 9, 18, 10, 0),
        datetime(2026, 9, 21, 10, 0),
        datetime(2026, 9, 24, 10, 0),
    ]


def test_window_bounds_are_inclusive():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="daily",
        recurrence_config={"times": "10:00,12:00,14:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        window_start_utc=datetime(2026, 9, 15, 12, 0),
        window_end_utc=datetime(2026, 9, 16, 10, 0),
    )

    assert items == [
        datetime(2026, 9, 15, 12, 0),
        datetime(2026, 9, 15, 14, 0),
        datetime(2026, 9, 16, 10, 0),
    ]


def test_cycle_start_lower_bound_is_always_enforced():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="selected_weekdays",
        recurrence_config={"weekdays": [1], "time": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 14, 12, 0),
        window_start_utc=datetime(2026, 9, 14, 0, 0),
        window_end_utc=datetime(2026, 9, 21, 23, 59),
    )

    assert items == [datetime(2026, 9, 21, 10, 0)]


def test_timezone_conversion_returns_naive_utc_datetime():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="daily",
        recurrence_config={"times": "10:00"},
        timezone_name="Europe/Moscow",
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 15, 23, 59),
    )

    assert items == [datetime(2026, 9, 15, 7, 0)]
    assert items[0].tzinfo is None


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError, match="Unknown timezone"):
        recurrence.calculate_recurrence_occurrences(
            recurrence_mode="daily",
            recurrence_config={"times": "10:00"},
            timezone_name="No/Such_Zone",
            cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
            window_start_utc=datetime(2026, 9, 15, 0, 0),
            window_end_utc=datetime(2026, 9, 15, 23, 59),
        )


def test_end_at_is_inclusive_and_filters_later_candidates():
    items = recurrence.calculate_recurrence_occurrences(
        recurrence_mode="daily",
        recurrence_config={"times": "10:00"},
        timezone_name="UTC",
        cycle_start_at_utc=datetime(2026, 9, 15, 0, 0),
        window_start_utc=datetime(2026, 9, 15, 0, 0),
        window_end_utc=datetime(2026, 9, 17, 23, 59),
        cycle_end_mode="at",
        cycle_end_at_utc=datetime(2026, 9, 16, 10, 0),
    )

    assert items == [
        datetime(2026, 9, 15, 10, 0),
        datetime(2026, 9, 16, 10, 0),
    ]


@pytest.mark.parametrize(
    ("successful_count", "expected"),
    [(0, True), (2, True), (3, False), (4, False)],
)
def test_count_capacity_uses_successful_occurrence_count(successful_count, expected):
    assert (
        recurrence.has_recurrence_capacity(
            cycle_end_mode="count",
            cycle_max_occurrences=3,
            successful_occurrence_count=successful_count,
        )
        is expected
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cycle_end_mode": "never", "cycle_end_at_utc": datetime(2026, 9, 15, 10, 0)},
        {"cycle_end_mode": "at"},
        {"cycle_end_mode": "at", "cycle_end_at_utc": datetime(2026, 9, 15, 10, 0), "cycle_max_occurrences": 3},
        {"cycle_end_mode": "count"},
        {"cycle_end_mode": "count", "cycle_max_occurrences": 0},
        {"cycle_end_mode": "count", "cycle_max_occurrences": 3, "cycle_end_at_utc": datetime(2026, 9, 15, 10, 0)},
    ],
)
def test_end_rule_validation_rejects_invalid_combinations(kwargs):
    with pytest.raises(ValueError):
        recurrence.validate_recurrence_end_rule(**kwargs)


def test_module_has_no_db_or_runtime_coupling():
    source = inspect.getsource(recurrence)

    assert "Session" not in source
    assert "SessionLocal" not in source
    assert "Ticket" not in source
    assert "materialize_internal_task" not in source
    assert "asyncio" not in source
    assert "sleep" not in source
    assert "select(" not in source
