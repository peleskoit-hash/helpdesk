from __future__ import annotations

from datetime import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import (
    Client,
    ClientProfile,
    Company,
    MessageType,
    TelegramContact,
    TelegramUpdate,
    Ticket,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketWorkflowType,
)
from api.app.routers import telegram as telegram_router
from api.app.routers import tickets as tickets_router


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _company(session):
    company = Company(id=str(uuid.uuid4()), name="ACME")
    session.add(company)
    session.commit()
    return company


def _seed_contact(
    session,
    *,
    company: Company,
    external_user_id: str = "100500",
    external_chat_id: str = "100500",
    verified: bool = True,
    destination: str = "Support",
    is_blocked: bool = False,
):
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Telegram Client")
    profile = ClientProfile(
        id=str(uuid.uuid4()),
        client_id=client.id,
        contact_name="Ivan",
        last_name="Petrov",
        is_verified=verified,
        destination=destination,
    )
    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id=external_user_id,
        external_chat_id=external_chat_id,
        external_username="telegram_user",
        external_display_name="Ivan Petrov",
        is_blocked=is_blocked,
    )
    session.add_all([client, profile, contact])
    session.commit()
    return client, profile, contact


def _seed_ticket(
    session,
    *,
    company_id: str,
    client_id: str,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    intake_state: str = TicketIntakeState.READY.value,
    status: str = TicketStatus.NEW.value,
    subject: str = "Need help",
):
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        workflow_type=workflow_type,
        intake_state=intake_state,
        status=status,
        subject=subject,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    session.commit()
    return ticket


def _message_payload(*, update_id: int, chat_id: str, user_id: str, text: str, chat_type: str = "private") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id) if str(chat_id).isdigit() else chat_id, "type": chat_type},
            "from": {"id": int(user_id) if str(user_id).isdigit() else user_id, "first_name": "Ivan", "last_name": "Petrov"},
            "text": text,
        },
    }


def _edited_message_payload(*, update_id: int, chat_id: str, user_id: str, text: str, chat_type: str = "private") -> dict:
    return {
        "update_id": update_id,
        "edited_message": {
            "message_id": update_id,
            "chat": {"id": int(chat_id) if str(chat_id).isdigit() else chat_id, "type": chat_type},
            "from": {"id": int(user_id) if str(user_id).isdigit() else user_id, "first_name": "Ivan", "last_name": "Petrov"},
            "text": text,
        },
    }


def _callback_payload(*, update_id: int, chat_id: str, user_id: str, data: str = "btn:test", chat_type: str = "private") -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": int(user_id) if str(user_id).isdigit() else user_id, "first_name": "Ivan", "last_name": "Petrov"},
            "data": data,
            "message": {
                "message_id": update_id,
                "chat": {"id": int(chat_id) if str(chat_id).isdigit() else chat_id, "type": chat_type},
            },
        },
    }


def _client_message_count(session, ticket_id: str) -> int:
    return len(
        session.execute(
            select(TicketMessage).where(
                TicketMessage.ticket_id == ticket_id,
                TicketMessage.message_type == MessageType.CLIENT.value,
            )
        ).scalars().all()
    )


def _seed_existing_client_message(session, *, ticket_id: str, body: str = "Existing message") -> None:
    session.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            message_type=MessageType.CLIENT.value,
            author_user_id=None,
            body=body,
            created_at=datetime.utcnow(),
        )
    )
    session.commit()


def test_unblocked_existing_client_message_continues_normal_flow():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_contact(session, company=company, is_blocked=False)
    ticket = _seed_ticket(session, company_id=company.id, client_id=client.id)
    _seed_existing_client_message(session, ticket_id=ticket.id)
    before_count = _client_message_count(session, ticket.id)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1001, chat_id=contact.external_chat_id, user_id=contact.external_user_id, text="Need help now"),
        notify_client=False,
    )

    assert result["ok"] is True
    assert result["ticket_id"] == ticket.id
    assert _client_message_count(session, ticket.id) == before_count + 1


def test_unknown_new_telegram_user_is_allowed_to_start_onboarding():
    session = _session()
    company = _company(session)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1002, chat_id="7001", user_id="7001", text="/start"),
        notify_client=False,
    )

    contacts = session.execute(select(TelegramContact)).scalars().all()
    tickets = session.execute(select(Ticket)).scalars().all()
    assert company is not None
    assert result["ok"] is True
    assert result["command"] == "start"
    assert result["first_connection"] is True
    assert len(contacts) == 1
    assert len(tickets) == 1


def test_blocked_start_does_not_run_start_flow_or_create_entities():
    session = _session()
    company = _company(session)
    _, _, contact = _seed_contact(session, company=company, is_blocked=True)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1003, chat_id=contact.external_chat_id, user_id=contact.external_user_id, text="/start"),
        notify_client=True,
    )

    assert result == {"ok": True, "blocked": True, "ignored": "blocked_contact"}
    assert session.execute(select(Ticket)).scalars().all() == []
    assert session.execute(select(TelegramUpdate)).scalars().all() != []


def test_blocked_client_text_does_not_create_message_and_keeps_active_ticket_unchanged():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_contact(session, company=company, is_blocked=True)
    ticket = _seed_ticket(session, company_id=company.id, client_id=client.id)
    original_status = ticket.status
    original_updated_at = ticket.updated_at

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1004, chat_id=contact.external_chat_id, user_id=contact.external_user_id, text="Blocked text"),
        notify_client=False,
    )

    session.refresh(ticket)
    assert result["blocked"] is True
    assert _client_message_count(session, ticket.id) == 0
    assert ticket.status == original_status
    assert ticket.updated_at == original_updated_at
    assert ticket.closed_at is None


def test_duplicate_identity_is_rejected_by_db_invariant():
    session = _session()
    company = _company(session)
    client, _, _canonical_contact = _seed_contact(
        session,
        company=company,
        external_user_id="453920478",
        external_chat_id="453920478",
        is_blocked=False,
    )
    duplicate_contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        external_user_id="453920478",
        external_chat_id="-5267117449",
        external_username="telegram_user",
        external_display_name="Ivan Petrov",
        is_blocked=True,
    )
    session.add(duplicate_contact)
    with pytest.raises(IntegrityError):
        session.commit()


def test_blocked_callback_does_not_execute_ticket_creation_handler(monkeypatch):
    session = _session()
    company = _company(session)
    _, _, contact = _seed_contact(session, company=company, is_blocked=True)
    called = {"value": False}

    def _fail(*args, **kwargs):
        called["value"] = True
        return {"ok": False}

    monkeypatch.setattr(telegram_router, "_process_problem_type_callback", _fail)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=1006, chat_id=contact.external_chat_id, user_id=contact.external_user_id, data="btn:create-ticket"),
        notify_client=False,
    )

    assert result["blocked"] is True
    assert called["value"] is False
    assert session.execute(select(Ticket)).scalars().all() == []


def test_blocked_callback_does_not_initiate_task(monkeypatch):
    session = _session()
    company = _company(session)
    _, _, contact = _seed_contact(session, company=company, is_blocked=True)
    called = {"value": False}

    def _fail(*args, **kwargs):
        called["value"] = True
        return {"ok": False}

    monkeypatch.setattr(telegram_router, "_process_problem_type_callback", _fail)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=1007, chat_id=contact.external_chat_id, user_id=contact.external_user_id, data="btn:create-task"),
        notify_client=False,
    )

    assert result["blocked"] is True
    assert called["value"] is False
    assert session.execute(select(Ticket)).scalars().all() == []


def test_blocked_callback_sends_no_automatic_reply(monkeypatch):
    session = _session()
    company = _company(session)
    _, _, contact = _seed_contact(session, company=company, is_blocked=True)
    sent_messages: list[tuple] = []
    answered_callbacks: list[tuple] = []

    monkeypatch.setattr(telegram_router, "send_message", lambda *args, **kwargs: sent_messages.append((args, kwargs)))
    monkeypatch.setattr(telegram_router, "answer_callback_query", lambda *args, **kwargs: answered_callbacks.append((args, kwargs)))

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=1008, chat_id=contact.external_chat_id, user_id=contact.external_user_id, data="btn:any"),
        notify_client=True,
    )

    assert result["blocked"] is True
    assert sent_messages == []
    assert answered_callbacks == []


def test_outbound_to_blocked_client_is_not_blocked(monkeypatch):
    session = _session()
    company = _company(session)
    client, _, contact = _seed_contact(session, company=company, is_blocked=True)
    ticket = _seed_ticket(session, company_id=company.id, client_id=client.id)
    sent = {}

    monkeypatch.setattr(
        tickets_router,
        "send_message",
        lambda chat_id, text, parse_mode=None, remove_keyboard=False, reply_to_message_id=None: sent.update(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "remove_keyboard": remove_keyboard,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        or {"ok": True},
    )

    response = tickets_router._send_to_telegram_client(session, ticket, "Hello from support")

    assert response == {"ok": True}
    assert sent["chat_id"] == contact.external_chat_id
    assert sent["text"]


def test_unblocked_after_block_incoming_works_again():
    session = _session()
    company = _company(session)
    client, _, contact = _seed_contact(session, company=company, is_blocked=True)
    ticket = _seed_ticket(session, company_id=company.id, client_id=client.id)
    _seed_existing_client_message(session, ticket_id=ticket.id)
    before_count = _client_message_count(session, ticket.id)

    blocked_result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1009, chat_id=contact.external_chat_id, user_id=contact.external_user_id, text="Blocked text"),
        notify_client=False,
    )
    session.refresh(contact)
    contact.is_blocked = False
    session.commit()

    allowed_result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1010, chat_id=contact.external_chat_id, user_id=contact.external_user_id, text="Allowed text"),
        notify_client=False,
    )

    assert blocked_result["blocked"] is True
    assert allowed_result["ticket_id"] == ticket.id
    assert _client_message_count(session, ticket.id) == before_count + 1


def test_group_message_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)
    access_called = {"value": False}
    contact_called = {"value": False}

    def _fail_access(*args, **kwargs):
        access_called["value"] = True
        raise AssertionError("access should not be called for group message")

    def _fail_contact(*args, **kwargs):
        contact_called["value"] = True
        raise AssertionError("contact resolution should not be called for group message")

    monkeypatch.setattr(telegram_router, "evaluate_incoming_telegram_access", _fail_access)
    monkeypatch.setattr(telegram_router, "get_or_create_contact_from_user", _fail_contact)

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1011, chat_id="-10001", user_id="7001", text="group text", chat_type="group"),
        notify_client=False,
    )

    updates = session.execute(select(TelegramUpdate)).scalars().all()
    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert access_called["value"] is False
    assert contact_called["value"] is False
    assert len(updates) == 1
    assert updates[0].update_id == "1011"
    assert session.execute(select(TelegramContact)).scalars().all() == []


def test_supergroup_message_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called for supergroup message")),
    )
    monkeypatch.setattr(
        telegram_router,
        "get_or_create_contact_from_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("contact resolution should not be called for supergroup message")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1012, chat_id="-10002", user_id="7002", text="supergroup text", chat_type="supergroup"),
        notify_client=False,
    )

    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert session.execute(select(TelegramUpdate)).scalars().all() != []
    assert session.execute(select(TelegramContact)).scalars().all() == []


def test_channel_message_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called for channel message")),
    )
    monkeypatch.setattr(
        telegram_router,
        "get_or_create_contact_from_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("contact resolution should not be called for channel message")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(update_id=1013, chat_id="-10003", user_id="7003", text="channel text", chat_type="channel"),
        notify_client=False,
    )

    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert session.execute(select(TelegramUpdate)).scalars().all() != []
    assert session.execute(select(TelegramContact)).scalars().all() == []


def test_message_without_chat_type_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called when chat type is missing")),
    )
    monkeypatch.setattr(
        telegram_router,
        "get_or_create_contact_from_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("contact resolution should not be called when chat type is missing")),
    )

    payload = _message_payload(update_id=1017, chat_id="7007", user_id="7007", text="missing type")
    payload["message"]["chat"].pop("type", None)

    result = telegram_router.process_telegram_update(session, payload, notify_client=False)

    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert session.execute(select(TelegramUpdate)).scalars().all() != []
    assert session.execute(select(TelegramContact)).scalars().all() == []


def test_message_with_unknown_chat_type_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called for unknown chat type")),
    )
    monkeypatch.setattr(
        telegram_router,
        "get_or_create_contact_from_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("contact resolution should not be called for unknown chat type")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _message_payload(
            update_id=1018,
            chat_id="7008",
            user_id="7008",
            text="future type",
            chat_type="future_chat_type",
        ),
        notify_client=False,
    )

    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert session.execute(select(TelegramUpdate)).scalars().all() != []
    assert session.execute(select(TelegramContact)).scalars().all() == []


def test_group_callback_is_ignored_before_access_or_callback_handlers(monkeypatch):
    session = _session()
    _company(session)
    problem_handler_called = {"value": False}

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called for group callback")),
    )
    monkeypatch.setattr(
        telegram_router,
        "_process_problem_type_callback",
        lambda *args, **kwargs: problem_handler_called.__setitem__("value", True),
    )

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=1014, chat_id="-10004", user_id="7004", data="btn:create-ticket", chat_type="group"),
        notify_client=False,
    )

    updates = session.execute(select(TelegramUpdate)).scalars().all()
    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert problem_handler_called["value"] is False
    assert len(updates) == 1
    assert updates[0].update_id == "1014"


def test_private_callback_continues_existing_flow(monkeypatch):
    session = _session()
    _company(session)
    called = {"value": False}

    def _fake_problem_type_callback(*args, **kwargs):
        called["value"] = True
        return {"ok": True, "from_handler": True}

    monkeypatch.setattr(telegram_router, "_process_problem_type_callback", _fake_problem_type_callback)

    result = telegram_router.process_telegram_update(
        session,
        _callback_payload(update_id=1015, chat_id="7005", user_id="7005", data="btn:create-ticket", chat_type="private"),
        notify_client=False,
    )

    assert result == {"ok": True, "from_handler": True}
    assert called["value"] is True


def test_non_private_edited_message_is_ignored_before_access_or_contact_resolution(monkeypatch):
    session = _session()
    _company(session)

    monkeypatch.setattr(
        telegram_router,
        "evaluate_incoming_telegram_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("access should not be called for edited group message")),
    )
    monkeypatch.setattr(
        telegram_router,
        "get_or_create_contact_from_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("contact resolution should not be called for edited group message")),
    )

    result = telegram_router.process_telegram_update(
        session,
        _edited_message_payload(update_id=1016, chat_id="-10006", user_id="7006", text="edited text", chat_type="group"),
        notify_client=False,
    )

    assert result == {"ok": True, "ignored": "non_private_chat"}
    assert session.execute(select(TelegramUpdate)).scalars().all() != []
