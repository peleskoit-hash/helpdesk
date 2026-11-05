import re
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin
from ..db import get_db
from ..models import BotMenuLevelLayout, ClientCardFieldConfig, User
from ..schemas import (
    BotButtonGroupOut,
    BotButtonInstructionUpdate,
    BotButtonItemOut,
    BotButtonItemsResponse,
    BotButtonMenuStructureOut,
    BotButtonStructureLeafOut,
    ClientCardFieldConfigOut,
)
from ..services.telegram_automation import get_automation_registry


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ROLE_LABELS = {
    "admin": "Администратор",
    "agent": "Сотрудник",
    "trainee": "Стажёр",
    "chiki_piki": "Наблюдатель",
}

CONDITIONAL_COMMON_KEYS: list[str] = [
    "employee_type",
    "location_type",
    "is_verified",
    "internal_number",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
]
CONDITIONAL_PESOK_KEYS: list[str] = [
    "sand_owner",
    "sales_group",
    "group_name",
]

CONDITIONAL_FIELD_TYPE_BY_CARD: dict[str, str] = {
    "text": "text",
    "textarea": "text",
    "phone": "text",
    "url": "text",
    "password": "text",
    "readonly": "text",
    "number": "number",
    "select": "select",
    "checkbox": "bool",
    "date": "text",
}

TEXT_OPERATORS = {"eq", "ne", "contains", "not_contains", "empty", "not_empty"}
SELECT_OPERATORS = {"eq", "ne", "empty", "not_empty"}
NUMBER_OPERATORS = {"eq", "ne", "gt", "lt", "gte", "lte", "empty", "not_empty"}
BOOL_OPERATORS = {"eq", "is_true", "is_false"}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


@router.get("/bot-buttons/assignees")
def list_bot_button_assignees(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    users = db.execute(
        select(User)
        .where(User.is_active.is_(True), User.role.in_(["admin", "agent", "trainee"]))
        .order_by(User.role.asc(), User.full_name.asc())
    ).scalars().all()
    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "role": u.role,
            "role_label": ROLE_LABELS.get(u.role, u.role),
        }
        for u in users
    ]
