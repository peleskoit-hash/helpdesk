"""One persistent composer draft per client. Callers own the transaction."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import WebClientDraft
from .web_auth import as_utc


def _state(row):
    return {**row, 'created_at': as_utc(row['created_at']), 'updated_at': as_utc(row['updated_at'])}


def get_draft(db, client_id):
    table = WebClientDraft.__table__
    now = datetime.now(timezone.utc)
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    db.execute(insert(table).values(id=str(uuid4()), client_id=client_id, text='', version=0,
                                   created_at=now, updated_at=now)
               .on_conflict_do_nothing(index_elements=[table.c.client_id]))
    row = db.execute(select(table).where(table.c.client_id == client_id)).mappings().one()
    return _state(row)


def save_draft(db, client_id, text, version):
    get_draft(db, client_id)
    table = WebClientDraft.__table__
    row = db.execute(update(table).where(table.c.client_id == client_id, table.c.version == version)
                     .values(text=text, version=table.c.version + 1, updated_at=datetime.now(timezone.utc))
                     .returning(table)).mappings().one_or_none()
    if row is None:
        raise HTTPException(409, detail={'error': {'code': 'web_draft_version_conflict',
            'message': 'Черновик уже изменён. Загрузите актуальную версию.', 'details': {}}})
    return _state(row)
