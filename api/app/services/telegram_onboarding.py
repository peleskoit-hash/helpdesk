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


def _is_negative_chat_id(external_chat_id: str | None) -> bool:
    return str(external_chat_id or "").strip().startswith("-")


def _is_private_chat_id_for_user(external_chat_id: str | None, external_user_id: str | None) -> bool:
    chat_id = str(external_chat_id or "").strip()
    user_id = str(external_user_id or "").strip()
    if not chat_id or chat_id.startswith("-"):
        return False
    if user_id and chat_id == user_id:
        return True
    return chat_id.isdigit()


def _should_update_contact_chat_id(current_chat_id: str | None, incoming_chat_id: str | None, external_user_id: str | None) -> bool:
    current = str(current_chat_id or "").strip()
    incoming = str(incoming_chat_id or "").strip()
    if not incoming:
        return False
    if not current:
        return True
    current_private = _is_private_chat_id_for_user(current, external_user_id)
    incoming_private = _is_private_chat_id_for_user(incoming, external_user_id)
    if current_private and _is_negative_chat_id(incoming):
        return False
    if _is_negative_chat_id(current) and incoming_private:
        return True
    if current_private and incoming_private:
        return current != incoming
    return current != incoming


def telegram_contact_by_chat_id(db: Session, external_chat_id: str, company_id: str | None = None) -> TelegramContact | None:
    stmt = select(TelegramContact).where(TelegramContact.external_chat_id == external_chat_id)
    if company_id is not None:
        stmt = stmt.where(TelegramContact.company_id == company_id)
    return db.execute(stmt).scalar_one_or_none()


def telegram_contact_by_user_id(db: Session, company_id: str, external_user_id: str) -> TelegramContact | None:
    if not external_user_id:
        return None
    return db.execute(
        select(TelegramContact)
        .where(
            TelegramContact.company_id == company_id,
            TelegramContact.external_user_id == external_user_id,
        )
        .order_by(TelegramContact.updated_at.desc(), TelegramContact.created_at.asc(), TelegramContact.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _refresh_contact_from_user(
    contact: TelegramContact,
    *,
    external_chat_id: str,
    username: str | None,
    display_name: str,
) -> TelegramContact:
    if _should_update_contact_chat_id(contact.external_chat_id, external_chat_id, contact.external_user_id):
        contact.external_chat_id = external_chat_id
    contact.external_username = username
    if display_name:
        contact.external_display_name = display_name
    contact.updated_at = datetime.utcnow()
    return contact


def get_or_create_contact_from_user(
    db: Session,
    company_id: str,
    external_chat_id: str,
    from_user: dict,
) -> TelegramContact:
    external_user_id = str(from_user.get("id", ""))
    display_name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x).strip()
    username = (from_user.get("username") or "").strip() or None

    chat_contact = telegram_contact_by_chat_id(db, external_chat_id, company_id=company_id)
    contact = telegram_contact_by_user_id(db, company_id, external_user_id)
    if contact is not None:
        if chat_contact is not None and chat_contact.id != contact.id:
            return _refresh_contact_from_user(
                contact,
                external_chat_id=contact.external_chat_id,
                username=username,
                display_name=display_name,
            )
        return _refresh_contact_from_user(
            contact,
            external_chat_id=external_chat_id,
            username=username,
            display_name=display_name,
        )

    contact = chat_contact

    if contact is None:
        return create_client_and_contact(
            db=db,
            company_id=company_id,
            external_user_id=external_user_id or external_chat_id,
            external_chat_id=external_chat_id,
            display_name=display_name or "Клиент Telegram",
            username=username,
        )

    if external_user_id and contact.external_user_id != external_user_id:
        contact.external_user_id = external_user_id
    return _refresh_contact_from_user(
        contact,
        external_chat_id=external_chat_id,
        username=username,
        display_name=display_name,
    )


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
