from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketAttachment, TicketMessage, TicketView, WebTaskResult, WebTaskResultFile
from .telegram_client_avatar_cleanup import schedule_avatar_cleanup


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
