"""Client history reads live Ticket fields; publications only indicate an available result."""
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from ..models import Client, InternalTaskOccurrence, Ticket, WebTaskResult

PAGE_SIZE = 15


def history_scope(db, client):
    owner = db.get(Client, client.id)
    if owner is None:
        raise HTTPException(401, detail='Требуется WEB-авторизация')
    return (
        Ticket.client_id == owner.id, Ticket.company_id == owner.company_id,
        or_(Ticket.intake_state == 'ready', Ticket.intake_state.is_(None)),
        or_(Ticket.task_kind != 'group_child', Ticket.task_kind.is_(None)),
        ~select(InternalTaskOccurrence.id).where(InternalTaskOccurrence.ticket_id == Ticket.id).exists(),
    )


def owned_history_ticket(db, client, ticket_id):
    ticket = db.scalar(select(Ticket).where(Ticket.id == str(ticket_id), *history_scope(db, client)))
    if ticket is None:
        raise HTTPException(404, detail='Обращение не найдено')
    return ticket


def list_history(db, client, page):
    scope = history_scope(db, client)
    total = db.scalar(select(func.count()).select_from(Ticket).where(*scope))
    publication = and_(
        WebTaskResult.task_id == Ticket.id, WebTaskResult.client_id == Ticket.client_id,
        WebTaskResult.company_id == Ticket.company_id, Ticket.channel == 'web',
    )
    rows = db.execute(select(
        Ticket.id, Ticket.subject, Ticket.channel, Ticket.created_at,
        WebTaskResult.task_id.is_not(None).label('has_web_result'), WebTaskResult.read_at,
    ).outerjoin(WebTaskResult, publication).where(*scope)
        .order_by(Ticket.created_at.desc(), Ticket.id).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).mappings().all()
    return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': PAGE_SIZE}
