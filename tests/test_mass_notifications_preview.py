import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client, ClientProfile, Company, MassNotificationOperation, MassNotificationRecipient, TelegramContact,
)
from api.app.routers import mass_notifications as notifications
from test_settings_client_card import _block, _field


PREVIEW = "/api/v1/mass-notifications/preview"
RECIPIENTS = "/api/v1/mass-notifications/recipients"


@pytest.fixture
def preview(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'preview.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        db.add_all([Company(id="company", name="Synthetic"), _block("routing", "Routing", 10)])
        for key, options in {
            "destination": ["Support", "Sales"],
            "location_type": ["Office", "Remote"],
            "custom_team": ["Alpha", "Beta"],
        }.items():
            field = _field(key, key, "routing", 10, is_system=key != "custom_team")
            field.field_type = "select"
            field.options_json = options
            db.add(field)
        for number in range(1, 9):
            key = str(number)
            db.add(Client(id=key, company_id="company", display_name=f"Synthetic Person {key}"))
            if number != 6:
                db.add(ClientProfile(
                    id=key, client_id=key, is_verified=number != 4,
                    destination="Sales" if number == 3 else "Support",
                    location_type="Remote" if number == 2 else "Office",
                    custom_fields={"custom_team": "Beta" if number == 3 else "Alpha"},
                ))
            if number != 8:
                db.add(TelegramContact(
                    id=key, client_id=key, company_id="company", external_user_id=key,
                    external_chat_id=" " if number == 7 else f"chat-{key}", is_blocked=number == 5,
                ))

    def get_db():
        with sessions() as db:
            yield db

    user = SimpleNamespace(role="admin")
    telegram = Mock(side_effect=AssertionError("preview must not send Telegram messages"))
    for name in ("send_message", "send_document", "send_photo"):
        monkeypatch.setattr(notifications, name, telegram)
    app = FastAPI()
    app.include_router(notifications.router)
    app.dependency_overrides[notifications.get_db] = get_db
    app.dependency_overrides[notifications.get_current_user] = lambda: user
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, user=user, telegram=telegram)
    engine.dispose()


@pytest.mark.parametrize("params", [{}, {"filters": "{}"}])
def test_preview_returns_only_count_and_exact_digest_for_all_eligible_recipients(preview, params):
    response = preview.client.get(PREVIEW, params=params)
    assert response.status_code == 200
    assert response.json() == {
        "count": 3,
        "audience_fingerprint": sha256(b'[["1","chat-1"],["2","chat-2"],["3","chat-3"]]').hexdigest(),
    }
    preview.telegram.assert_not_called()
    with preview.sessions() as db:
        for table in (MassNotificationOperation, MassNotificationRecipient):
            assert db.scalar(select(func.count()).select_from(table)) == 0


def test_preview_requires_administrator(preview):
    preview.user.role = "agent"
    assert preview.client.get(PREVIEW).status_code == 403


@pytest.mark.parametrize(("params", "expected"), [
    ({"filters": '{"destination":"Support"}'}, b'[["1","chat-1"],["2","chat-2"]]'),
    ({"filters": '{"location_type":"Office"}'}, b'[["1","chat-1"],["3","chat-3"]]'),
    ({"filters": '{"custom_team":"Beta"}'}, b'[["3","chat-3"]]'),
    ({"filters": json.dumps({"destination": "Support", "location_type": "Remote", "custom_team": "Alpha"})},
     b'[["2","chat-2"]]'),
    ({
        "filter_field": "destination", "filter_value": "Support",
        "branch_field": "custom_team", "branch_value": "Alpha", "location_type": "Remote",
    }, b'[["2","chat-2"]]'),
])
def test_preview_uses_the_same_system_custom_and_legacy_filters_as_recipients(preview, params, expected):
    recipients = preview.client.get(RECIPIENTS, params=params)
    response = preview.client.get(PREVIEW, params=params)
    assert recipients.status_code == response.status_code == 200
    assert [[row["client_id"], row["chat_id"]] for row in recipients.json()] == json.loads(expected)
    assert response.json() == {
        "count": len(recipients.json()), "audience_fingerprint": sha256(expected).hexdigest(),
    }


@pytest.mark.parametrize("params", [
    {"filters": '{"unknown":"Support"}'},
    {"filters": '{"destination":"Unknown"}'},
    {"filters": '{"custom_team":"Unknown"}'},
    {"filters": "not JSON"},
    {"branch_field": "unknown", "branch_value": "value"},
])
def test_invalid_filters_preserve_the_existing_validation_error(preview, params):
    response = preview.client.get(PREVIEW, params=params)
    existing = preview.client.get(RECIPIENTS, params=params)
    assert response.status_code == existing.status_code == 400
    assert response.json() == existing.json()


def test_renaming_client_and_reordering_audience_preserve_the_digest(preview):
    before = preview.client.get(PREVIEW).json()
    with preview.sessions.begin() as db:
        db.get(Client, "1").display_name = "Zulu"
    assert [row["client_id"] for row in preview.client.get(RECIPIENTS).json()] == ["2", "3", "1"]
    assert preview.client.get(PREVIEW).json() == before


@pytest.mark.parametrize(("attribute", "new_value"), [
    ("external_chat_id", "new-chat-1"),
    ("client_id", "8"),
])
def test_changing_identity_changes_digest_even_when_count_is_unchanged(preview, attribute, new_value):
    before = preview.client.get(PREVIEW).json()
    with preview.sessions.begin() as db:
        setattr(db.get(TelegramContact, "1"), attribute, new_value)
    after = preview.client.get(PREVIEW).json()
    assert after["count"] == before["count"] == 3
    assert after["audience_fingerprint"] != before["audience_fingerprint"]


def test_no_recipients_has_empty_array_digest(preview):
    response = preview.client.get(PREVIEW, params={"filters": '{"destination":"Sales","location_type":"Remote"}'})
    assert response.status_code == 200
    assert response.json() == {"count": 0, "audience_fingerprint": sha256(b"[]").hexdigest()}


def test_fingerprint_serializes_as_sorted_utf8_string_pairs():
    rows = [
        SimpleNamespace(client_id="клиент", external_chat_id=2),
        SimpleNamespace(client_id=1, external_chat_id=20),
        SimpleNamespace(client_id=1, external_chat_id=3),
    ]
    expected = sha256('[["1","20"],["1","3"],["клиент","2"]]'.encode("utf-8")).hexdigest()
    assert notifications._audience_fingerprint(rows) == expected
    assert notifications._audience_fingerprint(reversed(rows)) == expected
