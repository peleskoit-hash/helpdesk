from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..services.automatic_messages import (
    get_automatic_message_definition,
    list_automatic_message_definitions,
    reset_automatic_message_override,
    serialize_automatic_message,
    upsert_automatic_message_override,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class AutomaticMessageUpdatePayload(BaseModel):
    body: str | None = None
    html_enabled: bool | None = None


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступно только администратору")


@router.get("/automatic-messages")
def list_automatic_messages(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    items = [serialize_automatic_message(db, definition) for definition in list_automatic_message_definitions()]
    categories: list[dict] = []
    for item in items:
        category = item["category"]
        existing = next((candidate for candidate in categories if candidate["category"] == category), None)
        if existing is None:
            existing = {"category": category, "category_title": item["category_title"], "items": []}
            categories.append(existing)
        existing["items"].append(item)
    return {"items": items, "categories": categories}


@router.get("/automatic-messages/{template_key:path}")
def get_automatic_message(template_key: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    definition = get_automatic_message_definition(template_key)
    if definition is None:
        _error(404, "automatic_message_not_found", "Шаблон автоматического сообщения не найден")
    return serialize_automatic_message(db, definition)


@router.patch("/automatic-messages/{template_key:path}")
def update_automatic_message(
    template_key: str,
    payload: AutomaticMessageUpdatePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    definition = get_automatic_message_definition(template_key)
    if definition is None:
        _error(404, "automatic_message_not_found", "Шаблон автоматического сообщения не найден")
    upsert_automatic_message_override(db, template_key, payload.body, html_enabled=payload.html_enabled)
    db.commit()
    return serialize_automatic_message(db, definition)


@router.delete("/automatic-messages/{template_key:path}")
def reset_automatic_message(template_key: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    definition = get_automatic_message_definition(template_key)
    if definition is None:
        _error(404, "automatic_message_not_found", "Шаблон автоматического сообщения не найден")
    reset_automatic_message_override(db, template_key)
    db.commit()
    return serialize_automatic_message(db, definition)
