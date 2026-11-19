"""Only isolated databases. Set EMPLOYEE_SCHEDULE_TEST_DATABASE_URL for disposable PG."""
import asyncio
import calendar
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from api.app.config import Settings, settings
from api.app.models import EmployeeScheduleSnapshot as Snapshot, EmployeeScheduleSyncState as SyncState
from api.app.routers import employee_schedule as router
from api.app.services import employee_schedule_client as client, employee_schedule_sync as sync
from api.app.services.schema_evolution import ensure_employee_schedule_tables


AT = datetime(2026, 11, 19, 13)  # 16:00 Moscow


def export(month="2026-11"):
    year, number = map(int, month.split("-"))
    return {"version": 2, "month": month, "today": "2026-11-19", "extension": {"preserved": True},
            "departments": [{"id": 1, "name": "Test", "employees": [{
                "id": 1, "full_name": "Synthetic Employee", "default_shift": "09:00-18:00", "is_active": True,
                "dismissed_from": None, "last_work_month": None, "dismissed_next_month": False,
                "days": [{"date": date(year, number, day).isoformat(), "work_day": True,
                          "shift": "09:00-18:00", "mark": "", "duty": False, "is_weekend": False}
                         for day in range(1, calendar.monthrange(year, number)[1] + 1)]
            }]}]}


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path, monkeypatch):
    if request.param == "postgresql":
        url = os.environ.get("EMPLOYEE_SCHEDULE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Disposable PostgreSQL test URL not supplied")
        # Require the dedicated disposable database, never the application's helpdesk DB.
        from sqlalchemy.engine import make_url
        assert make_url(url).database == "employee_schedule_test"
        engine = create_engine(url)
        with engine.begin() as conn:
            Snapshot.__table__.drop(conn, checkfirst=True)
            SyncState.__table__.drop(conn, checkfirst=True)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'schedule.sqlite'}", connect_args={"timeout": 10})
    ensure_employee_schedule_tables(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(settings, "employee_schedule_enabled", True)
    monkeypatch.setattr(settings, "employee_schedule_api_base_url", "https://schedule.test")
    monkeypatch.setattr(settings, "employee_schedule_api_key", "test-secret")
    monkeypatch.setattr(sync, "SessionLocal", factory)
    yield SimpleNamespace(engine=engine, factory=factory)
    engine.dispose()


def claim(store, at=AT, manual=True):
    with store.factory() as db:
        status, result = sync.claim_sync(db, manual=manual, at=at)
    assert status == "accepted"
    return result


def run(store, at=AT, fetcher=export):
    return sync.run_sync(claim(store, at), session_factory=store.factory, fetcher=fetcher, clock=lambda: at)


def snapshots(store):
    with store.factory() as db:
        return [(r.month, r.payload_hash, r.revision, r.payload_json) for r in db.scalars(select(Snapshot).order_by(Snapshot.month))]


def test_migration_idempotent_and_data_preserved(store):
    assert run(store) == "success"
    before = snapshots(store)
    with store.engine.begin() as conn:
        conn.execute(text("CREATE TABLE existing_data (id INTEGER PRIMARY KEY, value VARCHAR(20))"))
        conn.execute(text("INSERT INTO existing_data VALUES (1, 'keep')"))
    ensure_employee_schedule_tables(store.engine)
    ensure_employee_schedule_tables(store.engine)
    assert snapshots(store) == before
    with store.factory() as db:
        assert db.get(SyncState, 1).status == "success"
        assert len(list(db.scalars(select(SyncState)))) == 1
    assert set(inspect(store.engine).get_table_names()) == {Snapshot.__tablename__, SyncState.__tablename__, "existing_data"}
    with store.engine.begin() as conn:
        assert conn.scalar(text("SELECT value FROM existing_data WHERE id = 1")) == "keep"
        conn.execute(text("DROP TABLE existing_data"))


def test_atomic_pair_retention_and_december_rollover(store):
    assert run(store) == "success"
    assert [x[0] for x in snapshots(store)] == ["2026-11", "2026-12"]
    assert snapshots(store)[0][3]["extension"] == {"preserved": True}
    at = datetime(2026, 12, 31, 13)
    assert run(store, at) == "success"
    assert [x[0] for x in snapshots(store)] == ["2026-12", "2027-01"]
    assert len({x[2] for x in snapshots(store)}) == 1


@pytest.mark.parametrize("failure", ["second_month", "network"])
def test_failed_sync_preserves_both_snapshots_and_last_success(store, failure):
    assert run(store) == "success"
    before = snapshots(store)
    def broken(month):
        if failure == "network" or month == "2026-12":
            raise client.ScheduleError("network_error")
        value = export(month)
        value["extension"] = {"changed": True}
        return value
    assert run(store, AT + timedelta(minutes=1), broken) == "error"
    assert snapshots(store) == before
    with store.factory() as db:
        state = db.get(SyncState, 1)
        assert state.status == "error" and state.last_success_at == AT
        assert state.last_error_code == "network_error"
        assert state.next_attempt_at == AT + timedelta(minutes=6)


def test_error_state_and_logs_never_include_remote_personal_data(store, caplog):
    def broken(month):
        raise ValueError("test-secret Synthetic Employee")
    assert run(store, fetcher=broken) == "error"
    with store.factory() as db:
        body = json.dumps(sync.sync_status(db), default=str)
        assert db.get(SyncState, 1).last_error_code == "sync_error"
    assert "test-secret" not in body + caplog.text
    assert "Synthetic Employee" not in body + caplog.text


def test_invalid_second_month_is_not_published(store):
    def broken(month):
        value = export(month)
        if month.endswith("12"):
            value["departments"][0]["employees"][0]["days"].pop()
        return value
    assert run(store, fetcher=broken) == "error"
    assert snapshots(store) == []


def test_storage_failure_rolls_back_state_and_pair(store):
    from sqlalchemy import event
    assert run(store) == "success"
    before = snapshots(store)
    failed = []
    def fail_after_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE employee_schedule_snapshots") and not failed:
            failed.append(True)
            raise RuntimeError("Synthetic storage failure")
    event.listen(store.engine, "after_cursor_execute", fail_after_write)
    try:
        assert run(store, AT + timedelta(minutes=1)) == "error"
    finally:
        event.remove(store.engine, "after_cursor_execute", fail_after_write)
    assert failed and snapshots(store) == before
    with store.factory() as db:
        assert db.get(SyncState, 1).last_success_at == AT


def test_claim_concurrency_and_single_fetch(store):
    barrier = Barrier(2)
    def contender(manual):
        with store.factory() as db:
            barrier.wait(timeout=5)
            return sync.claim_sync(db, manual=manual, at=AT)
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(contender, True), pool.submit(contender, False)
        outcomes = [a.result(timeout=10), b.result(timeout=10)]
    assert sorted(status for status, _ in outcomes) == ["accepted", "running"]
    calls = []
    def fetch(month):
        calls.append(month)
        # The other entry point cannot start while either HTTP request is in flight.
        with store.factory() as db:
            assert sync.claim_sync(db, manual=True, at=AT)[0] == "running"
        return export(month)
    winner = next(c for _, c in outcomes if c)
    assert sync.run_sync(winner, session_factory=store.factory, fetcher=fetch, clock=lambda: AT) == "success"
    assert calls == ["2026-11", "2026-12"]


def test_restart_lease_recovery_and_stale_worker_fencing(store):
    old = claim(store)
    later = AT + timedelta(seconds=sync.LEASE_SECONDS + 1)
    new = claim(store, later)
    assert sync.run_sync(old, session_factory=store.factory, fetcher=export, clock=lambda: later) == "superseded"
    assert snapshots(store) == []
    assert sync.run_sync(new, session_factory=store.factory, fetcher=export, clock=lambda: later) == "success"
    before = snapshots(store)
    assert sync.run_sync(old, session_factory=store.factory, fetcher=export, clock=lambda: later) == "superseded"
    assert snapshots(store) == before


def test_daily_1600_gate_bootstrap_and_durable_success(store):
    before = AT - timedelta(hours=1)
    assert run(store, before) == "success"  # Manual before 16:00 must not skip today's 16:00 run.
    with store.factory() as db:
        assert sync.claim_sync(db, at=AT - timedelta(seconds=1))[0] == "not_due"
    c = claim(store, AT, manual=False)
    assert sync.run_sync(c, session_factory=store.factory, fetcher=export, clock=lambda: AT) == "success"
    with store.factory() as db:
        assert sync.claim_sync(db, at=AT + timedelta(hours=1))[0] == "not_due"
    # A fresh session/process still sees the persisted success.
    c = claim(store, AT + timedelta(days=1, hours=2), manual=False)
    assert c.months == ("2026-11", "2026-12")


def test_failed_retry_backoff_and_month_changes_during_fetch(store):
    c = claim(store)
    assert sync.run_sync(c, session_factory=store.factory,
                         fetcher=lambda _: (_ for _ in ()).throw(client.ScheduleError("network_error")),
                         clock=lambda: AT) == "error"
    with store.factory() as db:
        assert sync.claim_sync(db, at=AT + timedelta(seconds=1))[0] == "not_due"
    assert claim(store, AT + timedelta(seconds=sync.RETRY_SECONDS), manual=False)
    # A request crossing the Moscow month boundary cannot publish the old horizon.
    end = datetime(2026, 11, 30, 20, 59)
    c = claim(store, end)
    assert sync.run_sync(c, session_factory=store.factory, fetcher=export,
                         clock=lambda: datetime(2026, 11, 30, 21)) == "error"
    assert snapshots(store) == []


def test_disabled_and_unmigrated_never_create_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.sqlite'}")
    monkeypatch.setattr(settings, "employee_schedule_enabled", False)
    def forbidden():
        raise AssertionError("Disabled integration touched DB")
    assert sync.run_due_sync(session_factory=forbidden) == "disabled"
    monkeypatch.setattr(settings, "employee_schedule_enabled", True)
    monkeypatch.setattr(settings, "employee_schedule_api_base_url", "https://schedule.test")
    monkeypatch.setattr(settings, "employee_schedule_api_key", "test-secret")
    with sessionmaker(engine)() as db:
        assert sync.claim_sync(db, manual=True)[0] == "migration_required"
        assert sync.sync_status(db)["schema_ready"] is False
    assert inspect(engine).get_table_names() == []
    engine.dispose()
    assert Settings(_env_file=None).employee_schedule_enabled is False
    assert "sensitive-key" not in repr(Settings(_env_file=None, employee_schedule_api_key="sensitive-key"))


@pytest.mark.parametrize("mutation", [
    "version", "month", "missing_day", "duplicate_day", "outside_month", "bad_date", "boolean_string",
    "duplicate_employee", "duplicate_department", "bool_id", "work_no_shift", "nonwork_shift",
    "reverse_shift", "night_shift", "invalid_hour", "invalid_minute", "short_hour", "empty_name", "days_type",
])
def test_validation_rejects_invalid_data(mutation):
    value = export()
    dep = value["departments"][0]; emp = dep["employees"][0]; day = emp["days"][0]
    if mutation == "version": value["version"] = True
    elif mutation == "month": value["month"] = "2026-12"
    elif mutation == "missing_day": emp["days"].pop()
    elif mutation == "duplicate_day": emp["days"][-1] = copy.deepcopy(day)
    elif mutation == "outside_month": day["date"] = "2026-12-01"
    elif mutation == "bad_date": day["date"] = "2026-11-31"
    elif mutation == "boolean_string": day["work_day"] = "true"
    elif mutation == "duplicate_employee": dep["employees"].append(copy.deepcopy(emp))
    elif mutation == "duplicate_department": value["departments"].append(copy.deepcopy(dep))
    elif mutation == "bool_id": emp["id"] = True
    elif mutation == "work_no_shift": day["shift"] = ""
    elif mutation == "nonwork_shift": day["work_day"] = False
    elif mutation == "reverse_shift": day["shift"] = "18:00-09:00"
    elif mutation == "night_shift": day["shift"] = "22:00-06:00"
    elif mutation == "invalid_hour": day["shift"] = "09:00-24:00"
    elif mutation == "invalid_minute": day["shift"] = "09:60-18:00"
    elif mutation == "short_hour": day["shift"] = "9:00-18:00"
    elif mutation == "empty_name": emp["full_name"] = " "
    elif mutation == "days_type": emp["days"] = {}
    with pytest.raises(client.ScheduleError, match="invalid_payload"):
        client.validate_export(value, "2026-11")


def test_validation_does_not_infer_work_from_marks_weekday_or_external_activity():
    value = export()
    emp = value["departments"][0]["employees"][0]
    emp.update(is_active=False, last_work_month="2026-11", dismissed_from="2026-12")
    emp["days"][0].update(mark="on_call", work_day=False, shift="", duty=True)
    emp["days"][1].update(mark="remote", is_weekend=True)  # Sunday is a valid work day.
    assert client.validate_export(value, "2026-11") is value


@pytest.mark.parametrize("field", ["dismissed_from", "last_work_month"])
@pytest.mark.parametrize("month", [None, "2026-11", "2027-01", "0001-01", "9999-12"])
def test_dismissal_month_accepts_null_or_calendar_month(field, month):
    value = export()
    value["departments"][0]["employees"][0][field] = month
    assert client.validate_export(value, "2026-11") is value


@pytest.mark.parametrize("field", ["dismissed_from", "last_work_month"])
@pytest.mark.parametrize("month", ["", "2026", "2026-1", "2026-00", "2026-13", "0000-01",
                                    "10000-01", "2026-11-01", "2026/11", " 2026-11", "2026-11\n",
                                    "not-a-month", 202611, True, [], {}])
def test_dismissal_month_rejects_invalid_values(field, month):
    value = export()
    value["departments"][0]["employees"][0][field] = month
    with pytest.raises(client.ScheduleError, match="invalid_payload"):
        client.validate_export(value, "2026-11")


@pytest.mark.parametrize("body,code", [(b'{"version":2,"version":2}', "invalid_json"),
                                       (b'{"bad":NaN}', "invalid_json"), (b'not json', "invalid_json"),
                                       (b'x' * 1025, "response_too_large")])
def test_transport_rejects_bad_json_and_limits(monkeypatch, body, code):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return body[:limit]
    monkeypatch.setattr(client, "build_opener", lambda _: SimpleNamespace(open=lambda *a, **kw: Response()))
    config = Settings(_env_file=None, employee_schedule_api_base_url="https://schedule.test",
                      employee_schedule_api_key="test-secret", employee_schedule_max_response_bytes=1024)
    with pytest.raises(client.ScheduleError, match=code):
        client.fetch_month("2026-11", config=config)


@pytest.mark.parametrize("error,code", [(HTTPError('https://test', 403, 'secret', {}, None), 'unauthorized'),
                                        (HTTPError('https://test', 429, 'secret', {}, None), 'rate_limited'),
                                        (HTTPError('https://test', 302, 'secret', {}, None), 'http_error'),
                                        (URLError('test-secret'), 'network_error'), (TimeoutError(), 'network_error')])
def test_transport_safe_errors(monkeypatch, error, code):
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr(client, "build_opener", lambda _: SimpleNamespace(open=fail))
    config = Settings(_env_file=None, employee_schedule_api_base_url="https://schedule.test", employee_schedule_api_key="test-secret")
    with pytest.raises(client.ScheduleError) as exc:
        client.fetch_month("2026-11", config=config)
    assert str(exc.value) == code and 'secret' not in str(exc.value)
    assert client._NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_transport_uses_get_header_and_preserves_payload(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps(export()).encode()
    def opened(request, timeout):
        assert request.get_method() == "GET"
        assert request.full_url == "https://schedule.test/api/schedule/export?month=2026-11"
        assert dict((k.lower(), v) for k, v in request.header_items())["x-api-key"] == "test-secret"
        assert timeout == 10
        return Response()
    monkeypatch.setattr(client, "build_opener", lambda _: SimpleNamespace(open=opened))
    config = Settings(_env_file=None, employee_schedule_api_base_url="https://schedule.test", employee_schedule_api_key="test-secret")
    assert client.fetch_month("2026-11", config=config) == export()


@pytest.mark.parametrize("base", ["http://schedule.test", "https://user:pass@schedule.test", "https://schedule.test/path", "https://schedule.test?secret=x"])
def test_transport_refuses_unsafe_origins(base):
    config = Settings(_env_file=None, employee_schedule_api_base_url=base, employee_schedule_api_key="test-secret")
    with pytest.raises(client.ScheduleError, match="not_configured"):
        client.fetch_month("2026-11", config=config)


def test_admin_api_auth_status_sanitization_and_async_manual_sync(store, monkeypatch):
    app = FastAPI()
    app.include_router(router.router)
    actor = SimpleNamespace(role="admin")
    app.dependency_overrides[router.get_current_user] = lambda: actor
    def db_dep():
        with store.factory() as db: yield db
    app.dependency_overrides[router.get_db] = db_dep
    calls = []
    monkeypatch.setattr(router, "run_sync", lambda claim: calls.append(claim))
    with TestClient(app) as http:
        url = "/api/v1/settings/employee-schedule"
        for role in ["agent", "chiki_piki"]:
            actor.role = role
            assert http.get(url).status_code == 403
            assert http.post(url + "/sync").status_code == 403
        actor.role = "admin"
        response = http.post(url + "/sync")
        assert response.status_code == 202 and len(calls) == 1
        assert http.post(url + "/sync").status_code == 409
        assert sync.run_sync(calls[0], session_factory=store.factory, fetcher=export) == "success"
        result = http.get(url)
        assert result.status_code == 200
        body = result.json()
        assert len(body["months"]) == 2
        for forbidden in ["test-secret", "Synthetic Employee", "payload_json", "worker_token", "full_name"]:
            assert forbidden not in result.text
        monkeypatch.setattr(settings, "employee_schedule_enabled", False)
        assert http.post(url + "/sync").status_code == 409


def test_scheduler_network_does_not_block_other_existing_jobs(monkeypatch):
    from api.app.services import internal_task_scheduler as scheduler
    started, release = Event(), Event()
    calls = []
    def slow_sync():
        started.set()
        assert release.wait(timeout=5)
    monkeypatch.setattr(settings, "employee_schedule_enabled", True)
    monkeypatch.setattr(sync, "run_due_sync", slow_sync)
    monkeypatch.setattr(sync, "_task", None)
    names = ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once",
             "run_telegram_after_hours_cleanup_once", "run_employee_auto_offline_once",
             "run_employee_notification_backlog_once", "run_ticket_auto_unfreeze_once"]
    for name in names:
        monkeypatch.setattr(scheduler, name, lambda name=name: calls.append(name))
    async def stop(seconds):
        assert seconds == 30
        assert started.is_set() and not release.is_set()
        assert calls == names
        release.set()
        raise asyncio.CancelledError
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    async def scenario():
        try:
            with pytest.raises(asyncio.CancelledError):
                await scheduler.internal_task_scheduler_loop()
        finally:
            release.set()
            await sync.finish_schedule_sync()
    asyncio.run(scenario())


def test_scheduler_tick_failure_preserves_other_jobs_and_next_pass(monkeypatch, caplog):
    from api.app.services import internal_task_scheduler as scheduler
    calls, ticks = [], []
    names = ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once",
             "run_telegram_after_hours_cleanup_once", "run_employee_auto_offline_once",
             "run_employee_notification_backlog_once", "run_ticket_auto_unfreeze_once"]
    def broken_tick():
        ticks.append(True)
        raise RuntimeError("sensitive remote details")
    monkeypatch.setattr(scheduler, "schedule_sync_tick", broken_tick)
    for name in names:
        monkeypatch.setattr(scheduler, name, lambda name=name: calls.append(name))
    async def stop_after_two_passes(seconds):
        assert seconds == 30
        if len(ticks) == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_two_passes)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.internal_task_scheduler_loop())
    assert len(ticks) == 2 and calls == names * 2
    assert "Employee schedule sync tick failed safely" in caplog.text
    assert "sensitive remote details" not in caplog.text


def test_startup_explicitly_excludes_schedule_tables_and_registers_router():
    from api.app import main
    import ast
    from pathlib import Path
    source = Path(main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    startup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_startup")
    sets = [node for node in ast.walk(startup) if isinstance(node, ast.Set)]
    assert any({Snapshot.__tablename__, SyncState.__tablename__} <= {e.value for e in s.elts if isinstance(e, ast.Constant)} for s in sets)
    assert "ensure_employee_schedule_tables" not in ast.unparse(startup)
    assert any(route.path == "/api/v1/settings/employee-schedule/sync" for route in main.app.routes)
