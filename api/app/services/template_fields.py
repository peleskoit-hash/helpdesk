from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ClientProfile, TelegramContact, Ticket, TicketMessage, User

_FIELD_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
_EMPTY = "не указано"


def _value(value: object) -> str:
    if value is None:
        return _EMPTY
    text = str(value).strip()
    return text if text else _EMPTY


def _profile(db: Session, ticket: Ticket | None) -> ClientProfile | None:
    if ticket is None:
        return None
    return db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()


def _contact(db: Session, ticket: Ticket | None, contact: TelegramContact | None) -> TelegramContact | None:
    if contact is not None:
        return contact
    if ticket is None:
        return None
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()


def _first_client_message(db: Session, ticket: Ticket | None) -> str | None:
    if ticket is None:
        return None
    message = db.execute(
        select(TicketMessage.body)
        .where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == "client")
        .order_by(TicketMessage.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    return message


def _formatted_created_at(ticket: Ticket | None) -> str | None:
    if ticket is None or ticket.created_at is None:
        return None
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    return created_at.astimezone(ZoneInfo(settings.app_timezone)).strftime("%d.%m.%Y, %H:%M")


def template_context(
    db: Session,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
) -> dict[str, str]:
    profile = _profile(db, ticket)
    contact = _contact(db, ticket, contact)
    profile_verified = bool(profile and profile.is_verified)

    first_name = profile.contact_name if profile_verified and profile else None
    last_name = profile.last_name if profile_verified and profile else None
    full_name = " ".join(part for part in [last_name, first_name] if part).strip() or None

    username = None
    if contact and contact.external_username:
        username = contact.external_username.strip()
        if username and not username.startswith("@"):
            username = f"@{username}"

    return {
        "contact_full_name": _value(full_name),
        "contact_first_name": _value(first_name),
        "contact_last_name": _value(last_name),
        "verification_status": _value("да" if profile_verified else "нет"),
        "destination": _value(profile.destination if profile_verified and profile else None),
        "department": _value(profile.department if profile_verified and profile else None),
        "sand_owner": _value(profile.sand_owner if profile_verified and profile else None),
        "sales_group": _value(profile.sales_group if profile_verified and profile else None),
        "location_type": _value(profile.location_type if profile_verified and profile else None),
        "anydesk": _value(profile.anydesk if profile_verified and profile else None),
        "internal_number": _value(profile.internal_number if profile_verified and profile else None),
        "bitrix_login": _value(profile.bitrix_login if profile_verified and profile else None),
        "bitrix_password": _value(profile.bitrix_password if profile_verified and profile else None),
        "bitrix_id": _value(profile.bitrix_id if profile_verified and profile else None),
        "telegram_username": _value(username),
        "telegram_display_name": _value(contact.external_display_name if contact else None),
        "telegram_user_id": _value(contact.external_user_id if contact else None),
        "telegram_chat_id": _value(contact.external_chat_id if contact else None),
        "ticket_subject": _value(ticket.subject if ticket else None),
        "ticket_created_at": _value(_formatted_created_at(ticket)),
        "ticket_id": _value(ticket.id if ticket else None),
        "client_message": _value(client_message or _first_client_message(db, ticket)),
        "agent_full_name": _value(agent.full_name if agent else None),
    }


def render_template_text(
    text: str | None,
    db: Session,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
) -> str | None:
    if text is None:
        return None
    context = template_context(db, ticket=ticket, contact=contact, agent=agent, client_message=client_message)
    return _FIELD_RE.sub(lambda match: context.get(match.group(1), _EMPTY), text)
