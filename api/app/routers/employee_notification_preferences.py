from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..services.employee_notification_preferences import (
    serialize_employee_notification_preferences,
    restore_employee_notification_preferences_from_base,
    update_employee_notification_preferences,
)

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class EmployeeNotificationTemplatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    use_base_settings: bool = True
    template: str | None = None
    html_enabled: bool = True
    disable_link_preview: bool = False
    send_silently: bool = False


class EmployeeNotificationDeliveryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transfer_to_agent: bool = True
    transfer_from_agent: bool = True
    new_message: bool = True


class EmployeeNotificationPreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EmployeeNotificationTemplatePayload]
    delivery: EmployeeNotificationDeliveryPayload = Field(default_factory=EmployeeNotificationDeliveryPayload)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


@router.get("/notification-bot")
def get_my_notification_bot_preferences(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return serialize_employee_notification_preferences(db, user_id=user.id)


@router.put("/notification-bot")
def update_my_notification_bot_preferences(
    payload: EmployeeNotificationPreferencesPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        update_employee_notification_preferences(
            db,
            user_id=user.id,
            items=[item.model_dump() for item in payload.items],
            receive_transfer_to_agent=payload.delivery.transfer_to_agent,
            receive_transfer_from_agent=payload.delivery.transfer_from_agent,
            receive_new_message=payload.delivery.new_message,
        )
    except ValueError as exc:
        _error(422, "notification_template_invalid", str(exc))
    db.commit()
    return serialize_employee_notification_preferences(db, user_id=user.id)


@router.post("/notification-bot/restore-base/{notification_type}")
def restore_my_notification_bot_preferences_from_base(
    notification_type: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        restore_employee_notification_preferences_from_base(db, user_id=user.id, notification_type=notification_type)
    except KeyError:
        _error(404, "notification_type_not_found", "Неизвестный тип уведомления")
    db.commit()
    return serialize_employee_notification_preferences(db, user_id=user.id)
