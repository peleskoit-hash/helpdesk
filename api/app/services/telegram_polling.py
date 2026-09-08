import asyncio
from datetime import datetime
import logging

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import TelegramContact, Ticket, TicketIntakeState, TicketStatus, TicketTaskKind, TicketWorkflowType
from .automatic_messages import render_automatic_message_for_telegram
from .telegram_bot import TelegramApiError
from .telegram_bot import get_updates
from .telegram_bot import send_message

_LOGGER = logging.getLogger(__name__)

DISTRIBUTION_WAIT_NOTICE_DELAY_SECONDS = 3
DISTRIBUTION_WAIT_NOTICE_TEXT = (
    "Ваша заявка находится на распределении. "
    "Как только сотрудники освободятся, Ваша заявка будет назначена."
)
DISTRIBUTION_WAIT_NOTICE_KEY = "telegram.ticket.distribution_wait.client"


def _is_regular_ticket_waiting_for_assignment(ticket: Ticket | None) -> bool:
    if ticket is None:
        return False
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    task_kind = ticket.task_kind or TicketTaskKind.NORMAL.value
    intake_state = ticket.intake_state or TicketIntakeState.READY.value
    return (
        workflow_type == TicketWorkflowType.TICKET.value
        and task_kind == TicketTaskKind.NORMAL.value
        and intake_state == TicketIntakeState.READY.value
        and ticket.status == TicketStatus.UNASSIGNED.value
        and ticket.assignee_id is None
        and not bool(ticket.is_special)
        and not bool(ticket.is_frozen)
        and ticket.closed_at is None
        and ticket.distribution_notice_sent_at is None
    )


def send_distribution_wait_notice_if_still_unassigned(db, ticket_id: str) -> bool:
    ticket = db.get(Ticket, ticket_id)
    if not _is_regular_ticket_waiting_for_assignment(ticket):
        return False
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        return False
    if bool(contact.is_blocked) or not str(contact.external_chat_id or "").strip():
        return False

    db.refresh(ticket)
    if not _is_regular_ticket_waiting_for_assignment(ticket):
        return False

    try:
        text, parse_mode = render_automatic_message_for_telegram(
            db,
            DISTRIBUTION_WAIT_NOTICE_KEY,
            DISTRIBUTION_WAIT_NOTICE_TEXT,
            ticket=ticket,
            contact=contact,
        )
        send_message(contact.external_chat_id, text, parse_mode=parse_mode)
    except TelegramApiError:
        db.rollback()
        _LOGGER.exception("Failed to send distribution wait notice for ticket %s", ticket_id)
        return False
    now = datetime.utcnow()
    ticket.distribution_notice_sent_at = now
    ticket.updated_at = now
    db.commit()
    return True


async def _notify_if_still_unassigned(ticket_id: str):
    await asyncio.sleep(DISTRIBUTION_WAIT_NOTICE_DELAY_SECONDS)
    try:
        with SessionLocal() as db:
            send_distribution_wait_notice_if_still_unassigned(db, ticket_id)
    except Exception as exc:
        print(f"Telegram delayed queue notification error: {exc}")


def _process_update_sync(update: dict) -> dict:
    with SessionLocal() as db:
        from ..routers.telegram import process_telegram_update

        return process_telegram_update(db, update, notify_client=True)


async def telegram_polling_loop():
    offset: int | None = None

    while True:
        try:
            with SessionLocal() as db:
                from ..routers.telegram import cleanup_expired_task_intents
                from .auto_close import cleanup_auto_close_tickets

                cleanup_expired_task_intents(db)
                cleanup_auto_close_tickets(db)

            updates = await asyncio.to_thread(get_updates, offset, settings.telegram_polling_timeout)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1

                result = await asyncio.to_thread(_process_update_sync, update)
                if result.get("status") == TicketStatus.UNASSIGNED.value and result.get("ticket_id"):
                    asyncio.create_task(
                        _notify_if_still_unassigned(
                            result["ticket_id"],
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Telegram polling error: {exc}")
            await asyncio.sleep(3)
