from datetime import datetime
import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import QuickReply, QuickReplyUsage, SavedFile, User
from ..services.saved_files import SavedFileStorageError, create_saved_file, resolve_saved_file_path


router = APIRouter(prefix="/api/v1/quick-replies", tags=["quick-replies"])


class QuickReplyPayload(BaseModel):
    short_title: str
    body: str
    saved_file_ids: list[str] | None = None


class QuickReplySavedFileOut(BaseModel):
    id: str
    display_filename: str
    file_size: int
    mime_type: str | None = None
    created_at: datetime | None = None


class QuickReplyOut(BaseModel):
    id: str
    short_title: str
    body: str
    author_user_id: str
    author_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    can_edit: bool = False
    saved_files: list[QuickReplySavedFileOut] = Field(default_factory=list)


def _error(status_code: int, code: str, message: str, details: dict | None = None):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": details or {}}})


def _can_create(user: CurrentUser) -> bool:
    return user.role in {"admin", "agent"}


def _can_edit(user: CurrentUser, reply: QuickReply) -> bool:
    return is_admin(user) or (user.role == "agent" and reply.author_user_id == user.id)


def _content_disposition_attachment(filename: str) -> str:
    normalized = (filename or "").strip() or "file"
    ascii_filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized).strip() or "file"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    encoded_filename = quote(normalized, safe="")
    return f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'


def _clean_payload(payload: QuickReplyPayload) -> tuple[str, str]:
    title = str(payload.short_title or "").strip()
    body = str(payload.body or "").strip()
    if not title:
        _error(422, "quick_reply_title_required", "Краткое название обязательно")
    if not body:
        _error(422, "quick_reply_body_required", "Текст быстрого ответа обязателен")
    return title, body


def _payload_has_saved_file_ids(payload: QuickReplyPayload) -> bool:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return "saved_file_ids" in fields_set


def _dedupe_saved_file_ids(saved_file_ids: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in saved_file_ids or []:
        file_id = str(value or "").strip()
        if file_id and file_id not in seen:
            result.append(file_id)
            seen.add(file_id)
    return result


def _saved_file_out(saved_file: SavedFile) -> QuickReplySavedFileOut:
    return QuickReplySavedFileOut(
        id=saved_file.id,
        display_filename=saved_file.display_filename,
        file_size=saved_file.file_size,
        mime_type=saved_file.mime_type,
        created_at=saved_file.created_at,
    )


def _load_saved_files_by_id(db: Session, saved_file_ids: list[str]) -> dict[str, SavedFile]:
    if not saved_file_ids:
        return {}
    rows = db.execute(select(SavedFile).where(SavedFile.id.in_(saved_file_ids))).scalars().all()
    return {row.id: row for row in rows}


def _validate_saved_file_ids(db: Session, saved_file_ids: list[str] | None) -> list[str]:
    ids = _dedupe_saved_file_ids(saved_file_ids)
    if len(ids) > 5:
        _error(422, "quick_reply_saved_files_limit", "К быстрому ответу можно прикрепить не более 5 файлов")
    files_by_id = _load_saved_files_by_id(db, ids)
    missing = [file_id for file_id in ids if file_id not in files_by_id]
    if missing:
        _error(404, "saved_file_not_found", "Сохранённый файл не найден", {"saved_file_ids": missing})
    return ids


def _reply_saved_file_ids(reply: QuickReply) -> list[str]:
    return _dedupe_saved_file_ids(reply.saved_file_ids_json if isinstance(reply.saved_file_ids_json, list) else [])


def _authors_by_id(db: Session, author_ids: set[str]) -> dict[str, str]:
    if not author_ids:
        return {}
    users = db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
    return {user.id: user.full_name for user in users}


def _out(
    reply: QuickReply,
    user: CurrentUser,
    authors: dict[str, str] | None = None,
    saved_files_by_id: dict[str, SavedFile] | None = None,
) -> QuickReplyOut:
    authors = authors or {}
    saved_files_by_id = saved_files_by_id or {}
    return QuickReplyOut(
        id=reply.id,
        short_title=reply.short_title,
        body=reply.body,
        author_user_id=reply.author_user_id,
        author_name=authors.get(reply.author_user_id),
        created_at=reply.created_at,
        updated_at=reply.updated_at,
        can_edit=_can_edit(user, reply),
        saved_files=[
            _saved_file_out(saved_files_by_id[file_id])
            for file_id in _reply_saved_file_ids(reply)
            if file_id in saved_files_by_id
        ],
    )


def _list_out(db: Session, replies: list[QuickReply], user: CurrentUser) -> list[QuickReplyOut]:
    authors = _authors_by_id(db, {reply.author_user_id for reply in replies})
    saved_file_ids: list[str] = []
    for reply in replies:
        saved_file_ids.extend(_reply_saved_file_ids(reply))
    saved_files_by_id = _load_saved_files_by_id(db, _dedupe_saved_file_ids(saved_file_ids))
    return [_out(reply, user, authors, saved_files_by_id) for reply in replies]


@router.get("", response_model=list[QuickReplyOut])
def list_quick_replies(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    replies = db.execute(select(QuickReply).order_by(QuickReply.short_title.asc(), QuickReply.id.asc())).scalars().all()
    return _list_out(db, replies, user)


@router.post("", response_model=QuickReplyOut)
def create_quick_reply(
    payload: QuickReplyPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_create(user):
        _error(403, "quick_reply_create_forbidden", "Нет прав на создание быстрого ответа")
    title, body = _clean_payload(payload)
    now = datetime.utcnow()
    reply = QuickReply(
        id=str(uuid.uuid4()),
        short_title=title,
        body=body,
        author_user_id=user.id,
        saved_file_ids_json=_validate_saved_file_ids(db, payload.saved_file_ids),
        created_at=now,
        updated_at=now,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return _list_out(db, [reply], user)[0]


@router.patch("/{reply_id}", response_model=QuickReplyOut)
def update_quick_reply(
    reply_id: str,
    payload: QuickReplyPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    reply = db.get(QuickReply, reply_id)
    if reply is None:
        _error(404, "quick_reply_not_found", "Быстрый ответ не найден")
    if not _can_edit(user, reply):
        _error(403, "quick_reply_edit_forbidden", "Нет прав на редактирование быстрого ответа")
    title, body = _clean_payload(payload)
    saved_file_ids = None
    if _payload_has_saved_file_ids(payload):
        saved_file_ids = _validate_saved_file_ids(db, payload.saved_file_ids)
    reply.short_title = title
    reply.body = body
    if saved_file_ids is not None:
        reply.saved_file_ids_json = saved_file_ids
    reply.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(reply)
    return _list_out(db, [reply], user)[0]


@router.post("/files", response_model=QuickReplySavedFileOut)
def upload_quick_reply_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_create(user):
        _error(403, "quick_reply_file_upload_forbidden", "Нет прав на загрузку файла быстрого ответа")
    saved_file = create_saved_file(
        db,
        content=file.file,
        display_filename=file.filename or "file.bin",
        mime_type=file.content_type,
    )
    db.commit()
    db.refresh(saved_file)
    return _saved_file_out(saved_file)


@router.get("/{reply_id}/files/{saved_file_id}/download")
def download_quick_reply_file(
    reply_id: str,
    saved_file_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    reply = db.get(QuickReply, reply_id)
    if reply is None:
        _error(404, "quick_reply_not_found", "Быстрый ответ не найден")
    if saved_file_id not in _reply_saved_file_ids(reply):
        _error(404, "quick_reply_file_not_found", "Файл быстрого ответа не найден")
    saved_file = db.get(SavedFile, saved_file_id)
    if saved_file is None:
        _error(404, "quick_reply_file_not_found", "Файл быстрого ответа не найден")
    try:
        path = resolve_saved_file_path(saved_file)
    except SavedFileStorageError:
        _error(404, "quick_reply_file_content_not_found", "Физический файл не найден")
    return FileResponse(
        str(path),
        media_type=saved_file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _content_disposition_attachment(saved_file.display_filename)},
    )


@router.delete("/{reply_id}")
def delete_quick_reply(
    reply_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not is_admin(user):
        _error(403, "quick_reply_delete_forbidden", "Нет прав на удаление быстрого ответа")
    reply = db.get(QuickReply, reply_id)
    if reply is None:
        _error(404, "quick_reply_not_found", "Быстрый ответ не найден")
    db.query(QuickReplyUsage).filter(QuickReplyUsage.quick_reply_id == reply.id).delete(synchronize_session=False)
    db.delete(reply)
    db.commit()
    return {"ok": True}


def _search_replies(db: Session, query: str) -> list[QuickReply]:
    normalized = query.strip().lower()
    pattern = f"%{normalized}%"
    rows = db.execute(
        select(QuickReply)
        .where(or_(func.lower(QuickReply.short_title).like(pattern), func.lower(QuickReply.body).like(pattern)))
    ).scalars().all()

    def rank(reply: QuickReply) -> tuple[int, str, str]:
        title = (reply.short_title or "").lower()
        body = (reply.body or "").lower()
        if title.startswith(normalized):
            bucket = 0
        elif normalized in title:
            bucket = 1
        elif normalized in body:
            bucket = 2
        else:
            bucket = 3
        return bucket, title, reply.id

    return sorted(rows, key=rank)[:5]


def _mru_replies(db: Session, user: CurrentUser) -> list[QuickReply]:
    return db.execute(
        select(QuickReply)
        .join(QuickReplyUsage, QuickReplyUsage.quick_reply_id == QuickReply.id)
        .where(QuickReplyUsage.user_id == user.id)
        .order_by(desc(QuickReplyUsage.last_used_at), QuickReply.id.asc())
        .limit(5)
    ).scalars().all()


@router.get("/search", response_model=list[QuickReplyOut])
def search_quick_replies(
    q: str = Query(default=""),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    replies = _mru_replies(db, user) if not q.strip() else _search_replies(db, q)
    return _list_out(db, replies, user)


@router.post("/{reply_id}/use", response_model=QuickReplyOut)
def use_quick_reply(
    reply_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    reply = db.get(QuickReply, reply_id)
    if reply is None:
        _error(404, "quick_reply_not_found", "Быстрый ответ не найден")
    now = datetime.utcnow()
    usage = db.execute(
        select(QuickReplyUsage).where(
            and_(QuickReplyUsage.user_id == user.id, QuickReplyUsage.quick_reply_id == reply.id)
        )
    ).scalar_one_or_none()
    if usage is None:
        usage = QuickReplyUsage(user_id=user.id, quick_reply_id=reply.id, last_used_at=now)
        db.add(usage)
    else:
        usage.last_used_at = now
    db.commit()
    db.refresh(reply)
    return _list_out(db, [reply], user)[0]
