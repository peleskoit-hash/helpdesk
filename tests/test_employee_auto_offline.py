import asyncio
import calendar
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace
from threading import Barrier
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (Company, Client, User, Ticket, TicketMessage, StatsUserStatusInterval, StatsWorkInterval,
                            EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot)
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


@pytest.mark.parametrize("role", ["agent"])
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
    assert (intervals[1].change_source, intervals[1].change_source_time) == ("workday_end", "19:00")


def test_custom_workday_time_is_recorded_only_on_status_change(case):
    user = employee(case)
    now = datetime(2026, 11, 2, 14, 31)
    set_user_availability_status(case.db, user, "offline", now=now,
                                 change_source="workday_end", change_source_time="17:30")
    set_user_availability_status(case.db, user, "offline", now=now,
                                 change_source="workday_end", change_source_time="20:00")
    intervals = case.db.scalars(select(StatsUserStatusInterval).order_by(StatsUserStatusInterval.started_at)).all()
    assert len(intervals) == 2
    assert intervals[0].change_source is None and intervals[0].change_source_time is None
    assert intervals[1].started_at == now
    assert (intervals[1].change_source, intervals[1].change_source_time) == ("workday_end", "17:30")


@pytest.mark.parametrize("status,role,active", [
    ("offline", "agent", True),
    ("online", "agent", False), ("lunch", "agent", False),
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


def individual(case, user, *, shift="11:00-17:00", connected_on="2026-11-02", months=("2026-11",)):
    ident = int(uuid.uuid4().int)
    for month in months:
        year, number = map(int, month.split("-"))
        row = case.db.get(Snapshot, month)
        payload = copy.deepcopy(row.payload_json) if row else {
            "version": 2, "month": month, "today": "2026-11-02",
            "departments": [{"id": 1, "name": "Synthetic", "employees": []}],
        }
        payload["departments"][0]["employees"].append({
            "id": ident, "full_name": "Synthetic employee", "is_active": True,
            "default_shift": "08:00-19:00", "dismissed_from": None, "last_work_month": None,
            "days": [{"date": date(year, number, day).isoformat(), "shift": shift,
                      "work_day": True, "mark": "", "duty": False, "is_weekend": False}
                     for day in range(1, calendar.monthrange(year, number)[1] + 1)],
        })
        if row is None:
            row = Snapshot(month=month, fetched_at=datetime(2000, 1, 1), revision=1, payload_hash="synthetic")
            case.db.add(row)
        row.payload_json = payload
    binding = Binding(user_id=user.id, enabled=True, external_employee_id=str(ident),
                      external_full_name="Synthetic employee", external_department_id="1",
                      external_department_name="Synthetic", connected_at=datetime.fromisoformat(connected_on),
                      last_checked_at=datetime.fromisoformat(connected_on))
    case.db.add(binding)
    case.db.commit()
    return binding


def edit_day(case, binding, day, **changes):
    row = case.db.get(Snapshot, day[:7])
    value = copy.deepcopy(row.payload_json)
    worker = next(e for e in value["departments"][0]["employees"] if str(e["id"]) == binding.external_employee_id)
    next(d for d in worker["days"] if d["date"] == day).update(changes)
    row.payload_json = value
    case.db.commit()


@pytest.mark.parametrize("shift,before,end", [
    ("11:00-17:00", "2026-11-02T16:59:59", "2026-11-02T17:00:00"),
    ("13:00-22:30", "2026-11-02T22:29:59", "2026-11-02T22:30:00"),
])
def test_individual_exact_end_and_actual_utc_history(case, shift, before, end):
    user = employee(case, "waiting")
    binding = individual(case, user, shift=shift)
    assert case.run(moscow(before)) == 0
    assert case.run(moscow(end)) == 1
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "offline" and binding.last_offline_work_date == date(2026, 11, 2)
    rows = case.db.scalars(select(StatsUserStatusInterval).where(StatsUserStatusInterval.user_id == user.id)
                           .order_by(StatsUserStatusInterval.started_at)).all()
    actual = moscow(end).astimezone(timezone.utc).replace(tzinfo=None)
    assert len(rows) == 2 and rows[0].ended_at == rows[1].started_at == actual
    assert (rows[1].change_source, rows[1].change_source_time, rows[1].changed_by_admin) == ("workday_end", shift[-5:], False)


def test_individual_late_shift_is_excluded_from_1901(case):
    user = employee(case)
    binding = individual(case, user, shift="13:00-22:30")
    assert case.run() == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None
    assert case.run(moscow("2026-11-02T22:30:00")) == 1


@pytest.mark.parametrize("connected_utc,expected", [
    ("2026-11-02T15:00:00", 0),  # 18:00 Moscow, after the 17:00 ending.
    ("2026-11-02T13:00:00", 1),  # 16:00 Moscow, before the ending.
    ("2026-11-02T14:00:00", 0),  # Exactly at the ending is excluded.
    ("2026-11-02T13:59:59.999999", 1),
    ("2026-11-02T14:00:00.000001", 0),
])
def test_individual_shift_must_end_strictly_after_connection(case, connected_utc, expected):
    user = employee(case)
    binding = individual(case, user, connected_on=connected_utc)
    assert case.run(moscow("2026-11-02T18:30:00")) == expected
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == ("offline" if expected else "online")
    assert binding.last_offline_work_date == (date(2026, 11, 2) if expected else None)
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 1 + expected


def test_individual_repeat_restart_and_manual_online_are_not_overridden(case, monkeypatch):
    user = employee(case)
    binding = individual(case, user)
    assert case.run(moscow("2026-11-02T17:00:00")) == 1
    case.db.refresh(user)
    set_user_availability_status(case.db, user, "online", now=datetime(2026, 11, 2, 14, 1))
    case.db.commit()
    # Clearing the old in-memory guard simulates a process restart; the binding is persistent.
    monkeypatch.setattr(offline, "_last_processed_date", None)
    assert case.run(moscow("2026-11-02T17:02:00")) == 0
    assert case.run() == 0
    assert case.run(moscow("2026-11-02T23:59:00")) == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date == date(2026, 11, 2)
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 3


def test_individual_already_offline_only_records_date_and_counts_transitions(case):
    already = employee(case, "offline")
    other = employee(case, "lunch")
    a = individual(case, already)
    b = individual(case, other)
    assert case.run(moscow("2026-11-02T17:00:00")) == 1
    case.db.refresh(a); case.db.refresh(b)
    assert a.last_offline_work_date == b.last_offline_work_date == date(2026, 11, 2)
    assert len(case.db.scalars(select(StatsUserStatusInterval).where(StatsUserStatusInterval.user_id == already.id)).all()) == 1


def test_individual_working_sunday(case):
    user = employee(case)
    binding = individual(case, user, connected_on="2026-11-08")
    edit_day(case, binding, "2026-11-08", is_weekend=True)
    assert case.run(moscow("2026-11-08T17:00:00")) == 1
    case.db.refresh(binding)
    assert binding.last_offline_work_date == date(2026, 11, 8)


@pytest.mark.parametrize("kind", ["missing_month", "missing_employee", "missing_date", "non_working", "bad_shift"])
def test_individual_unknown_or_nonworking_never_falls_back_to_1901(case, kind):
    user = employee(case)
    binding = individual(case, user)
    row = case.db.get(Snapshot, "2026-11")
    value = copy.deepcopy(row.payload_json)
    worker = value["departments"][0]["employees"][0]
    if kind == "missing_month":
        case.db.delete(row)
    else:
        if kind == "missing_employee": worker["id"] += 1
        elif kind == "missing_date": worker["days"].pop(1)
        elif kind == "non_working": worker["days"][1].update(work_day=False, shift="", mark="on_call")
        else: worker["days"][1]["shift"] = "22:00-06:00"
        row.payload_json = value
    case.db.commit()
    assert case.run() == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 1


def test_individual_schedule_edits_later_earlier_and_cancellation(case):
    user = employee(case)
    binding = individual(case, user)
    assert case.run(moscow("2026-11-02T16:00:00")) == 0
    edit_day(case, binding, "2026-11-02", shift="11:00-21:00")
    assert case.run(moscow("2026-11-02T17:00:00")) == 0
    edit_day(case, binding, "2026-11-02", work_day=False, shift="", mark="vacation")
    assert case.run() == 0
    edit_day(case, binding, "2026-11-02", work_day=True, shift="11:00-16:00", mark="")
    assert case.run(moscow("2026-11-02T19:02:00")) == 1
    # Extending a processed shift cannot automatically bring the employee Online.
    edit_day(case, binding, "2026-11-02", shift="11:00-23:00")
    assert case.run(moscow("2026-11-02T20:00:00")) == 0
    case.db.refresh(user)
    assert user.availability_status == "offline"


@pytest.mark.parametrize("at,processed", [("2026-11-02T18:15:00", date(2026, 11, 2)),
                                         ("2026-11-03T09:00:00", date(2026, 11, 2))])
def test_individual_catchup_after_missed_pass_uses_actual_time(case, at, processed):
    user = employee(case)
    binding = individual(case, user)
    assert case.run(moscow(at)) == 1
    case.db.refresh(binding)
    assert binding.last_offline_work_date == processed
    last = case.db.scalars(select(StatsUserStatusInterval).where(StatsUserStatusInterval.user_id == user.id)
                           .order_by(StatsUserStatusInterval.started_at.desc())).first()
    assert last.started_at == moscow(at).astimezone(timezone.utc).replace(tzinfo=None)
    assert last.change_source_time == "17:00"


def test_individual_catchup_stops_at_started_next_shift_and_unknown_gap(case):
    user = employee(case)
    binding = individual(case, user)
    assert case.run(moscow("2026-11-03T11:00:00")) == 0
    # Before the next shift is not sufficient if an intermediate date is unknown.
    row = case.db.get(Snapshot, "2026-11")
    value = copy.deepcopy(row.payload_json)
    value["departments"][0]["employees"][0]["days"].pop(1)
    row.payload_json = value; case.db.commit()
    assert case.run(moscow("2026-11-03T09:00:00")) == 0
    case.db.refresh(binding)
    assert binding.last_offline_work_date is None


def test_individual_catchup_known_nonwork_gap_only_before_next_working_shift(case):
    user = employee(case)
    binding = individual(case, user, connected_on="2026-11-06")
    for day in ("2026-11-07", "2026-11-08"):
        edit_day(case, binding, day, work_day=False, shift="")
    assert case.run(moscow("2026-11-07T12:00:00")) == 1
    case.db.refresh(binding)
    assert binding.last_offline_work_date == date(2026, 11, 6)
    rows = case.db.scalars(select(StatsUserStatusInterval).order_by(StatsUserStatusInterval.started_at)).all()
    assert len(rows) == 2 and rows[-1].started_at == datetime(2026, 11, 7, 9)
    assert rows[-1].change_source_time == "17:00"
    case.db.refresh(user)
    set_user_availability_status(case.db, user, "online", now=datetime(2026, 11, 7, 9, 1))
    case.db.commit()
    assert case.run(moscow("2026-11-08T12:00:00")) == 0
    assert case.run(moscow("2026-11-09T09:00:00")) == 0
    case.db.refresh(user)
    assert user.availability_status == "online"
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 3


@pytest.mark.parametrize("connected_utc,expected", [("2026-11-06T13:00:00", 1),
                                                  ("2026-11-06T14:00:00", 0),
                                                  ("2026-11-06T15:00:00", 0)])
def test_individual_weekend_catchup_respects_connection_instant(case, connected_utc, expected):
    user = employee(case)
    binding = individual(case, user, connected_on=connected_utc)
    edit_day(case, binding, "2026-11-07", work_day=False, shift="")
    assert case.run(moscow("2026-11-07T12:00:00")) == expected
    case.db.refresh(binding)
    assert binding.last_offline_work_date == (date(2026, 11, 6) if expected else None)


@pytest.mark.parametrize("kind", ["missing_date", "bad_shift", "next_shift_started"])
def test_individual_weekend_catchup_requires_unbroken_known_gap(case, kind):
    user = employee(case)
    binding = individual(case, user, connected_on="2026-11-06")
    for day in ("2026-11-07", "2026-11-08"):
        edit_day(case, binding, day, work_day=False, shift="")
    if kind == "next_shift_started":
        edit_day(case, binding, "2026-11-07", work_day=True, shift="11:00-17:00")
        at = moscow("2026-11-07T12:00:00")
    else:
        row = case.db.get(Snapshot, "2026-11")
        value = copy.deepcopy(row.payload_json)
        days = value["departments"][0]["employees"][0]["days"]
        if kind == "missing_date": days.pop(6)
        else: days[6]["shift"] = "broken"
        row.payload_json = value; case.db.commit()
        at = moscow("2026-11-08T12:00:00")
    assert case.run(at) == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 1


@pytest.mark.parametrize("with_previous_month", [False, True])
def test_individual_catchup_month_boundary_requires_published_previous_month(case, with_previous_month):
    user = employee(case)
    case.db.scalars(select(StatsUserStatusInterval)).one().started_at = datetime(2026, 10, 31, 8)
    case.db.commit()
    binding = individual(case, user, connected_on="2026-10-31",
                         months=("2026-10", "2026-11") if with_previous_month else ("2026-11",))
    assert case.run(moscow("2026-11-01T09:00:00")) == int(with_previous_month)
    case.db.refresh(binding)
    assert binding.last_offline_work_date == (date(2026, 10, 31) if with_previous_month else None)


@pytest.mark.parametrize("role,active", [("admin", True), ("chiki_piki", True), ("agent", False)])
def test_individual_only_active_agents(case, role, active):
    user = employee(case, role=role, active=active)
    binding = individual(case, user)
    assert case.run(moscow("2026-11-02T17:00:00")) == 0
    assert case.run() == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None


def test_individual_resolver_error_does_not_block_legacy_or_fall_back(case, monkeypatch):
    managed = employee(case)
    binding = individual(case, managed)
    legacy = employee(case)
    def fail(*args): raise RuntimeError("Synthetic failure")
    monkeypatch.setattr(offline, "resolve_employee_schedule", fail)
    assert case.run() == 1
    case.db.refresh(managed); case.db.refresh(legacy); case.db.refresh(binding)
    assert managed.availability_status == "online" and legacy.availability_status == "offline"
    assert binding.last_offline_work_date is None


def test_individual_status_and_marker_rollback_together(case, monkeypatch):
    user = employee(case)
    binding = individual(case, user)
    original = offline.set_user_availability_status
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic failure after history update")
    monkeypatch.setattr(offline, "set_user_availability_status", fail)
    assert case.run(moscow("2026-11-02T17:00:00")) == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None
    assert len(case.db.scalars(select(StatsUserStatusInterval)).all()) == 1


def test_disabled_binding_keeps_legacy_window(case):
    user = employee(case)
    binding = individual(case, user)
    binding.enabled = False; case.db.commit()
    assert case.run(moscow("2026-11-02T17:00:00")) == 0
    assert case.run() == 1
    case.db.refresh(binding)
    assert binding.last_offline_work_date is None


@pytest.mark.parametrize("change", ["inactive", "other_role", "unbound"])
def test_individual_rechecks_user_and_binding_after_candidate_scan(case, change):
    user = employee(case)
    binding = individual(case, user)
    calls = []
    def factory():
        calls.append(True)
        if len(calls) == 2:
            with case.factory.begin() as writer:
                if change == "inactive": writer.get(User, user.id).is_active = False
                elif change == "other_role": writer.get(User, user.id).role = "admin"
                else: writer.get(Binding, user.id).enabled = False
        return case.factory()
    assert offline.run_employee_auto_offline_once(at=moscow("2026-11-02T17:00:00"), session_factory=factory) == 0
    case.db.refresh(user); case.db.refresh(binding)
    assert user.availability_status == "online" and binding.last_offline_work_date is None


def test_legacy_window_without_binding_migration(case):
    user = employee(case)
    Binding.__table__.drop(case.db.get_bind())
    assert case.run(moscow("2026-11-02T17:00:00")) == 0
    assert case.run() == 1
    case.db.refresh(user)
    assert user.availability_status == "offline"


def test_individual_concurrent_passes_make_one_transition(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'offline.sqlite'}", connect_args={"timeout": 5})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory() as db:
        company = Company(id=str(uuid.uuid4()), name="Synthetic")
        db.add(company); db.commit()
        isolated = SimpleNamespace(db=db, company=company, factory=factory)
        user = employee(isolated)
        binding = individual(isolated, user)
        barrier = Barrier(2)
        def run():
            barrier.wait(timeout=5)
            return offline.run_employee_auto_offline_once(at=moscow("2026-11-02T17:00:00"), session_factory=factory)
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(run), pool.submit(run)
            assert sorted((a.result(timeout=10), b.result(timeout=10))) == [0, 1]
        db.refresh(binding)
        assert binding.last_offline_work_date == date(2026, 11, 2)
        assert len(db.scalars(select(StatsUserStatusInterval)).all()) == 2
    engine.dispose()


def test_scheduler_runs_independent_job_and_survives_failure(monkeypatch):
    calls = []
    names = ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once",
        "run_telegram_after_hours_cleanup_once", "run_employee_auto_offline_once",
        "run_employee_notification_backlog_once", "run_ticket_auto_unfreeze_once"]
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


def test_status_table_displays_explicit_and_legacy_change_sources():
    path = Path(__file__).resolve().parents[1] / "api/app/static/status_transitions.js"
    script = r'''
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const row = source.match(/function rowHtml\(item\) \{[\s\S]*?\n    \}/)[0];
const render = new Function('item', 'timezone', 'LABELS', 'escapeHtml', row + '; return rowHtml(item);');
for (const [item, label] of [
  [{changed_by_admin: true}, 'Да'],
  [{changed_by_admin: false}, '19:00'],
  [{changed_by_admin: null}, ''],
  [{change_source: 'onsite_start', changed_by_admin: true}, 'Иду к Вам'],
  [{change_source: 'onsite_resolved', changed_by_admin: false}, 'Решил на месте'],
  [{change_source: 'workday_end', change_source_time: '17:30', changed_by_admin: true}, '17:30'],
  [{change_source: 'workday_end', change_source_time: '24:00', changed_by_admin: false}, ''],
  [{change_source: 'workday_end', change_source_time: '19:60', changed_by_admin: false}, ''],
  [{change_source: 'workday_end', changed_by_admin: false}, ''],
]) {
  const html = render(item, 'Europe/Moscow', {}, String);
  assert.ok(html.includes('<td data-label="Источник изменения">' + label + '</td>'));
}
'''
    subprocess.run(["node", "-e", script, str(path)], check=True, capture_output=True, text=True)
