from __future__ import annotations

from datetime import datetime, timedelta
import logging
import uuid

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from ..models import MessageType, TelegramContact, Ticket, TicketIntakeState, TicketMessage, TicketStatus, TicketWorkflowType
from .automatic_messages import render_automatic_message_for_telegram
from .statistics_lifecycle import sync_work_interval_after_close
from .telegram_bot import send_message

AUTO_CLOSE_MINUTES = 20
AUTO_CLOSE_CLIENT_TEXT = (
    "Ваша заявка закрыта в связи с отсутствием активности в ней, "
    "если Ваша проблема не была решена, пожалуйста, создайте новую нажав /start"
)
_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


def _is_ticket_auto_close_scope(ticket: Ticket) -> bool:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    intake_state = ticket.intake_state or TicketIntakeState.READY.value
    return (
        workflow_type == TicketWorkflowType.TICKET.value
        and intake_state != TicketIntakeState.COLLECTING.value
        and ticket.status in {TicketStatus.NEW.value, TicketStatus.PENDING.value}
        and not bool(ticket.is_frozen)
        and not bool(ticket.is_special)
        and not bool(ticket.auto_close_disabled)
    )


def _last_client_visible_message(db: Session, ticket_id: str) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == ticket_id,
                TicketMessage.message_type.in_([MessageType.CLIENT.value, MessageType.AGENT.value]),
            )
        )
        .order_by(desc(TicketMessage.created_at), desc(TicketMessage.id))
        .limit(1)
    ).scalar_one_or_none() if ticket_id else None


def clear_ticket_auto_close_timer(ticket: Ticket):
    ticket.auto_close_started_at = None
    ticket.auto_close_due_at = None


def refresh_ticket_auto_close_timer(db: Session, ticket: Ticket, restart_from_now: bool = False):
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    if workflow_type != TicketWorkflowType.TICKET.value:
        clear_ticket_auto_close_timer(ticket)
        return

    if ticket.status == TicketStatus.CLOSED.value:
        clear_ticket_auto_close_timer(ticket)
        return

    if not _is_ticket_auto_close_scope(ticket):
        clear_ticket_auto_close_timer(ticket)
        return

    last_message = _last_client_visible_message(db, ticket.id)
    if last_message is None:
        clear_ticket_auto_close_timer(ticket)
        return

    if last_message.message_type == MessageType.CLIENT.value:
        clear_ticket_auto_close_timer(ticket)
        return

    started_at = _now() if restart_from_now else (last_message.created_at or _now())
    ticket.auto_close_started_at = started_at
    ticket.auto_close_due_at = started_at + timedelta(minutes=AUTO_CLOSE_MINUTES)


def can_use_auto_close_controls(ticket: Ticket) -> bool:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    intake_state = ticket.intake_state or TicketIntakeState.READY.value
    return (
        workflow_type == TicketWorkflowType.TICKET.value
        and intake_state != TicketIntakeState.COLLECTING.value
        and ticket.status != TicketStatus.CLOSED.value
        and not bool(ticket.is_special)
    )


def _add_system_message(db: Session, ticket: Ticket, body: str, now: datetime) -> None:
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.SYSTEM.value,
            body=body,
            author_user_id=None,
            agent_visible=True,
            message_status="normal",
            created_at=now,
        )
    )


def _send_auto_close_to_client(db: Session, ticket: Ticket) -> None:
    if ticket.channel == "web":
        return
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        return
    text, parse_mode = render_automatic_message_for_telegram(db, "telegram.auto_close.client", AUTO_CLOSE_CLIENT_TEXT, ticket=ticket)
    send_message(contact.external_chat_id, text, parse_mode=parse_mode, remove_keyboard=True)


def cleanup_auto_close_tickets(db: Session) -> int:
    now = datetime.utcnow()
    tickets = db.execute(
        select(Ticket).where(
            and_(
                Ticket.workflow_type == TicketWorkflowType.TICKET.value,
                Ticket.status.in_([TicketStatus.NEW.value, TicketStatus.PENDING.value]),
                Ticket.intake_state != TicketIntakeState.COLLECTING.value,
                Ticket.is_frozen.is_(False),
                Ticket.is_special.is_(False),
                Ticket.auto_close_disabled.is_(False),
                Ticket.auto_close_due_at.is_not(None),
                Ticket.auto_close_due_at <= now,
            )
        )
    ).scalars().all()
    closed = 0
    for ticket in tickets:
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_at = now
        ticket.auto_closed_at = now
        ticket.is_frozen = False
        ticket.freeze_until = None
        sync_work_interval_after_close(db, ticket, now=now)
        ticket.closed_by_user_id = None
        clear_ticket_auto_close_timer(ticket)
        ticket.updated_at = now
        _add_system_message(db, ticket, "Заявка автоматически закрыта из-за отсутствия активности", now)
        try:
            _send_auto_close_to_client(db, ticket)
        except Exception:
            _LOGGER.exception("Failed to send auto-close notification for ticket %s", ticket.id)
            _add_system_message(db, ticket, "Не удалось отправить клиенту сообщение об автозакрытии", now)
        closed += 1
    if closed:
        db.commit()
    return closed
