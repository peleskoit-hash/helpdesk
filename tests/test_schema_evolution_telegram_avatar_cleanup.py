from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api.app.models import TelegramAvatarCleanupState
from api.app.services import schema_evolution
from api.app.services import telegram_client_avatar_nightly as nightly


def _clock(monkeypatch, hour):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 17, hour, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(schema_evolution, "datetime", Clock, raising=False)


@pytest.mark.parametrize("hour,fallback", [(23, "2026-10-17"), (0, "2026-10-17")])
def test_sqlite_migrates_legacy_table_without_losing_success_dates(monkeypatch, hour, fallback):
    # 23:00 UTC is 02:00 MSK on Oct 18; 00:00 UTC is 03:00 MSK on Oct 17.
    engine = create_engine("sqlite://")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    _clock(monkeypatch, hour)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE telegram_avatar_cleanup_state (id INTEGER PRIMARY KEY, last_success_on DATE)"))
        conn.execute(text("INSERT INTO telegram_avatar_cleanup_state VALUES (1, '2026-10-15'), (2, NULL), (3, '2026-10-17')"))
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, last_success_on, next_run_on FROM telegram_avatar_cleanup_state ORDER BY id")).all()
    assert rows == [(1, "2026-10-15", "2026-10-16"), (2, None, fallback),
                    (3, "2026-10-17", "2026-10-18")]
    assert not next(c for c in inspect(engine).get_columns("telegram_avatar_cleanup_state") if c["name"] == "next_run_on")["nullable"]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO telegram_avatar_cleanup_state VALUES (4, NULL, NULL)"))
    engine.dispose()


def test_sqlite_creates_missing_table_and_preserves_existing_planned_date(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    _clock(monkeypatch, 0)
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO telegram_avatar_cleanup_state VALUES (1, '2026-10-16', '2026-10-25')"))
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT last_success_on, next_run_on FROM telegram_avatar_cleanup_state")).one() == (
            "2026-10-16", "2026-10-25")
    engine.dispose()


@pytest.mark.parametrize("hour,last,should_run", [
    (23, "2026-10-17", False),  # Yesterday succeeded; today's 03:00 has not arrived.
    (23, "2026-10-16", True),   # Yesterday was missed: catch up before today's 03:00.
    (23, None, True),           # Legacy state without success also has a missed due date.
    (0, "2026-10-16", True),    # Exactly 03:00 MSK.
    (0, "2026-10-17", False),   # Already succeeded today.
])
def test_migrated_dates_preserve_schedule_and_catch_up(monkeypatch, hour, last, should_run):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    _clock(monkeypatch, hour)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE telegram_avatar_cleanup_state (id INTEGER PRIMARY KEY, last_success_on DATE)"))
        conn.execute(text("INSERT INTO telegram_avatar_cleanup_state VALUES (1, :last)"), {"last": last})
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    monkeypatch.setattr(nightly, "_try_job_lock", lambda _db: True)
    monkeypatch.setattr(nightly, "_cleanup_all", lambda *_args: True)
    assert nightly.run_avatar_cleanup_once(
        session_factory=sessionmaker(bind=engine),
        now_utc=datetime(2026, 10, 17, hour, tzinfo=timezone.utc),
    ) is should_run
    engine.dispose()


@pytest.mark.parametrize("hour,fallback", [(23, date(2026, 10, 17)), (0, date(2026, 10, 17))])
@pytest.mark.parametrize("missing", [False, True])
def test_postgres_adds_backfills_then_enforces_not_null_idempotently(monkeypatch, hour, fallback, missing):
    # Exercise PostgreSQL DDL/order and backfill parameters without a live DEV DB.
    columns = {"id": False, "last_success_on": True}
    if not missing:
        columns["next_run_on"] = True  # Also repair an interrupted nullable-column migration.
    statements = []
    rows = [[1, date(2026, 10, 15), None], [2, None, None],
            [3, date(2026, 10, 17), None]]
    if not missing:
        rows.append([4, date(2026, 10, 16), date(2026, 10, 25)])

    def execute(statement, params=None):
        sql = str(statement)
        statements.append(sql)
        if "ADD COLUMN" in sql:
            columns["next_run_on"] = True
        elif sql.startswith("UPDATE"):
            assert "last_success_on + 1" in sql and params["due_date"] == fallback
            for row in rows:
                if row[2] is None:
                    row[2] = row[1] + timedelta(days=1) if row[1] else params["due_date"]
        elif "SET NOT NULL" in sql:
            assert all(row[2] is not None for row in rows)
            columns["next_run_on"] = False

    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), execute=execute)

    @contextmanager
    def begin():
        yield connection

    monkeypatch.setattr(schema_evolution, "engine", SimpleNamespace(begin=begin))
    monkeypatch.setattr(TelegramAvatarCleanupState.__table__, "create", lambda **_kwargs: None)
    monkeypatch.setattr(schema_evolution, "inspect", lambda _conn: SimpleNamespace(
        get_columns=lambda _name: [{"name": name, "nullable": nullable} for name, nullable in columns.items()]), raising=False)
    _clock(monkeypatch, hour)
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    assert rows[:3] == [[1, date(2026, 10, 15), date(2026, 10, 16)], [2, None, fallback],
                       [3, date(2026, 10, 17), date(2026, 10, 18)]]
    if not missing:
        assert rows[3][2] == date(2026, 10, 25)
    assert statements.index(next(sql for sql in statements if sql.startswith("UPDATE"))) < statements.index(
        next(sql for sql in statements if "SET NOT NULL" in sql))
    statements.clear()
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    assert not any("ADD COLUMN" in sql or "SET NOT NULL" in sql for sql in statements)
