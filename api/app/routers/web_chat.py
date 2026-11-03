from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_chat import list_messages
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/chat', tags=['web-chat'], route_class=WebAuthRoute)


class ChatMessage(BaseModel):
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


@router.get('', response_model=ChatPage)
def chat(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
         client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return list_messages(db, client, page, page_size)
