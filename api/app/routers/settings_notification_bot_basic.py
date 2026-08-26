from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..services.automatic_messages import (
    get_notification_bot_basic_definition,
    list_notification_bot_basic_definitions,
    serialize_notification_bot_basic_template,
    upsert_notification_bot_basic_template,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class NotificationBotBasicUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    template: str | None = None
    html_enabled: bool = True
    disable_link_preview: bool = False
    send_silently: bool = False


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Доступно только администратору")


@router.get("/notification-bot/basic")
def list_notification_bot_basic_templates(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    items = [
        serialize_notification_bot_basic_template(db, definition)
        for definition in list_notification_bot_basic_definitions()
    ]
    return {"items": items}


@router.put("/notification-bot/basic/{notification_type}")
def update_notification_bot_basic_template(
    notification_type: str,
    payload: NotificationBotBasicUpdatePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    definition = get_notification_bot_basic_definition(notification_type)
    if definition is None:
        _error(404, "notification_type_not_found", "Неизвестный тип уведомления")
    try:
        upsert_notification_bot_basic_template(
            db,
            notification_type,
            enabled=payload.enabled,
            template=payload.template,
            html_enabled=payload.html_enabled,
            disable_link_preview=payload.disable_link_preview,
            send_silently=payload.send_silently,
        )
    except ValueError as exc:
        _error(422, "notification_template_invalid", str(exc))
    db.commit()
    return serialize_notification_bot_basic_template(db, definition)
