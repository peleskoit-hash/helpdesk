from unittest.mock import Mock

import pytest
from sqlalchemy import select

from api.app.models import MessageType, Ticket, TicketMessage
from api.app.routers import telegram
from test_telegram_client_blocking import (
    _company,
    _message_payload,
    _seed_contact,
    _seed_ticket,
    _session,
)


START_NOTE = "Клиент нажал /start и пытается создать новое обращение"


@pytest.fixture
def case(monkeypatch):
    with _session() as db:
        company = _company(db)
        client, _, contact = _seed_contact(db, company=company)
        send = Mock(return_value={"ok": True, "result": {"message_id": 999}})
        monkeypatch.setattr(telegram, "send_message", send)
        yield db, company, client, contact, send


def _start(db, contact, update_id, notify_client):
    return telegram.process_telegram_update(
        db,
        _message_payload(
            update_id=update_id,
            chat_id=contact.external_chat_id,
            user_id=contact.external_user_id,
            text="/start",
        ),
        notify_client=notify_client,
    )


@pytest.mark.parametrize("notify_client", [False, True])
def test_active_start_persists_agent_visible_note_without_sending_it(case, notify_client):
    db, company, client, contact, send = case
    ticket = _seed_ticket(db, company_id=company.id, client_id=client.id)
    ticket_id = ticket.id

    result = _start(db, contact, 1, notify_client)
    # Discard any uncommitted work before checking persistence.
    db.rollback()
    db.expire_all()

    assert result == {"ok": True, "command": "start", "ticket_id": ticket_id}
    assert [row.id for row in db.scalars(select(Ticket))] == [ticket_id]
    note = db.scalars(select(TicketMessage)).one()
    assert note.ticket_id == ticket_id
    assert note.body == START_NOTE
    assert note.message_type == MessageType.SYSTEM.value
    assert note.agent_visible is True
    assert note.author_user_id is None
    if notify_client:
        send.assert_called_once()
        assert send.call_args.args[:2] == (
            contact.external_chat_id,
            "У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
        )
    else:
        send.assert_not_called()


def test_new_start_adds_note_but_duplicate_update_does_not(case):
    db, company, client, contact, send = case
    ticket = _seed_ticket(db, company_id=company.id, client_id=client.id)
    ticket_id = ticket.id

    _start(db, contact, 1, False)
    assert _start(db, contact, 1, False) == {"ok": True, "duplicate": True}
    _start(db, contact, 2, False)
    db.rollback()
    db.expire_all()

    notes = db.scalars(select(TicketMessage)).all()
    assert len(notes) == 2
    assert len({note.id for note in notes}) == 2
    assert all(note.ticket_id == ticket_id and note.body == START_NOTE for note in notes)
    assert db.scalars(select(Ticket)).one().id == ticket_id
    send.assert_not_called()


def test_start_without_active_ticket_preserves_onboarding(case):
    db, _, _, contact, send = case
    profile = telegram._client_profile(db, contact.client_id)
    profile.is_verified = False
    db.commit()

    result = _start(db, contact, 1, False)
    db.rollback()
    db.expire_all()

    assert result["ok"] is True
    assert result["command"] == "start"
    assert result["first_connection"] is True
    assert db.scalars(select(Ticket)).one().id == result["ticket_id"]
    assert not db.scalars(select(TicketMessage).where(TicketMessage.body == START_NOTE)).all()
    send.assert_not_called()
