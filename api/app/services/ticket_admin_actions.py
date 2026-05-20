from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketAttachment, TicketMessage, TicketView


def _delete_ticket_hard(db: Session, ticket: Ticket) -> list[str]:
    warnings: list[str] = []
    attachment_rows = db.execute(
        select(TicketAttachment.local_path).where(TicketAttachment.ticket_id == ticket.id)
    ).all()
    for row in attachment_rows:
        file_path = row[0]
        if not file_path:
            continue
        try:
            path_obj = Path(file_path)
            if path_obj.exists():
                os.remove(path_obj)
        except Exception as exc:
            warnings.append(f"{ticket.ticket_number or ticket.id}: {file_path} ({exc})")

    child_ids = db.execute(select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id)).scalars().all()
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
