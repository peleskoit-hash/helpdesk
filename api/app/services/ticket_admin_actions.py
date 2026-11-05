from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StatsCompletion, StatsWorkInterval, Ticket, TicketAttachment, TicketMessage, TicketView, WebTaskResult, WebTaskResultFile
from .button_config import resolve_button_row
from .telegram_client_avatar_cleanup import schedule_avatar_cleanup


def is_preliminary_telegram_ticket(db: Session, ticket: Ticket) -> bool:
    """Only an unformed /start ticket; never a task or a terminal automation."""
    if not (
        ticket.channel == "telegram"
        and ticket.workflow_type == "ticket"
        and ticket.intake_state == "collecting"
        and ticket.ticket_number is None
        and ticket.status == "unassigned"
        and ticket.assignee_id is None
        and ticket.closed_at is None
        and ticket.task_kind == "normal"
        and ticket.parent_ticket_id is None
        and not ticket.is_special
        and not ticket.quick_action_state_json
    ):
        return False
    for key in (ticket.instruction_button_key, ticket.button_config_key):
        if key:
            button = resolve_button_row(db, key, include_inactive=True)
            if button is not None and button.automation_key:
                return False
    return True


def delete_preliminary_telegram_ticket(db: Session, ticket: Ticket) -> None:
    if not is_preliminary_telegram_ticket(db, ticket):
        raise ValueError("Only an unformed Telegram ticket can be cancelled by deletion")
    # Flush pending system messages before the bulk correspondence deletion.
    db.flush()
    for model in (StatsCompletion, StatsWorkInterval):
        db.execute(model.__table__.delete().where(
            model.company_id == ticket.company_id, model.object_id == ticket.id, model.object_type == "ticket",
        ))
    _delete_ticket_hard(db, ticket)


def _delete_ticket_hard(db: Session, ticket: Ticket) -> list[str]:
    warnings: list[str] = []
    children = db.execute(select(Ticket.id, Ticket.company_id, Ticket.client_id).where(Ticket.parent_ticket_id == ticket.id)).all()
    child_ids = [child.id for child in children]
    for child in children:
        schedule_avatar_cleanup(db, child.company_id, child.client_id)
    attachment_ticket_ids = [ticket.id]
    if child_ids:
        attachment_ticket_ids.extend(child_ids)
    from .telegram_outbound_delivery import schedule_ticket_upload_delete
    schedule_ticket_upload_delete(db, attachment_ticket_ids, warnings)

    # Publications have no FK to mutable correspondence. Delete blobs before their result,
    # for both the selected ticket and children, in the caller's existing transaction.
    db.execute(WebTaskResultFile.__table__.delete().where(WebTaskResultFile.result_task_id.in_(attachment_ticket_ids)))
    db.execute(WebTaskResult.__table__.delete().where(WebTaskResult.task_id.in_(attachment_ticket_ids)))

    if child_ids:
        db.execute(TicketAttachment.__table__.delete().where(TicketAttachment.ticket_id.in_(child_ids)))
        db.execute(TicketMessage.__table__.delete().where(TicketMessage.ticket_id.in_(child_ids)))
        db.execute(TicketView.__table__.delete().where(TicketView.ticket_id.in_(child_ids)))
        db.execute(Ticket.__table__.delete().where(Ticket.id.in_(child_ids)))

    db.execute(TicketAttachment.__table__.delete().where(TicketAttachment.ticket_id == ticket.id))
    db.execute(TicketMessage.__table__.delete().where(TicketMessage.ticket_id == ticket.id))
    db.execute(TicketView.__table__.delete().where(TicketView.ticket_id == ticket.id))
    db.delete(ticket)
    return warnings
