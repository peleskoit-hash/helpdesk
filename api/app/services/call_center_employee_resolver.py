from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import CallCenterEmployeeResolverState


SYSTEM_SOURCE_KEY = "call_center_employee_by_internal_number"
STATE_KEY = "latest"
BITRIX_METHOD = "im.department.employees.get"

STATUS_FOUND = "found"
STATUS_NOT_FOUND = "not_found_in_successful_response"
STATUS_STALE_FALLBACK = "stale_fallback_used"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_REFRESH_TIMEOUT = "refresh_timeout"
STATUS_TECHNICAL_ERROR = "technical_error"
STATUS_AMBIGUOUS = "ambiguous_internal_number"

_LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()
_REFRESH_EVENT: threading.Event | None = None
_REFRESH_THREAD: threading.Thread | None = None
_REFRESH_EVENT_ID: str | None = None
_PERIODIC_STOP_EVENT: threading.Event | None = None
_PERIODIC_THREAD: threading.Thread | None = None

ERROR_NOT_CONFIGURED = "not_configured"
ERROR_HTTP = "http_error"
ERROR_NETWORK = "network_error"
ERROR_TIMEOUT = "request_timeout"
ERROR_INVALID_JSON = "invalid_json"
ERROR_INVALID_PAYLOAD = "invalid_payload"
ERROR_BITRIX_API = "bitrix_api_error"
ERROR_STORAGE = "storage_error"


class CallCenterEmployeeResolverError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class CallCenterEmployee:
    internal_number: str
    bitrix_id: str
    first_name: str
    last_name: str
    full_name: str
    department_ids: list[int]
    bitrix_login: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "internal_number": self.internal_number,
            "bitrix_id": self.bitrix_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "department_ids": list(self.department_ids),
            "bitrix_login": self.bitrix_login,
        }


@dataclass(frozen=True)
class ResolveResult:
    status: str
    employee: CallCenterEmployee | None = None
    internal_number: str = ""
    used_stale_fallback: bool = False
    error_code: str | None = None
    error_message: str | None = None

    @property
    def found(self) -> bool:
        return self.employee is not None and self.status in {STATUS_FOUND, STATUS_STALE_FALLBACK}

    def employee_payload(self) -> dict[str, Any]:
        empty = {
            "internal_number": self.internal_number,
            "bitrix_id": "",
            "first_name": "",
            "last_name": "",
            "full_name": "",
            "department_ids": [],
            "resolution_status": self.status,
            "used_stale_fallback": self.used_stale_fallback,
            "bitrix_login": None,
        }
        if self.employee is None:
            return empty
        payload = self.employee.as_dict()
        payload.update(
            {
                "resolution_status": self.status,
                "used_stale_fallback": self.used_stale_fallback,
            }
        )
        return payload


def configured() -> bool:
    return bool((settings.bitrix_kc_webhook_base or "").strip())


def department_ids() -> list[int]:
    values: list[int] = []
    for item in str(settings.bitrix_kc_department_ids or "60").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError:
            continue
    return values or [60]


def get_descriptor() -> dict[str, Any]:
    return {
        "key": SYSTEM_SOURCE_KEY,
        "title": "Сотрудник КЦ по внутреннему номеру",
        "category": "system_source",
        "removable": False,
        "editable_runtime": False,
        "purpose": "Определяет актуального сотрудника Колл-центра по ежедневно назначаемому внутреннему номеру.",
        "input": ["destination/source = Колл Центр", "internal_number"],
        "output": [
            "internal_number",
            "bitrix_id",
            "first_name",
            "last_name",
            "full_name",
            "department_ids",
            "resolution_status",
            "used_stale_fallback",
        ],
        "external_method": BITRIX_METHOD,
        "department_ids": department_ids(),
        "trigger_points": ["early_precreate", "final_create_distribution", "scheduled_refresh"],
        "configuration_keys": [
            "BITRIX_KC_WEBHOOK_BASE",
            "BITRIX_KC_DEPARTMENT_IDS",
            "BITRIX_KC_REFRESH_INTERVAL_SECONDS",
            "BITRIX_KC_MAX_STALE_SECONDS",
            "BITRIX_KC_FINAL_WAIT_SECONDS",
            "BITRIX_KC_REQUEST_TIMEOUT_SECONDS",
            "BITRIX_KC_RETRY_COUNT",
        ],
        "implementation_path": "api/app/services/call_center_employee_resolver.py",
        "contains_secrets": True,
        "secret_values_exposed": False,
        "configured": configured(),
    }


def get_status(db: Session | None = None) -> dict[str, Any]:
    state = _read_state()
    snapshot = state["snapshot"]
    metadata = state["metadata"]
    fetched_at = _parse_dt(snapshot.get("fetched_at")) or state["fetched_at"]
    age = _age_seconds(fetched_at)
    return {
        "configured": configured(),
        "enabled": True,
        "snapshot_available": fetched_at is not None or bool(snapshot),
        "snapshot_age_seconds": age,
        "last_attempt_at": _iso(state["last_attempt_at"] or metadata.get("last_attempt_at")),
        "last_success_at": _iso(state["last_success_at"] or metadata.get("last_success_at")),
        "last_duration_ms": metadata.get("last_duration_ms"),
        "employee_count": metadata.get("total_employee_count", 0),
        "assigned_employee_count": metadata.get("assigned_employee_count", 0),
        "ambiguous_internal_number_count": metadata.get("ambiguous_internal_number_count", metadata.get("ambiguous_internal_numbers", 0)),
        "department_ids": metadata.get("department_ids") or department_ids(),
        "last_error_code": metadata.get("last_error_code"),
        "last_error_message": metadata.get("last_error_message"),
        "refresh_in_progress": refresh_in_progress(),
    }


def refresh_in_progress() -> bool:
    with _LOCK:
        return _REFRESH_EVENT is not None and not _REFRESH_EVENT.is_set()


def start_refresh_if_needed(internal_number: str | None = None) -> bool:
    try:
        with SessionLocal() as db:
            if not _should_refresh(db, internal_number=internal_number, force=False):
                return False
    except Exception as exc:
        _LOGGER.warning("Call-center employee refresh precheck failed safely: %s", type(exc).__name__)
    return _start_background_refresh(force=bool(internal_number))


def start_periodic_refresh_loop() -> threading.Thread:
    global _PERIODIC_STOP_EVENT, _PERIODIC_THREAD
    with _LOCK:
        if _PERIODIC_THREAD is not None and _PERIODIC_THREAD.is_alive():
            return _PERIODIC_THREAD
        _PERIODIC_STOP_EVENT = threading.Event()
        thread = threading.Thread(
            target=_periodic_refresh_loop,
            args=(_PERIODIC_STOP_EVENT,),
            name="call-center-employee-refresh-loop",
            daemon=True,
        )
        _PERIODIC_THREAD = thread
        thread.start()
    if configured():
        start_refresh_if_needed(None)
    return thread


def stop_periodic_refresh_loop(join_timeout: float = 5.0) -> None:
    global _PERIODIC_STOP_EVENT, _PERIODIC_THREAD
    with _LOCK:
        stop_event = _PERIODIC_STOP_EVENT
        thread = _PERIODIC_THREAD
        if stop_event is not None:
            stop_event.set()
    if thread is not None:
        thread.join(timeout=max(0.0, float(join_timeout)))
    with _LOCK:
        if _PERIODIC_THREAD is thread and (thread is None or not thread.is_alive()):
            _PERIODIC_THREAD = None
            _PERIODIC_STOP_EVENT = None


def resolve(db: Session | None, internal_number: str | None, final_wait_seconds: float | None = None) -> ResolveResult:
    number = _normalize_internal_number(internal_number)
    if not number:
        return ResolveResult(status=STATUS_NOT_FOUND, internal_number="")
    if not configured():
        return ResolveResult(status=STATUS_NOT_CONFIGURED, internal_number=number)

    before_state = _read_state()
    snapshot = before_state["snapshot"]
    direct = _resolve_from_snapshot(snapshot, number, stale=False)
    if direct is not None and _snapshot_is_fresh(snapshot) and direct.status in {STATUS_FOUND, STATUS_AMBIGUOUS}:
        return direct

    should_force = direct is None or direct.status in {STATUS_NOT_FOUND, STATUS_AMBIGUOUS}
    event = _ensure_refresh_started(force=should_force)
    refresh_id = _refresh_id_for_event(event)
    timeout = final_wait_seconds
    if timeout is None:
        timeout = float(settings.bitrix_kc_final_wait_seconds)
    if event is not None:
        event.wait(max(0.0, float(timeout)))

    if event is not None and not event.is_set():
        fallback = _found_stale_fallback(snapshot, number)
        if fallback is not None:
            return fallback
        return ResolveResult(status=STATUS_REFRESH_TIMEOUT, internal_number=number)

    refreshed_state = _read_state()
    metadata = refreshed_state["metadata"]
    refreshed_snapshot = refreshed_state["snapshot"]
    if refresh_id and metadata.get("last_success_refresh_id") == refresh_id:
        refreshed = _resolve_from_snapshot(refreshed_snapshot, number, stale=False)
        if refreshed is not None:
            return refreshed
        return ResolveResult(status=STATUS_NOT_FOUND, internal_number=number)

    if refresh_id and metadata.get("last_error_refresh_id") == refresh_id:
        fallback = _found_stale_fallback(snapshot, number)
        if fallback is not None:
            return fallback
        return ResolveResult(
            status=STATUS_TECHNICAL_ERROR,
            internal_number=number,
            error_code=metadata.get("last_error_code"),
            error_message=metadata.get("last_error_message"),
        )

    if refresh_id and metadata.get("last_attempt_refresh_id") != refresh_id:
        fallback = _found_stale_fallback(snapshot, number)
        if fallback is not None:
            return fallback
        return ResolveResult(status=STATUS_TECHNICAL_ERROR, internal_number=number, error_code=ERROR_STORAGE)

    refreshed = _resolve_from_snapshot(refreshed_snapshot, number, stale=False)
    if refreshed is not None and _snapshot_is_fresh(refreshed_snapshot) and refreshed.status in {STATUS_FOUND, STATUS_AMBIGUOUS}:
        return refreshed

    if _last_refresh_failed():
        fallback = _found_stale_fallback(snapshot, number)
        if fallback is not None:
            return fallback
        return ResolveResult(status=STATUS_TECHNICAL_ERROR, internal_number=number)

    return ResolveResult(status=STATUS_NOT_FOUND, internal_number=number)


def refresh(force: bool = False) -> bool:
    if not configured():
        return False
    with SessionLocal() as db:
        if not _should_refresh(db, internal_number=None, force=force):
            return True
    event = _ensure_refresh_started(force=force)
    if event is None:
        return True
    event.wait()
    with SessionLocal() as db:
        return not _last_refresh_failed(db)


def _periodic_refresh_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            refresh(force=False)
        except Exception as exc:
            _LOGGER.warning("Call-center employee scheduled refresh failed safely: %s", type(exc).__name__)
        stop_event.wait(max(30, int(settings.bitrix_kc_refresh_interval_seconds)))


def _ensure_refresh_started(*, force: bool) -> threading.Event | None:
    with _LOCK:
        global _REFRESH_EVENT, _REFRESH_THREAD, _REFRESH_EVENT_ID
        if _REFRESH_EVENT is not None and not _REFRESH_EVENT.is_set():
            _LOGGER.info("Call-center employee refresh skipped because current single-flight exists")
            return _REFRESH_EVENT
        _REFRESH_EVENT = threading.Event()
        event = _REFRESH_EVENT
        _REFRESH_EVENT_ID = str(uuid.uuid4())
        _REFRESH_THREAD = threading.Thread(
            target=_refresh_worker,
            args=(event, force, _REFRESH_EVENT_ID),
            name="call-center-employee-refresh",
            daemon=True,
        )
        _REFRESH_THREAD.start()
        return event


def _start_background_refresh(*, force: bool) -> bool:
    return _ensure_refresh_started(force=force) is not None


def _refresh_id_for_event(event: threading.Event | None) -> str | None:
    if event is None:
        return None
    with _LOCK:
        if _REFRESH_EVENT is event:
            return _REFRESH_EVENT_ID
    return None


def _refresh_worker(event: threading.Event, force: bool, refresh_id: str) -> None:
    global _REFRESH_EVENT, _REFRESH_THREAD, _REFRESH_EVENT_ID
    try:
        _refresh_once(force=force, refresh_id=refresh_id)
    except Exception as exc:
        _LOGGER.warning("Call-center employee refresh failed safely outside storage: %s", type(exc).__name__)
    finally:
        event.set()
        with _LOCK:
            if _REFRESH_EVENT is event:
                _REFRESH_EVENT = None
                _REFRESH_THREAD = None
                _REFRESH_EVENT_ID = None


def _refresh_once(*, force: bool, refresh_id: str | None = None) -> None:
    started = time.monotonic()
    attempt_at = datetime.utcnow()
    refresh_id = refresh_id or str(uuid.uuid4())
    deps = department_ids()
    _LOGGER.info("Call-center employee refresh started for departments %s", deps)
    payload: dict[str, Any] | None = None
    error_code = None
    error_message = None
    attempts = max(0, int(settings.bitrix_kc_retry_count)) + 1
    for index in range(attempts):
        if index > 0:
            _LOGGER.info("Call-center employee refresh retry started")
        try:
            payload = _call_bitrix(deps)
            _validate_result_payload(payload)
            error_code = None
            error_message = None
            break
        except CallCenterEmployeeResolverError as exc:
            payload = None
            error_code = exc.code
            error_message = _safe_error_message(exc)
            if not exc.retryable or index >= attempts - 1:
                break
        except Exception as exc:
            payload = None
            error_code = ERROR_STORAGE
            error_message = _safe_error_message(exc)
            break
    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        with SessionLocal() as db:
            state = _get_or_create_state(db)
            state.last_attempt_at = attempt_at
            metadata = dict(state.metadata_json or {})
            metadata.update(
                {
                    "current_refresh_id": refresh_id,
                    "last_attempt_refresh_id": refresh_id,
                    "last_attempt_at": _iso(attempt_at),
                    "last_duration_ms": duration_ms,
                    "department_ids": deps,
                    "last_error_code": error_code,
                    "last_error_message": error_message,
                }
            )
            if payload is None:
                metadata["last_error_refresh_id"] = refresh_id
                state.metadata_json = metadata
                state.updated_at = datetime.utcnow()
                db.commit()
                _LOGGER.warning("Call-center employee refresh failed: %s", error_code or "technical_error")
                return
            snapshot, counts = _build_snapshot(payload, deps)
            fetched_at = datetime.utcnow()
            snapshot["fetched_at"] = _iso(fetched_at)
            snapshot["refresh_id"] = refresh_id
            metadata.update(
                {
                    "last_success_refresh_id": refresh_id,
                    "last_error_refresh_id": None,
                    "last_success_at": _iso(fetched_at),
                    "total_employee_count": counts["total"],
                    "assigned_employee_count": counts["assigned"],
                    "ambiguous_internal_numbers": counts["ambiguous"],
                    "ambiguous_internal_number_count": counts["ambiguous"],
                    "last_error_code": None,
                    "last_error_message": None,
                }
            )
            state.snapshot_json = snapshot
            state.metadata_json = metadata
            state.fetched_at = fetched_at
            state.last_success_at = fetched_at
            state.updated_at = fetched_at
            db.commit()
            _LOGGER.info(
                "Call-center employee refresh succeeded: total=%s assigned=%s duration_ms=%s",
                counts["total"],
                counts["assigned"],
                duration_ms,
            )
    except Exception as exc:
        _LOGGER.warning("Call-center employee refresh storage update failed safely: %s", type(exc).__name__)


def _call_bitrix(deps: list[int]) -> dict[str, Any]:
    base = (settings.bitrix_kc_webhook_base or "").strip()
    if not base:
        raise CallCenterEmployeeResolverError(ERROR_NOT_CONFIGURED, "BITRIX_KC_WEBHOOK_BASE is not configured", retryable=False)
    base = base if base.endswith("/") else f"{base}/"
    url = f"{base}{BITRIX_METHOD}.json"
    body = json.dumps({"ID": deps, "USER_DATA": "Y"}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"accept": "application/json", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1, int(settings.bitrix_kc_request_timeout_seconds))) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        retryable = exc.code == 429 or 500 <= exc.code < 600
        raise CallCenterEmployeeResolverError(ERROR_HTTP, f"HTTP {exc.code} from {BITRIX_METHOD}", retryable=retryable) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise CallCenterEmployeeResolverError(ERROR_TIMEOUT, f"Request timeout from {BITRIX_METHOD}", retryable=True) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise CallCenterEmployeeResolverError(ERROR_TIMEOUT, f"Request timeout from {BITRIX_METHOD}", retryable=True) from exc
        raise CallCenterEmployeeResolverError(ERROR_NETWORK, f"Network error from {BITRIX_METHOD}", retryable=True) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CallCenterEmployeeResolverError(ERROR_INVALID_JSON, f"Invalid JSON from {BITRIX_METHOD}", retryable=False) from exc
    if not isinstance(payload, dict):
        raise CallCenterEmployeeResolverError(ERROR_INVALID_PAYLOAD, f"Unexpected response from {BITRIX_METHOD}", retryable=False)
    if payload.get("error"):
        raise CallCenterEmployeeResolverError(ERROR_BITRIX_API, f"Bitrix error from {BITRIX_METHOD}", retryable=False)
    return payload


def _build_snapshot(payload: dict[str, Any], deps: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    employees = _extract_employees(payload.get("result"))
    by_number: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    seen_identity: set[tuple[str, str]] = set()
    total = 0
    assigned = 0
    for raw in employees:
        if not isinstance(raw, dict):
            continue
        total += 1
        employee = _normalize_employee(raw)
        if employee is None:
            continue
        identity = (employee.bitrix_id, employee.internal_number)
        if employee.bitrix_id and identity in seen_identity:
            continue
        seen_identity.add(identity)
        assigned += 1
        if employee.internal_number in by_number:
            ambiguous.add(employee.internal_number)
            by_number.pop(employee.internal_number, None)
            continue
        if employee.internal_number in ambiguous:
            continue
        by_number[employee.internal_number] = employee.as_dict()
    return (
        {
            "version": 1,
            "department_ids": deps,
            "employees_by_number": by_number,
            "ambiguous_internal_numbers": sorted(ambiguous),
        },
        {"total": total, "assigned": assigned, "ambiguous": len(ambiguous)},
    )


def _validate_result_payload(payload: dict[str, Any]) -> None:
    result = payload.get("result")
    if isinstance(result, list):
        return
    if isinstance(result, dict):
        for value in result.values():
            if not isinstance(value, (list, dict)):
                raise CallCenterEmployeeResolverError(
                    ERROR_INVALID_PAYLOAD,
                    f"Unexpected result structure from {BITRIX_METHOD}",
                    retryable=False,
                )
        return
    raise CallCenterEmployeeResolverError(
        ERROR_INVALID_PAYLOAD,
        f"Unexpected result structure from {BITRIX_METHOD}",
        retryable=False,
    )


def _extract_employees(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if not isinstance(result, dict):
        return []
    employees: list[dict[str, Any]] = []
    for value in result.values():
        if isinstance(value, list):
            employees.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            employees.extend(_extract_employees(value))
    return employees


def _normalize_employee(raw: dict[str, Any]) -> CallCenterEmployee | None:
    phones = raw.get("phones") if isinstance(raw.get("phones"), dict) else raw.get("PHONES")
    if not isinstance(phones, dict):
        phones = {}
    internal = _normalize_internal_number(
        phones.get("inner_phone")
        or phones.get("INNER_PHONE")
        or raw.get("inner_phone")
        or raw.get("INNER_PHONE")
    )
    if not internal:
        return None
    bitrix_id = str(raw.get("id") or raw.get("ID") or "").strip()
    first_name = str(raw.get("first_name") or raw.get("NAME") or "").strip()
    last_name = str(raw.get("last_name") or raw.get("LAST_NAME") or "").strip()
    full_name = str(raw.get("name") or raw.get("FULL_NAME") or " ".join(part for part in [last_name, first_name] if part)).strip()
    departments = raw.get("departments") or raw.get("UF_DEPARTMENT") or raw.get("DEPARTMENTS") or []
    if not isinstance(departments, list):
        departments = [departments]
    department_values: list[int] = []
    for item in departments:
        try:
            department_values.append(int(item))
        except (TypeError, ValueError):
            continue
    login = raw.get("login") or raw.get("LOGIN")
    return CallCenterEmployee(
        internal_number=internal,
        bitrix_id=bitrix_id,
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        department_ids=department_values,
        bitrix_login=str(login).strip() if login else None,
    )


def _resolve_from_snapshot(snapshot: dict[str, Any], number: str, *, stale: bool) -> ResolveResult | None:
    if not snapshot:
        return None
    ambiguous = set(snapshot.get("ambiguous_internal_numbers") or [])
    if number in ambiguous:
        return ResolveResult(status=STATUS_AMBIGUOUS, internal_number=number)
    employees = snapshot.get("employees_by_number")
    if not isinstance(employees, dict):
        return None
    item = employees.get(number)
    if not isinstance(item, dict):
        return ResolveResult(status=STATUS_NOT_FOUND, internal_number=number)
    employee = CallCenterEmployee(
        internal_number=str(item.get("internal_number") or number),
        bitrix_id=str(item.get("bitrix_id") or ""),
        first_name=str(item.get("first_name") or ""),
        last_name=str(item.get("last_name") or ""),
        full_name=str(item.get("full_name") or ""),
        department_ids=[int(v) for v in item.get("department_ids") or []],
        bitrix_login=item.get("bitrix_login"),
    )
    return ResolveResult(
        status=STATUS_STALE_FALLBACK if stale else STATUS_FOUND,
        employee=employee,
        internal_number=number,
        used_stale_fallback=stale,
    )


def _should_refresh(db: Session, *, internal_number: str | None, force: bool) -> bool:
    if not configured():
        return False
    if force:
        return True
    snapshot = _snapshot_from_state(_get_state(db))
    if not snapshot:
        return True
    if not _snapshot_is_fresh(snapshot):
        return True
    number = _normalize_internal_number(internal_number)
    if number:
        direct = _resolve_from_snapshot(snapshot, number, stale=False)
        return direct is None or direct.status != STATUS_FOUND
    return False


def _snapshot_is_fresh(snapshot: dict[str, Any]) -> bool:
    fetched_at = _parse_dt(snapshot.get("fetched_at"))
    age = _age_seconds(fetched_at)
    return age is not None and age <= int(settings.bitrix_kc_refresh_interval_seconds)


def _snapshot_can_be_stale_fallback(snapshot: dict[str, Any]) -> bool:
    fetched_at = _parse_dt(snapshot.get("fetched_at"))
    age = _age_seconds(fetched_at)
    return age is not None and age <= int(settings.bitrix_kc_max_stale_seconds)


def _last_refresh_failed(db: Session | None = None) -> bool:
    metadata = _read_state()["metadata"]
    return bool(metadata.get("last_error_code"))


def _get_state(db: Session) -> CallCenterEmployeeResolverState | None:
    return db.get(CallCenterEmployeeResolverState, STATE_KEY)


def _read_state() -> dict[str, Any]:
    empty = {
        "snapshot": {},
        "metadata": {},
        "fetched_at": None,
        "last_attempt_at": None,
        "last_success_at": None,
    }
    try:
        with SessionLocal() as db:
            state = _get_state(db)
            if state is None:
                return empty
            return {
                "snapshot": dict(state.snapshot_json) if isinstance(state.snapshot_json, dict) else {},
                "metadata": dict(state.metadata_json) if isinstance(state.metadata_json, dict) else {},
                "fetched_at": state.fetched_at,
                "last_attempt_at": state.last_attempt_at,
                "last_success_at": state.last_success_at,
            }
    except Exception as exc:
        _LOGGER.warning("Call-center employee resolver state read failed safely: %s", type(exc).__name__)
        failed = dict(empty)
        failed["metadata"] = {"last_error_code": ERROR_STORAGE, "last_error_message": _safe_error_message(exc)}
        return failed


def _get_or_create_state(db: Session) -> CallCenterEmployeeResolverState:
    state = _get_state(db)
    if state is None:
        state = CallCenterEmployeeResolverState(
            key=STATE_KEY,
            snapshot_json=None,
            metadata_json={},
            updated_at=datetime.utcnow(),
        )
        db.add(state)
        db.flush()
    return state


def _snapshot_from_state(state: CallCenterEmployeeResolverState | None) -> dict[str, Any]:
    if state is None or not isinstance(state.snapshot_json, dict):
        return {}
    return state.snapshot_json


def _found_stale_fallback(snapshot: dict[str, Any], number: str) -> ResolveResult | None:
    fallback = _resolve_from_snapshot(snapshot, number, stale=True)
    if fallback is None or not fallback.found:
        return None
    if not _snapshot_can_be_stale_fallback(snapshot):
        return None
    return fallback


def _normalize_internal_number(value: Any) -> str:
    return str(value or "").strip()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return max(0, int((datetime.utcnow() - value).total_seconds()))


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _safe_error_message(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    forbidden = (settings.bitrix_kc_webhook_base or "").strip()
    if forbidden and forbidden in text:
        text = text.replace(forbidden, "[redacted]")
    return text[:300]
