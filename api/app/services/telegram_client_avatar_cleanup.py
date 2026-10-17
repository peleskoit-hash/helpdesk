"""Remove full avatars after committed ticket closures/deletions, without network I/O."""
import logging

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import TelegramContact, Ticket, TicketStatus
from .telegram_client_avatars import delete_full_avatar


logger = logging.getLogger(__name__)
_PENDING_KEY = "telegram_client_avatar_cleanup"


def schedule_avatar_cleanup(db: Session, company_id: str, client_id: str) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(_PENDING_KEY, {}).setdefault(transaction, set()).add((company_id, client_id))


@event.listens_for(Session, "before_flush")
def _before_flush(db: Session, *_args) -> None:
    # Serialize new intake with avatar cleanup: a committed new ticket must be
    # visible before a cleaner holding the same contact lock checks inactivity.
    clients = sorted({(ticket.company_id, ticket.client_id) for ticket in db.new if isinstance(ticket, Ticket)})
    for company_id, client_id in clients:
        db.execute(select(TelegramContact.id).where(
            TelegramContact.company_id == company_id,
            TelegramContact.client_id == client_id,
        ).order_by(TelegramContact.id).with_for_update()).all()
    for ticket in db.deleted.union(db.dirty).union(db.new):
        if isinstance(ticket, Ticket) and (
            ticket in db.deleted or (
                ticket.status == TicketStatus.CLOSED.value
                and inspect(ticket).attrs.status.history.has_changes()
            )
        ):
            schedule_avatar_cleanup(db, ticket.company_id, ticket.client_id)


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    pending = db.info.get(_PENDING_KEY, {})
    nested = db.get_nested_transaction()
    if nested is not None:
        pending.setdefault(nested.parent, set()).update(pending.pop(nested, set()))
        return
    db.info.pop(_PENDING_KEY, None)
    clients = set().union(*pending.values()) if pending else set()
    for company_id, client_id in clients:
        try:
            # Own session: never commit/rollback the ticket's transaction here.
            with SessionLocal(bind=db.get_bind()) as cleanup_db:
                contact_ids = cleanup_db.scalars(select(TelegramContact.id).where(
                    TelegramContact.company_id == company_id,
                    TelegramContact.client_id == client_id,
                    TelegramContact.avatar_path.is_not(None),
                )).all()
                for contact_id in contact_ids:
                    # Recheck all workflows under the same contact lock used by save_avatar.
                    delete_full_avatar(cleanup_db, contact_id, only_if_inactive=True)
        except Exception:
            logger.warning("Telegram client avatar cleanup failed")


@event.listens_for(Session, "after_rollback")
def _after_rollback(db: Session) -> None:
    nested = db.get_nested_transaction()
    if nested is not None:
        db.info.get(_PENDING_KEY, {}).pop(nested, None)
    else:
        db.info.pop(_PENDING_KEY, None)
