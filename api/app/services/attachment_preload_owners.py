"""Owner checks shared by preload services; no WEB routes or intake transitions."""
from fastapi import HTTPException
from sqlalchemy import false, or_, select

from ..models import Client, InternalTaskOccurrence, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, User


def owner_scope(*, user_id=None, client_id=None):
    if (user_id is None) == (client_id is None):
        return (false(),)
    return (TicketAttachmentPreload.user_id == user_id, TicketAttachmentPreload.client_id == client_id)


def client_collecting_ticket(db, ticket_id, client_id):
    return db.scalar(select(Ticket).join(Client, Client.id == Ticket.client_id).where(
        Ticket.id == str(ticket_id), Client.id == client_id, Ticket.company_id == Client.company_id,
        Ticket.channel == 'web', Ticket.workflow_type == 'task', Ticket.task_kind == 'normal',
        Ticket.intake_state == 'collecting', Ticket.status == 'unassigned', Ticket.assignee_id.is_(None),
        ~select(InternalTaskOccurrence.id).where(InternalTaskOccurrence.ticket_id == Ticket.id).exists()))


def owned_client_preload(db, preload_id, ticket_id, client_id, *, allow_bound=False):
    row = db.scalar(select(TicketAttachmentPreload).where(TicketAttachmentPreload.id == str(preload_id),
        TicketAttachmentPreload.ticket_id == str(ticket_id), *owner_scope(client_id=client_id)))
    allowed = row is not None and (client_collecting_ticket(db, ticket_id, client_id) is not None
                                  or (allow_bound and client_bound_attachment(db, row) is not None))
    if not allowed:
        raise HTTPException(404, detail='Предзагруженный файл не найден')
    return row


def archive_owner(db, preload):
    if preload.client_id is not None and preload.user_id is None:
        client = db.get(Client, preload.client_id)
        ticket = db.get(Ticket, preload.ticket_id)
        if client and ticket and ticket.client_id == client.id and ticket.company_id == client.company_id:
            return client
        return None
    if preload.user_id is not None and preload.client_id is None:
        return db.get(User, preload.user_id)
    return None


def client_bound_attachment(db, preload):
    """A final_attachment_id by itself is not proof of an accepted client file."""
    if preload.client_id is None or preload.user_id is not None or not preload.final_attachment_id:
        return None
    return db.scalar(select(TicketAttachment).join(TicketMessage, TicketMessage.id == TicketAttachment.message_id)
        .join(Ticket, Ticket.id == TicketAttachment.ticket_id).join(Client, Client.id == Ticket.client_id).where(
            TicketAttachment.id == preload.final_attachment_id, Ticket.id == preload.ticket_id,
            TicketMessage.ticket_id == Ticket.id, TicketMessage.message_type == 'client',
            TicketMessage.author_user_id.is_(None), or_(TicketMessage.message_status.is_(None), TicketMessage.message_status != 'deleted'),
            Client.id == preload.client_id, Client.company_id == Ticket.company_id,
            Ticket.channel == 'web', Ticket.workflow_type == 'task', Ticket.task_kind == 'normal',
            Ticket.intake_state.in_(['collecting', 'ready']),
            ~select(InternalTaskOccurrence.id).where(InternalTaskOccurrence.ticket_id == Ticket.id).exists()))


def client_preload_allowed(db, preload):
    # Completion may continue after intake accepted the already-bound initial message.
    return (preload.client_id is None or client_bound_attachment(db, preload) is not None
            or (preload.user_id is None and client_collecting_ticket(db, preload.ticket_id, preload.client_id) is not None))
