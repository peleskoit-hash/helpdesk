from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_composer_draft import get_draft, save_draft
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/composer-draft', tags=['web-composer-draft'], route_class=WebAuthRoute)


class DraftState(BaseModel):
    id: UUID
    text: str
    version: int
    created_at: datetime
    updated_at: datetime


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    text: str = Field(max_length=10000, strict=True, pattern=r'^[^\x00]*$')
    version: int = Field(ge=0, strict=True)


@router.get('', response_model=DraftState)
def read_draft(client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    draft = get_draft(db, client.id)
    db.commit()
    return draft


@router.put('', response_model=DraftState)
def update_draft(payload: DraftUpdate, client: CurrentWebClient = Depends(get_current_web_client),
                 db: Session = Depends(get_db)):
    draft = save_draft(db, client.id, payload.text, payload.version)
    db.commit()
    return draft
