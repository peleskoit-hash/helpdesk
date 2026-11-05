from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_chat import list_messages
from .web_auth import WebAuthRoute
from ..services.web_file_notifications import attachment_too_large
from ..services.web_task_attachments import MAX_FILE_SIZE

router = APIRouter(prefix='/api/v1/web/chat', tags=['web-chat'], route_class=WebAuthRoute)


class ChatMessage(BaseModel):
    notice_event_id: UUID | None = None
    id: str
    created_at: datetime
    message_type: Literal['client', 'bot', 'system']
    author: str
    text: str


class ChatPage(BaseModel):
    items: list[ChatMessage]
    total: int
    page: int
    page_size: int


@router.get('', response_model=ChatPage, response_model_exclude_none=True)
def chat(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
         client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return list_messages(db, client, page, page_size)


class LargeAttachmentAttempt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    file_name: str = Field(min_length=1, max_length=255, strict=True, pattern=r'^[^\x00-\x1f\x7f]+$')
    file_size: int = Field(gt=MAX_FILE_SIZE, le=9007199254740991, strict=True)
    event_id: UUID


class NoticeSaved(BaseModel):
    ok: Literal[True] = True
    message_id: str


@router.post('/attachment-too-large', response_model=NoticeSaved)
def large_attachment(payload: LargeAttachmentAttempt,
                     client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    message = attachment_too_large(db, client, payload.file_name, payload.file_size, payload.event_id)
    message_id = str(message.id)
    db.commit()
    return NoticeSaved(message_id=message_id)
