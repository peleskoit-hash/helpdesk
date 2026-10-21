from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..config import settings
from ..db import get_db
from ..models import TelegramAutomationGroup
from ..services import telegram_group_registry as registry
from ..services.telegram_bot import TelegramApiError

router = APIRouter(prefix="/api/v1/settings/telegram-groups", tags=["settings"])


def _admin(db, user):
    if user.role != "admin":
        raise HTTPException(403, "Доступно только администратору")
    try:
        return registry.require_admin(db, user.id)
    except PermissionError as exc:
        raise HTTPException(403, "Доступно только действующему администратору") from exc


@router.get("")
def list_groups(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    admin = _admin(db, user)
    groups = db.scalars(select(TelegramAutomationGroup).where(
        TelegramAutomationGroup.company_id == admin.company_id,
        TelegramAutomationGroup.environment == settings.app_env,
    ).order_by(TelegramAutomationGroup.title)).all()
    return {"items": [registry.group_out(group) for group in groups if not registry._is_archive(group.chat_id)]}


@router.post("/registration-code")
def create_code(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    admin = _admin(db, user)
    try:
        return registry.create_registration_code(db, admin)
    except TelegramApiError as exc:
        raise HTTPException(502, "Не удалось проверить клиентского Telegram-бота") from exc


@router.post("/{group_id}/check")
def check_group(group_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    admin = _admin(db, user)
    try:
        group = registry.group_for_admin(db, admin, group_id)
    except ValueError as exc:
        raise HTTPException(404, "Группа недоступна") from exc
    registry.recheck_group(db, group)
    db.commit()
    return registry.group_out(group)
