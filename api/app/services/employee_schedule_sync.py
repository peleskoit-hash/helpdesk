"""Persistent Spark sync coordinated by the existing scheduler, not a new timer."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, inspect, or_, select, update

from ..config import settings
from ..db import SessionLocal
from ..models import EmployeeScheduleSnapshot as Snapshot, EmployeeScheduleSyncState as SyncState
from .employee_schedule_client import ScheduleError, fetch_month, validate_export


logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")
LEASE_SECONDS = 300
RETRY_SECONDS = 300
_task: asyncio.Task | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def target_months(at: datetime) -> tuple[str, str]:
    local = _utc(at).replace(tzinfo=timezone.utc).astimezone(MOSCOW)
    year, month = local.year, local.month
    return f"{year:04d}-{month:02d}", f"{year + (month == 12):04d}-{month % 12 + 1:02d}"


def schema_ready(db) -> bool:
    inspector = inspect(db.connection())
    return all(inspector.has_table(table) for table in (Snapshot.__tablename__, SyncState.__tablename__))


@dataclass(frozen=True)
class SyncClaim:
    token: str
    generation: int
    months: tuple[str, str]


def claim_sync(db, *, manual: bool = False, at: datetime | None = None) -> tuple[str, SyncClaim | None]:
    if not settings.employee_schedule_enabled:
        return "disabled", None
    if not settings.employee_schedule_api_base_url or not settings.employee_schedule_api_key:
        return "not_configured", None
    if not schema_ready(db):
        return "migration_required", None
    now = _utc(at) if at is not None else utc_now()
    state = db.get(SyncState, 1)
    if state is None:
        return "migration_required", None
    if state.lease_until is not None and state.lease_until > now:
        return "running", None
    months = target_months(now)
    if not manual:
        if state.next_attempt_at is not None and now < state.next_attempt_at:
            return "not_due", None
        local = now.replace(tzinfo=timezone.utc).astimezone(MOSCOW)
        cutoff = local.replace(hour=16, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        saved_months = set(db.scalars(select(Snapshot.month)))
        overdue = now >= cutoff and (state.last_success_at is None or state.last_success_at < cutoff)
        if not (overdue or saved_months != set(months) or state.status in ("error", "running")):
            return "not_due", None
    generation = state.generation
    # Release SQLite's read transaction before the conditional write. The generation
    # predicate still makes a simultaneous PostgreSQL or SQLite claim lose safely.
    db.rollback()
    claim = SyncClaim(str(uuid.uuid4()), generation + 1, months)
    changed = db.execute(update(SyncState).where(
        SyncState.id == 1, SyncState.generation == generation,
        or_(SyncState.lease_until.is_(None), SyncState.lease_until <= now),
    ).values(generation=claim.generation, status="running", source="manual" if manual else "automatic",
             last_attempt_at=now, last_error_code=None, next_attempt_at=None,
             worker_token=claim.token, lease_until=now + timedelta(seconds=LEASE_SECONDS))
             .execution_options(synchronize_session=False)).rowcount
    db.commit()
    return ("accepted", claim) if changed == 1 else ("running", None)


def _owner(claim: SyncClaim, now: datetime):
    return (SyncState.id == 1, SyncState.worker_token == claim.token,
            SyncState.generation == claim.generation, SyncState.lease_until > now)


def _record_failure(claim, code, factory, now):
    with factory() as db:
        changed = db.execute(update(SyncState).where(*_owner(claim, now)).values(
            status="error", last_error_code=code, next_attempt_at=now + timedelta(seconds=RETRY_SECONDS),
            worker_token=None, lease_until=None,
        ).execution_options(synchronize_session=False)).rowcount
        db.commit()
    return "error" if changed else "superseded"


def run_sync(claim: SyncClaim, *, session_factory=None, fetcher=None, clock=None) -> str:
    factory, fetcher, clock = session_factory or SessionLocal, fetcher or fetch_month, clock or utc_now
    try:
        if not settings.employee_schedule_enabled:
            raise ScheduleError("disabled")
        # No DB transaction or connection is held during either network request.
        payloads = {month: validate_export(fetcher(month), month) for month in claim.months}
        now = _utc(clock())
        if target_months(now) != claim.months:
            raise ScheduleError("month_changed")
        if not settings.employee_schedule_enabled:
            raise ScheduleError("disabled")
        with factory() as db:
            # This conditional write locks the singleton until pair publication commits.
            changed = db.execute(update(SyncState).where(*_owner(claim, now)).values(
                status="success", last_success_at=now, last_error_code=None,
                next_attempt_at=None, worker_token=None, lease_until=None,
            ).execution_options(synchronize_session=False)).rowcount
            if changed != 1:
                db.rollback()
                return "superseded"
            for month, payload in payloads.items():
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                  allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                row = db.get(Snapshot, month)
                if row is None:
                    row = Snapshot(month=month)
                    db.add(row)
                row.payload_json, row.payload_hash = payload, digest
                row.revision, row.fetched_at = claim.generation, now
            db.execute(delete(Snapshot).where(Snapshot.month.not_in(claim.months)))
            db.commit()
        return "success"
    except Exception as exc:
        # SQL exceptions may contain payload parameters; remote exceptions may contain
        # personal data or credentials. Persist/log only fixed codes, never exc text.
        code = exc.code if isinstance(exc, ScheduleError) else "sync_error"
        logger.warning("Employee schedule sync failed: %s", code)
        try:
            return _record_failure(claim, code, factory, _utc(clock()))
        except Exception:
            logger.warning("Employee schedule sync failure state could not be saved")
            return "error"  # The persisted lease permits recovery after expiry.


def run_due_sync(*, session_factory=None, at=None, fetcher=None, clock=None) -> str:
    if not settings.employee_schedule_enabled:
        return "disabled"  # In particular, no DB access before explicit migration.
    factory = session_factory or SessionLocal
    with factory() as db:
        result, claim = claim_sync(db, at=at)
    return run_sync(claim, session_factory=factory, fetcher=fetcher, clock=clock) if claim else result


async def _run_background():
    try:
        await asyncio.to_thread(run_due_sync)
    except Exception:
        logger.warning("Employee schedule sync precheck failed safely")


def schedule_sync_tick() -> None:
    global _task
    if settings.employee_schedule_enabled and (_task is None or _task.done()):
        _task = asyncio.create_task(_run_background())


async def finish_schedule_sync() -> None:
    global _task
    if _task is not None:
        # Do not cancel a to_thread write halfway through publication. The transport
        # has bounded request timeouts and the DB lease survives an abrupt shutdown.
        await asyncio.shield(_task)
        _task = None


def sync_status(db) -> dict:
    configured = bool(settings.employee_schedule_api_base_url and settings.employee_schedule_api_key)
    result = {"enabled": settings.employee_schedule_enabled, "configured": configured,
              "schema_ready": schema_ready(db), "status": "migration_required", "months": []}
    if not result["schema_ready"]:
        return result
    state = db.get(SyncState, 1)
    if state is None:
        result["schema_ready"] = False
        return result
    status = state.status
    if status == "running" and (state.lease_until is None or state.lease_until <= utc_now()):
        status = "interrupted"
    def timestamp(value):
        return value.replace(tzinfo=timezone.utc) if value is not None else None
    result.update(status=status, source=state.source, last_attempt_at=timestamp(state.last_attempt_at),
                  last_success_at=timestamp(state.last_success_at), last_error_code=state.last_error_code,
                  next_attempt_at=timestamp(state.next_attempt_at))
    for row in db.scalars(select(Snapshot).order_by(Snapshot.month)):
        departments = row.payload_json["departments"]
        employees = [employee for department in departments for employee in department["employees"]]
        result["months"].append({"month": row.month, "revision": row.revision,
                                 "fetched_at": timestamp(row.fetched_at), "departments": len(departments),
                                 "employees": len(employees),
                                 "calendar_days": sum(len(employee["days"]) for employee in employees)})
    return result
