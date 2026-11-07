from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from api.app.models import TelegramUpdate, Ticket
from api.app.routers import telegram
from api.app.services.automatic_messages import upsert_automatic_message_override
from api.app.services.telegram_intake_hours import is_telegram_intake_open
from test_telegram_client_blocking import (
    _company,
    _message_payload,
    _seed_contact,
    _seed_ticket,
    _session,
)


@pytest.fixture
def case(monkeypatch):
    with _session() as db:
        company = _company(db)
        client, _, contact = _seed_contact(db, company=company, verified=False)
        send = Mock(return_value={"ok": True, "result": {"message_id": 999}})
        monkeypatch.setattr(telegram, "send_message", send)
        yield db, company, client, contact, send


def _set_time(monkeypatch, value):
    at = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Europe/Moscow"))
    check = Mock(side_effect=lambda: is_telegram_intake_open(at))
    monkeypatch.setattr(telegram, "is_telegram_intake_open", check)
    return check


def _start(db, contact, notify_client):
    return telegram.process_telegram_update(
        db,
        _message_payload(
            update_id=1,
            chat_id=contact.external_chat_id,
            user_id=contact.external_user_id,
            text="/start",
        ),
        notify_client=notify_client,
    )


@pytest.mark.parametrize("at", ["2026-11-02T07:49:59", "2026-11-02T19:00:00", "2026-11-08T12:00:00"])
@pytest.mark.parametrize("notify_client", [False, True])
def test_first_connection_is_blocked_without_creating_ticket(case, monkeypatch, at, notify_client):
    db, _, _, contact, send = case
    _set_time(monkeypatch, at)
    first_connection = Mock()
    config = Mock()
    ensure_ticket = Mock()
    monkeypatch.setattr(telegram, "process_first_connection_start", first_connection)
    monkeypatch.setattr(telegram, "first_connection_values", config)
    monkeypatch.setattr(telegram, "_ensure_ticket_for_incoming", ensure_ticket)
    custom_text = "<b>Приём закрыт</b>. Обратитесь позже."
    upsert_automatic_message_override(db, "telegram.after_hours.creation_blocked.client", custom_text)
    db.commit()

    assert _start(db, contact, notify_client) == {"ok": True, "command": "start", "after_hours_blocked": True}
    db.rollback()
    assert db.scalars(select(Ticket)).all() == []
    assert db.scalars(select(TelegramUpdate)).one().update_id == "1"
    first_connection.assert_not_called()
    config.assert_not_called()
    ensure_ticket.assert_not_called()
    if notify_client:
        send.assert_called_once_with(contact.external_chat_id, custom_text, parse_mode="HTML")
    else:
        send.assert_not_called()


@pytest.mark.parametrize("notify_client", [False, True])
def test_first_connection_during_working_hours_preserves_scenario(case, monkeypatch, notify_client):
    db, _, _, contact, send = case
    _set_time(monkeypatch, "2026-11-02T08:00:00")
    first_connection = Mock(wraps=telegram.process_first_connection_start)
    monkeypatch.setattr(telegram, "process_first_connection_start", first_connection)

    result = _start(db, contact, notify_client)
    db.rollback()
    assert result["ok"] is True
    assert result["command"] == "start"
    assert result["first_connection"] is True
    assert "after_hours_blocked" not in result
    assert db.scalars(select(Ticket)).one().id == result["ticket_id"]
    first_connection.assert_called_once()
    if notify_client:
        send.assert_called_once()
    else:
        send.assert_not_called()


@pytest.mark.parametrize("at", ["2026-11-02T19:00:00", "2026-11-08T12:00:00"])
@pytest.mark.parametrize("notify_client", [False, True])
def test_active_ticket_is_not_blocked_even_when_onboarding_is_needed(case, monkeypatch, at, notify_client):
    db, company, client, contact, send = case
    check = _set_time(monkeypatch, at)
    ticket = _seed_ticket(db, company_id=company.id, client_id=client.id)
    ticket_id = ticket.id

    assert _start(db, contact, notify_client) == {"ok": True, "command": "start", "ticket_id": ticket_id}
    db.rollback()
    assert [row.id for row in db.scalars(select(Ticket))] == [ticket_id]
    check.assert_not_called()
    if notify_client:
        send.assert_called_once()
        assert "активное обращение" in send.call_args.args[1]
    else:
        send.assert_not_called()
