from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Client
from ..services import web_auth, web_tasks
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


@router.post('/{task_id}/description', response_model=TaskOut)
def describe(task_id: UUID, payload: Description, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.describe_task(db, *context, task_id, payload.text)


@router.post('/{task_id}/cancel', response_model=TaskOut)
def cancel(task_id: UUID, context=Depends(task_context), db: Session = Depends(get_db)):
    return web_tasks.cancel_task(db, *context, task_id)
