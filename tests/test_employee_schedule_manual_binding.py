"""Manual binding uses published synthetic snapshots and isolated test databases."""
import copy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from test_employee_schedule_binding import store, publish, connect, AT, NAME, Binding, User, Snapshot
from api.app.routers import employee_schedule as router
from api.app.services import employee_schedule as schedule


def two_employees(value):
    employee = copy.deepcopy(value["departments"][0]["employees"][0])
    employee.update(id=2, full_name="Selected Synthetic Employee")
    value["departments"].append({"id": 2, "name": "Selected department", "employees": [employee]})


def manual(store, ident="2", user="a", enabled=True, at=AT):
    with store.factory() as db:
        schedule.set_binding(db, user, enabled, external_employee_id=ident, at=at)


def saved(store, user="a"):
    with store.factory() as db:
        return db.execute(select(Binding.__table__).where(Binding.user_id == user)).mappings().one_or_none()


@pytest.mark.parametrize("enabled", [False, True])
def test_list_excludes_reserved_ids_including_disabled_and_current_user(store, enabled):
    publish(store, edit=two_employees)
    connect(store)
    if not enabled:
        connect(store, enabled=False)
    with store.factory() as db:
        result = schedule.list_employees(db, at=AT)
        assert len(result) == 1
        assert {row["external_employee_id"]: row["available"] for row in result} == {"2": True}
        own = schedule.list_employees(db, user_id="a", at=AT)
        assert own == result
        other = schedule.list_employees(db, user_id="b", at=AT)
        assert other == result
        selected = next(row for row in result if row["external_employee_id"] == "2")
        assert selected == {"external_employee_id": "2", "full_name": "Selected Synthetic Employee",
                            "department_id": "2", "department_name": "Selected department", "available": True}


@pytest.mark.parametrize("user", ["a", "admin"])
def test_manual_selection_ignores_local_name_for_agents_and_admins(store, user):
    publish(store, edit=two_employees)
    manual(store, user=user)
    row = saved(store, user)
    assert row["enabled"] and row["external_employee_id"] == "2"
    assert row["external_full_name"] == "Selected Synthetic Employee"
    assert row["external_department_id"] == "2" and row["external_department_name"] == "Selected department"


def test_manual_selection_handles_homonyms_and_initial_id_is_required(store):
    def edit(value):
        two_employees(value)
        value["departments"][1]["employees"][0]["full_name"] = NAME
    publish(store, edit=edit)
    with store.factory() as db:
        with pytest.raises(schedule.BindingError, match="external_id_required"):
            schedule.set_binding(db, "a", True, at=AT)
    manual(store)
    assert saved(store)["external_employee_id"] == "2"


def test_rebinding_resets_previous_identity_marks_and_same_id_preserves_them(store):
    publish(store, edit=two_employees); connect(store)
    with store.factory.begin() as db:
        db.get(Binding, "a").last_offline_work_date = date(2026, 11, 18)
    later = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    manual(store, at=later)
    row = saved(store)
    assert row["external_employee_id"] == "2" and row["last_offline_work_date"] is None
    assert row["connected_at"] == row["last_checked_at"] == later.replace(tzinfo=None)
    with store.factory.begin() as db:
        db.get(Binding, "a").last_offline_work_date = date(2026, 11, 20)
    manual(store, at=later)
    assert saved(store)["last_offline_work_date"] == date(2026, 11, 20)
    assert saved(store)["connected_at"] == row["connected_at"]


def test_disable_keeps_id_and_reenable_does_not_rematch_name(store):
    publish(store, edit=two_employees); manual(store)
    before = saved(store)
    connect(store, enabled=False)
    assert saved(store)["external_employee_id"] == "2" and not saved(store)["enabled"]
    with store.factory.begin() as db: db.get(User, "a").full_name = "Changed Local Name"
    connect(store)
    assert saved(store)["external_employee_id"] == "2" and saved(store)["connected_at"] == before["connected_at"]
    before = saved(store)
    with pytest.raises(schedule.BindingError, match="external_id_on_disable"):
        manual(store, ident="1", enabled=False)
    assert saved(store) == before


@pytest.mark.parametrize("reason,code", [("occupied", "external_id_in_use"), ("inactive", "user_inactive"),
    ("missing", "employee_missing"), ("conflicting", "inconsistent_employee"), ("invalid", "spark_invalid_payload"),
    ("no_data", "spark_network_error"), ("missing_user", "user_not_found")])
def test_selection_errors_do_not_damage_existing_binding(store, reason, code):
    publish(store, edit=two_employees); connect(store)
    if reason == "occupied":
        manual(store, user="b")
        connect(store, user="b", enabled=False)
    with store.factory.begin() as db:
        if reason == "inactive": db.get(User, "a").is_active = False
        elif reason in {"conflicting", "invalid"}:
            row = db.get(Snapshot, "2026-12")
            value = copy.deepcopy(row.payload_json)
            employee = value["departments"][1]["employees"][0]
            if reason == "conflicting": employee["full_name"] = "Conflicting identity"
            else: employee["days"].pop()
            row.payload_json = value
            store.live["2026-12"] = copy.deepcopy(value)
        elif reason == "no_data":
            for row in db.scalars(select(Snapshot)): db.delete(row)
            store.live.clear()
    before = saved(store)
    with pytest.raises(schedule.BindingError, match=code):
        manual(store, ident="999" if reason == "missing" else "2", user="missing" if reason == "missing_user" else "a")
    assert saved(store) == before


def test_integrity_failure_rolls_back_rebinding(store):
    publish(store, edit=two_employees); connect(store)
    before = saved(store)
    def fail(conn, cursor, statement, params, context, many):
        if statement.startswith("UPDATE employee_schedule_bindings"):
            raise IntegrityError("Synthetic conflict", {}, Exception())
    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(schedule.BindingError, match="binding_conflict"):
            manual(store)
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert saved(store) == before


def test_list_and_manual_selection_ignore_outdated_months_and_handle_year_rollover(store):
    publish(store, months=("2026-10",))
    with store.factory() as db:
        with pytest.raises(schedule.BindingError, match="spark_network_error"):
            schedule.list_employees(db, at=AT)
    with pytest.raises(schedule.BindingError, match="spark_network_error"):
        manual(store, ident="1")
    publish(store, months=("2026-12", "2027-01"), edit=two_employees)
    with store.factory() as db:
        assert len(schedule.list_employees(db, at=datetime(2026, 12, 31, tzinfo=timezone.utc))) == 2


def test_list_rejects_inconsistent_id_and_missing_target_user(store):
    def edit(value):
        if value["month"] == "2026-12": value["departments"][0]["employees"][0]["full_name"] = "Different"
    publish(store, edit=edit)
    with store.factory() as db:
        with pytest.raises(schedule.BindingError, match="inconsistent_employee"):
            schedule.list_employees(db, at=AT)
        with pytest.raises(schedule.BindingError, match="user_not_found"):
            schedule.list_employees(db, user_id="missing", at=AT)


def test_admin_api_manual_selection_validation_and_protection(store, monkeypatch):
    publish(store, edit=two_employees)
    clock = schedule._now
    monkeypatch.setattr(schedule, "_now", lambda at: clock(at or AT))
    app = FastAPI(); app.include_router(router.router)
    actor = SimpleNamespace(role="agent")
    app.dependency_overrides[router.get_current_user] = lambda: actor
    def db_dep():
        with store.factory() as db: yield db
    app.dependency_overrides[router.get_db] = db_dep
    with TestClient(app) as http:
        listing = "/api/v1/settings/employee-schedule/employees"
        url = "/api/v1/settings/users/a/schedule"
        assert http.get(listing).status_code == 403
        assert http.patch(url, json={"enabled": True, "external_employee_id": "2"}).status_code == 403
        actor.role = "admin"
        result = http.get(listing)
        assert result.status_code == 200 and len(result.json()) == 2
        assert all(set(row) == {"external_employee_id", "full_name", "department_id", "department_name", "available"}
                   for row in result.json())
        for ident in (2, "", "x" * 101):
            assert http.patch(url, json={"enabled": True, "external_employee_id": ident}).status_code == 422
        response = http.patch(url, json={"enabled": True, "external_employee_id": "2"})
        assert response.status_code == 200 and response.json()["external_employee_id"] == "2"
        assert http.patch(url, json={"enabled": False, "external_employee_id": "1"}).status_code == 400
        assert http.get(url).json()["enabled"]
        assert http.patch(url, json={"enabled": False}).json()["external_employee_id"] == "2"
        assert http.patch(url, json={"enabled": True}).json()["external_employee_id"] == "2"
        assert all(row["external_employee_id"] != "2" for row in http.get(listing).json())
        assert all(row["external_employee_id"] != "2" for row in http.get(listing + "?user_id=a").json())
        with store.factory.begin() as db:
            for row in db.scalars(select(Snapshot)): db.delete(row)
        # No published snapshots are needed by the live list.
        assert http.get(listing).status_code == 200
        store.live.clear()
        response = http.get(listing)
        assert response.status_code == 503 and response.json()["detail"]["error"]["code"] == "spark_network_error"


@pytest.mark.parametrize("explicit", [False, True])
def test_live_binding_and_search_without_snapshots_or_network_transaction(store, monkeypatch, explicit):
    from test_employee_schedule_binding import payload
    calls = []
    with store.factory() as db:
        def fetch(month):
            assert not db.in_transaction()
            assert store.engine.pool.checkedout() == 0
            calls.append(month)
            value = payload(month)
            two_employees(value)
            return value
        monkeypatch.setattr(schedule, "fetch_month", fetch)
        db.execute(select(User))  # Simulate the administrative authorization read.
        result = schedule.list_employees(db, user_id="a", at=AT)
        assert len(result) == 2
        if not explicit:
            with pytest.raises(schedule.BindingError, match="external_id_required"):
                schedule.set_binding(db, "a", True, at=AT)
        schedule.set_binding(db, "a", True, external_employee_id="2" if explicit else "1", at=AT)
        assert db.get(Binding, "a").external_employee_id == ("2" if explicit else "1")
        assert not list(db.scalars(select(Snapshot)))
    assert calls == ["2026-11", "2026-12"] * 2


@pytest.mark.parametrize("failed_month", ["2026-11", "2026-12"])
def test_live_failure_never_uses_old_snapshot_and_disable_stays_local(store, monkeypatch, failed_month):
    publish(store, edit=two_employees); connect(store)
    before = saved(store)
    calls = []
    def fail(month):
        calls.append(month)
        if month == failed_month:
            raise schedule.ScheduleError("network_error")
        return copy.deepcopy(store.live[month])
    monkeypatch.setattr(schedule, "fetch_month", fail)
    with pytest.raises(schedule.BindingError, match="spark_network_error"):
        manual(store)
    with store.factory() as db:
        with pytest.raises(schedule.BindingError, match="spark_network_error"):
            schedule.list_employees(db, at=AT)
    assert saved(store) == before
    count = len(calls)
    connect(store, enabled=False)
    assert len(calls) == count
    assert not saved(store)["enabled"] and saved(store)["external_employee_id"] == "1"


@pytest.mark.parametrize("change,code", [("inactive", "user_inactive"), ("occupied", "external_id_in_use")])
def test_local_state_is_rechecked_after_live_fetch(store, monkeypatch, change, code):
    publish(store, edit=two_employees); connect(store)
    before = saved(store)
    def fetch(month):
        if month == "2026-12":
            with store.factory.begin() as other:
                if change == "inactive":
                    other.get(User, "a").is_active = False
                else:
                    other.add(Binding(user_id="b", external_employee_id="2", enabled=False,
                        external_full_name="Other", external_department_id="2", external_department_name="Other",
                        connected_at=AT.replace(tzinfo=None), last_checked_at=AT.replace(tzinfo=None)))
        return copy.deepcopy(store.live[month])
    monkeypatch.setattr(schedule, "fetch_month", fetch)
    with pytest.raises(schedule.BindingError, match=code):
        manual(store)
    assert saved(store) == before


@pytest.mark.parametrize("enabled", [False, True])
def test_remove_binding_is_local_idempotent_and_releases_id(store, monkeypatch, enabled):
    publish(store, edit=two_employees); manual(store)
    if not enabled:
        connect(store, enabled=False)
    with store.factory() as db:
        user_before = db.execute(select(User.__table__).where(User.id == "a")).mappings().one()
        snapshots_before = db.execute(select(Snapshot.__table__).order_by(Snapshot.month)).mappings().all()
    def forbidden(month):
        raise AssertionError("Unlink must never contact Spark")
    monkeypatch.setattr(schedule, "fetch_month", forbidden)
    for _ in range(2):
        with store.factory() as db:
            schedule.remove_binding(db, "a")
            result = schedule.binding_status(db, "a", at=AT)
            assert not result["enabled"] and result["external_employee_id"] is None
            assert result["schedule"]["reason"] == "not_connected"
    with store.factory() as db:
        assert db.execute(select(User.__table__).where(User.id == "a")).mappings().one() == user_before
        assert db.execute(select(Snapshot.__table__).order_by(Snapshot.month)).mappings().all() == snapshots_before
    monkeypatch.setattr(schedule, "fetch_month", lambda month: copy.deepcopy(store.live[month]))
    with store.factory() as db:
        assert "2" in {employee["external_employee_id"] for employee in schedule.list_employees(db, at=AT)}
    manual(store, user="b")
    manual(store, ident="1")
    assert saved(store, "b")["external_employee_id"] == "2" and saved(store)["external_employee_id"] == "1"


def test_remove_binding_rolls_back_on_delete_failure(store):
    publish(store, edit=two_employees); manual(store)
    before = saved(store)
    def fail(conn, cursor, statement, params, context, many):
        if statement.startswith("DELETE FROM employee_schedule_bindings"):
            raise RuntimeError("Synthetic delete failure")
    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        with store.factory() as db:
            with pytest.raises(RuntimeError, match="Synthetic"):
                schedule.remove_binding(db, "a")
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert saved(store) == before


def test_delete_api_requires_admin_handles_missing_user_and_inactive_binding(store, monkeypatch):
    publish(store, edit=two_employees); manual(store)
    with store.factory.begin() as db:
        db.get(User, "a").is_active = False
    app = FastAPI(); app.include_router(router.router)
    actor = SimpleNamespace(role="agent")
    app.dependency_overrides[router.get_current_user] = lambda: actor
    def db_dep():
        with store.factory() as db: yield db
    app.dependency_overrides[router.get_db] = db_dep
    def forbidden(month): raise AssertionError("No HTTP on DELETE")
    monkeypatch.setattr(schedule, "fetch_month", forbidden)
    with TestClient(app) as http:
        url = "/api/v1/settings/users/a/schedule"
        for role in ("agent", "chiki_piki"):
            actor.role = role
            assert http.delete(url).status_code == 403
            assert saved(store) is not None
        actor.role = "admin"
        assert http.delete("/api/v1/settings/users/missing/schedule").status_code == 404
        for _ in range(2):
            response = http.delete(url)
            assert response.status_code == 200
            assert response.json()["external_employee_id"] is None and not response.json()["enabled"]
            assert response.json()["local_active"] is False
