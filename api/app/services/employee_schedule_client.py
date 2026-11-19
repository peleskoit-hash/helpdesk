"""Spark v2 transport and strict calendar validation. Never logs remote bodies."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import settings


class ScheduleError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool) -> None:
    if not condition:
        raise ScheduleError("invalid_payload")


def _iso_date(value) -> date:
    _require(type(value) is str)
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ScheduleError("invalid_payload") from None
    _require(result.isoformat() == value)
    return result


def _shift(value: str) -> None:
    match = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])", value)
    _require(match is not None)
    start_h, start_m, end_h, end_m = map(int, match.groups())
    _require(start_h * 60 + start_m < end_h * 60 + end_m)


def validate_export(payload, month: str) -> dict:
    _require(type(month) is str and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) is not None)
    first = _iso_date(month + "-01")
    expected = {date(first.year, first.month, day).isoformat()
                for day in range(1, calendar.monthrange(first.year, first.month)[1] + 1)}
    _require(type(payload) is dict and type(payload.get("version")) is int and payload["version"] == 2)
    _require(payload.get("month") == month)
    _iso_date(payload.get("today"))
    departments = payload.get("departments")
    _require(type(departments) is list and bool(departments))
    department_ids, employee_ids = set(), set()
    for department in departments:
        _require(type(department) is dict)
        ident = department.get("id")
        _require(type(ident) is int and ident > 0 and ident not in department_ids)
        department_ids.add(ident)
        _require(type(department.get("name")) is str and bool(department["name"].strip()))
        _require(type(department.get("employees")) is list)
        for employee in department["employees"]:
            _require(type(employee) is dict)
            ident = employee.get("id")
            _require(type(ident) is int and ident > 0 and ident not in employee_ids)
            employee_ids.add(ident)
            _require(type(employee.get("full_name")) is str and bool(employee["full_name"].strip()))
            _require(type(employee.get("is_active")) is bool)
            _require(type(employee.get("default_shift")) is str)
            if employee["default_shift"]:
                _shift(employee["default_shift"])
            # Dismissed employees may legitimately retain their final month's days.
            # Their external activity flag never filters this calendar.
            for field in ("dismissed_from", "last_work_month"):
                value = employee.get(field)
                if value is not None:
                    _require(type(value) is str and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) is not None)
                    _iso_date(value + "-01")
            if "dismissed_next_month" in employee:
                _require(type(employee["dismissed_next_month"]) is bool)
            _require(type(employee.get("days")) is list)
            seen = set()
            for day in employee["days"]:
                _require(type(day) is dict)
                value = _iso_date(day.get("date")).isoformat()
                _require(value in expected and value not in seen)
                seen.add(value)
                for field in ("work_day", "duty", "is_weekend"):
                    _require(type(day.get(field)) is bool)
                for field in ("shift", "mark"):
                    _require(type(day.get(field)) is str)
                for field in ("weekday", "mark_label"):
                    if field in day:
                        _require(type(day[field]) is str)
                if day["work_day"]:
                    _shift(day["shift"])
                else:
                    _require(day["shift"] == "")
                # mark (including on_call), duty and weekday do not define work time.
            _require(seen == expected)
    return payload  # Preserve the entire validated JSON, including extension fields.


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward X-API-Key to another origin.


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScheduleError("invalid_json")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ScheduleError("invalid_json")


def fetch_month(month: str, *, config=None) -> dict:
    config = config or settings
    base = (config.employee_schedule_api_base_url or "").rstrip("/")
    key = config.employee_schedule_api_key
    try:
        url = urlsplit(base)
        configured = (url.scheme == "https" and bool(url.hostname) and not url.username
                      and not url.password and url.path in ("", "/") and not url.query and not url.fragment)
    except ValueError:
        configured = False
    if not configured or not key:
        raise ScheduleError("not_configured")
    request = Request(base + "/api/schedule/export?" + urlencode({"month": month}),
                      headers={"X-API-Key": key, "Accept": "application/json"}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=config.employee_schedule_request_timeout_seconds) as response:
            if response.status != 200:
                raise ScheduleError("http_error")
            body = response.read(config.employee_schedule_max_response_bytes + 1)
            if len(body) > config.employee_schedule_max_response_bytes:
                raise ScheduleError("response_too_large")
        payload = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except HTTPError as exc:
        code = "unauthorized" if exc.code in (401, 403) else "rate_limited" if exc.code == 429 else "http_error"
        raise ScheduleError(code) from None
    except (TimeoutError, URLError, OSError):
        raise ScheduleError("network_error") from None
    except (ValueError, UnicodeError, RecursionError):
        raise ScheduleError("invalid_json") from None
    return validate_export(payload, month)
