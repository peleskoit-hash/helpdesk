from __future__ import annotations

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..models import Ticket, TicketNumberCounter


TICKET_NUMBER_ALLOCATION_ADVISORY_LOCK_KEY = 5537870853908772136
"""Stable PostgreSQL transaction advisory lock key for ticket_number allocation."""


def _bind_dialect_name(db: Session) -> str | None:
    bind = db.get_bind()
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", None)


def _lock_ticket_number_allocation(db: Session) -> None:
    if _bind_dialect_name(db) != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": TICKET_NUMBER_ALLOCATION_ADVISORY_LOCK_KEY},
    )


def ensure_ticket_number(db: Session, ticket: Ticket) -> int:
    if ticket.ticket_number is not None:
        return ticket.ticket_number
    _lock_ticket_number_allocation(db)
    number = db.execute(
        update(TicketNumberCounter)
        .where(TicketNumberCounter.id == 1)
        .values(last_number=TicketNumberCounter.last_number + 1)
        .returning(TicketNumberCounter.last_number)
    ).scalar_one_or_none()
    if number is None:
        # Bootstrap a metadata-created database once; the UPDATE above serializes SQLite writers.
        max_number = db.execute(select(func.max(Ticket.ticket_number))).scalar_one() or 0
        number = int(max_number) + 1
        db.add(TicketNumberCounter(id=1, last_number=number))
    ticket.ticket_number = number
    db.flush()
    return ticket.ticket_number
