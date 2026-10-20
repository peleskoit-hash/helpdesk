import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin
from ..db import get_db
from ..models import BotButtonSetting, BotMenuLevelLayout, ClientCardFieldConfig, User
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

CONDITIONAL_SELECT_PRESETS: dict[str, list[str]] = {
    "sand_owner": [
        "песок Власова",
        "песок Шабурова",
        "песок Сайдаралиева",
        "песок Макаренко",
        "песок Шахалиевой",
        "песок Кочуры",
        "песок Москвиченко",
    ],
    "sales_group": [
        "группа Власова",
        "группа Шабурова",
        "группа Сайдаралиева",
        "группа Макаренко",
        "группа Шахалиевой",
        "группа Кочуры",
        "группа Москвиченко",
    ],
    "location_type": ["Офисный", "Удалённый"],
    "employee_type": ["Стажёр", "Сотрудник"],
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


@router.post("/bot-buttons/item/rebind")
def rebind_bot_button_setting(
    old_button_key: str,
    new_button_key: str,
    menu_type: str,
    workflow_type: str,
    button_path: str,
    button_label: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if old_button_key == new_button_key:
        return {"ok": True, "moved": False, "reason": "same_key"}

    source = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == old_button_key)).scalar_one_or_none()
    if source is None:
        _error(404, "button_setting_not_found", "Исходная запись кнопки не найдена")

    text_value = (source.instruction_text or "").strip() or None
    source_client_text = (source.client_text or "").strip() or None
    source_client_text_overridden = bool(source.client_text_overridden)
    source_system_text = (source.system_text or "").strip() or None
    source_system_overridden = bool(source.system_text_overridden)
    source_fixed_enabled = bool(source.fixed_assignee_enabled)
    source_fixed_user_id = source.fixed_assignee_user_id
    source_freeze_on_create = bool(source.freeze_on_create)
    source_confirmation_enabled = bool(source.confirmation_enabled)
    source_confirmation_text = (source.confirmation_text or "").strip() or None
    source_confirmation_overridden = bool(source.confirmation_overridden)
    source_files_overridden = bool(source.files_overridden)
    source_files_json = source.files_json if isinstance(source.files_json, list) else None
    source_conditional_overridden = bool(source.conditional_files_overridden)
    source_conditional_json = source.conditional_files_json if isinstance(source.conditional_files_json, list) else None
    source_is_enabled = bool(source.is_enabled)
    source_visibility_overridden = bool(source.visibility_overridden)
    source_label_override = (source.label_override or "").strip() or None
    source_label_overridden = bool(source.label_overridden)
    target = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == new_button_key)).scalar_one_or_none()
    now = datetime.utcnow()

    if target is None:
        target = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=source.stable_button_id or str(uuid.uuid4()),
            button_key=new_button_key,
            legacy_button_key=old_button_key,
            menu_type=menu_type,
            workflow_type=workflow_type,
            button_path=button_path,
            button_label=button_label,
            label_override=source_label_override if source_label_overridden else None,
            label_overridden=source_label_overridden,
            currently_present=True,
            instruction_text=text_value,
            client_text=source_client_text,
            client_text_overridden=source_client_text_overridden,
            system_text=source_system_text,
            system_text_overridden=source_system_overridden,
            fixed_assignee_enabled=source_fixed_enabled,
            fixed_assignee_user_id=source_fixed_user_id,
            freeze_on_create=source_freeze_on_create,
            confirmation_enabled=source_confirmation_enabled,
            confirmation_text=source_confirmation_text,
            confirmation_overridden=source_confirmation_overridden,
            files_overridden=source_files_overridden,
            files_json=source_files_json,
            conditional_files_overridden=source_conditional_overridden,
            conditional_files_json=source_conditional_json,
            is_enabled=source_is_enabled,
            visibility_overridden=source_visibility_overridden,
            created_at=now,
            updated_at=now,
        )
        db.add(target)
    else:
        if not target.stable_button_id:
            target.stable_button_id = source.stable_button_id or str(uuid.uuid4())
        if not target.legacy_button_key:
            target.legacy_button_key = old_button_key
        target.menu_type = menu_type
        target.workflow_type = workflow_type
        target.button_path = button_path
        target.button_label = button_label
        target.label_overridden = source_label_overridden
        target.label_override = source_label_override if source_label_overridden else None
        target.currently_present = True
        target.instruction_text = text_value
        target.client_text = source_client_text
        target.client_text_overridden = source_client_text_overridden
        target.system_text = source_system_text
        target.system_text_overridden = source_system_overridden
        target.fixed_assignee_enabled = source_fixed_enabled
        target.fixed_assignee_user_id = source_fixed_user_id
        target.freeze_on_create = source_freeze_on_create
        target.confirmation_enabled = source_confirmation_enabled
        target.confirmation_text = source_confirmation_text
        target.confirmation_overridden = source_confirmation_overridden
        target.files_overridden = source_files_overridden
        target.files_json = source_files_json
        target.conditional_files_overridden = source_conditional_overridden
        target.conditional_files_json = source_conditional_json
        target.is_enabled = source_is_enabled
        target.visibility_overridden = source_visibility_overridden
        target.updated_at = now

    db.delete(source)
    db.commit()
    return {"ok": True, "moved": True, "button_key": new_button_key}

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
