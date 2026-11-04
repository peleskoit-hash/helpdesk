from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_composer_draft import get_draft, save_draft
from .web_auth import WebAuthRoute
from .web_tasks import preload_upload, PreloadRetry
from .tickets import _content_disposition_attachment
from ..services import web_composer_draft_attachments as attachments
from ..services.telegram_outbound_delivery import deliver_attachment_preload

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


@router.post('/attachment-preloads')
def create_preload(background_tasks: BackgroundTasks, upload=Depends(preload_upload),
                   client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    row, schedule = attachments.create_preload(db, client, *upload)
    result = attachments.payload(row)
    if schedule:
        background_tasks.add_task(deliver_attachment_preload, preload_id=row.id)
    return result


@router.get('/attachment-preloads')
def list_preloads(client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return attachments.list_preloads(db, client)


@router.get('/attachment-preloads/{preload_id}')
def preload_status(preload_id: UUID, client: CurrentWebClient = Depends(get_current_web_client),
                   db: Session = Depends(get_db)):
    return attachments.status(db, client, preload_id)


@router.post('/attachment-preloads/{preload_id}/retry')
def retry_preload(preload_id: UUID, background_tasks: BackgroundTasks,
                  body: PreloadRetry = Body(default=PreloadRetry()),
                  client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    row, schedule = attachments.retry(db, client, preload_id, body.confirm_uncertain)
    result = attachments.payload(row)
    if schedule:
        background_tasks.add_task(deliver_attachment_preload, preload_id=row.id)
    return result


@router.delete('/attachment-preloads/{preload_id}')
def delete_preload(preload_id: UUID, client: CurrentWebClient = Depends(get_current_web_client),
                   db: Session = Depends(get_db)):
    return attachments.delete(db, client, preload_id)


@router.get('/attachment-preloads/{preload_id}/content')
def preload_content(preload_id: UUID, client: CurrentWebClient = Depends(get_current_web_client),
                    db: Session = Depends(get_db)):
    path, filename = attachments.content(db, client, preload_id)
    return FileResponse(path, media_type='application/octet-stream', headers={
        'Content-Disposition': _content_disposition_attachment(filename),
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
    })
