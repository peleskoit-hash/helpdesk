from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_history import list_history
from ..services.web_history_messages import history_attachment, history_messages
from .tickets import _content_disposition_attachment
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/history', tags=['web-history'], route_class=WebAuthRoute)


class HistoryItem(BaseModel):
    id: str
    subject: str
    channel: Literal['web', 'telegram']
    status: str
    created_at: datetime
    has_web_result: bool
    read_at: datetime | None


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int
    page: int
    page_size: Literal[15]


@router.get('', response_model=HistoryPage)
def history(page: int = Query(1, ge=1), client: CurrentWebClient = Depends(get_current_web_client),
            db: Session = Depends(get_db)):
    return list_history(db, client, page)


class HistoryFile(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int | None
    source: str
    preview_kind: Literal['image', 'sticker'] | None
    download_url: str


class HistoryMessage(BaseModel):
    id: str
    message_type: Literal['client', 'agent']
    text: str
    created_at: datetime
    files: list[HistoryFile]
    wrong_problem_hint: str | None = None


class HistoryMessages(BaseModel):
    ticket_id: str
    items: list[HistoryMessage]


@router.get('/{ticket_id}/messages', response_model=HistoryMessages, response_model_exclude_unset=True)
def messages(ticket_id: UUID, client: CurrentWebClient = Depends(get_current_web_client),
             db: Session = Depends(get_db)):
    return history_messages(db, client, ticket_id)


@router.get('/{ticket_id}/attachments/{attachment_id}/download')
def download(ticket_id: UUID, attachment_id: UUID, client: CurrentWebClient = Depends(get_current_web_client),
             db: Session = Depends(get_db)):
    path, filename = history_attachment(db, client, ticket_id, attachment_id)
    return FileResponse(path, media_type='application/octet-stream', headers={
        'Content-Disposition': _content_disposition_attachment(filename),
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
    })
