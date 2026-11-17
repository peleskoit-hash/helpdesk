import json
from base64 import b64decode
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock, call
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from api.app.models import ClientProfile, MassNotificationRecipient, TelegramContact
from api.app.routers import mass_notifications as router
from api.app.services import mass_notification_delivery as delivery
from api.app.services import mass_notification_staging as staging
from api.app.services import telegram_bot, telegram_http
from test_mass_notifications_send_operation import PREVIEW, post_multipart, send_env, send_payload, state


STAGING_CHAT = "synthetic-staging"
PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jf9sAAAAASUVORK5CYII=")
PDF = b"%PDF-1.4\n% synthetic document\n%%EOF\n"
FILES = [("pixel.png", PNG, "image/png"), ("notice.pdf", PDF, "application/pdf")]
HTML = '<b>Уведомление</b> &amp; <i>детали</i>'


def confirmed(message_id):
    return {"ok": True, "result": {"message_id": message_id}}


@pytest.fixture
def e2e(send_env, monkeypatch):
    never_network = Mock(side_effect=AssertionError("Real Telegram transport is disabled"))
    monkeypatch.setattr(telegram_bot, "open_telegram_url", never_network)
    monkeypatch.setattr(telegram_http, "open_telegram_url", never_network)
    monkeypatch.setattr(telegram_bot, "urlopen", never_network)
    monkeypatch.setattr(router, "run_mass_notification_operation", delivery.run_mass_notification_operation)
    monkeypatch.setattr(delivery, "SessionLocal", send_env.sessions)
    monkeypatch.setattr(delivery.settings, "telegram_staging_chat_id", STAGING_CHAT)
    monkeypatch.setattr(delivery, "time", SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _delay: None))

    schedule = BackgroundTasks.add_task
    jobs = []

    def track(self, function, *args, **kwargs):
        if function is delivery.run_mass_notification_operation:
            jobs.append(args)
        return schedule(self, function, *args, **kwargs)

    monkeypatch.setattr(BackgroundTasks, "add_task", track)
    telegram = Mock()
    for module, name, message_id in [
        (staging, "send_photo", 9001), (staging, "send_document", 9002),
        (delivery, "send_message", 9101), (delivery, "copy_message", 9102),
    ]:
        method = getattr(telegram, name)
        method.return_value = confirmed(message_id)
        monkeypatch.setattr(module, name, method)
    yield SimpleNamespace(env=send_env, telegram=telegram, jobs=jobs)
    never_network.assert_not_called()


def get_journal(env, operation_id):
    response = env.client.get(f"/api/v1/mass-notifications/operations/{operation_id}")
    assert response.status_code == 200
    return response.json()["operation"], response.text


def test_preview_to_background_delivery_and_idempotent_replay(e2e):
    env, telegram = e2e.env, e2e.telegram
    filters = {"destination": "Support", "custom_team": "Alpha"}
    preview = env.client.get(PREVIEW, params={"filters": json.dumps(filters)})
    assert preview.status_code == 200
    assert preview.json() == {
        "count": 2,
        "audience_fingerprint": sha256(b'[["1","chat-1"],["2","chat-2"]]').hexdigest(),
    }
    payload = {
        "idempotency_key": str(uuid4()), "audience_fingerprint": preview.json()["audience_fingerprint"],
        "filters": filters, "text": HTML, "text_format": "telegram_html",
    }

    def verify_ready(chat_id, *args, **kwargs):
        operations, recipients = state(env)
        assert [item["status"] for item in operations[0].attachments_json] == ["ready", "ready"]
        recipient = next(row for row in recipients if row.chat_id == chat_id)
        assert recipient.components_json[0]["status"] == "sending"
        return confirmed(9101)

    telegram.send_message.side_effect = verify_ready
    response = post_multipart(env, payload, *FILES)
    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    assert response.json() == {"operation_id": operation_id, "status": "queued", "total_count": 2, "created": True}
    operations, recipients = state(env)
    assert [(op.id, op.text, op.status) for op in operations] == [(operation_id, HTML, "completed")]
    assert [(row.client_id, row.chat_id, row.status) for row in recipients] == [
        ("1", "chat-1", "sent"), ("2", "chat-2", "sent"),
    ]
    assert [[part["telegram_message_id"] for part in row.components_json] for row in recipients] == [[9101, 9102, 9102]] * 2
    prepared = operations[0].attachments_json
    assert [item["staging_message_id"] for item in prepared] == [9001, 9002]
    document_path = env.root / operation_id / prepared[1]["stored_name"]
    expected_calls = [
        call.send_photo(chat_id=STAGING_CHAT, content=PNG, filename="pixel.png", mime_type="image/png"),
        call.send_document(chat_id=STAGING_CHAT, file_path=document_path, filename="notice.pdf", mime_type="application/pdf"),
    ]
    recipient_order = [entry.args[0] for entry in telegram.send_message.call_args_list]
    assert sorted(recipient_order) == ["chat-1", "chat-2"]
    for chat_id in recipient_order:
        expected_calls.extend([
            call.send_message(chat_id, HTML, parse_mode="HTML", retry_only_parse_errors=True),
            call.copy_message(chat_id=chat_id, from_chat_id=STAGING_CHAT, message_id=9001),
            call.copy_message(chat_id=chat_id, from_chat_id=STAGING_CHAT, message_id=9002),
        ])
    assert telegram.mock_calls == expected_calls
    assert not document_path.parent.exists()
    journal, public_body = get_journal(env, operation_id)
    assert (journal["status"], journal["total_count"], journal["processed_count"], journal["sent_count"]) == (
        "completed", 2, 2, 2,
    )
    assert [item["status"] for item in journal["attachments"]] == ["ready", "ready"]
    assert [row["status"] for row in journal["recipients"]] == ["sent", "sent"]
    assert all(value not in public_body for value in (
        "chat-1", "chat-2", STAGING_CHAT, "staging_message_id", "telegram_message_id",
        "stored_name", prepared[1]["stored_name"], json.dumps(str(document_path))[1:-1],
    ))
    assert all(set(item) == {"index", "original_filename", "file_size", "status", "error"}
               for item in journal["attachments"])
    assert all(set(part) == {"key", "kind", "status", "error"}
               for row in journal["recipients"] for part in row["components"])

    replay = post_multipart(env, payload, *FILES)
    assert replay.status_code == 202
    assert replay.json() == dict(response.json(), status="completed", created=False)
    assert len(state(env)[0]) == 1
    assert e2e.jobs == [(operation_id,)]
    assert telegram.mock_calls == expected_calls
    assert list(env.root.iterdir()) == []


def test_same_count_changed_audience_rejects_old_preview(e2e):
    env = e2e.env
    payload = send_payload(env, text=HTML, text_format="telegram_html")
    with env.sessions.begin() as db:
        db.get(TelegramContact, "1").external_chat_id = "replacement-chat"
    current = env.client.get(PREVIEW).json()
    assert current["count"] == 3
    assert current["audience_fingerprint"] != payload["audience_fingerprint"]
    response = post_multipart(env, payload, FILES[1])
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "audience_changed"
    assert state(env) == ([], [])
    assert list(env.root.iterdir()) == []
    assert not e2e.jobs and not e2e.telegram.mock_calls


def test_recipient_who_loses_eligibility_is_skipped_without_stopping_delivery(e2e):
    env, telegram = e2e.env, e2e.telegram
    skipped = []

    def block_next_contact(chat_id, *args, **kwargs):
        if not skipped:
            with env.sessions.begin() as db:
                rows = db.scalars(select(MassNotificationRecipient).order_by(
                    MassNotificationRecipient.created_at, MassNotificationRecipient.id,
                )).all()
                assert len(rows) == 3 and rows[0].chat_id == chat_id
                skipped.append(rows[1].chat_id)
                db.get(TelegramContact, rows[1].client_id).is_blocked = True
        return confirmed(9101)

    telegram.send_message.side_effect = block_next_contact
    response = post_multipart(env, send_payload(env))
    assert response.status_code == 202
    journal, _ = get_journal(env, response.json()["operation_id"])
    _, rows = state(env)
    assert {row.chat_id: row.status for row in rows} == {
        f"chat-{number}": "skipped" if f"chat-{number}" in skipped else "sent" for number in (1, 2, 3)
    }
    sent_chats = [entry.args[0] for entry in telegram.send_message.call_args_list]
    assert sorted(sent_chats) == sorted({"chat-1", "chat-2", "chat-3"} - set(skipped))
    assert (journal["status"], journal["processed_count"], journal["sent_count"], journal["skipped_count"]) == (
        "completed", 3, 2, 1,
    )


def test_mixed_telegram_outcomes_are_recorded_without_retry_or_global_abort(e2e):
    env, telegram = e2e.env, e2e.telegram
    with env.sessions.begin() as db:
        db.get(ClientProfile, "4").is_verified = True

    def send(chat_id, *args, **kwargs):
        if chat_id == "chat-2":
            raise telegram_bot.TelegramApiError("Forbidden: bot was blocked by the user")
        if chat_id == "chat-4":
            raise TimeoutError("Telegram reply was lost")
        return confirmed(9101)

    def copy(*, chat_id, from_chat_id, message_id):
        if chat_id == "chat-2":
            raise telegram_bot.TelegramApiError("Bad Request: chat not found")
        if chat_id in {"chat-3", "chat-4"}:
            return {"ok": True, "result": {}}
        return confirmed(9102)

    telegram.send_message.side_effect = send
    telegram.copy_message.side_effect = copy
    response = post_multipart(env, send_payload(env), FILES[1])
    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    journal, _ = get_journal(env, operation_id)
    _, rows = state(env)
    assert [(row.status, [part["status"] for part in row.components_json]) for row in rows] == [
        ("sent", ["sent", "sent"]), ("failed", ["failed", "failed"]),
        ("partial", ["sent", "unknown"]), ("unknown", ["unknown", "unknown"]),
    ]
    assert rows[2].components_json[0]["telegram_message_id"] == 9101
    assert {key: journal[key] for key in (
        "status", "processed_count", "sent_count", "failed_count", "partial_count", "unknown_count",
        "skipped_count", "pending_count", "sending_count",
    )} == {
        "status": "completed", "processed_count": 4, "sent_count": 1, "failed_count": 1, "partial_count": 1,
        "unknown_count": 1, "skipped_count": 0, "pending_count": 0, "sending_count": 0,
    }
    expected = [f"chat-{number}" for number in range(1, 5)]
    assert sorted(entry.args[0] for entry in telegram.send_message.call_args_list) == expected
    assert sorted(entry.kwargs["chat_id"] for entry in telegram.copy_message.call_args_list) == expected
    telegram.send_document.assert_called_once()
    telegram.send_photo.assert_not_called()
    assert e2e.jobs == [(operation_id,)]
