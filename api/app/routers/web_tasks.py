from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..db import get_db
from ..models import Client
from ..services import web_auth, web_tasks, telegram_upload_state
from ..services.telegram_outbound_delivery import deliver_attachment_preload
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/tasks', tags=['web-tasks'], route_class=WebAuthRoute)


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    button_id: str = Field(min_length=1, max_length=36)
    request_id: UUID = Field(description='Stable UUID for one selection; reuse on retries, including after a timeout')
    confirmed: bool = False


class Description(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=10000)
    preload_ids: list[UUID] = Field(default_factory=list, max_length=1000)


class TaskOut(BaseModel):
    task_id: UUID
    state: Literal['collecting', 'confirmation_required', 'ready', 'cancelled']
    status: str
    text: str
    expires_at: datetime | None


def task_context(request: Request, client=Depends(web_auth.get_current_web_client), db: Session = Depends(get_db)):
    web_tasks.lock_client(db, client.id)
    # A request may have waited for another worker; recheck the session under the lock.
    client = web_auth.get_current_web_client(request, db)
    return db.get(Client, client.id), client


@router.post('/select', response_model=TaskOut)
def select(payload: Selection, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.select_task(db, *context, payload.button_id, payload.request_id, payload.confirmed)


async def description_payload(request: Request, client=Depends(web_auth.get_current_web_client)):
    form = None
    try:
        try:
            if request.headers.get('content-type', '').lower().startswith('multipart/form-data'):
                form = await request.form()
                if set(form) - {'text', 'files'} or len(form.getlist('text')) != 1:
                    raise HTTPException(422, detail='Некорректное описание задачи')
                payload = Description.model_validate({'text': form.get('text')})
                uploads = form.getlist('files')
                if not all(isinstance(file, UploadFile) for file in uploads):
                    raise HTTPException(422, detail='Некорректные файлы')
            else:
                payload = Description.model_validate(await request.json())
                uploads = []
        except ValueError as exc:
            raise HTTPException(422, detail='Некорректное описание задачи') from exc
        yield payload.text, uploads, [str(value) for value in payload.preload_ids]
    finally:
        if form is not None:
            await form.close()


@router.post('/{task_id}/description', response_model=TaskOut)
def describe(task_id: UUID, payload=Depends(description_payload), context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.describe_task(db, *context, task_id, *payload)


@router.post('/{task_id}/cancel', response_model=TaskOut)
def cancel(task_id: UUID, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.cancel_task(db, *context, task_id)


class PreloadRetry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_uncertain: bool = False


async def preload_upload(request: Request, client=Depends(web_auth.get_current_web_client)):
    form = await request.form(max_files=1, max_fields=1)
    try:
        if set(form) != {'file', 'upload_id'} or any(len(form.getlist(key)) != 1 for key in form):
            raise HTTPException(422, detail='Укажите один файл и стабильный upload_id')
        if not isinstance(form['file'], UploadFile):
            raise HTTPException(422, detail='Некорректный файл')
        try:
            upload_id = UUID(str(form['upload_id']))
        except ValueError as exc:
            raise HTTPException(422, detail='Некорректный upload_id') from exc
        yield form['file'], upload_id
    finally:
        await form.close()


@router.post('/{task_id}/attachment-preloads')
def create_preload(task_id: UUID, background_tasks: BackgroundTasks, upload=Depends(preload_upload),
                   context=Depends(task_context), db: Session = Depends(get_db)):
    row, schedule = web_tasks.create_preload(db, context[0], task_id, *upload)
    payload = telegram_upload_state.preload_payload(row)
    if schedule:
        background_tasks.add_task(deliver_attachment_preload, preload_id=row.id)
    return payload


@router.get('/{task_id}/attachment-preloads/{preload_id}')
def preload_status(task_id: UUID, preload_id: UUID, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.preload_status(db, context[0], task_id, preload_id)


@router.post('/{task_id}/attachment-preloads/{preload_id}/retry')
def retry_preload(task_id: UUID, preload_id: UUID, background_tasks: BackgroundTasks,
                  payload: PreloadRetry = Body(default=PreloadRetry()),
                  context=Depends(task_context), db: Session = Depends(get_db)):
    row, schedule = web_tasks.retry_preload(db, context[0], task_id, preload_id, payload.confirm_uncertain)
    result = telegram_upload_state.preload_payload(row)
    if schedule:
        background_tasks.add_task(deliver_attachment_preload, preload_id=row.id)
    return result


@router.delete('/{task_id}/attachment-preloads/{preload_id}')
def delete_preload(task_id: UUID, preload_id: UUID, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.delete_preload(db, context[0], task_id, preload_id)
