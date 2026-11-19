"""Local schedule binding and read-only shift resolution. No network or automation."""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from ..models import EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot, User
from .employee_schedule_client import ScheduleError, validate_export


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


def _published_employees(db):
    if not _has_table(db, Snapshot):
        raise BindingError("schedule_unavailable", "No published schedule data")
    # One statement reads a consistent pair. SHARE locks keep publication from
    # replacing these rows until the short binding transaction finishes on PG.
    rows = list(db.scalars(select(Snapshot).order_by(Snapshot.month).with_for_update(read=True)))
    if not rows:
        raise BindingError("schedule_unavailable", "No published schedule data")
    result = {}
    for row in rows:
        try:
            validate_export(row.payload_json, row.month)
        except ScheduleError:
            raise BindingError("invalid_snapshot", "Published schedule data is invalid") from None
        for department in row.payload_json["departments"]:
            for employee in department["employees"]:
                result.setdefault(str(employee["id"]), []).append((row.month, employee, department))
    return result


def _identity(records, at):
    # Status, calendar and department changes between months are not an identity
    # conflict. Different normalized full names for the same ID are unsafe to bind.
    if len({normalize_name(employee["full_name"]) for _, employee, _ in records}) != 1:
        raise BindingError("inconsistent_employee", "Conflicting names for the external employee ID")
    month = at.astimezone(MOSCOW).strftime("%Y-%m")
    return next((record for record in records if record[0] == month), records[-1])


def set_binding(db, user_id: str, enabled: bool, *, at: datetime | None = None):
    at = _now(at)
    try:
        # Request-local session: release the auth read before taking SQLite's write
        # lock. PG serializes competing changes to this user with FOR UPDATE.
        if db.get_bind().dialect.name == "sqlite":
            db.rollback()
            db.execute(text("BEGIN IMMEDIATE"))
        _require_binding_table(db)
        user = _user(db, user_id, lock=True)
        binding = db.get(Binding, user_id)
        if not enabled:
            if binding is not None:
                binding.enabled = False
            db.commit()
            return
        if not user.is_active:
            raise BindingError("user_inactive", "Only an active local user can be connected")
        employees = _published_employees(db)
        if binding is None:
            name = normalize_name(user.full_name)
            matches = [ident for ident, records in employees.items()
                       if any(normalize_name(employee["full_name"]) == name for _, employee, _ in records)]
            if not matches:
                raise BindingError("employee_not_found", "No employee matches the full name")
            if len(matches) != 1:
                raise BindingError("employee_ambiguous", "Multiple external IDs match the full name")
            ident = matches[0]
        else:
            ident = binding.external_employee_id
            if ident not in employees:
                raise BindingError("employee_missing", "The saved external ID is absent from published data")
        _, employee, department = _identity(employees[ident], at)
        occupied = db.scalar(select(Binding.user_id).where(Binding.external_employee_id == ident,
                                                         Binding.user_id != user_id))
        if occupied is not None:
            raise BindingError("external_id_in_use", "This external ID is already bound to another user")
        now = at.astimezone(timezone.utc).replace(tzinfo=None)
        if binding is None:
            binding = Binding(user_id=user_id, external_employee_id=ident, connected_at=now)
            db.add(binding)
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
