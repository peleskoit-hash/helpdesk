"""Synthetic calendars on isolated SQLite or explicitly configured local test PG."""
import ast
import calendar
import copy
import os
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from api.app.config import settings
from api.app.models import Company, EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot, User
from api.app.routers import employee_schedule as router
from api.app.services import employee_schedule as schedule
from api.app.services.schema_evolution import ensure_employee_schedule_binding_table


AT = datetime(2026, 11, 19, 13, tzinfo=timezone.utc)
NAME = "Федоров Иван Петрович"


def payload(month="2026-11", ident=1, name=NAME):
    year, number = map(int, month.split("-"))
    return {"version": 2, "month": month, "today": "2026-11-19", "departments": [{
        "id": 1, "name": "Synthetic department", "employees": [{
            "id": ident, "full_name": name, "is_active": True, "default_shift": "08:00-19:00",
            "dismissed_from": None, "last_work_month": None,
            "days": [{"date": date(year, number, day).isoformat(), "work_day": True,
                      "shift": "11:00-17:00", "mark": "", "duty": False, "is_weekend": False}
                     for day in range(1, calendar.monthrange(year, number)[1] + 1)]
        }]}]}


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path, monkeypatch):
    if request.param == "postgresql":
        raw_url = os.environ.get("EMPLOYEE_SCHEDULE_TEST_DATABASE_URL")
        if not raw_url:
            pytest.skip("Disposable PostgreSQL test URL not supplied")
        try:
            url = make_url(raw_url)
            safe = (url.get_backend_name() == "postgresql" and url.database == "employee_schedule_test"
                    and url.host in {"localhost", "127.0.0.1"}
                    and not {"host", "hostaddr", "dbname", "service"}.intersection(url.query))
        except (ArgumentError, ValueError, TypeError):
            safe = False
        if not safe:
            pytest.fail("PostgreSQL tests require employee_schedule_test on localhost/127.0.0.1 without routing overrides", pytrace=False)
        engine = create_engine(url)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'binding.sqlite'}")
        @event.listens_for(engine, "connect")
        def foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    models = (Company, User, Snapshot, Binding)
    try:
        # No metadata-wide drop/create or CASCADE: only these four tables.
        with engine.begin() as conn:
            for model in reversed(models):
                model.__table__.drop(conn, checkfirst=True)
            for model in models[:-1]:
                model.__table__.create(conn)
        ensure_employee_schedule_binding_table(engine)
        factory = sessionmaker(engine, autoflush=False)
        with factory.begin() as db:
            db.add(Company(id="c", name="Synthetic"))
            db.flush()
            db.add_all([User(id="a", company_id="c", full_name=NAME, role="agent", is_active=True, availability_status="online"),
                        User(id="b", company_id="c", full_name="Other Synthetic Employee", role="agent", is_active=True),
                        User(id="admin", company_id="c", full_name="Synthetic Admin", role="admin", is_active=True)])
        live = {}
        def fetch(month):
            if month not in live:
                raise schedule.ScheduleError("network_error")
            return copy.deepcopy(live[month])
        monkeypatch.setattr(schedule, "fetch_month", fetch)
        yield SimpleNamespace(engine=engine, factory=factory, live=live)
    finally:
        try:
            with engine.begin() as conn:
                for model in reversed(models):
                    model.__table__.drop(conn, checkfirst=True)
        finally:
            engine.dispose()


def publish(store, months=("2026-11", "2026-12"), edit=None):
    with store.factory.begin() as db:
        for month in months:
            value = payload(month)
            if edit:
                edit(value)
            store.live[month] = copy.deepcopy(value)
            row = db.get(Snapshot, month)
            if row is None:
                row = Snapshot(month=month)
                db.add(row)
            row.payload_json, row.payload_hash = value, "synthetic-hash"
            row.revision, row.fetched_at = 1, datetime(2000, 1, 1)  # Age alone must never reject a valid month.


def connect(store, user="a", enabled=True, at=AT):
    with store.factory() as db:
        ident = "1" if enabled and db.get(Binding, user) is None else None
        schedule.set_binding(db, user, enabled, external_employee_id=ident, at=at)


def resolve(store, at=AT):
    with store.factory() as db:
        return schedule.resolve_employee_schedule(db, "a", at)


def test_explicit_migration_idempotence_defaults_and_cascade(store):
    before = {name: [c["name"] for c in inspect(store.engine).get_columns(name)]
              for name in ("users", "companies", "employee_schedule_snapshots")}
    publish(store)
    connect(store)
    with store.factory() as db:
        row = db.get(Binding, "a")
        original = (row.external_employee_id, row.connected_at, row.last_checked_at)
    ensure_employee_schedule_binding_table(store.engine)
    ensure_employee_schedule_binding_table(store.engine)
    assert before == {name: [c["name"] for c in inspect(store.engine).get_columns(name)] for name in before}
    with store.factory() as db:
        row = db.get(Binding, "a")
        assert (row.external_employee_id, row.connected_at, row.last_checked_at) == original
        assert row.last_offline_work_date is None
        db.execute(text("INSERT INTO employee_schedule_bindings (user_id,external_employee_id,external_full_name,external_department_id,external_department_name,connected_at,last_checked_at) VALUES ('b','2','Synthetic','1','Synthetic',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        db.commit()
        assert db.get(Binding, "b").enabled is False
        db.execute(delete(User).where(User.id == "a"))
        db.commit()
        db.expire_all()
        assert db.get(Binding, "a") is None
    fk = inspect(store.engine).get_foreign_keys(Binding.__tablename__)[0]
    assert fk["referred_table"] == "users" and fk["options"]["ondelete"] == "CASCADE"


def test_name_normalization_all_departments_and_repeated_month_id(store):
    with store.factory.begin() as db:
        db.get(User, "a").full_name = "  ФЁДОРОВ\u00a0 Иван\tПетрович  "
    def second_department(value):
        dep = value["departments"][0]
        value["departments"] = [{"id": 2, "name": "Empty", "employees": []}, dep]
    publish(store, edit=second_department)
    flag = settings.employee_schedule_enabled
    connect(store)
    assert settings.employee_schedule_enabled is flag
    with store.factory() as db:
        result = schedule.binding_status(db, "a", at=AT)
        assert result["enabled"] and result["external_employee_id"] == "1"
        assert result["external_full_name"] == NAME
        assert result["external_department_name"] == "Synthetic department"
        assert result["schedule"]["status"] == "working"
    assert schedule.normalize_name("ＦＯＯ\u2003 BAR") == "foo bar"


@pytest.mark.parametrize("name", ["Федоров И. П.", "Петрович Иван Федоров", "Федоров Иван"])
def test_no_fuzzy_initials_or_word_reordering(store, name):
    publish(store)
    with store.factory.begin() as db:
        db.get(User, "a").full_name = name
    with store.factory() as db:
        with pytest.raises(schedule.BindingError, match="external_id_required"):
            schedule.set_binding(db, "a", True, at=AT)
    with store.factory() as db:
        assert db.get(Binding, "a") is None


@pytest.mark.parametrize("kind,code", [("conflicting_name", "inconsistent_employee"),
                                      ("duplicate_id", "spark_invalid_payload"), ("bad_calendar", "spark_invalid_payload")])
def test_ambiguous_or_inconsistent_data_never_creates_binding(store, kind, code):
    def edit(value):
        employee = value["departments"][0]["employees"][0]
        if kind == "homonyms":
            twin = copy.deepcopy(employee); twin["id"] = 2
            value["departments"][0]["employees"].append(twin)
        elif kind == "conflicting_name" and value["month"] == "2026-12":
            employee["full_name"] = "Different Synthetic Employee"
        elif kind == "duplicate_id":
            value["departments"].append({"id": 2, "name": "Other department", "employees": [copy.deepcopy(employee)]})
        elif kind == "bad_calendar":
            employee["days"].pop()
    publish(store, edit=edit)
    with pytest.raises(schedule.BindingError, match=code):
        connect(store)
    with store.factory() as db:
        assert db.get(Binding, "a") is None


def test_external_id_unique_even_when_other_binding_disabled(store):
    publish(store)
    with store.factory.begin() as db:
        db.get(User, "b").full_name = NAME
    connect(store)
    connect(store, enabled=False)
    with pytest.raises(schedule.BindingError, match="external_id_in_use"):
        connect(store, "b")
    with store.factory() as db:
        db.add(Binding(user_id="b", external_employee_id="1", external_full_name=NAME,
                       external_department_id="1", external_department_name="Synthetic",
                       connected_at=datetime(2026, 11, 19), last_checked_at=datetime(2026, 11, 19)))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(Binding, "b") is None and db.get(Binding, "a").enabled is False


def test_disable_reenable_preserves_id_dates_and_offline_marker(store):
    publish(store)
    connect(store)
    with store.factory.begin() as db:
        row = db.get(Binding, "a")
        created = row.connected_at
        row.last_offline_work_date = date(2026, 11, 18)
        db.get(User, "a").full_name = "Local renamed employee"
    connect(store, enabled=False)
    assert resolve(store).reason == "binding_disabled"
    later = datetime(2026, 11, 20, tzinfo=timezone.utc)
    connect(store, at=later)
    with store.factory() as db:
        row = db.get(Binding, "a")
        assert row.external_employee_id == "1" and row.connected_at == created
        assert row.last_checked_at == later.replace(tzinfo=None)
        assert row.last_offline_work_date == date(2026, 11, 18)


def test_saved_id_missing_does_not_rebind_to_same_name(store):
    publish(store)
    connect(store)
    connect(store, enabled=False)
    with store.factory() as db:
        checked = db.get(Binding, "a").last_checked_at
    publish(store, edit=lambda p: p["departments"][0]["employees"][0].update(id=2))
    with pytest.raises(schedule.BindingError, match="employee_missing"):
        connect(store)
    with store.factory() as db:
        row = db.get(Binding, "a")
        assert not row.enabled and row.external_employee_id == "1" and row.last_checked_at == checked


def test_inactive_local_user_no_mutation_and_reactivation(store):
    publish(store)
    connect(store)
    with store.factory.begin() as db:
        db.get(User, "a").is_active = False
    assert resolve(store).reason == "user_inactive"
    with pytest.raises(schedule.BindingError, match="user_inactive"):
        connect(store)
    with store.factory.begin() as db:
        assert db.get(Binding, "a").enabled
        assert db.get(User, "a").availability_status == "online"
        db.get(User, "a").is_active = True
    assert resolve(store).status == "working"


@pytest.mark.parametrize("at,status", [("2026-11-19T07:59:59+00:00", "before_shift"),
                                      ("2026-11-19T08:00:00+00:00", "working"),
                                      ("2026-11-19T13:59:59+00:00", "working"),
                                      ("2026-11-19T14:00:00+00:00", "after_shift"),
                                      ("2026-11-19T20:59:59+00:00", "after_shift"),
                                      ("2026-11-19T21:00:00+00:00", "before_shift")])
def test_moscow_half_open_shift_boundaries(store, at, status):
    publish(store)
    connect(store)
    result = resolve(store, datetime.fromisoformat(at))
    assert result.status == status
    assert result.start_at.hour == 11 and result.end_at.hour == 17
    assert result.start_at.utcoffset().total_seconds() == 10800
    assert result.shift == "11:00-17:00"


def test_working_sunday_and_external_dismissal_do_not_disable_shift(store):
    def edit(p):
        employee = p["departments"][0]["employees"][0]
        employee.update(is_active=False, last_work_month="2026-11", dismissed_from="2026-12")
        for day in employee["days"]:
            day.update(is_weekend=True, duty=False, mark="on_call")
    publish(store, edit=edit)
    connect(store)
    sunday = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
    assert resolve(store, sunday).status == "working"
    with store.factory() as db:
        assert db.get(User, "a").is_active and db.get(User, "a").availability_status == "online"


def test_known_nonwork_and_no_default_shift_fallback(store):
    def edit(p):
        for day in p["departments"][0]["employees"][0]["days"]:
            day.update(work_day=False, shift="", mark="on_call", duty=True, is_weekend=False)
    publish(store, edit=edit)
    connect(store)
    result = resolve(store)
    assert result.status == "non_working" and result.shift is None and result.start_at is None


@pytest.mark.parametrize("kind", ["month", "employee", "date", "duplicate_date", "invalid_shift"])
def test_missing_or_invalid_data_resolves_unknown(store, kind):
    publish(store)
    connect(store)
    with store.factory.begin() as db:
        row = db.get(Snapshot, "2026-11")
        value = copy.deepcopy(row.payload_json)
        employee = value["departments"][0]["employees"][0]
        if kind == "month":
            db.delete(row)
        else:
            if kind == "employee": employee["id"] = 2
            elif kind == "date": employee["days"].pop(18)
            elif kind == "duplicate_date": employee["days"][18] = copy.deepcopy(employee["days"][0])
            elif kind == "invalid_shift": employee["days"][18]["shift"] = "17:00-11:00"
            row.payload_json = value
    result = resolve(store)
    assert result.status == "unknown" and result.shift is None and result.start_at is None


def test_unbound_missing_data_and_read_only_resolver(store):
    with store.factory() as db:
        assert schedule.resolve_employee_schedule(db, "missing", AT).reason == "user_not_found"
        assert schedule.binding_status(db, "a", at=AT)["enabled"] is False
    connect(store, enabled=False)
    with store.factory() as db:
        assert db.get(Binding, "a") is None
    with pytest.raises(schedule.BindingError, match="spark_network_error"):
        connect(store)
    publish(store)
    connect(store)
    writes = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}:
            writes.append(statement)
    event.listen(store.engine, "before_cursor_execute", observe)
    try:
        with store.factory() as db:
            for _ in range(2):
                assert schedule.binding_status(db, "a", at=AT)["schedule"]["status"] == "working"
                assert not db.new and not db.dirty and not db.deleted
    finally:
        event.remove(store.engine, "before_cursor_execute", observe)
    assert writes == []
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve(store, datetime(2026, 11, 19, 13))


def test_resolver_refreshes_committed_data_without_flushing_caller_edits(store):
    publish(store)
    connect(store)
    with store.factory() as db:
        caller = db.get(User, "a")
        caller.availability_status = "waiting"
        assert schedule.resolve_employee_schedule(db, "a", AT).status == "working"
        with store.factory.begin() as other:
            other.get(User, "a").is_active = False
        assert schedule.resolve_employee_schedule(db, "a", AT).reason == "user_inactive"
        assert caller in db.dirty and caller.availability_status == "waiting"
        db.rollback()
    with store.factory() as db:
        assert db.get(User, "a").availability_status == "online"


def test_reenable_invalid_snapshot_preserves_existing_binding(store):
    publish(store)
    connect(store)
    connect(store, enabled=False)
    with store.factory() as db:
        row = db.get(Binding, "a")
        before = (row.external_employee_id, row.enabled, row.connected_at, row.last_checked_at)
    publish(store, edit=lambda p: p["departments"][0]["employees"][0]["days"].pop())
    with pytest.raises(schedule.BindingError, match="spark_invalid_payload"):
        connect(store)
    with store.factory() as db:
        row = db.get(Binding, "a")
        assert (row.external_employee_id, row.enabled, row.connected_at, row.last_checked_at) == before


def test_unmigrated_binding_returns_unknown_or_safe_admin_error(store):
    Binding.__table__.drop(store.engine)
    with store.factory() as db:
        assert schedule.resolve_employee_schedule(db, "a", AT).reason == "migration_required"
        with pytest.raises(schedule.BindingError, match="migration_required"):
            schedule.binding_status(db, "a", at=AT)
        with pytest.raises(schedule.BindingError, match="migration_required"):
            schedule.set_binding(db, "a", True, at=AT)
    assert not inspect(store.engine).has_table(Binding.__tablename__)


def test_admin_routes_validation_and_existing_routes_preserved(store):
    publish(store)
    app = FastAPI(); app.include_router(router.router)
    actor = SimpleNamespace(role="admin")
    app.dependency_overrides[router.get_current_user] = lambda: actor
    def db_dep():
        with store.factory() as db: yield db
    app.dependency_overrides[router.get_db] = db_dep
    with TestClient(app) as http:
        url = "/api/v1/settings/users/a/schedule"
        for role in ("agent", "chiki_piki"):
            actor.role = role
            assert http.get(url).status_code == 403
            assert http.patch(url, json={"enabled": True}).status_code == 403
        actor.role = "admin"
        for value in ({}, {"enabled": "true"}, {"enabled": 1}, {"enabled": None}, {"enabled": True, "external_employee_id": 2}):
            assert http.patch(url, json=value).status_code == 422
        assert http.get("/api/v1/settings/users/missing/schedule").status_code == 404
        assert http.patch("/api/v1/settings/users/missing/schedule", json={"enabled": True}).status_code == 404
        assert http.get(url).json()["external_employee_id"] is None
        assert http.patch(url, json={"enabled": True}).status_code == 400
        result = http.patch(url, json={"enabled": True, "external_employee_id": "1"})
        assert result.status_code == 200 and result.json()["external_employee_id"] == "1"
        assert "payload_json" not in result.text
        assert http.patch(url, json={"enabled": False}).json()["enabled"] is False
        assert http.get("/api/v1/settings/employee-schedule").status_code == 200
        assert "/api/v1/settings/employee-schedule/sync" in {r.path for r in app.routes}


def test_startup_excludes_binding_migration_and_table():
    from api.app import main
    source = Path(main.__file__).read_text(encoding="utf-8")
    startup = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_startup")
    assert any(Binding.__tablename__ in {e.value for e in node.elts if isinstance(e, ast.Constant)}
               for node in ast.walk(startup) if isinstance(node, ast.Set))
    assert "ensure_employee_schedule_binding_table" not in ast.unparse(startup)
