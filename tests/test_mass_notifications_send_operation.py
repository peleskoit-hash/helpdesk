import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client, ClientProfile, Company, MassNotificationOperation as Operation,
    MassNotificationRecipient as Recipient, TelegramContact,
)
from api.app.routers import mass_notifications as notifications
from api.app.services import mass_notification_operations as operations
from test_settings_client_card import _block, _field


SEND = "/api/v1/mass-notifications/send"
PREVIEW = "/api/v1/mass-notifications/preview"
RECIPIENTS = "/api/v1/mass-notifications/recipients"
ORIGIN = "https://desk.example.test"


@pytest.fixture
def send_env(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications.settings, "app_env", "dev")
    monkeypatch.setattr(notifications.settings, "app_public_base_url", ORIGIN)
    monkeypatch.setattr(notifications.tempfile, "gettempdir", lambda: str(tmp_path))
    runner = Mock()
    telegram = Mock(side_effect=AssertionError("HTTP request must not call Telegram"))
    monkeypatch.setattr(notifications, "run_mass_notification_operation", runner)
    for method in ("send_message", "send_document", "send_photo"):
        monkeypatch.setattr(notifications, method, telegram)

    engine = create_engine(f"sqlite:///{(tmp_path / 'send.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        db.add_all([Company(id="company", name="Synthetic"), _block("routing", "Routing", 10)])
        for key, options in {
            "destination": ["Support", "Sales"], "location_type": ["Office", "Remote"],
            "custom_team": ["Alpha", "Beta"],
        }.items():
            field = _field(key, key, "routing", 10, is_system=key != "custom_team")
            field.field_type = "select"
            field.options_json = options
            db.add(field)
        for number in range(1, 9):
            key = str(number)
            db.add(Client(id=key, company_id="company", display_name=f"Synthetic {key}"))
            if number != 6:
                db.add(ClientProfile(
                    id=key, client_id=key, is_verified=number != 4,
                    last_name=" Family " if number == 1 else None, contact_name=" Given " if number == 1 else None,
                    destination="Sales" if number == 3 else "Support",
                    location_type="Remote" if number == 2 else "Office",
                    custom_fields={"custom_team": "Beta" if number == 3 else "Alpha"},
                ))
            if number != 8:
                db.add(TelegramContact(
                    id=key, client_id=key, company_id="company", external_user_id=key,
                    external_chat_id="" if number == 7 else f"chat-{key}", is_blocked=number == 5,
                ))

    def get_db():
        with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(notifications.router)
    user = SimpleNamespace(id="admin", role="admin")
    app.dependency_overrides[notifications.get_current_user] = lambda: user
    app.dependency_overrides[notifications.get_db] = get_db

    with TestClient(app) as client:
        yield SimpleNamespace(
            app=app, client=client, user=user, runner=runner, sessions=sessions,
            root=tmp_path / "es-desk-mass-notifications",
        )
    telegram.assert_not_called()
    engine.dispose()


def send_payload(env, *, filters=None, text="Notice", **fields):
    filters = {} if filters is None else filters
    preview = env.client.get(PREVIEW, params={"filters": json.dumps(filters)})
    assert preview.status_code == 200
    return {
        "text": text, "filters": filters, "idempotency_key": str(uuid4()),
        "audience_fingerprint": preview.json()["audience_fingerprint"], **fields,
    }


def post_multipart(env, payload, *attachments):
    fields = [
        (key, (None, json.dumps(value) if key == "filters" else value)) for key, value in payload.items()
    ]
    return env.client.post(SEND, files=[*fields, *(("files", file) for file in attachments)], headers={"Origin": ORIGIN})


def state(env):
    with env.sessions() as db:
        return db.scalars(select(Operation)).all(), db.scalars(select(Recipient).order_by(Recipient.chat_id)).all()


def assert_no_operation(env):
    assert state(env) == ([], [])
    assert not env.root.exists() or not list(env.root.iterdir())
    env.runner.assert_not_called()


@pytest.mark.parametrize("field", ["idempotency_key", "audience_fingerprint", "filters", "text"])
def test_send_requires_all_confirmation_fields(send_env, field):
    payload = send_payload(send_env)
    del payload[field]
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 400
    assert_no_operation(send_env)


@pytest.mark.parametrize(("field", "value"), [
    ("idempotency_key", "arbitrary-key"),
    ("idempotency_key", "BA49898A-FC57-41FF-997C-174688F8F64A"),
    ("audience_fingerprint", "A" * 64),
    ("filters", None),
    ("text", " "),
])
def test_send_rejects_invalid_confirmation_fields(send_env, field, value):
    payload = send_payload(send_env)
    payload[field] = value
    assert send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN}).status_code == 400
    assert_no_operation(send_env)


def test_text_only_submission_snapshots_eligible_recipients_and_escapes_html(send_env):
    payload = send_payload(send_env, text='  <b>Alert</b> & "more"  ')
    original = send_env.client.get(RECIPIENTS).json()
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    assert str(UUID(operation_id)) == operation_id
    assert response.json() == {"operation_id": operation_id, "status": "queued", "total_count": 3, "created": True}
    operations_rows, recipients = state(send_env)
    assert len(operations_rows) == 1
    operation = operations_rows[0]
    assert (operation.text, operation.filters_json, operation.attachments_json) == (
        '&lt;b&gt;Alert&lt;/b&gt; &amp; "more"', {}, [],
    )
    assert [(row.client_id, row.chat_id, row.client_name) for row in recipients] == [
        ("1", "chat-1", "Family Given"), ("2", "chat-2", "Synthetic 2"), ("3", "chat-3", "Synthetic 3"),
    ]
    assert (send_env.root / operation_id).is_dir()
    send_env.runner.assert_called_once_with(operation_id)
    assert send_env.client.get(RECIPIENTS).json() == original
    assert send_env.client.get(PREVIEW).json()["audience_fingerprint"] == payload["audience_fingerprint"]


@pytest.mark.parametrize("zero", [False, True])
def test_changed_or_empty_audience_returns_conflict(send_env, zero):
    if zero:
        payload = send_payload(send_env, filters={"destination": "Sales", "location_type": "Remote"})
    else:
        payload = send_payload(send_env)
        with send_env.sessions.begin() as db:
            db.get(TelegramContact, "1").external_chat_id = "replaced-chat"
        assert send_env.client.get(PREVIEW).json()["count"] == 3
    assert send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN}).status_code == 409
    assert_no_operation(send_env)


def test_legacy_filters_normalize_and_conflicting_filters_fail(send_env):
    payload = send_payload(send_env, filters={"destination": "Support", "custom_team": "Alpha", "location_type": "Remote"})
    payload.update(filters={"destination": "Support", "location_type": ""}, branch_field="custom_team",
                   branch_value="Alpha", location_type="Remote")
    bad = dict(payload, filter_field="destination", filter_value="Sales")
    assert send_env.client.post(SEND, json=bad, headers={"Origin": ORIGIN}).status_code == 400
    assert_no_operation(send_env)
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 202
    operation = state(send_env)[0][0]
    assert operation.filters_json == {"destination": "Support", "custom_team": "Alpha", "location_type": "Remote"}
    assert operation.total_count == 1


def test_attachment_only_streams_files_and_hashes_the_canonical_request(send_env, monkeypatch):
    content = b"x" * (2 * 1024 ** 2 + 7)
    read_sizes = []
    upload_read = notifications.UploadFile.read

    async def measured_read(file, size=-1):
        read_sizes.append(size)
        return await upload_read(file, size)

    monkeypatch.setattr(notifications.UploadFile, "read", measured_read)
    payload = send_payload(send_env, text="")
    def check_ready(operation_id):
        assert len(list((send_env.root / operation_id).iterdir())) == 2
    send_env.runner.side_effect = check_ready
    response = post_multipart(send_env, payload, ("folder/report.pdf", content, "Application/PDF"), ("picture.png", b"photo", "image/png"))
    assert response.status_code == 202
    operation = state(send_env)[0][0]
    folder = send_env.root / operation.id
    attachments = operation.attachments_json
    assert [item["original_filename"] for item in attachments] == ["report.pdf", "picture.png"]
    assert [(folder / item["stored_name"]).read_bytes() for item in attachments] == [content, b"photo"]
    assert all(item["status"] == "pending" and len(item["stored_name"]) == 32 for item in attachments)
    assert all(0 < size <= 1024 ** 2 for size in read_sizes)
    assert len(read_sizes) >= 6
    canonical = {
        "text": "", "filters": {}, "audience_fingerprint": payload["audience_fingerprint"],
        "attachments": [
            dict(original_filename=name, mime_type=mime, file_size=len(data), sha256=sha256(data).hexdigest())
            for name, mime, data in [("report.pdf", "application/pdf", content), ("picture.png", "image/png", b"photo")]
        ],
    }
    assert operation.request_hash == sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert list(send_env.root.iterdir()) == [folder]


@pytest.mark.parametrize("attachments", [
    [("report.pdf", b"", "application/pdf")],
    [("a.jpg", b"x", "image/png")],
    [("a.png", b"x", "application/pdf")],
    [("report.pdf", b"x", "application/pdf")] * 11,
])
def test_bad_attachments_never_create_an_operation(send_env, attachments):
    assert post_multipart(send_env, send_payload(send_env), *attachments).status_code == 400
    assert_no_operation(send_env)


@pytest.mark.parametrize(("filename", "mime", "limit"), [
    ("a.jpg", "image/jpeg", "PHOTO_LIMIT"), ("a.png", "application/octet-stream", "PHOTO_LIMIT"),
    ("document.pdf", "application/pdf", "DOCUMENT_LIMIT"),
])
def test_attachment_size_is_checked_while_reading(send_env, monkeypatch, filename, mime, limit):
    monkeypatch.setattr(notifications, limit, 4)
    assert post_multipart(send_env, send_payload(send_env), (filename, b"12345", mime)).status_code == 400
    assert_no_operation(send_env)


def test_replay_returns_saved_operation_ignoring_live_audience_and_never_replaces_files(send_env):
    payload = send_payload(send_env)
    upload = ("one.pdf", b"initial", "application/pdf")
    initial = post_multipart(send_env, payload, upload)
    assert initial.status_code == 202
    operation_id = initial.json()["operation_id"]
    directory = send_env.root / operation_id
    original_paths = list(directory.iterdir())
    with send_env.sessions.begin() as db:
        db.get(Operation, operation_id).status = "completed"
        db.get(ClientProfile, "1").is_verified = False
    assert send_env.client.get(PREVIEW).json()["count"] == 2
    repeated = post_multipart(send_env, payload, upload)
    assert repeated.status_code == 202
    assert repeated.json() == dict(initial.json(), created=False, status="completed")
    assert post_multipart(send_env, payload, ("one.pdf", b"altered", "application/pdf")).status_code == 409
    send_env.user.id = "another-admin"
    assert post_multipart(send_env, payload, upload).status_code == 409
    assert list(directory.iterdir()) == original_paths
    assert original_paths[0].read_bytes() == b"initial"
    assert list(send_env.root.iterdir()) == [directory]
    assert len(state(send_env)[1]) == 3
    send_env.runner.assert_called_once_with(operation_id)


def test_concurrent_creation_schedules_one_job(send_env, monkeypatch):
    payload = send_payload(send_env)
    barrier = Barrier(2)
    find = operations._find_operation

    def synchronized_find(db, key):
        existing = find(db, key)
        if existing is None:
            barrier.wait(timeout=5)
        return existing

    monkeypatch.setattr(operations, "_find_operation", synchronized_find)

    def submit():
        # Independent TestClient portals allow both requests to reach the database race.
        return TestClient(send_env.app).post(SEND, json=payload, headers={"Origin": ORIGIN})

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(submit) for _ in range(2)]
        responses = [task.result(timeout=10) for task in tasks]
    assert [response.status_code for response in responses] == [202, 202]
    data = [response.json() for response in responses]
    assert data[0]["operation_id"] == data[1]["operation_id"]
    assert sorted(item["created"] for item in data) == [False, True]
    assert len(state(send_env)[0]) == 1
    assert len(list(send_env.root.iterdir())) == 1
    send_env.runner.assert_called_once_with(data[0]["operation_id"])


@pytest.mark.parametrize("fail_commit", [False, True])
def test_failed_finalize_never_schedules_and_only_cleans_request_files(send_env, monkeypatch, fail_commit):
    finalize = notifications._finalize_files

    def fail(directory, operation_id):
        existing = directory.with_name(operation_id)
        existing.mkdir()
        (existing / "keep").write_bytes(b"old")
        if fail_commit:
            def reject_commit(db):
                if db.info.get("after_finalize"):
                    raise RuntimeError("synthetic commit failure")
            event.listen(send_env.sessions, "before_commit", reject_commit)
            # Install a single fault in the request's status-update transaction.
            def mark_session(db, operation_id):
                db.info["after_finalize"] = True
                return record_failure(db, operation_id)
            monkeypatch.setattr(notifications, "_record_finalization_failure", mark_session)
        finalize(directory, operation_id)

    record_failure = notifications._record_finalization_failure
    monkeypatch.setattr(notifications, "_finalize_files", fail)
    response = post_multipart(send_env, send_payload(send_env), ("a.pdf", b"new", "application/pdf"))
    assert response.status_code == 500
    operation = state(send_env)[0][0]
    assert operation.status == ("queued" if fail_commit else "failed")
    if not fail_commit:
        assert operation.stage == "file_finalization" and operation.error_message and operation.finished_at
    directory = send_env.root / operation.id
    assert list(send_env.root.iterdir()) == [directory]
    assert (directory / "keep").read_bytes() == b"old"
    send_env.runner.assert_not_called()


@pytest.mark.parametrize("filters", [{"unknown": "value"}, {"destination": "Unknown"}, {"custom_team": "Unknown"}])
def test_send_preserves_filter_validation_errors(send_env, filters):
    payload = dict(send_payload(send_env), filters=filters)
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    preview = send_env.client.get(PREVIEW, params={"filters": json.dumps(filters)})
    assert response.status_code == preview.status_code == 400
    assert response.json() == preview.json()
    assert_no_operation(send_env)


def test_replay_during_finalization_does_not_start_a_worker(send_env, monkeypatch):
    payload = send_payload(send_env)
    finalize = notifications._finalize_files

    def replay_before_finalize(directory, operation_id):
        assert not directory.with_name(operation_id).exists()
        replay = TestClient(send_env.app).post(SEND, json=payload, headers={"Origin": ORIGIN})
        assert replay.status_code == 202
        assert replay.json() == {
            "operation_id": operation_id, "status": "queued", "total_count": 3, "created": False,
        }
        send_env.runner.assert_not_called()
        finalize(directory, operation_id)

    monkeypatch.setattr(notifications, "_finalize_files", replay_before_finalize)
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 202
    assert response.json()["created"] is True
    assert list(send_env.root.iterdir()) == [send_env.root / response.json()["operation_id"]]
    send_env.runner.assert_called_once_with(response.json()["operation_id"])


@pytest.mark.parametrize(("text", "expected"), [
    (' <STRONG>Привет & мир</strong><blockquote expandable>5 > 3</blockquote> ',
     '<b>Привет &amp; мир</b><blockquote expandable>5 &gt; 3</blockquote>'),
    ('<a href="https://example.test/?a=1&amp;b=2">link</a> &lt;img&gt;',
     '<a href="https://example.test/?a=1&amp;b=2">link</a> &lt;img&gt;'),
    ('<pre><code>i &lt; j</code></pre><tg-spoiler><u>secret</u></tg-spoiler>',
     '<pre><code>i &lt; j</code></pre><tg-spoiler><u>secret</u></tg-spoiler>'),
])
def test_send_normalizes_valid_telegram_html(send_env, text, expected):
    payload = send_payload(send_env, text=text, text_format="telegram_html")
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 202
    assert state(send_env)[0][0].text == expected


@pytest.mark.parametrize("text", [
    "<script>alert(1)</script>", "<b onclick='x'>Hi</b>", "<i><b>Hi</i></b>",
    "<a href='javascript:alert(1)'>bad</a>", "<a href='java&#x09;script:bad'>bad</a>",
    "<a href='/relative'>bad</a>", "<blockquote class='x'>bad</blockquote>",
    "<b>unclosed", "<b", "<b>bad</b accidental>", "<b/>", "<!-- hidden -->",
])
def test_send_rejects_invalid_telegram_html(send_env, text):
    payload = send_payload(send_env, text=text, text_format="telegram_html")
    response = send_env.client.post(SEND, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_telegram_html"
    assert_no_operation(send_env)


def test_send_allows_attachment_only_telegram_html(send_env):
    payload = send_payload(send_env, text="", text_format="telegram_html")
    assert post_multipart(send_env, payload, ("a.pdf", b"data", "application/pdf")).status_code == 202


def test_operation_journal_is_private_and_counts_only_terminal_recipients(send_env):
    latest = "/api/v1/mass-notifications/operations/latest"
    assert send_env.client.get(latest).json() == {"operation": None}
    response = post_multipart(send_env, send_payload(send_env, text="private message"), ("a.pdf", b"x", "application/pdf"))
    operation_id = response.json()["operation_id"]
    detail = f"/api/v1/mass-notifications/operations/{operation_id}"
    with send_env.sessions.begin() as db:
        operation = db.get(Operation, operation_id)
        operation.status = "failed"
        operation.error_message = "Traceback token=hidden /private/local-path"
        attachment = dict(operation.attachments_json[0], status="unknown", staging_chat_id="private-chat",
                          staging_message_id=99999, error="path /private/data")
        operation.attachments_json = [attachment]
        sent, partial, pending = db.scalars(select(Recipient).order_by(Recipient.chat_id)).all()
        sent.status = "sent"
        partial.status = "partial"
        pending.status = "sending"
        sent.components_json = [dict(component, status="sent", telegram_message_id=99999)
                                for component in sent.components_json]
        partial.components_json = [
            dict(partial.components_json[0], status="sent", telegram_message_id=99999),
            dict(partial.components_json[1], status="failed", error="Forbidden: bot was blocked by the user"),
        ]
    fetched = send_env.client.get(detail)
    assert fetched.status_code == 200
    assert send_env.client.get(latest).json() == fetched.json()
    journal = fetched.json()["operation"]
    assert {
        key: journal[key] for key in ("total_count", "processed_count", "sent_count", "partial_count", "sending_count")
    } == {"total_count": 3, "processed_count": 2, "sent_count": 1, "partial_count": 1, "sending_count": 1}
    assert journal["status"] == "failed"
    assert journal["error_message"]
    assert journal["attachments"] == [{
        "index": 0, "original_filename": "a.pdf", "file_size": 1,
        "status": "unknown", "error": "Подробности ошибки недоступны",
    }]
    partial_row = next(row for row in journal["recipients"] if row["status"] == "partial")
    assert partial_row["components"][1]["error"] == "Получатель заблокировал бота"
    assert not any(value in fetched.text for value in (
        "private message", "chat-1", "99999", "private-chat", "/private", "hidden", "stored_name",
    ))
    send_env.user.id = "different-admin"
    assert send_env.client.get(latest).json() == {"operation": None}
    assert send_env.client.get(detail).status_code == 404
    send_env.user.role = "agent"
    assert send_env.client.get(latest).status_code == 403
    assert send_env.client.get(detail).status_code == 403
