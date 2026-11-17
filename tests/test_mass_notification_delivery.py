from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientProfile, Company,
    MassNotificationOperation as Operation, MassNotificationRecipient as Recipient, TelegramContact,
)
from api.app.services import mass_notification_delivery as delivery
from api.app.services import mass_notification_staging as staging
from api.app.services.mass_notification_operations import create_mass_notification_operation
from api.app.services.telegram_bot import TelegramApiError


def confirmed(message_id):
    return {"ok": True, "result": {"message_id": message_id}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'delivery.db').as_posix()}")
    Base.metadata.create_all(engine, tables=[
        model.__table__ for model in (
            Company, Client, ClientProfile, TelegramContact, ClientCardBlockConfig,
            ClientCardFieldConfig, Operation, Recipient,
        )
    ])
    sessions = sessionmaker(engine)
    workers = []

    def worker_session():
        db = sessions()
        workers.append(db)
        return db

    now = 0.0

    def advance(seconds):
        nonlocal now
        assert not workers[-1].in_transaction()
        now += seconds

    monkeypatch.setattr(delivery, "SessionLocal", worker_session)
    monkeypatch.setattr(delivery, "time", SimpleNamespace(monotonic=lambda: now, sleep=advance))
    monkeypatch.setattr(delivery.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(delivery.settings, "telegram_staging_chat_id", "test-staging")
    mocks = SimpleNamespace(
        send=Mock(return_value=confirmed(101)), copy=Mock(return_value=confirmed(102)),
        photo=Mock(return_value=confirmed(201)), document=Mock(return_value=confirmed(202)),
    )
    monkeypatch.setattr(delivery, "send_message", mocks.send)
    monkeypatch.setattr(delivery, "copy_message", mocks.copy)
    monkeypatch.setattr(staging, "send_photo", mocks.photo)
    monkeypatch.setattr(staging, "send_document", mocks.document)
    with sessions.begin() as db:
        db.add(Company(id="company", name="Test"))
        db.add(ClientCardBlockConfig(id="block", key="routing", title="Routing"))
        db.add(ClientCardFieldConfig(
            id="field", key="destination", label="Destination", field_type="select",
            block_key="routing", options_json=["Support", "Sales"],
        ))
        for number in range(1, 7):
            key = str(number)
            db.add_all([
                Client(id=key, company_id="company", display_name=f"Client {key}"),
                ClientProfile(id=key, client_id=key, is_verified=True, destination="Support"),
                TelegramContact(
                    id=key, company_id="company", client_id=key, external_user_id=key, external_chat_id=f"chat-{key}",
                ),
            ])
    root = tmp_path / "es-desk-mass-notifications"

    def create(*, files=(), filters=None, chat_count=2, text="<b>Alert</b>"):
        attachments = [
            {
                "stored_name": f"{index:032x}", "original_filename": name, "mime_type": mime_type,
                "file_size": 4, "status": "pending", "staging_chat_id": None, "staging_message_id": None, "error": None,
            }
            for index, (name, mime_type) in enumerate(files)
        ]
        with sessions() as db:
            operation, _ = create_mass_notification_operation(
                db, "admin", "request", "a" * 64, text, filters or {}, attachments,
                [{"client_id": str(i), "chat_id": f"chat-{i}", "client_name": f"Client {i}"}
                 for i in range(1, chat_count + 1)],
            )
            operation_id = operation.id
        directory = root / operation_id
        directory.mkdir(parents=True)
        for attachment in attachments:
            (directory / attachment["stored_name"]).write_bytes(b"test")
        return operation_id, directory, attachments

    def read(operation_id):
        with sessions() as db:
            return db.get(Operation, operation_id), db.scalars(
                select(Recipient).where(Recipient.operation_id == operation_id).order_by(Recipient.chat_id)
            ).all()

    yield SimpleNamespace(
        create=create, read=read, sessions=sessions, workers=workers, mocks=mocks, clock=lambda: now,
    )
    engine.dispose()


def assert_no_telegram(env):
    for mock in vars(env.mocks).values():
        mock.assert_not_called()


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_nonqueued_operation_is_never_sent_or_cleaned(env, status):
    operation_id, directory, _ = env.create()
    with env.sessions.begin() as db:
        db.get(Operation, operation_id).status = status
    delivery.run_mass_notification_operation(operation_id)
    assert env.read(operation_id)[0].status == status
    assert directory.is_dir()
    assert_no_telegram(env)


def test_text_only_claimed_once_and_sent_as_html_without_staging(env, monkeypatch):
    monkeypatch.setattr(delivery.settings, "telegram_staging_chat_id", None)
    operation_id, directory, _ = env.create(chat_count=1)
    sibling = directory.parent / "another-operation"
    sibling.mkdir()
    (sibling / "keep").touch()

    def send(*args, **kwargs):
        assert not env.workers[0].in_transaction()
        assert env.read(operation_id)[1][0].components_json[0]["status"] == "sending"
        delivery.run_mass_notification_operation(operation_id)
        return confirmed(42)

    env.mocks.send.side_effect = send
    delivery.run_mass_notification_operation(operation_id)
    delivery.run_mass_notification_operation(operation_id)

    env.mocks.send.assert_called_once_with("chat-1", "<b>Alert</b>", parse_mode="HTML", retry_only_parse_errors=True)
    env.mocks.photo.assert_not_called()
    env.mocks.document.assert_not_called()
    operation, recipients = env.read(operation_id)
    assert operation.status == operation.stage == "completed"
    assert operation.finished_at is not None
    assert recipients[0].components_json[0]["telegram_message_id"] == 42
    assert not directory.exists()
    assert (sibling / "keep").is_file()


def test_stages_photo_and_document_once_before_delivery_and_throttles_calls(env):
    operation_id, directory, _ = env.create(files=[("a.png", "image/png"), ("b.pdf", "application/pdf")])
    requests = []

    def stage(index, message_id):
        def mock(**kwargs):
            assert not env.workers[-1].in_transaction()
            assert env.read(operation_id)[0].attachments_json[index]["status"] == "uploading"
            env.mocks.send.assert_not_called()
            env.mocks.copy.assert_not_called()
            return confirmed(message_id)
        return mock

    def send(chat_id, *args, **kwargs):
        assert not env.workers[-1].in_transaction()
        assert [item["status"] for item in env.read(operation_id)[0].attachments_json] == ["ready", "ready"]
        requests.append((chat_id, env.clock()))
        return confirmed(100 + len(requests))

    env.mocks.photo.side_effect = stage(0, 201)
    env.mocks.document.side_effect = stage(1, 202)
    env.mocks.send.side_effect = env.mocks.copy.side_effect = send
    delivery.run_mass_notification_operation(operation_id)

    env.mocks.photo.assert_called_once_with(
        chat_id="test-staging", content=b"test", filename="a.png", mime_type="image/png",
    )
    env.mocks.document.assert_called_once_with(
        chat_id="test-staging", file_path=directory / f"{1:032x}", filename="b.pdf", mime_type="application/pdf",
    )
    assert [entry.args[0] for entry in env.mocks.send.call_args_list] == list(dict.fromkeys(chat for chat, _ in requests))
    assert env.mocks.copy.call_args_list == [
        call(chat_id=chat, from_chat_id="test-staging", message_id=message_id)
        for chat in dict.fromkeys(chat for chat, _ in requests)
        for message_id in (201, 202)
    ]
    assert len(requests) == 6
    for (first_chat, first_time), (second_chat, second_time) in zip(requests, requests[1:]):
        expected = 1.0 if first_chat == second_chat else 0.05
        assert second_time - first_time == pytest.approx(expected)
    operation, recipients = env.read(operation_id)
    assert operation.status == "completed"
    assert [attachment["staging_message_id"] for attachment in operation.attachments_json] == [201, 202]
    assert {component["telegram_message_id"] for row in recipients for component in row.components_json} == set(range(101, 107))
    assert not directory.exists()


@pytest.mark.parametrize("invalid", ["no-chat", "missing", "empty", "oversize", "traversal", "file-link", "directory-link"])
def test_preflight_checks_every_file_before_first_upload(env, monkeypatch, tmp_path, invalid):
    operation_id, directory, attachments = env.create(files=[("a.pdf", None), ("b.png", None)])
    path = directory / attachments[1]["stored_name"]
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.write_bytes(b"test")
    if invalid == "no-chat":
        monkeypatch.setattr(delivery.settings, "telegram_staging_chat_id", " ")
    elif invalid == "missing":
        path.unlink()
    elif invalid == "empty":
        path.write_bytes(b"")
    elif invalid == "oversize":
        with path.open("wb") as file:
            file.truncate(10 * 1024 ** 2 + 1)
    elif invalid == "traversal":
        attachments[1]["stored_name"] = "../outside/keep"
        with env.sessions.begin() as db:
            db.get(Operation, operation_id).attachments_json = attachments
    else:
        link = path if invalid == "file-link" else directory
        if invalid == "file-link":
            path.unlink()
        else:
            directory.rename(tmp_path / "moved")
        try:
            link.symlink_to(marker if invalid == "file-link" else outside, target_is_directory=invalid == "directory-link")
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            # Windows may require a privilege not granted to the test process.
            is_symlink = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda current: current == link or is_symlink(current))
    delivery.run_mass_notification_operation(operation_id)

    assert_no_telegram(env)
    operation, recipients = env.read(operation_id)
    assert operation.status == "failed"
    assert operation.error_message
    assert all(recipient.status == "pending" for recipient in recipients)
    assert "failed" in [item["status"] for item in operation.attachments_json]
    if invalid != "directory-link":
        assert not directory.exists()
    assert marker.read_bytes() == b"test"


def test_noncanonical_uuid_cannot_be_used_for_preparation(env):
    operation_id, directory, _ = env.create(files=[("a.pdf", None)])
    invalid_id = operation_id.upper()
    with env.sessions.begin() as db:
        db.execute(update(Operation).where(Operation.id == operation_id).values(id=invalid_id))
    delivery.run_mass_notification_operation(invalid_id)
    operation, _ = env.read(invalid_id)
    assert operation.status == operation.attachments_json[0]["status"] == "failed"
    assert_no_telegram(env)
    assert directory.exists()


@pytest.mark.parametrize("status", ["uploading", "ready", "failed", "unknown"])
def test_prior_attachment_state_never_triggers_an_upload(env, status):
    operation_id, _, attachments = env.create(files=[("a.pdf", None)])
    attachments[0]["status"] = status
    with env.sessions.begin() as db:
        db.get(Operation, operation_id).attachments_json = attachments
    delivery.run_mass_notification_operation(operation_id)
    result, _ = env.read(operation_id)
    assert result.status == "failed"
    assert result.attachments_json[0]["status"] == status
    assert_no_telegram(env)


@pytest.mark.parametrize("response", [None, {"result": {}}])
def test_unconfirmed_staging_stops_delivery_without_a_retry(env, response):
    operation_id, _, _ = env.create(files=[("a.pdf", None), ("b.png", None)])
    if response is None:
        env.mocks.document.side_effect = TimeoutError("staging timeout")
    else:
        env.mocks.document.return_value = response
    delivery.run_mass_notification_operation(operation_id)
    delivery.run_mass_notification_operation(operation_id)
    operation, recipients = env.read(operation_id)
    assert operation.status == "failed"
    assert operation.attachments_json[0]["status"] == "unknown"
    assert operation.attachments_json[0]["error"]
    assert operation.attachments_json[1]["status"] == "pending"
    assert all(row.status == "pending" for row in recipients)
    env.mocks.document.assert_called_once()
    env.mocks.photo.assert_not_called()
    env.mocks.copy.assert_not_called()
    env.mocks.send.assert_not_called()


@pytest.mark.parametrize(("filters", "expected_status"), [
    ({"destination": "Support"}, "skipped"), ({}, "sent"),
])
def test_eligibility_is_rechecked_against_live_configuration_and_original_contact(env, filters, expected_status):
    operation_id, _, _ = env.create(filters=filters, chat_count=5)
    visited = []

    def send(chat_id, *args, **kwargs):
        visited.append(chat_id)
        if len(visited) == 1:
            # Change eligibility only after this operation has already started delivering.
            others = [str(i) for i in range(1, 6) if f"chat-{i}" != chat_id]
            with env.sessions.begin() as db:
                db.get(ClientProfile, others[0]).is_verified = False
                db.get(TelegramContact, others[1]).is_blocked = True
                db.get(ClientProfile, others[2]).destination = "Sales"
                db.get(TelegramContact, others[3]).external_chat_id = "changed-chat"
            expected.update(dict(zip(others, ["skipped", "skipped", expected_status, "skipped"])))
            expected[chat_id[-1]] = "sent"
        return confirmed(31)

    expected = {}
    env.mocks.send.side_effect = send
    delivery.run_mass_notification_operation(operation_id)
    operation, rows = env.read(operation_id)
    assert operation.status == "completed"
    assert {row.client_id: row.status for row in rows} == expected
    assert sorted(visited) == sorted(f"chat-{key}" for key, status in expected.items() if status == "sent")


def test_filter_configuration_error_fails_closed(env):
    operation_id, _, _ = env.create(filters={"destination": "Support"})
    with env.sessions.begin() as db:
        db.get(ClientCardBlockConfig, "block").is_active = False
    delivery.run_mass_notification_operation(operation_id)
    operation, recipients = env.read(operation_id)
    assert operation.status == "failed"
    assert operation.stage == "sending"
    assert all(row.status == "pending" for row in recipients)
    assert_no_telegram(env)


@pytest.mark.parametrize(("error", "status"), [
    (TimeoutError("network"), "unknown"),
    (TelegramApiError("unspecified Telegram error"), "unknown"),
    (TelegramApiError("Forbidden: bot was blocked by the user"), "failed"),
    (None, "unknown"),
])
def test_copy_error_is_recorded_once_and_other_components_continue(env, error, status):
    operation_id, _, _ = env.create(files=[("a.pdf", None), ("b.pdf", None)], chat_count=1)
    env.mocks.copy.side_effect = [error if error is not None else {}, confirmed(103)]
    delivery.run_mass_notification_operation(operation_id)
    operation, rows = env.read(operation_id)
    assert operation.status == "completed"
    assert rows[0].status == "partial"
    assert [component["status"] for component in rows[0].components_json] == ["sent", status, "sent"]
    assert rows[0].components_json[1]["error"]
    assert env.mocks.copy.call_count == 2


def test_commit_failure_after_telegram_confirmation_does_not_continue(env):
    operation_id, directory, _ = env.create()

    def send(*args, **kwargs):
        def fail_commit(db):
            raise OperationalError("commit", {}, RuntimeError("DB unavailable"))
        event.listen(env.workers[-1], "before_commit", fail_commit, once=True)
        return confirmed(42)

    env.mocks.send.side_effect = send
    delivery.run_mass_notification_operation(operation_id)
    delivery.run_mass_notification_operation(operation_id)
    env.mocks.send.assert_called_once()
    operation, recipients = env.read(operation_id)
    assert operation.status == "running"
    assert sorted(row.status for row in recipients) == ["pending", "sending"]
    assert all(row.components_json[0]["telegram_message_id"] is None for row in recipients)
    assert directory.exists()


@pytest.mark.parametrize("message", ["Too Many Requests: retry after 5", "retry after 5"])
def test_429_records_unknown_and_stops_without_retry(env, message):
    operation_id, directory, _ = env.create()
    env.mocks.send.side_effect = TelegramApiError(message)
    delivery.run_mass_notification_operation(operation_id)
    delivery.run_mass_notification_operation(operation_id)
    env.mocks.send.assert_called_once()
    operation, recipients = env.read(operation_id)
    assert operation.status == "failed"
    assert operation.error_message == message
    assert sorted(row.status for row in recipients) == ["pending", "unknown"]
    assert not directory.exists()


def test_lost_worker_does_not_save_results_continue_or_clean(env):
    operation_id, directory, _ = env.create()

    def send(*args, **kwargs):
        with env.sessions.begin() as db:
            db.get(Operation, operation_id).worker_token = "new-owner"
        return confirmed(11)

    env.mocks.send.side_effect = send
    delivery.run_mass_notification_operation(operation_id)
    operation, recipients = env.read(operation_id)
    assert (operation.worker_token, operation.status, operation.finished_at) == ("new-owner", "running", None)
    assert sorted(row.status for row in recipients) == ["pending", "sending"]
    env.mocks.send.assert_called_once()
    assert directory.exists()


def test_operation_updates_require_matching_worker_token(env):
    operation_id, _, _ = env.create()
    with env.sessions.begin() as db:
        operation = db.get(Operation, operation_id)
        operation.status = "running"
        operation.worker_token = "owner"
    with env.sessions() as db:
        with pytest.raises(delivery._OwnershipLost):
            delivery._update_operation(db, operation_id, "other", stage="sending")
    operation, _ = env.read(operation_id)
    assert operation.stage == "queued"
    assert operation.heartbeat_at is None
