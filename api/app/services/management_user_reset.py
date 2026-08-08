from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import Client, ClientProfile, TelegramContact, Ticket, TicketIntakeState, TicketStatus, TicketTaskKind, TicketWorkflowType
from ..schemas import ClientProfileOut


RESET_FALLBACK_DISPLAY_NAME = "\u041a\u043b\u0438\u0435\u043d\u0442 Telegram"


@dataclass(frozen=True)
class ResetManagementUserDataResult:
    client_id: str
    telegram_contact_id: str
    is_blocked: bool
    profile: ClientProfileOut


class ResetManagementUserDataError(RuntimeError):
    pass


class ResetManagementUserDataActiveTicketError(ResetManagementUserDataError):
    pass


def _dedupe_contacts_for_client(db: Session, client_id: str) -> tuple[TelegramContact, list[TelegramContact]]:
    contact = db.execute(
        select(TelegramContact)
        .where(TelegramContact.client_id == client_id)
        .order_by(TelegramContact.updated_at.desc(), TelegramContact.created_at.desc())
    ).scalar_one_or_none()
    if contact is None:
        raise LookupError("telegram_contact_not_found")
    external_user_id = (contact.external_user_id or "").strip()
    if not external_user_id:
        return contact, [contact]
    contacts = db.execute(
        select(TelegramContact).where(
            TelegramContact.company_id == contact.company_id,
            TelegramContact.external_user_id == external_user_id,
        )
    ).scalars().all()
    return contact, contacts or [contact]


def _reset_display_name(contact: TelegramContact) -> str:
    display_name = str(contact.external_display_name or "").strip()
    return display_name or RESET_FALLBACK_DISPLAY_NAME


def _active_ordinary_ticket_exists(db: Session, company_id: str, client_ids: list[str]) -> bool:
    if not client_ids:
        return False
    return db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.client_id.in_(client_ids),
                Ticket.status != TicketStatus.CLOSED.value,
                or_(
                    Ticket.workflow_type.is_(None),
                    Ticket.workflow_type.notin_([TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]),
                ),
                or_(Ticket.intake_state.is_(None), Ticket.intake_state == TicketIntakeState.READY.value),
            )
        )
        .limit(1)
    ).first() is not None


def _active_task_exists(db: Session, company_id: str, client_ids: list[str]) -> bool:
    if not client_ids:
        return False
    return db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.client_id.in_(client_ids),
                Ticket.status != TicketStatus.CLOSED.value,
                Ticket.workflow_type.in_([TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]),
                or_(
                    Ticket.task_kind.is_(None),
                    Ticket.task_kind != TicketTaskKind.GROUP_PARENT.value,
                ),
            )
        )
        .limit(1)
    ).first() is not None


def reset_management_user_data(db: Session, client_id: str) -> ResetManagementUserDataResult:
    requested_contact, contacts = _dedupe_contacts_for_client(db, client_id)
    contacts_by_client_id: dict[str, TelegramContact] = {}
    for contact in contacts:
        contacts_by_client_id.setdefault(contact.client_id, contact)

    client_ids = list(contacts_by_client_id.keys())
    if (
        _active_ordinary_ticket_exists(db, requested_contact.company_id, client_ids)
        or _active_task_exists(db, requested_contact.company_id, client_ids)
    ):
        raise ResetManagementUserDataActiveTicketError("client_has_active_ticket")
    profiles = db.execute(select(ClientProfile).where(ClientProfile.client_id.in_(client_ids))).scalars().all()
    for profile in profiles:
        db.delete(profile)

    clients = db.execute(select(Client).where(Client.id.in_(client_ids))).scalars().all()
    for client in clients:
        contact = contacts_by_client_id.get(client.id)
        if contact is not None:
            client.display_name = _reset_display_name(contact)

    db.commit()

    return ResetManagementUserDataResult(
        client_id=client_id,
        telegram_contact_id=requested_contact.id,
        is_blocked=bool(requested_contact.is_blocked),
        profile=ClientProfileOut(
            telegram_username=requested_contact.external_username,
            telegram_display_name=requested_contact.external_display_name,
        ),
    )
