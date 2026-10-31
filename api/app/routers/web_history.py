from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_history import list_history
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/history', tags=['web-history'], route_class=WebAuthRoute)


class HistoryItem(BaseModel):
    id: str
    subject: str
    channel: Literal['web', 'telegram']
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
