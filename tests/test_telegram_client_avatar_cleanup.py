from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from api.app.auth import get_current_user
from api.app.db import Base, get_db
from api.app.models import Client, Company, TelegramContact, Ticket, TicketMessage
from api.app.routers import ticket_client_avatars as avatar_router
from api.app.services import auto_close, telegram_client_close
from api.app.services import telegram_client_avatar_cleanup as cleanup
from api.app.services import telegram_client_avatar_sync as sync
from api.app.services import telegram_client_avatars as storage
from api.app.services import ticket_client_avatars as viewer
from api.app.services.ticket_admin_actions import _delete_ticket_hard


@pytest.fixture
def case(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    directory = tmp_path / "telegram_client_avatars"
    monkeypatch.setattr(storage.settings, "telegram_client_avatar_storage_dir", str(directory))
    # Test storage/lifecycle only: no images are generated or decoded.
    monkeypatch.setattr(storage, "prepare_avatar", lambda _: (b"saved-thumbnail", "jpg"))
    monkeypatch.setattr(viewer, "prepare_avatar", lambda _: (b"saved-thumbnail", "jpg"))
    monkeypatch.setattr(sync, "SessionLocal", factory)
    monkeypatch.setattr(sync.settings, "telegram_bot_token", "test-token")
    api = Mock(side_effect=AssertionError("Unexpected Telegram call"))
    monkeypatch.setattr(sync, "telegram_request", api)
    with factory() as db:
        db.add_all([
            Company(id="company", name="Company"),
            Client(id="client", company_id="company", display_name="Client"),
            TelegramContact(id="contact", company_id="company", client_id="client",
                            external_user_id="123", external_chat_id="123"),
        ])
        ticket = Ticket(id="ticket", company_id="company", client_id="client", subject="Test", status="pending")
        db.add(ticket)
        db.add(TicketMessage(id="message", ticket_id=ticket.id, message_type="client", body="Request"))
        db.commit()
        storage.save_avatar(db, "contact", "photo-unique", b"full-photo-test-bytes")
        contact = db.get(TelegramContact, "contact")
        path = storage._resolve_path(contact.avatar_path)
        snapshot = (contact.avatar_thumbnail, contact.avatar_file_unique_id, contact.avatar_updated_at,
                    contact.external_user_id, contact.external_chat_id, contact.updated_at)
        yield SimpleNamespace(db=db, factory=factory, ticket=ticket, directory=directory,
                              path=path, snapshot=snapshot, api=api)
    engine.dispose()


def _assert_clean(case):
    case.db.expire_all()
    contact = case.db.get(TelegramContact, "contact")
    assert contact.avatar_path is None
    assert not case.path.exists()
    assert (contact.avatar_thumbnail, contact.avatar_file_unique_id, contact.avatar_updated_at,
            contact.external_user_id, contact.external_chat_id, contact.updated_at) == case.snapshot


def _other_ticket(case, **kwargs):
    values = dict(id="other", company_id="company", client_id="client", subject="Other", status="pending")
    values.update(kwargs)
    ticket = Ticket(**values)
    case.db.add(ticket)
    case.db.commit()
    return ticket


def test_close_last_ticket_cleans_only_after_commit_in_separate_session(case, monkeypatch):
    sessions = []
    original = cleanup.delete_full_avatar

    def record(db, *args, **kwargs):
        assert db is not case.db
        assert db.get(Ticket, "ticket").status == "closed"
        sessions.append(db)
        return original(db, *args, **kwargs)

    monkeypatch.setattr(cleanup, "delete_full_avatar", record)
    case.ticket.status = "closed"
    case.db.flush()
    assert case.path.exists() and not sessions
    case.db.commit()
    _assert_clean(case)
    assert len(sessions) == 1
    assert case.db.get(TicketMessage, "message").body == "Request"
    case.api.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"status": "new"}, {"status": "unassigned"}, {"is_frozen": True},
    {"workflow_type": "task"}, {"workflow_type": "auto"}, {"intake_state": "collecting"},
    {"workflow_type": "task", "task_kind": "group_parent"},
    {"workflow_type": "task", "task_kind": "group_child", "parent_ticket_id": "ticket"},
])
def test_any_unclosed_ticket_keeps_full_photo(case, overrides):
    other = _other_ticket(case, **overrides)
    case.ticket.status = "closed"
    case.db.commit()
    assert case.path.is_file()
    case.db.refresh(case.db.get(TelegramContact, "contact"))
    assert case.db.get(TelegramContact, "contact").avatar_path
    other.status = "closed"
    case.db.commit()
    _assert_clean(case)


@pytest.mark.parametrize("action", ["close", "delete"])
def test_rollback_does_not_delete_photo_or_leak_pending_cleanup(case, action):
    if action == "close":
        case.ticket.status = "closed"
    else:
        _delete_ticket_hard(case.db, case.ticket)
    case.db.flush()
    case.db.rollback()
    assert case.path.exists()
    assert cleanup._PENDING_KEY not in case.db.info
    case.db.get(Ticket, "ticket").subject = "Still active"
    case.db.commit()
    assert case.path.exists()


@pytest.mark.parametrize("group", [False, True])
def test_hard_delete_last_ticket_and_sql_deleted_children(case, group):
    if group:
        case.ticket.workflow_type = "task"
        case.ticket.task_kind = "group_parent"
        _other_ticket(case, workflow_type="task", task_kind="group_child", parent_ticket_id="ticket")
    assert _delete_ticket_hard(case.db, case.ticket) == []
    assert case.path.exists()
    case.db.commit()
    _assert_clean(case)
    assert case.db.scalar(select(Ticket.id)) is None


def test_delete_one_of_many_keeps_photo(case):
    _other_ticket(case, is_frozen=True)
    _delete_ticket_hard(case.db, case.ticket)
    case.db.commit()
    assert case.path.exists()


def test_group_sql_delete_registers_child_client_not_only_parent(case):
    case.db.add(Client(id="parent-client", company_id="company", display_name="Parent"))
    parent = _other_ticket(case, client_id="parent-client", workflow_type="task", task_kind="group_parent")
    case.ticket.parent_ticket_id = parent.id
    case.ticket.task_kind = "group_child"
    case.db.commit()
    _delete_ticket_hard(case.db, parent)
    case.db.commit()
    _assert_clean(case)


@pytest.mark.parametrize("missing", [False, True])
def test_cleanup_is_idempotent_even_without_physical_file(case, missing):
    if missing:
        case.path.unlink()
    case.ticket.status = "closed"
    case.db.commit()
    for _ in range(2):
        cleanup.schedule_avatar_cleanup(case.db, "company", "client")
        case.db.commit()
    _assert_clean(case)


def test_unrelated_storage_and_other_clients_are_untouched(case, tmp_path):
    employee = tmp_path / "avatars" / "employee.dat"
    employee.parent.mkdir()
    employee.write_bytes(b"employee-avatar-test-bytes")
    unrelated = case.directory / "unrelated.dat"
    unrelated.write_bytes(b"unrelated")
    case.db.add_all([
        Client(id="other-client", company_id="company", display_name="Other"),
        TelegramContact(id="other-contact", company_id="company", client_id="other-client",
                        external_user_id="456", external_chat_id="456"),
    ])
    case.db.commit()
    storage.save_avatar(case.db, "other-contact", "other-photo", b"other-photo-test-bytes")
    other_path = storage._resolve_path(case.db.get(TelegramContact, "other-contact").avatar_path)
    case.ticket.status = "closed"
    case.db.commit()
    _assert_clean(case)
    assert employee.read_bytes() == b"employee-avatar-test-bytes"
    assert unrelated.read_bytes() == b"unrelated"
    assert other_path.is_file()


@pytest.mark.parametrize("commit_savepoint", [False, True])
def test_savepoint_never_removes_file_before_outer_commit(case, commit_savepoint):
    nested = case.db.begin_nested()
    case.ticket.status = "closed"
    case.db.flush()
    if commit_savepoint:
        nested.commit()
    else:
        nested.rollback()
    assert case.path.exists()
    case.db.commit()
    if commit_savepoint:
        _assert_clean(case)
    else:
        assert case.path.exists()


def test_closing_then_reopening_in_same_transaction_keeps_photo(case):
    case.ticket.status = "closed"
    case.db.flush()
    case.ticket.status = "pending"
    case.db.commit()
    assert case.path.exists()


@pytest.mark.parametrize("unique_id", ["new-photo", "photo-unique"])
def test_late_background_download_cannot_recreate_full_photo(case, monkeypatch, unique_id):
    if unique_id == "photo-unique":
        case.path.unlink()  # Restoration of the same photo also races with closing.

    def result(method, *_args):
        if method == "getUserProfilePhotos":
            return {"total_count": 1, "photos": [[{
                "width": 600, "height": 600, "file_id": "file", "file_unique_id": unique_id,
            }]]}
        return {"file_path": "photos/photo.jpg"}

    def download(*_args):
        # Commit the closure while the background worker is waiting for network bytes.
        case.ticket.status = "closed"
        case.db.commit()
        assert not case.path.exists()
        return b"late-photo-test-bytes"

    monkeypatch.setattr(sync, "_result", result)
    monkeypatch.setattr(sync, "_download", download)
    sync._refresh("ticket")
    _assert_clean(case)
    assert list(case.directory.iterdir()) == []


def test_download_committed_before_close_is_cleaned_and_new_intake_can_restore(case):
    with case.factory() as db:
        storage.delete_full_avatar(db, "contact")
        assert storage.save_avatar(db, "contact", "photo-unique", b"photo", require_open_ticket=True)
        saved_path = storage._resolve_path(db.get(TelegramContact, "contact").avatar_path)
    case.ticket.status = "closed"
    case.db.commit()
    _assert_clean(case)
    assert not saved_path.exists()
    with case.factory() as db:
        assert not storage.save_avatar(db, "contact", "photo-unique", b"photo", require_open_ticket=True)
    _other_ticket(case)
    with case.factory() as db:
        assert storage.save_avatar(db, "contact", "photo-unique", b"restored", require_open_ticket=True)
        contact = db.get(TelegramContact, "contact")
        assert contact.avatar_thumbnail == case.snapshot[0]
        assert storage._resolve_path(contact.avatar_path).read_bytes() == b"restored"


@pytest.mark.parametrize("operation", ["delete", "save"])
def test_open_ticket_checks_follow_postgres_contact_row_lock(case, operation):
    statements = []

    def capture(state):
        statements.append(str(state.statement.compile(dialect=postgresql.dialect())))

    with case.factory() as db:
        event.listen(db, "do_orm_execute", capture)
        if operation == "delete":
            storage.delete_full_avatar(db, "contact", only_if_inactive=True)
        else:
            storage.save_avatar(db, "contact", "photo-unique", b"photo", require_open_ticket=True)
    assert any("FOR UPDATE" in statement for statement in statements)
    lock = next(i for i, sql in enumerate(statements) if "FOR UPDATE" in sql)
    check = next(i for i, sql in enumerate(statements) if "FROM tickets" in sql)
    assert lock < check
    assert case.path.exists()


def test_failed_commit_does_not_remove_file(case):
    def fail(*_args):
        raise RuntimeError("Commit failed")

    case.ticket.status = "closed"
    case.db.flush()
    event.listen(case.db, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="Commit failed"):
            case.db.commit()
    finally:
        event.remove(case.db, "before_commit", fail)
        case.db.rollback()
    assert case.path.exists()
    assert case.db.get(Ticket, "ticket").status == "pending"


def test_outer_rollback_after_savepoint_commit_keeps_photo(case):
    # Explicit outer write makes SQLite hold a real outer transaction before SAVEPOINT.
    case.ticket.subject = "Changed"
    case.db.flush()
    with case.db.begin_nested():
        case.ticket.status = "closed"
    assert case.path.exists()
    case.db.rollback()
    assert case.path.exists()
    assert case.db.get(Ticket, "ticket").status == "pending"


def test_cleanup_failure_does_not_fail_ticket_commit_or_log_private_details(case, monkeypatch, caplog):
    original = cleanup.delete_full_avatar
    monkeypatch.setattr(cleanup, "delete_full_avatar", Mock(side_effect=RuntimeError("secret-token-and-path")))
    case.ticket.status = "closed"
    case.db.commit()
    assert case.db.get(Ticket, "ticket").status == "closed"
    assert case.path.exists()
    assert "cleanup failed" in caplog.text and "secret-token-and-path" not in caplog.text
    monkeypatch.setattr(cleanup, "delete_full_avatar", original)
    cleanup.schedule_avatar_cleanup(case.db, "company", "client")
    case.db.commit()
    _assert_clean(case)


@pytest.mark.parametrize("route", ["auto_close", "client_close"])
def test_real_close_services_trigger_common_cleanup(case, monkeypatch, route):
    if route == "auto_close":
        case.ticket.status = "new"
        case.ticket.auto_close_due_at = datetime.utcnow() - timedelta(minutes=1)
        case.db.commit()
        monkeypatch.setattr(auto_close, "send_message", Mock())
        assert auto_close.cleanup_auto_close_tickets(case.db) == 1
    else:
        telegram_client_close.close_active_ticket_for_contact(
            case.db, case.db.get(TelegramContact, "contact"),
            active_ticket_for_client_fn=lambda *_: case.ticket,
            collecting_ticket_for_client_fn=lambda *_: None,
            remove_menu_keyboard_fn=Mock(), remove_client_close_button_fn=Mock(),
            append_system_message_fn=Mock(),
        )
        assert case.path.exists()
        case.db.commit()
    _assert_clean(case)


@pytest.mark.parametrize("identity", ["photo-unique", "changed", None])
def test_closed_ticket_view_keeps_thumbnail_and_never_persists_temporary_full_photo(case, monkeypatch, identity):
    case.ticket.status = "closed"
    case.db.commit()
    result = Mock(side_effect=lambda method, *_: {
        "total_count": int(identity is not None),
        "photos": [[{"width": 600, "height": 600, "file_id": "file", "file_unique_id": identity}]],
    } if method == "getUserProfilePhotos" else {"file_path": "photos/photo.jpg"})
    download = Mock(return_value=b"temporary-photo-test-bytes")
    monkeypatch.setattr(viewer, "_result", result)
    monkeypatch.setattr(viewer, "_download", download)
    app = FastAPI()
    app.include_router(avatar_router.router)
    app.dependency_overrides[get_db] = lambda: case.db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="admin", role="admin")
    with TestClient(app) as client:
        thumbnail = client.get("/api/v1/tickets/ticket/client-avatar/thumbnail")
        assert thumbnail.status_code == 200 and thumbnail.content == case.snapshot[0]
        result.assert_not_called()
        full = client.get("/api/v1/tickets/ticket/client-avatar/full")
    assert full.status_code == (200 if identity == "photo-unique" else 404)
    if identity == "photo-unique":
        assert full.content == b"temporary-photo-test-bytes"
    else:
        download.assert_not_called()
    _assert_clean(case)
    assert list(case.directory.iterdir()) == []
