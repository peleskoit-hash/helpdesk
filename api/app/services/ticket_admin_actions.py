from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketAttachment, TicketMessage, TicketView
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
    attachment_rows = db.execute(
        select(TicketAttachment.local_path).where(TicketAttachment.ticket_id.in_(attachment_ticket_ids))
    ).all()
    attachment_paths = list(dict.fromkeys(row[0] for row in attachment_rows if row[0]))
    for file_path in attachment_paths:
        try:
            path_obj = Path(file_path)
            if path_obj.exists():
                os.remove(path_obj)
        except Exception as exc:
            warnings.append(f"{ticket.ticket_number or ticket.id}: {file_path} ({exc})")

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
