from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import web_task_results as results
from ..services.web_auth import CurrentWebClient, get_current_web_client
from .tickets import _content_disposition_attachment
from .web_auth import WebAuthRoute

router = APIRouter(prefix='/api/v1/web/task-results', tags=['web-task-results'], route_class=WebAuthRoute)


class ResultSummary(BaseModel):
    task_id: str
    message_id: str
    closed_at: datetime
    read_at: datetime | None


class ResultFile(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    download_url: str


class ResultDetail(ResultSummary):
    text: str
    files: list[ResultFile]


class ResultList(BaseModel):
    items: list[ResultSummary]
    total: int
    unread_count: int


class ReadResult(BaseModel):
    task_id: str
    read_at: datetime


@router.get('', response_model=ResultList)
def list_results(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                 client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return results.list_results(db, client, limit, offset)


@router.get('/{task_id}', response_model=ResultDetail)
def get_result(task_id: UUID, client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return results.result_detail(db, client, task_id)


@router.post('/{task_id}/read', response_model=ReadResult)
def read_result(task_id: UUID, client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return results.mark_read(db, client, task_id)


@router.get('/{task_id}/files/{file_id}')
def download(task_id: UUID, file_id: UUID, client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    file = results.result_file(db, client, task_id, file_id)
    return Response(file.data, media_type='application/octet-stream', headers={
        'Content-Disposition': _content_disposition_attachment(file.filename),
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
    })
