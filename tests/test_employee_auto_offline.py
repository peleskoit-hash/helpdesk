import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import Company, Client, User, Ticket, TicketMessage, StatsUserStatusInterval, StatsWorkInterval
from api.app.services import employee_auto_offline as offline, internal_task_scheduler as scheduler
from api.app.services.statistics_lifecycle import set_user_availability_status


def moscow(value="2026-11-02T19:01:00"):
    return datetime.fromisoformat(value).replace(tzinfo=offline.MOSCOW)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(offline, "_last_processed_date", None)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        company = Company(id=str(uuid.uuid4()), name="Support")
        db.add(company)
        db.commit()
        yield SimpleNamespace(db=db, company=company, factory=factory,
            run=lambda at=moscow(): offline.run_employee_auto_offline_once(at=at, session_factory=factory))
    engine.dispose()


def employee(case, status="online", role="agent", active=True):
    user = User(id=str(uuid.uuid4()), company_id=case.company.id, full_name="Employee", role=role,
        is_active=active, availability_status=status, updated_at=datetime(2026, 11, 2, 8))
    case.db.add(user)
    case.db.flush()
    case.db.add(StatsUserStatusInterval(id=str(uuid.uuid4()), company_id=case.company.id,
        user_id=user.id, status=status, started_at=datetime(2026, 11, 2, 8)))
    case.db.commit()
    return user


@pytest.mark.parametrize("value,count", [
    ("2026-11-02T19:00:59", 0), ("2026-11-02T19:01:00", 1),
    ("2026-11-02T19:01:59", 1), ("2026-11-02T19:02:00", 0),
    ("2026-11-08T19:01:00", 1),
])
def test_exact_moscow_window_including_sunday(case, value, count):
    user = employee(case)
    assert case.run(moscow(value)) == count
    case.db.refresh(user)
    assert user.availability_status == ("offline" if count else "online")


@pytest.mark.parametrize("role", ["agent", "trainee"])
@pytest.mark.parametrize("status", ["online", "on_ticket", "waiting", "lunch"])
def test_all_active_support_statuses_are_reset_with_history(case, role, status):
    user = employee(case, status, role)
    assert case.run() == 1
    case.db.refresh(user)
    assert user.availability_status == "offline"
    intervals = case.db.scalars(select(StatsUserStatusInterval).where(
        StatsUserStatusInterval.user_id == user.id).order_by(StatsUserStatusInterval.started_at)).all()
    assert len(intervals) == 2
    assert intervals[0].ended_at == datetime(2026, 11, 2, 16, 1)
    assert intervals[1].started_at == intervals[0].ended_at
    assert intervals[1].ended_at is None
    assert intervals[1].status == "offline" and intervals[1].changed_by_admin is False


@pytest.mark.parametrize("status,role,active", [
    ("offline", "agent", True), ("offline", "trainee", True),
    ("online", "agent", False), ("lunch", "trainee", False),
    ("online", "admin", True), ("online", "chiki_piki", True),
])
def test_offline_inactive_and_other_roles_are_untouched(case, status, role, active):
    user = employee(case, status, role, active)
    old_updated = user.updated_at
    assert case.run() == 0
    case.db.refresh(user)
    assert user.availability_status == status and user.updated_at == old_updated
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 1


def test_repeat_does_not_undo_manual_change_and_next_day_runs(case):
    user = employee(case)
    assert case.run() == 1
    case.db.refresh(user)
    set_user_availability_status(case.db, user, "online", now=datetime(2026, 11, 2, 16, 1, 10))
    case.db.commit()
    assert case.run(moscow("2026-11-02T19:01:30")) == 0
    assert case.run(moscow("2026-11-02T19:02:00")) == 0
    case.db.refresh(user)
    assert user.availability_status == "online"
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 3
    assert case.run(moscow("2026-11-03T19:01:00")) == 1
    case.db.refresh(user)
    assert user.availability_status == "offline"


def test_assignments_messages_and_work_intervals_are_not_changed(case):
    user = employee(case, "on_ticket")
    client = Client(id=str(uuid.uuid4()), company_id=case.company.id, display_name="Client")
    case.db.add(client)
    case.db.flush()
    ids = []
    for workflow in ("ticket", "task"):
        ticket = Ticket(id=str(uuid.uuid4()), company_id=case.company.id, client_id=client.id,
            assignee_id=user.id, workflow_type=workflow, intake_state="ready", status="pending", subject="Active")
        case.db.add(ticket)
        case.db.flush()
        case.db.add_all([
            TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type="client", body="History"),
            StatsWorkInterval(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket.id,
                object_type=workflow, user_id=user.id, started_at=datetime(2026, 11, 2, 8)),
        ])
        ids.append(ticket.id)
    case.db.commit()
    def snapshot():
        return {model.__tablename__: [tuple(row) for row in case.db.execute(select(model.__table__))]
            for model in (Ticket, TicketMessage, StatsWorkInterval)}
    before = snapshot()
    assert case.run() == 1
    case.db.expire_all()
    assert snapshot() == before


def test_admin_system_and_self_changes_remain_distinct(case):
    user = employee(case)
    for status, flag, now in [("waiting", None, datetime(2026, 11, 2, 12)),
                               ("lunch", True, datetime(2026, 11, 2, 13))]:
        set_user_availability_status(case.db, user, status, now=now, changed_by_admin=flag)
        case.db.commit()
    assert case.run() == 1
    flags = case.db.scalars(select(StatsUserStatusInterval.changed_by_admin).order_by(
        StatsUserStatusInterval.started_at)).all()
    assert flags == [None, None, True, False]


def test_utc_input_and_default_clock_use_moscow(case, monkeypatch):
    user = employee(case)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == offline.MOSCOW
            return moscow()
    monkeypatch.setattr(offline, "datetime", FixedDatetime)
    assert offline.run_employee_auto_offline_once(session_factory=case.factory) == 1
    case.db.refresh(user)
    assert user.availability_status == "offline"
    monkeypatch.setattr(offline, "_last_processed_date", None)
    user.availability_status = "online"
    case.db.commit()
    assert case.run(datetime(2026, 11, 2, 16, 1, tzinfo=timezone.utc)) == 1


def test_naive_datetime_is_rejected(case):
    with pytest.raises(ValueError, match="timezone-aware"):
        case.run(datetime(2026, 11, 2, 19, 1))


def test_scheduler_runs_independent_job_and_survives_failure(monkeypatch):
    calls = []
    names = ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once",
        "run_telegram_after_hours_cleanup_once", "run_employee_auto_offline_once"]
    async def to_thread(fn):
        calls.append(fn.__name__)
        if fn is scheduler.run_employee_auto_offline_once:
            raise RuntimeError("failure")
    async def sleep(seconds):
        assert seconds == 30
        if len(calls) == 2 * len(names):
            raise asyncio.CancelledError
    monkeypatch.setattr(scheduler.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.internal_task_scheduler_loop())
    assert calls == names * 2


def test_status_table_displays_all_three_change_origins():
    path = Path(__file__).resolve().parents[1] / "api/app/static/status_transitions.js"
    script = r'''
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const row = source.match(/function rowHtml\(item\) \{[\s\S]*?\n    \}/)[0];
const render = new Function('item', 'timezone', 'LABELS', 'escapeHtml', row + '; return rowHtml(item);');
for (const [value, label] of [[true, 'Да'], [false, 'Автом.'], [null, '']]) {
  const html = render({changed_by_admin:value}, 'Europe/Moscow', {}, String);
  assert.ok(html.includes('<td data-label="Изменено Админом">' + label + '</td>'));
}
'''
    subprocess.run(["node", "-e", script, str(path)], check=True, capture_output=True, text=True)
