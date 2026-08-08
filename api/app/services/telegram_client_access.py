from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TelegramContact


@dataclass(frozen=True)
class TelegramIncomingAccessDecision:
    blocked: bool
    contact: TelegramContact | None = None


def _identity_contacts(db: Session, company_id: str, contact: TelegramContact) -> list[TelegramContact]:
    external_user_id = (contact.external_user_id or "").strip()
    if external_user_id:
        return db.execute(
            select(TelegramContact).where(
                TelegramContact.company_id == company_id,
                TelegramContact.external_user_id == external_user_id,
            )
        ).scalars().all()
    return [contact]


def _known_contact_for_incoming(
    db: Session,
    company_id: str,
    external_chat_id: str,
    external_user_id: str,
) -> TelegramContact | None:
    normalized_user_id = (external_user_id or "").strip()
    if normalized_user_id:
        contact = db.execute(
            select(TelegramContact)
            .where(
                TelegramContact.company_id == company_id,
                TelegramContact.external_user_id == normalized_user_id,
            )
            .order_by(TelegramContact.updated_at.desc(), TelegramContact.created_at.asc(), TelegramContact.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if contact is not None:
            return contact

    normalized_chat_id = (external_chat_id or "").strip()
    if not normalized_chat_id:
        return None
    return db.execute(
        select(TelegramContact).where(
            TelegramContact.company_id == company_id,
            TelegramContact.external_chat_id == normalized_chat_id,
        )
    ).scalar_one_or_none()


def evaluate_incoming_telegram_access(
    db: Session,
    *,
    company_id: str,
    external_chat_id: str,
    external_user_id: str,
) -> TelegramIncomingAccessDecision:
    contact = _known_contact_for_incoming(db, company_id, external_chat_id, external_user_id)
    if contact is None:
        return TelegramIncomingAccessDecision(blocked=False, contact=None)
    blocked = any(item.is_blocked for item in _identity_contacts(db, company_id, contact))
    return TelegramIncomingAccessDecision(blocked=blocked, contact=contact)
