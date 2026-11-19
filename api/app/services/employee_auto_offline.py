"""Individual shift endings, plus the unchanged 19:01 legacy daily window."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select, text

from ..db import SessionLocal
from ..models import EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot, User
from .employee_schedule import resolve_employee_schedule
from .statistics_lifecycle import set_user_availability_status


MOSCOW = ZoneInfo("Europe/Moscow")
_last_processed_date: date | None = None
logger = logging.getLogger(__name__)


def _write_lock(db) -> None:
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def _finished_shift(db, user_id, binding, local):
    # Never infer an earlier shift before this connection existed. Walk backwards
    # only through confirmed dates; the first working date is the latest shift.
    connected_at = binding.connected_at.replace(tzinfo=timezone.utc)
    first_date = connected_at.astimezone(MOSCOW).date()
    probe = local
    while probe.date() >= first_date:
        if binding.last_offline_work_date is not None and probe.date() <= binding.last_offline_work_date:
            return None
        shift = resolve_employee_schedule(db, user_id, probe)
        if shift.status in {"unknown", "working"}:
            return None
        if shift.status == "after_shift":
            if (shift.date == probe.date() and shift.end_at is not None and shift.start_at is not None
                    and shift.end_at.utcoffset() is not None and shift.start_at.utcoffset() is not None
                    and shift.start_at < shift.end_at <= local and shift.end_at > connected_at):
                return shift
            return None
        if shift.status not in {"before_shift", "non_working"}:
            return None
        probe = datetime.combine(probe.date() - timedelta(days=1), time.max, tzinfo=MOSCOW)
    return None


def _run_individual_offline(factory, local) -> int:
    with factory() as db:
        if not inspect(db.connection()).has_table(Binding.__tablename__):
            return 0
        ids = list(db.scalars(select(User.id).join(Binding, Binding.user_id == User.id).where(
            User.is_active.is_(True), User.role == "agent", Binding.enabled.is_(True),
        ).order_by(User.id)))
    changed = 0
    for user_id in ids:
        try:
            with factory() as db:
                _write_lock(db)
                user = db.scalar(select(User).where(User.id == user_id).with_for_update()
                                 .execution_options(populate_existing=True))
                if user is None or not user.is_active or user.role != "agent":
                    continue
                binding = db.scalar(select(Binding).where(Binding.user_id == user_id).with_for_update())
                if binding is None or not binding.enabled:
                    continue
                # Match the existing User -> Binding -> snapshots lock order. Keep
                # a concurrent publication from changing the calendar before commit.
                if inspect(db.connection()).has_table(Snapshot.__tablename__):
                    list(db.scalars(select(Snapshot.month).order_by(Snapshot.month).with_for_update(read=True)))
                shift = _finished_shift(db, user_id, binding, local)
                if shift is None:
                    continue
                transition = user.availability_status != "offline"
                if transition:
                    set_user_availability_status(
                        db, user, "offline", now=local.astimezone(timezone.utc).replace(tzinfo=None),
                        changed_by_admin=False, change_source="workday_end",
                        change_source_time=shift.end_at.astimezone(MOSCOW).strftime("%H:%M"),
                    )
                binding.last_offline_work_date = shift.date
                db.commit()
                changed += int(transition)
        except Exception:
            # Do not expose snapshot payloads from DB exceptions, or let a single
            # recipient failure block others. Legacy handling still excludes them.
            logger.warning("Individual schedule offline pass failed safely")
    return changed


def run_employee_auto_offline_once(*, at: datetime | None = None, session_factory=None) -> int:
    global _last_processed_date
    if at is None:
        at = datetime.now(MOSCOW)
    elif at.utcoffset() is None:
        raise ValueError("at must be a timezone-aware datetime")
    local = at.astimezone(MOSCOW)
    session_factory = session_factory or SessionLocal
    changed = _run_individual_offline(session_factory, local)
    if local.hour != 19 or local.minute != 1 or _last_processed_date == local.date():
        return changed
    # Status history elsewhere uses UTC timestamps, never Moscow wall time.
    now = local.astimezone(timezone.utc).replace(tzinfo=None)
    with session_factory() as db:
        _write_lock(db)
        has_bindings = inspect(db.connection()).has_table(Binding.__tablename__)
        users = db.scalars(select(User).where(
            User.is_active.is_(True), User.role == "agent", User.availability_status != "offline",
        ).order_by(User.id).with_for_update()).all()
        for user in users:
            binding = db.get(Binding, user.id) if has_bindings else None
            if binding is not None and binding.enabled:
                continue
            set_user_availability_status(
                db, user, "offline", now=now, changed_by_admin=False,
                change_source="workday_end", change_source_time="19:00",
            )
            changed += 1
        db.commit()
    _last_processed_date = local.date()
    return changed
