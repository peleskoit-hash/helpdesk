from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import QuickReply, QuickReplyUsage, User


router = APIRouter(prefix="/api/v1/quick-replies", tags=["quick-replies"])


class QuickReplyPayload(BaseModel):
    short_title: str
    body: str


class QuickReplyOut(BaseModel):
    id: str
    short_title: str
    body: str
    author_user_id: str
    author_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    can_edit: bool = False


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _can_create(user: CurrentUser) -> bool:
    return user.role in {"admin", "agent"}


def _can_edit(user: CurrentUser, reply: QuickReply) -> bool:
    return is_admin(user) or (user.role == "agent" and reply.author_user_id == user.id)


def _clean_payload(payload: QuickReplyPayload) -> tuple[str, str]:
    title = str(payload.short_title or "").strip()
    body = str(payload.body or "").strip()
    if not title:
        _error(422, "quick_reply_title_required", "Краткое название обязательно")
    if not body:
        _error(422, "quick_reply_body_required", "Текст быстрого ответа обязателен")
    return title, body


def _authors_by_id(db: Session, author_ids: set[str]) -> dict[str, str]:
    if not author_ids:
        return {}
    users = db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
    return {user.id: user.full_name for user in users}


def _out(reply: QuickReply, user: CurrentUser, authors: dict[str, str] | None = None) -> QuickReplyOut:
    authors = authors or {}
    return QuickReplyOut(
        id=reply.id,
        short_title=reply.short_title,
        body=reply.body,
        author_user_id=reply.author_user_id,
        author_name=authors.get(reply.author_user_id),
        created_at=reply.created_at,
        updated_at=reply.updated_at,
        can_edit=_can_edit(user, reply),
    )


def _list_out(db: Session, replies: list[QuickReply], user: CurrentUser) -> list[QuickReplyOut]:
    authors = _authors_by_id(db, {reply.author_user_id for reply in replies})
    return [_out(reply, user, authors) for reply in replies]


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
        created_at=now,
        updated_at=now,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return _out(reply, user, _authors_by_id(db, {reply.author_user_id}))


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
    reply.short_title = title
    reply.body = body
    reply.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(reply)
    return _out(reply, user, _authors_by_id(db, {reply.author_user_id}))


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
    return _out(reply, user, _authors_by_id(db, {reply.author_user_id}))
