"""Explicit migration on isolated SQLite or a guarded disposable local PostgreSQL."""
import ast
import os
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import sessionmaker

from api.app.models import Company, User, EmployeeNotificationPeriod as Period, EmployeeNotificationBacklog as Backlog
from api.app.services.schema_evolution import ensure_employee_notification_schedule_schema as migrate


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path):
    if request.param == "postgresql":
        raw = os.environ.get("EMPLOYEE_SCHEDULE_TEST_DATABASE_URL")
        if not raw:
            pytest.skip("Disposable PostgreSQL test URL not supplied")
        try:
            url = make_url(raw)
            safe = (url.get_backend_name() == "postgresql" and url.database == "employee_schedule_test"
                    and url.host in {"localhost", "127.0.0.1"}
                    and not {"host", "hostaddr", "dbname", "service"}.intersection(url.query))
        except (ArgumentError, TypeError, ValueError):
            safe = False
        if not safe:
            pytest.fail("Migration tests require employee_schedule_test on localhost/127.0.0.1 without routing overrides", pytrace=False)
        engine = create_engine(url)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'notification_migration.sqlite'}")
        @event.listens_for(engine, "connect")
        def enable_fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    tables = (Company.__table__, User.__table__, Period.__table__, Backlog.__table__)
    try:
        with engine.begin() as conn:
            for table in reversed(tables):
                table.drop(conn, checkfirst=True)
            Company.__table__.create(conn)
            User.__table__.create(conn)
        factory = sessionmaker(engine, autoflush=False)
        with factory.begin() as db:
            db.add(Company(id="company", name="Synthetic")); db.flush()
            db.add_all([User(id=name, company_id="company", full_name="Synthetic " + name,
                             role="agent", is_active=True) for name in ("a", "b")])
        yield SimpleNamespace(engine=engine, factory=factory)
    finally:
        try:
            with engine.begin() as conn:
                for table in reversed(tables):
                    table.drop(conn, checkfirst=True)
        finally:
            engine.dispose()


def legacy(store):
    serial = "SERIAL" if store.engine.dialect.name == "postgresql" else "INTEGER"
    with store.engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE employee_notification_periods (
            id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL REFERENCES users(id),
            work_date DATE NOT NULL, chat_id VARCHAR(100) NOT NULL, message_id VARCHAR(100),
            offered BOOLEAN NOT NULL DEFAULT FALSE,
            CONSTRAINT uq_employee_notification_period_user_date UNIQUE (user_id, work_date))""")
        conn.exec_driver_sql("CREATE INDEX ix_employee_notification_periods_work_date ON employee_notification_periods (work_date)")
        conn.exec_driver_sql(f"""CREATE TABLE employee_notification_backlog (
            id {serial} PRIMARY KEY, period_id VARCHAR(36) NOT NULL REFERENCES employee_notification_periods(id),
            text TEXT NOT NULL, parse_mode VARCHAR(30), disable_link_preview BOOLEAN NOT NULL,
            send_silently BOOLEAN NOT NULL)""")
        conn.exec_driver_sql("CREATE INDEX ix_employee_notification_backlog_period_id ON employee_notification_backlog (period_id)")
        conn.execute(text("INSERT INTO employee_notification_periods (id,user_id,work_date,chat_id,message_id,offered) "
                          "VALUES (:id,:user,:day,:chat,:message,:offered)"), [
            {"id": "p1", "user": "a", "day": date(2026, 11, 9), "chat": "chat-a", "message": "21", "offered": True},
            {"id": "p2", "user": "a", "day": date(2026, 11, 10), "chat": "chat-a", "message": None, "offered": False},
            {"id": "p3", "user": "b", "day": date(2026, 11, 9), "chat": "chat-b", "message": "23", "offered": True},
        ])
        conn.execute(text("INSERT INTO employee_notification_backlog (period_id,text,parse_mode,disable_link_preview,send_silently) "
                          "VALUES (:period,:body,:mode,:preview,:silent)"), [
            {"period": "p1", "body": "<b>Synthetic one</b>", "mode": "HTML", "preview": True, "silent": False},
            {"period": "p1", "body": "Synthetic two", "mode": None, "preview": False, "silent": True},
            {"period": "p2", "body": "Synthetic three", "mode": "MarkdownV2", "preview": True, "silent": True},
            {"period": "p3", "body": "Synthetic four", "mode": None, "preview": False, "silent": False},
        ])


def old_values(store):
    with store.engine.connect() as conn:
        return (
            conn.execute(text("SELECT id,user_id,work_date,chat_id,message_id,offered FROM employee_notification_periods ORDER BY id")).all(),
            conn.execute(text("SELECT id,period_id,text,parse_mode,disable_link_preview,send_silently FROM employee_notification_backlog ORDER BY id")).all(),
        )


def assert_fk(store):
    fk = inspect(store.engine).get_foreign_keys(Backlog.__tablename__)
    assert any(x["constrained_columns"] == ["period_id"] and x["referred_table"] == Period.__tablename__ for x in fk)
    if store.engine.dialect.name == "sqlite":
        with store.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_upgrade_preserves_legacy_records_and_unknown_timestamps(store):
    legacy(store)
    before = old_values(store)
    migrate(store.engine)
    assert old_values(store) == before
    assert next(c for c in inspect(store.engine).get_columns(Period.__tablename__) if c["name"] == "work_date")["nullable"]
    with store.factory() as db:
        for period in db.scalars(select(Period)):
            assert (period.period_mode, period.window_state, period.offer_state, period.offer_generation) == ("legacy", "legacy", "idle", 0)
            assert period.anchor_at is period.offer_at is period.expires_at is period.schedule_revision is None
        for item in db.scalars(select(Backlog)):
            assert item.send_state == "pending" and item.created_at is None and item.send_attempt_id is None
    assert_fk(store)


def test_new_database_orm_and_server_defaults_are_legacy_compatible(store):
    migrate(store.engine)
    with store.factory.begin() as db:
        db.add(Period(id="orm", user_id="a", work_date=date(2026, 11, 9), chat_id="chat"))
        db.flush()
        db.add(Backlog(period_id="orm", text="Synthetic", disable_link_preview=True, send_silently=False))
    with store.engine.begin() as conn:
        conn.execute(text("INSERT INTO employee_notification_periods (id,user_id,work_date,chat_id) VALUES ('sql','b','2026-11-09','chat')"))
        conn.execute(text("INSERT INTO employee_notification_backlog (period_id,text,disable_link_preview,send_silently) VALUES ('sql','Synthetic',FALSE,TRUE)"))
    with store.factory() as db:
        for period in db.scalars(select(Period)):
            assert period.period_mode == period.window_state == "legacy"
            assert period.offer_generation == 0 and period.offer_state == "idle" and period.anchor_at is None
        assert all(item.send_state == "pending" and item.created_at is None for item in db.scalars(select(Backlog)))
    assert_fk(store)


def test_schedule_null_date_partial_uniqueness_and_known_date_uniqueness(store):
    legacy(store); migrate(store.engine)
    with store.factory.begin() as db:
        db.add(Period(id="unresolved-a", user_id="a", chat_id="chat", period_mode="schedule", window_state="unresolved"))
        db.add(Period(id="unresolved-b", user_id="b", chat_id="chat", period_mode="schedule", window_state="unresolved"))
    with pytest.raises(IntegrityError):
        with store.factory.begin() as db:
            db.add(Period(id="duplicate", user_id="a", chat_id="chat", period_mode="schedule"))
    with pytest.raises(IntegrityError):
        with store.factory.begin() as db:
            db.add(Period(id="same-known-day", user_id="a", work_date=date(2026, 11, 9), chat_id="chat", period_mode="schedule"))
    with store.factory.begin() as db:
        # The partial index must not accidentally prohibit legacy NULL dates.
        db.add_all([Period(id=f"legacy-null-{n}", user_id="a", chat_id="chat") for n in (1, 2)])
    assert_fk(store)


def test_rerun_preserves_all_filled_new_fields(store):
    legacy(store); migrate(store.engine)
    anchor, start, end = datetime(2026, 11, 8, 12), datetime(2026, 11, 9, 8), datetime(2026, 11, 9, 14)
    with store.factory.begin() as db:
        period = db.get(Period, "p1")
        period.period_mode, period.window_state = "schedule", "resolved"
        period.anchor_at, period.offer_at, period.expires_at = anchor, start, end
        period.schedule_revision, period.offer_generation, period.offer_state = 7, 3, "offered"
        item = db.scalars(select(Backlog).order_by(Backlog.id)).first()
        item.created_at, item.send_state, item.send_attempt_id = anchor, "sending", "synthetic-attempt"
    with store.engine.connect() as conn:
        before = [conn.execute(text(f"SELECT * FROM {table.name} ORDER BY id")).all() for table in (Period.__table__, Backlog.__table__)]
    migrate(store.engine); migrate(store.engine)
    with store.engine.connect() as conn:
        after = [conn.execute(text(f"SELECT * FROM {table.name} ORDER BY id")).all() for table in (Period.__table__, Backlog.__table__)]
    assert after == before
    assert_fk(store)


def test_extra_columns_and_indexes_survive_rebuild(store):
    legacy(store)
    with store.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE employee_notification_periods ADD COLUMN custom_note TEXT")
        conn.exec_driver_sql("UPDATE employee_notification_periods SET custom_note='keep'")
        conn.exec_driver_sql("CREATE INDEX ix_notification_custom_chat ON employee_notification_periods (chat_id)")
        if store.engine.dialect.name == "sqlite":
            conn.exec_driver_sql("CREATE TRIGGER notification_custom_trigger AFTER UPDATE OF offered ON employee_notification_periods BEGIN SELECT 1; END")
    migrate(store.engine)
    with store.engine.connect() as conn:
        assert conn.scalars(text("SELECT custom_note FROM employee_notification_periods")).all() == ["keep"] * 3
        if store.engine.dialect.name == "sqlite":
            assert conn.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='notification_custom_trigger'")) == 1
    indexes = {x["name"] for x in inspect(store.engine).get_indexes(Period.__tablename__)}
    assert {"ix_notification_custom_chat", "ix_employee_notification_periods_work_date", "uq_employee_notification_schedule_unresolved_user"} <= indexes
    assert_fk(store)


def test_actual_legacy_accumulation_still_works_after_upgrade(store):
    from api.app.services.employee_notification_backlog import accumulate_delivery, MOSCOW
    legacy(store); migrate(store.engine)
    delivery = SimpleNamespace(user_id="a", telegram_chat_id="chat-a", text="New synthetic notification",
                               parse_mode="HTML", disable_link_preview=True, send_silently=False)
    assert accumulate_delivery(delivery, session_factory=store.factory,
                               now=datetime(2026, 11, 7, 19, tzinfo=MOSCOW))
    with store.factory() as db:
        rows = list(db.scalars(select(Backlog).order_by(Backlog.id)))
        assert len(rows) == 5 and rows[-1].text == delivery.text
        assert rows[-1].send_state == "pending" and rows[-1].created_at is None
        assert db.get(Period, "p1").period_mode == "legacy"


@pytest.mark.parametrize("state_column,value", [("period_mode", "bad"), ("window_state", "bad"),
                                               ("offer_state", "bad"), ("send_state", "bad")])
def test_state_constraints_reject_unknown_values(store, state_column, value):
    legacy(store); migrate(store.engine)
    table = Backlog.__tablename__ if state_column == "send_state" else Period.__tablename__
    with pytest.raises(IntegrityError):
        with store.engine.begin() as conn:
            conn.execute(text(f"UPDATE {table} SET {state_column}=:value"), {"value": value})


def test_rollback_old_schema_after_late_failure(store):
    legacy(store)
    before = old_values(store)
    def reflected_columns(table):
        return [{**column, "type": str(column["type"])} for column in inspect(store.engine).get_columns(table)]
    columns = {table: reflected_columns(table) for table in (Period.__tablename__, Backlog.__tablename__)}
    failed = []
    def fail(conn, cursor, statement, params, context, many):
        matches = (statement.startswith("ALTER TABLE employee_notification_periods_schedule_upgrade RENAME")
                   if store.engine.dialect.name == "sqlite" else
                   statement.startswith("CREATE UNIQUE INDEX uq_employee_notification_schedule_unresolved_user"))
        if matches and not failed:
            failed.append(True)
            raise RuntimeError("Synthetic late migration failure")
    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic late"):
            migrate(store.engine)
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert failed and old_values(store) == before
    for table in columns:
        assert reflected_columns(table) == columns[table]
    assert not inspect(store.engine).has_table("employee_notification_periods_schedule_upgrade")
    assert_fk(store)


def test_rollback_fresh_schema_on_creation_failure(store):
    failed = []
    def fail(conn, cursor, statement, params, context, many):
        if "CREATE TABLE employee_notification_backlog" in statement:
            failed.append(True)
            raise RuntimeError("Synthetic creation failure")
    event.listen(store.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic creation"):
            migrate(store.engine)
    finally:
        event.remove(store.engine, "before_cursor_execute", fail)
    assert failed
    assert not inspect(store.engine).has_table(Period.__tablename__)
    assert not inspect(store.engine).has_table(Backlog.__tablename__)
    with store.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM users")) == 2
        if store.engine.dialect.name == "sqlite":
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_fk_enforcement_survives_parent_replacement(store):
    legacy(store); migrate(store.engine)
    with pytest.raises(IntegrityError):
        with store.engine.begin() as conn:
            conn.execute(text("INSERT INTO employee_notification_backlog (period_id,text,disable_link_preview,send_silently) VALUES ('missing','Synthetic',FALSE,FALSE)"))
    with pytest.raises(IntegrityError):
        with store.engine.begin() as conn:
            conn.execute(text("DELETE FROM employee_notification_periods WHERE id='p1'"))
    assert_fk(store)


def test_sqlite_preexisting_fk_violation_aborts_without_schema_changes(store):
    if store.engine.dialect.name != "sqlite":
        pytest.skip("SQLite-specific FK recovery")
    legacy(store)
    with store.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF"); conn.commit()
        conn.execute(text("INSERT INTO employee_notification_backlog (period_id,text,disable_link_preview,send_silently) VALUES ('missing','Synthetic',FALSE,FALSE)")); conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON"); conn.commit()
    before = old_values(store)
    with pytest.raises(RuntimeError, match="Existing SQLite foreign key violations"):
        migrate(store.engine)
    assert old_values(store) == before
    assert "period_mode" not in {c["name"] for c in inspect(store.engine).get_columns(Period.__tablename__)}
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_migration_is_not_called_by_startup_or_scheduler():
    from api.app import main
    from api.app.services import internal_task_scheduler
    for module in (main, internal_task_scheduler):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id == "ensure_employee_notification_schedule_schema" for node in ast.walk(tree))
