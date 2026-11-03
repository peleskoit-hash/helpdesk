"""Persistent technical WEB chat. Writers own the transaction; no implicit commit."""
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import Client, WebChatMessage
from .web_auth import as_utc


def record_message(db, client_id, *, message_type, author, text, created_at=None, event_key=None):
    """The first write for a client's event_key wins, including concurrent retries."""
    if message_type not in {'client', 'bot', 'system'}:
        raise ValueError('Invalid technical chat message type')
    if not isinstance(author, str) or not 1 <= len(author) <= 200:
        raise ValueError('Invalid technical chat author')
    if not isinstance(text, str):
        raise ValueError('Invalid technical chat text')
    if event_key is not None and (not isinstance(event_key, str) or not 1 <= len(event_key) <= 200):
        raise ValueError('Invalid technical chat event key')
    values = dict(client_id=client_id, message_type=message_type, author=author, text=text,
                  created_at=as_utc(created_at or datetime.now(timezone.utc)), event_key=event_key)
    # Serialize this client's insert + trim until the caller commits/rolls back.
    # SQLite's INSERT itself acquires the database write lock.
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(select(Client.id).where(Client.id == client_id).with_for_update()).scalar_one_or_none()
    table = WebChatMessage.__table__
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    statement = insert(table).values(**values)
    if event_key is not None:
        statement = statement.on_conflict_do_nothing(index_elements=[table.c.client_id, table.c.event_key])
    message_id = db.execute(statement.returning(table.c.id)).scalar_one_or_none()
    if message_id is not None:
        message = db.get(WebChatMessage, message_id)
        oldest = select(table.c.id).where(table.c.client_id == client_id).order_by(
            table.c.created_at.desc(), table.c.id.desc()).offset(50)
        db.execute(delete(WebChatMessage).where(
            WebChatMessage.client_id == client_id, WebChatMessage.id.in_(oldest)))
        return message
    return db.scalar(select(WebChatMessage).where(
        WebChatMessage.client_id == client_id, WebChatMessage.event_key == event_key))


def list_messages(db, client, page=1, page_size=50):
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError('Invalid technical chat pagination')
    owned = WebChatMessage.client_id == client.id
    total = db.scalar(select(func.count()).select_from(WebChatMessage).where(owned))
    rows = db.scalars(select(WebChatMessage).where(owned)
                      .order_by(WebChatMessage.created_at, WebChatMessage.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [dict(id=str(row.id), message_type=row.message_type, author=row.author,
                           text=row.text, created_at=as_utc(row.created_at)) for row in rows],
            'total': total, 'page': page, 'page_size': page_size}
