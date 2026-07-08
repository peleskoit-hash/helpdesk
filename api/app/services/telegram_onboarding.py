from __future__ import annotations

import uuid
from datetime import datetime
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, TelegramContact, Ticket, User


def create_client_and_contact(
    db: Session,
    company_id: str,
    external_user_id: str,
    external_chat_id: str,
    display_name: str,
    username: str | None,
) -> TelegramContact:
    client = Client(
        id=str(uuid.uuid4()),
        company_id=company_id,
        display_name=display_name or "Клиент Telegram",
    )
    db.add(client)
    db.flush()

    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client.id,
        external_user_id=external_user_id,
        external_chat_id=external_chat_id,
        external_username=username,
        external_display_name=display_name or "Клиент Telegram",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(contact)
    db.flush()
    return contact


def telegram_contact_by_chat_id(db: Session, external_chat_id: str) -> TelegramContact | None:
    return db.execute(
        select(TelegramContact).where(TelegramContact.external_chat_id == external_chat_id)
    ).scalar_one_or_none()


def get_or_create_contact_from_user(
    db: Session,
    company_id: str,
    external_chat_id: str,
    from_user: dict,
) -> TelegramContact:
    external_user_id = str(from_user.get("id", ""))
    display_name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x).strip()
    username = (from_user.get("username") or "").strip() or None

    contact = telegram_contact_by_chat_id(db, external_chat_id)

    if contact is None:
        return create_client_and_contact(
            db=db,
            company_id=company_id,
            external_user_id=external_user_id or external_chat_id,
            external_chat_id=external_chat_id,
            display_name=display_name or "Клиент Telegram",
            username=username,
        )

    contact.updated_at = datetime.utcnow()
    contact.external_username = username
    if display_name:
        contact.external_display_name = display_name
    return contact


def process_first_connection_start(
    db: Session,
    contact: TelegramContact,
    external_chat_id: str,
    notify_client: bool,
    *,
    first_connection_subject: str,
    first_connection_agent_text: str,
    first_connection_client_text: str,
    ensure_ticket_for_incoming_fn,
    append_system_message_fn,
    user_by_id_fn,
    send_message_fn,
    client_close_markup_fn,
    store_client_close_button_fn,
    notify_client_about_assignee_fn,
    response_message_id_fn,
) -> dict:
    ticket = ensure_ticket_for_incoming_fn(
        db,
        contact.company_id,
        contact.client_id,
        first_connection_subject,
    )
    template_values = defaultdict(str, {"username_text": "@username"})
    append_system_message_fn(
        db,
        ticket,
        first_connection_agent_text.format_map(template_values),
        agent_visible=True,
    )

    assigned_agent = None
    if ticket.assignee_id:
        assigned_agent = user_by_id_fn(db, ticket.assignee_id)

    db.commit()

    if notify_client:
        response = send_message_fn(external_chat_id, first_connection_client_text, reply_markup=client_close_markup_fn())
        store_client_close_button_fn(ticket, response, response_message_id_fn=response_message_id_fn)
        db.commit()
        if assigned_agent is not None:
            notify_client_about_assignee_fn(db, ticket, assigned_agent)

    return {
        "ok": True,
        "command": "start",
        "first_connection": True,
        "ticket_id": ticket.id,
        "status": ticket.status,
        "external_chat_id": external_chat_id,
    }
