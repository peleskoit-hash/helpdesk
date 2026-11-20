"""Live administrative binding and read-only resolution of published shifts."""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from ..models import EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot, User
from .employee_schedule_client import ScheduleError, fetch_month, validate_export


MOSCOW = ZoneInfo("Europe/Moscow")


class BindingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("\u0451", "\u0435").split())


def _now(at: datetime | None) -> datetime:
    at = at if at is not None else datetime.now(timezone.utc)
    if at.utcoffset() is None:
        raise ValueError("at must be a timezone-aware datetime")
    return at


def _has_table(db, model) -> bool:
    return inspect(db.connection()).has_table(model.__tablename__)


def _require_binding_table(db):
    if not _has_table(db, Binding):
        raise BindingError("migration_required", "Schedule binding migration required", 503)


def _user(db, user_id, *, lock=False):
    statement = select(User).where(User.id == user_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    user = db.scalar(statement)
    if user is None:
        raise BindingError("user_not_found", "User not found", 404)
    return user


def _live_employees(at):
    local = at.astimezone(MOSCOW)
    following = date(local.year + (local.month == 12), local.month % 12 + 1, 1)
    result = {}
    for month in (local.strftime("%Y-%m"), following.strftime("%Y-%m")):
        try:
            payload = validate_export(fetch_month(month), month)
        except ScheduleError as exc:
            raise BindingError("spark_" + exc.code,
                               "Не удалось получить актуальные данные Spark. Повторите запрос позже.", 503) from None
        for department in payload["departments"]:
            for employee in department["employees"]:
                result.setdefault(str(employee["id"]), []).append((month, employee, department))
    return result


def _identity(records, at):
    # Status, calendar and department changes between months are not an identity
    # conflict. Different normalized full names for the same ID are unsafe to bind.
    if len({normalize_name(employee["full_name"]) for _, employee, _ in records}) != 1:
        raise BindingError("inconsistent_employee", "Conflicting names for the external employee ID")
    month = at.astimezone(MOSCOW).strftime("%Y-%m")
    return next((record for record in records if record[0] == month), records[-1])


def list_employees(db, *, user_id: str | None = None, at: datetime | None = None):
    at = _now(at)
    _require_binding_table(db)
    if user_id is not None:
        _user(db, user_id)
    # Release the request's authorization/preflight read before HTTP, on PG too.
    db.rollback()
    employees = _live_employees(at)
    _require_binding_table(db)
    if user_id is not None:
        _user(db, user_id)
    occupied = dict(db.execute(select(Binding.external_employee_id, Binding.user_id)).all())
    result = []
    for ident, records in employees.items():
        if ident in occupied:
            continue
        _, employee, department = _identity(records, at)
        result.append({"external_employee_id": ident, "full_name": employee["full_name"],
                       "department_id": str(department["id"]), "department_name": department["name"],
                       "available": True})
    return sorted(result, key=lambda employee: (normalize_name(employee["full_name"]), employee["external_employee_id"]))


def set_binding(db, user_id: str, enabled: bool, *, external_employee_id: str | None = None,
                at: datetime | None = None):
    at = _now(at)
    try:
        _require_binding_table(db)
        user = _user(db, user_id)
        if enabled and not user.is_active:
            raise BindingError("user_inactive", "Only an active local user can be connected")
        if enabled and external_employee_id is None and db.get(Binding, user_id) is None:
            raise BindingError("external_id_required", "Select a Spark employee before connecting", 400)
        db.rollback()
        employees = _live_employees(at) if enabled else None
        # Only now lock/recheck mutable local state. No HTTP runs in this transaction.
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        _require_binding_table(db)
        user = _user(db, user_id, lock=True)
        binding = db.get(Binding, user_id)
        if not enabled:
            if external_employee_id is not None:
                raise BindingError("external_id_on_disable", "An external ID cannot be selected while disabling", 400)
            if binding is not None:
                binding.enabled = False
            db.commit()
            return
        if not user.is_active:
            raise BindingError("user_inactive", "Only an active local user can be connected")
        if external_employee_id is not None:
            ident = external_employee_id
            if ident not in employees:
                raise BindingError("employee_missing", "The selected external ID is absent from the live Spark response")
        elif binding is None:
            raise BindingError("external_id_required", "Select a Spark employee before connecting", 400)
        else:
            ident = binding.external_employee_id
            if ident not in employees:
                raise BindingError("employee_missing", "The saved external ID is absent from the live Spark response")
        _, employee, department = _identity(employees[ident], at)
        occupied = db.scalar(select(Binding.user_id).where(Binding.external_employee_id == ident,
                                                         Binding.user_id != user_id))
        if occupied is not None:
            raise BindingError("external_id_in_use", "This external ID is already bound to another user")
        now = at.astimezone(timezone.utc).replace(tzinfo=None)
        if binding is None:
            binding = Binding(user_id=user_id, external_employee_id=ident, connected_at=now)
            db.add(binding)
        elif binding.external_employee_id != ident:
            binding.external_employee_id = ident
            binding.connected_at = now
            binding.last_offline_work_date = None
        binding.enabled = True
        binding.external_full_name = employee["full_name"]
        binding.external_department_id = str(department["id"])
        binding.external_department_name = department["name"]
        binding.last_checked_at = now
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BindingError("binding_conflict", "The external ID or user binding is already occupied") from None
    except Exception:
        db.rollback()
        raise


def remove_binding(db, user_id: str):
    """Release only the local binding, including disabled reservations. No HTTP."""
    try:
        db.rollback()
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        _require_binding_table(db)
        _user(db, user_id, lock=True)
        binding = db.get(Binding, user_id)
        if binding is not None:
            db.delete(binding)
        db.commit()
    except Exception:
        db.rollback()
        raise


@dataclass(frozen=True)
class ResolvedShift:
    status: str
    date: date
    reason: str | None = None
    timezone: str = "Europe/Moscow"
    shift: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    snapshot_updated_at: datetime | None = None


def resolve_employee_schedule(db, user_id: str, at: datetime | None = None) -> ResolvedShift:
    """No writes, expiry-by-age, default hours or implicit network refresh."""
    local = _now(at).astimezone(MOSCOW)
    def unknown(reason):
        return ResolvedShift("unknown", local.date(), reason=reason)
    with db.no_autoflush:
        # Select values rather than cached ORM identities: subsequent calls in one
        # session must see committed updates without flushing/discarding caller edits.
        user = db.execute(select(User.is_active).where(User.id == user_id)).one_or_none()
        if user is None:
            return unknown("user_not_found")
        if not user.is_active:
            return unknown("user_inactive")
        if not _has_table(db, Binding):
            return unknown("migration_required")
        binding = db.execute(select(Binding.enabled, Binding.external_employee_id)
                             .where(Binding.user_id == user_id)).one_or_none()
        if binding is None:
            return unknown("not_connected")
        if not binding.enabled:
            return unknown("binding_disabled")
        if not _has_table(db, Snapshot):
            return unknown("month_missing")
        month = local.strftime("%Y-%m")
        snapshot = db.execute(select(Snapshot.payload_json, Snapshot.fetched_at)
                              .where(Snapshot.month == month)).one_or_none()
        if snapshot is None:
            return unknown("month_missing")
        try:
            validate_export(snapshot.payload_json, month)
        except ScheduleError:
            return unknown("invalid_snapshot")
        employees = [employee for department in snapshot.payload_json["departments"]
                     for employee in department["employees"] if str(employee["id"]) == binding.external_employee_id]
        if len(employees) != 1:
            return unknown("employee_missing")
        days = [day for day in employees[0]["days"] if day["date"] == local.date().isoformat()]
        if len(days) != 1:
            return unknown("date_missing")
        day = days[0]
        fetched = snapshot.fetched_at.replace(tzinfo=timezone.utc)
        if not day["work_day"]:
            return ResolvedShift("non_working", local.date(), snapshot_updated_at=fetched)
        start, end = (datetime.combine(local.date(), time.fromisoformat(part), tzinfo=MOSCOW)
                      for part in day["shift"].split("-"))
        status = "before_shift" if local < start else "after_shift" if local >= end else "working"
        return ResolvedShift(status, local.date(), shift=day["shift"], start_at=start, end_at=end,
                             snapshot_updated_at=fetched)


def binding_status(db, user_id: str, *, at: datetime | None = None) -> dict:
    with db.no_autoflush:
        user = _user(db, user_id)
        _require_binding_table(db)
        binding = db.get(Binding, user_id)
        def timestamp(value):
            return value.replace(tzinfo=timezone.utc) if value is not None else None
        return {
            "user_id": user_id, "local_active": bool(user.is_active),
            "enabled": bool(binding and binding.enabled),
            "external_employee_id": binding.external_employee_id if binding else None,
            "external_full_name": binding.external_full_name if binding else None,
            "external_department_id": binding.external_department_id if binding else None,
            "external_department_name": binding.external_department_name if binding else None,
            "connected_at": timestamp(binding.connected_at) if binding else None,
            "last_checked_at": timestamp(binding.last_checked_at) if binding else None,
            "last_offline_work_date": binding.last_offline_work_date if binding else None,
            "schedule": asdict(resolve_employee_schedule(db, user_id, at)),
        }
