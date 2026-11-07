"""Remove unfinished ordinary client Telegram intake while reception is closed."""
import logging

from sqlalchemy import select

from ..db import SessionLocal
from ..models import TelegramContact, Ticket, TicketMessage
from .automatic_messages import render_automatic_message_for_telegram
from .button_config import resolve_button_row
from .internal_tasks import is_internal_task_ticket
from .telegram_bot import remove_inline_keyboard, send_message
from .telegram_client_close import remove_client_close_button
from .telegram_intake_hours import is_telegram_intake_open
from .telegram_menu_flow import _remove_menu_keyboard
from .ticket_admin_actions import (
    delete_preliminary_telegram_ticket,
    delete_uncreated_normal_task,
    delete_uncreated_telegram_group_parent_task,
    is_preliminary_telegram_ticket,
    is_uncreated_normal_task,
    is_uncreated_telegram_group_parent_task,
)


logger = logging.getLogger(__name__)
BATCH_SIZE = 50


def _has_automation(db, ticket) -> bool:
    if ticket.quick_action_state_json or ticket.workflow_type == "auto":
        return True
    for key in (ticket.button_config_key, ticket.instruction_button_key):
        row = resolve_button_row(db, key, include_inactive=True) if key else None
        if row is not None and (row.automation_key or row.workflow_type == "auto"):
            return True
    return False


def is_ordinary_telegram_task_draft(db, ticket) -> bool:
    """Identify the ordinary description path, independently of deletion safety."""
    return (ticket.channel == "telegram" and ticket.intake_state == "collecting"
            and ticket.workflow_type == "task" and ticket.task_kind in {"normal", "group_parent"}
            and not ticket.is_special
            and not _has_automation(db, ticket) and not is_internal_task_ticket(db, ticket))


def _draft_deleter(db, ticket):
    db.flush()
    if not (ticket.channel == "telegram" and ticket.intake_state == "collecting"
            and ticket.status == "unassigned" and ticket.assignee_id is None
            and ticket.closed_at is None and ticket.ticket_number is None
            and ticket.parent_ticket_id is None and not ticket.is_special):
        return None
    if _has_automation(db, ticket) or is_internal_task_ticket(db, ticket):
        return None
    if db.scalar(select(TicketMessage.id).where(
        TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == "client").limit(1)) is not None:
        return None
    if db.scalar(select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id).limit(1)) is not None:
        return None
    if ticket.workflow_type == "ticket":
        selected = resolve_button_row(db, ticket.button_config_key, include_inactive=True) if ticket.button_config_key else None
        if (selected is not None and not selected.automation_key and selected.button_kind != "section"
                and selected.workflow_type in {"ticket", "task", "group_task"}
                and selected.confirmation_enabled and is_preliminary_telegram_ticket(db, ticket)):
            return delete_preliminary_telegram_ticket
    elif is_ordinary_telegram_task_draft(db, ticket):
        if is_uncreated_normal_task(db, ticket):
            return delete_uncreated_normal_task
        if is_uncreated_telegram_group_parent_task(db, ticket):
            return delete_uncreated_telegram_group_parent_task
    return None


def run_telegram_after_hours_cleanup_once(*, session_factory=None) -> int:
    """Scan in small batches; commit each deletion before attempting delivery."""
    if is_telegram_intake_open():
        return 0
    session_factory = session_factory or SessionLocal
    deleted = 0
    after_id = ""
    while True:
        with session_factory() as db:
            ids = list(db.scalars(select(Ticket.id).where(
                Ticket.id > after_id, Ticket.channel == "telegram", Ticket.intake_state == "collecting",
                Ticket.status == "unassigned", Ticket.assignee_id.is_(None),
            ).order_by(Ticket.id).limit(BATCH_SIZE)))
        if not ids:
            return deleted
        for ticket_id in ids:
            if is_telegram_intake_open():
                return deleted
            try:
                with session_factory() as db:
                    ticket = db.get(Ticket, ticket_id)
                    delete = _draft_deleter(db, ticket) if ticket is not None else None
                    if delete is None:
                        continue
                    contact = db.scalar(select(TelegramContact).where(
                        TelegramContact.company_id == ticket.company_id, TelegramContact.client_id == ticket.client_id,
                    ).limit(1))
                    if contact is None or not contact.external_chat_id:
                        continue
                    chat_id = contact.external_chat_id
                    text, mode = render_automatic_message_for_telegram(
                        db, "telegram.after_hours.creation_blocked.client", ticket=ticket, contact=contact,
                    )
                    _remove_menu_keyboard(db, ticket, fallback_chat_id=chat_id)
                    remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
                    delete(db, ticket)
                    db.commit()
                    deleted += 1
            except Exception:
                logger.exception("Telegram after-hours cleanup failed for ticket %s", ticket_id)
                continue
            try:
                send_message(chat_id, text, parse_mode=mode)
            except Exception:
                logger.exception("Telegram after-hours notification failed for ticket %s", ticket_id)
        after_id = ids[-1]
