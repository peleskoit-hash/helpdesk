import asyncio
from datetime import date, datetime, timedelta, timezone
import os
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from api.app.db import Base
from api.app.models import Client, Company, TelegramAvatarCleanupState, TelegramContact, Ticket
from api.app.services import internal_task_scheduler as scheduler
from api.app.services import schema_evolution
from api.app.services import telegram_client_avatar_cleanup  # Register intake/cleanup serialization.
from api.app.services import telegram_client_avatar_nightly as nightly
from api.app.services import telegram_client_avatars as storage


NOW = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)  # 03:00 Europe/Moscow.


@pytest.fixture
def case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    directory = tmp_path / "telegram_client_avatars"
    directory.mkdir()
    monkeypatch.setattr(storage.settings, "telegram_client_avatar_storage_dir", str(directory))
    monkeypatch.setattr(storage, "prepare_avatar", lambda _: (b"test-thumbnail", "jpg"))
    # PostgreSQL owns the distributed advisory lock; SQLite tests exercise all work
    # behind that guard, using fake bytes rather than generating image fixtures.
    monkeypatch.setattr(nightly, "_try_job_lock", Mock(return_value=True))
    with factory() as db:
        db.add(Company(id="company", name="Company"))
        db.commit()

    def seed(key="client", age=366, status="closed", **ticket_fields):
        filename = f"{uuid.uuid4().hex}.jpg"
        path = directory / filename
        path.write_bytes(b"test-full-photo")
        old = (NOW - timedelta(days=3)).timestamp()
        os.utime(path, (old, old))
        with factory() as db:
            db.add_all([
                Client(id=key, company_id="company", display_name=key),
                TelegramContact(id=key, company_id="company", client_id=key,
                                external_user_id=key, external_chat_id=key,
                                avatar_thumbnail=b"test-thumbnail", avatar_file_unique_id="photo",
                                avatar_updated_at=NOW, avatar_path=f"telegram_client_avatars/{filename}"),
            ])
            db.commit()
            if age is not None:
                # Historical rows intentionally bypass immediate close cleanup:
                # the nightly job must repair previously missed cleanup.
                db.execute(Ticket.__table__.insert().values(
                    id=key, company_id="company", client_id=key, subject="History", status=status,
                    created_at=NOW - timedelta(days=age), updated_at=NOW, **ticket_fields,
                ))
                db.commit()
        return path

    yield SimpleNamespace(engine=engine, factory=factory, directory=directory, seed=seed,
                          run=lambda when=NOW: nightly.run_avatar_cleanup_once(session_factory=factory, now_utc=when))
    engine.dispose()


def _avatar(case, key="client"):
    with case.factory() as db:
        contact = db.get(TelegramContact, key)
        return contact.avatar_thumbnail, contact.avatar_file_unique_id, contact.avatar_path, contact.avatar_updated_at


def test_runs_at_moscow_0300_not_earlier_and_only_once_per_day(case, monkeypatch):
    work = Mock(return_value=True)
    monkeypatch.setattr(nightly, "_cleanup_all", work)
    assert not case.run(NOW - timedelta(seconds=1))
    work.assert_not_called()
    assert case.run()
    assert not case.run(NOW + timedelta(hours=10))
    assert not case.run(NOW + timedelta(days=1, seconds=-1))
    assert case.run(NOW + timedelta(days=1))
    assert work.call_count == 2


def test_restart_uses_persisted_success_date(case, monkeypatch):
    work = Mock(return_value=True)
    monkeypatch.setattr(nightly, "_cleanup_all", work)
    assert case.run()
    restarted_engine = create_engine(case.engine.url)
    restarted_factory = sessionmaker(bind=restarted_engine)
    try:
        assert not nightly.run_avatar_cleanup_once(session_factory=restarted_factory, now_utc=NOW + timedelta(hours=2))
    finally:
        restarted_engine.dispose()
    assert work.call_count == 1


def test_run_finishing_after_midnight_counts_as_success_on_completion_day(case, monkeypatch):
    clock = Mock()
    clock.now.side_effect = [NOW + timedelta(hours=20), NOW + timedelta(hours=22)]
    monkeypatch.setattr(nightly, "datetime", clock)
    monkeypatch.setattr(nightly, "_cleanup_all", Mock(return_value=True))
    assert nightly.run_avatar_cleanup_once(session_factory=case.factory)
    with case.factory() as db:
        assert db.get(TelegramAvatarCleanupState, 1).last_success_on == date(2026, 10, 21)
    assert not case.run(NOW + timedelta(days=1))


def test_missed_first_run_is_caught_up_after_downtime_before_next_0300(case, monkeypatch):
    work = Mock(return_value=True)
    monkeypatch.setattr(nightly, "_cleanup_all", work)
    assert not case.run(NOW - timedelta(hours=1))  # Persist the first planned date.
    assert case.run(NOW + timedelta(hours=23))  # Next day 02:00, yesterday's run was missed.
    assert not case.run(NOW + timedelta(days=1))  # At most one success on this calendar day.
    assert work.call_count == 1


def test_start_after_0300_catches_up_and_collapses_multiple_missed_days(case, monkeypatch):
    work = Mock(return_value=True)
    monkeypatch.setattr(nightly, "_cleanup_all", work)
    assert case.run(NOW + timedelta(hours=8))
    assert case.run(NOW + timedelta(days=5, hours=12))
    assert not case.run(NOW + timedelta(days=5, hours=13))
    assert work.call_count == 2


@pytest.mark.parametrize("age,expired", [(366, True), (365, True), (364, False), (None, False)])
def test_retention_uses_last_ticket_created_at_not_other_dates(case, age, expired):
    path = case.seed(age=age)
    assert case.run()
    thumbnail, identity, full_path, updated = _avatar(case)
    assert full_path is None and not path.exists()
    assert thumbnail == (None if expired else b"test-thumbnail")
    assert identity == (None if expired else "photo")
    assert (updated is None) == expired
    with case.factory() as db:
        assert db.get(Client, "client") is not None
        if age is not None:
            assert db.get(Ticket, "client").subject == "History"


def test_recent_ticket_prevents_expiration_of_thumbnail_from_old_ticket(case):
    case.seed()
    with case.factory() as db:
        db.execute(Ticket.__table__.insert().values(id="recent", company_id="company", client_id="client",
                                                  subject="Recent", status="closed", created_at=NOW - timedelta(days=2)))
        db.commit()
    assert case.run()
    assert _avatar(case)[:2] == (b"test-thumbnail", "photo")


def test_new_intake_after_expiration_can_download_and_save_avatar_again(case):
    case.seed()
    assert case.run()
    with case.factory() as db:
        contact = db.get(TelegramContact, "client")
        assert storage.needs_avatar_download(contact, "photo")
        db.add(Ticket(id="new", company_id="company", client_id="client", subject="New", status="new"))
        db.commit()
    with case.factory() as db:
        assert storage.save_avatar(db, "client", "photo", b"new-photo-bytes", require_open_ticket=True)
    assert _avatar(case)[:2] == (b"test-thumbnail", "photo")


@pytest.mark.parametrize("fields", [
    {"status": "pending"}, {"status": "new", "is_frozen": True},
    {"status": "unassigned", "workflow_type": "task"},
    {"status": "pending", "workflow_type": "task", "task_kind": "group_parent"},
    {"status": "pending", "workflow_type": "task", "task_kind": "group_child", "parent_ticket_id": "parent"},
])
def test_any_open_frozen_or_group_task_preserves_every_avatar_field(case, fields):
    path = case.seed(**fields)
    before = _avatar(case)
    assert case.run()
    assert _avatar(case) == before and path.is_file()


def test_orphans_respect_grace_registration_and_storage_boundary(case, tmp_path):
    active = case.seed(status="pending")
    old = case.directory / f"{uuid.uuid4().hex}.jpg"
    recent = case.directory / f"{uuid.uuid4().hex}.webp"
    unrelated = case.directory / "unrelated.dat"
    employee = tmp_path / "avatars" / "employee.dat"
    attachment = tmp_path / "attachments" / "document.dat"
    nested = case.directory / "nested"
    nested.mkdir()
    nested_file = nested / f"{uuid.uuid4().hex}.jpg"
    for path in [old, recent, unrelated, employee, attachment, nested_file]:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"test-bytes")
        timestamp = (NOW - timedelta(hours=25)).timestamp()
        os.utime(path, (timestamp, timestamp))
    os.utime(recent, (NOW.timestamp(), NOW.timestamp()))
    assert case.run()
    assert not old.exists()
    assert all(path.exists() for path in [active, recent, unrelated, employee, attachment, nested_file])


def test_orphan_symlinks_are_not_followed(case, tmp_path):
    target = tmp_path / "employee.dat"
    target.write_bytes(b"employee")
    link = case.directory / f"{uuid.uuid4().hex}.jpg"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows account")
    assert case.run()
    assert target.read_bytes() == b"employee" and link.is_symlink()


def test_file_being_saved_is_not_mistaken_for_orphan(case):
    case.seed(status="pending")
    checked = []
    with case.factory() as db:
        def before_commit(_db):
            contact = db.get(TelegramContact, "client")
            path = storage._resolve_path(contact.avatar_path)
            os.utime(path, (NOW.timestamp(), NOW.timestamp()))
            assert nightly._cleanup_orphans(case.factory, NOW)
            assert path.is_file()
            checked.append(path)

        event.listen(db, "before_commit", before_commit)
        assert storage.save_avatar(db, "client", "new-photo", b"new-test-bytes", require_open_ticket=True)
    assert len(checked) == 1 and checked[0].is_file()
    assert _avatar(case)[1] == "new-photo"


def test_request_committed_before_cleanup_contact_lock_is_rechecked(case, monkeypatch):
    path = case.seed()
    original = nightly._locked_contact

    def new_request_then_lock(db, contact_id):
        with case.factory() as intake:
            intake.add(Ticket(id="new-request", company_id="company", client_id="client",
                              subject="New request", status="new", created_at=NOW))
            intake.commit()
        return original(db, contact_id)

    monkeypatch.setattr(nightly, "_locked_contact", new_request_then_lock)
    before = _avatar(case)
    assert case.run()
    assert _avatar(case) == before and path.exists()


def test_new_request_acquires_contact_lock_before_insert(case):
    case.seed()
    statements = []
    with case.factory() as db:
        event.listen(db, "do_orm_execute", lambda state: statements.append(
            str(state.statement.compile(dialect=postgresql.dialect()))))
        db.add(Ticket(id="new", company_id="company", client_id="client", subject="New", status="new"))
        event.listen(db, "after_flush", lambda *_: statements.append("FLUSHED"))
        db.commit()
    lock = next(i for i, sql in enumerate(statements) if "telegram_contacts" in sql and "FOR UPDATE" in sql)
    assert lock < statements.index("FLUSHED")


def test_one_contact_failure_does_not_rollback_others_or_mark_success(case, monkeypatch, caplog):
    bad_path = case.seed("bad")
    good_path = case.seed("good")
    original = nightly._cleanup_contact

    def cleanup(db, contact_id, cutoff):
        if contact_id == "bad":
            raise RuntimeError("private-token-and-path")
        return original(db, contact_id, cutoff)

    monkeypatch.setattr(nightly, "_cleanup_contact", cleanup)
    assert not case.run()
    assert bad_path.exists() and not good_path.exists()
    assert _avatar(case, "bad")[0] == b"test-thumbnail"
    assert _avatar(case, "good") == (None, None, None, None)
    with case.factory() as db:
        assert db.get(TelegramAvatarCleanupState, 1).last_success_on is None
    assert "private-token-and-path" not in caplog.text
    monkeypatch.setattr(nightly, "_cleanup_contact", original)
    assert case.run() and not bad_path.exists()


def test_second_instance_skips_job_when_postgres_lock_is_owned(case, monkeypatch):
    lock = Mock(side_effect=[True, False])
    monkeypatch.setattr(nightly, "_try_job_lock", lock)

    def work(*_args):
        assert not case.run()
        return True

    work_mock = Mock(side_effect=work)
    monkeypatch.setattr(nightly, "_cleanup_all", work_mock)
    assert case.run()
    assert work_mock.call_count == 1


@pytest.mark.parametrize("acquired", [False, True])
def test_actual_postgres_lock_is_nonblocking_and_transaction_scoped(acquired):
    from api.app.services.telegram_client_avatar_nightly import _try_job_lock
    db = Mock()
    db.scalar.return_value = acquired
    assert _try_job_lock(db) is acquired
    statement, params = db.scalar.call_args.args
    assert "pg_try_advisory_xact_lock" in str(statement)
    assert params == {"key": "telegram_client_avatar_nightly"}


def test_schema_evolution_is_repeatable_and_retains_job_state(case, monkeypatch):
    monkeypatch.setattr(schema_evolution, "engine", case.engine)
    TelegramAvatarCleanupState.__table__.drop(case.engine)
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    assert case.run()
    schema_evolution.ensure_telegram_avatar_cleanup_state_table()
    with case.factory() as db:
        assert db.get(TelegramAvatarCleanupState, 1).last_success_on == date(2026, 10, 20)
    ddl = str(CreateTable(TelegramAvatarCleanupState.__table__).compile(dialect=postgresql.dialect()))
    assert "last_success_on DATE" in ddl and "next_run_on DATE" in ddl


def test_nightly_failure_does_not_stop_existing_scheduler_passes(monkeypatch):
    calls = []
    sleeps = []

    async def to_thread(fn):
        calls.append(fn.__name__)
        if fn is scheduler.run_avatar_cleanup_once:
            raise RuntimeError("failure")

    async def sleep(_seconds):
        sleeps.append(True)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.internal_task_scheduler_loop())
    assert calls == ["run_internal_task_scheduler_once", "run_internal_task_recurring_once", "run_avatar_cleanup_once",
                     "run_telegram_after_hours_cleanup_once", "run_employee_auto_offline_once"] * 2
