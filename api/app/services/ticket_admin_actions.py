from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketAttachment, TicketMessage, TicketView


def _delete_ticket_hard(db: Session, ticket: Ticket) -> list[str]:
    warnings: list[str] = []
    child_ids = db.execute(select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id)).scalars().all()
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
