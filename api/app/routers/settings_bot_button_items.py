import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, ClientCardFieldConfig, User
from ..schemas import BotButtonInstructionUpdate
from ..services.button_config import (
    BUTTON_CONFIG_DIR,
    find_button,
    list_terminal_buttons,
)
from ..services.telegram_automation import get_automation_registry

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

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


def _fallback_system_text(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    return button.system_text


def _fallback_client_text(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    return button.text


def _fallback_automation_key(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    value = (button.automation_key or "").strip()
    return value or None


def _normalize_relative_button_file_path(raw_path: str) -> str | None:
    value = (raw_path or "").strip().replace("\\", "/")
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return None
    normalized = Path(*[part for part in candidate.parts if part not in ("", ".")]).as_posix()
    if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return None
    return normalized


def _list_available_button_files() -> list[str]:
    files_root = BUTTON_CONFIG_DIR / "files"
    if not files_root.exists() or not files_root.is_dir():
        return []
    items: list[str] = []
    for path in files_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(files_root).as_posix()
        normalized = _normalize_relative_button_file_path(rel)
        if normalized:
            items.append(normalized)
    return sorted(set(items), key=lambda x: x.lower())


def _fallback_direct_files(menu_type: str, button_key: str) -> list[str]:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return []
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return []
    button = find_button(source_button_key)
    if button is None:
        return []
    raw = (button.file_path or "").strip()
    normalized = _normalize_relative_button_file_path(raw) if raw else None
    return [normalized] if normalized else []


def _fallback_conditional_files(db: Session, menu_type: str, button_key: str) -> list[dict]:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return []
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return []
    button = find_button(source_button_key)
    if button is None or not button.file_rules:
        return []
    catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    result: list[dict] = []
    for rule in button.file_rules:
        field_name = (rule.get("field") or "").strip()
        file_name = _normalize_relative_button_file_path((rule.get("file") or "").strip())
        equals = (rule.get("equals") or "").strip()
        if not field_name or not file_name:
            continue
        if field_name not in catalog:
            continue
        result.append(
            {
                "conditions": [{"field": field_name, "operator": "eq", "value": equals}],
                "files": [file_name],
            }
        )
    return result


def _normalize_conditional_rules(raw_rules: list[dict], available_files: set[str], catalog: dict[str, dict]) -> list[dict]:
    normalized_rules: list[dict] = []
    for idx, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            _error(422, "invalid_conditional_rule", f"Некорректный формат правила #{idx + 1}")
        raw_conditions = raw_rule.get("conditions") or []
        raw_files = raw_rule.get("files") or []
        if not isinstance(raw_conditions, list):
            _error(422, "invalid_conditional_rule_conditions", f"conditions должен быть списком в правиле #{idx + 1}")
        if not isinstance(raw_files, list):
            _error(422, "invalid_conditional_rule_files", f"files должен быть списком в правиле #{idx + 1}")
        conditions: list[dict] = []
        for cidx, raw_condition in enumerate(raw_conditions):
            if not isinstance(raw_condition, dict):
                _error(422, "invalid_conditional_condition", f"Некорректное условие #{cidx + 1} в правиле #{idx + 1}")
            field_name = str(raw_condition.get("field") or "").strip()
            operator = str(raw_condition.get("operator") or "").strip()
            value = raw_condition.get("value")
            if field_name not in catalog:
                _error(422, "conditional_field_not_allowed", f"Поле недоступно для условий: {field_name}")
            field_type = str(catalog[field_name].get("type") or "text")
            if field_type == "bool":
                if operator not in BOOL_OPERATORS:
                    _error(422, "conditional_operator_not_allowed", f"Оператор недоступен для поля {field_name}: {operator}")
                bool_value: bool | None = None
                if operator == "is_true":
                    bool_value = True
                    operator = "eq"
                elif operator == "is_false":
                    bool_value = False
                    operator = "eq"
                else:
                    if isinstance(value, bool):
                        bool_value = value
                    else:
                        text = str(value or "").strip().lower()
                        if text in {"true", "1", "yes", "да"}:
                            bool_value = True
                        elif text in {"false", "0", "no", "нет"}:
                            bool_value = False
                if bool_value is None:
                    _error(422, "conditional_bool_required", f"Для поля {field_name} нужно выбрать Да/Нет")
                normalized_value = bool_value
            elif field_type == "number":
                if operator not in NUMBER_OPERATORS:
                    _error(422, "conditional_operator_not_allowed", f"Оператор недоступен для поля {field_name}: {operator}")
                if operator in {"empty", "not_empty"}:
                    normalized_value = None
                else:
                    text = str(value or "").strip()
                    if not text:
                        _error(422, "conditional_value_required", f"Укажите значение для поля {field_name}")
                    try:
                        normalized_value = float(text.replace(",", "."))
                    except Exception:
                        _error(422, "conditional_number_invalid", f"Значение поля {field_name} должно быть числом")
            elif field_type == "select":
                if operator not in SELECT_OPERATORS:
                    _error(422, "conditional_operator_not_allowed", f"Оператор недоступен для поля {field_name}: {operator}")
                if operator in {"empty", "not_empty"}:
                    normalized_value = None
                else:
                    text = str(value or "").strip()
                    if not text:
                        _error(422, "conditional_value_required", f"Укажите значение для поля {field_name}")
                    normalized_value = text
            else:
                if operator not in TEXT_OPERATORS:
                    _error(422, "conditional_operator_not_allowed", f"Оператор недоступен для поля {field_name}: {operator}")
                if operator in {"empty", "not_empty"}:
                    normalized_value = None
                else:
                    text = str(value or "").strip()
                    if not text:
                        _error(422, "conditional_value_required", f"Укажите значение для поля {field_name}")
                    normalized_value = text
            conditions.append({"field": field_name, "operator": operator, "value": normalized_value})
        normalized_files: list[str] = []
        for raw_file in raw_files:
            normalized = _normalize_relative_button_file_path(str(raw_file))
            if not normalized:
                _error(422, "invalid_file_path", f"Некорректный путь файла в правиле #{idx + 1}")
            if normalized not in available_files:
                _error(422, "file_not_allowed", f"Файл недоступен для выбора: {normalized}")
            normalized_files.append(normalized)
        normalized_files = list(dict.fromkeys(normalized_files))
        if not conditions:
            _error(422, "conditional_rule_empty", f"Добавьте хотя бы одно условие в правило #{idx + 1}")
        if not normalized_files:
            _error(422, "conditional_rule_files_empty", f"Добавьте хотя бы один файл в правило #{idx + 1}")
        normalized_rules.append({"conditions": conditions, "files": normalized_files})
    return normalized_rules


def _distinct_profile_values(db: Session, field_name: str) -> list[str]:
    if field_name.startswith("custom_fields."):
        custom_key = field_name.split(".", 1)[1]
        from ..models import ClientProfile

        rows = db.execute(select(ClientProfile.custom_fields)).scalars().all()
        values: set[str] = set()
        for payload in rows:
            if not isinstance(payload, dict):
                continue
            value = payload.get(custom_key)
            text = str(value or "").strip()
            if text:
                values.add(text)
        return sorted(values, key=lambda x: x.lower())
    from ..models import ClientProfile

    column = getattr(ClientProfile, field_name, None)
    if column is None:
        return []
    rows = db.execute(select(column).distinct()).scalars().all()
    values = {str(v).strip() for v in rows if str(v or "").strip()}
    return sorted(values, key=lambda x: x.lower())


def _field_options(db: Session, field: ClientCardFieldConfig) -> list[str]:
    options = field.options_json if isinstance(field.options_json, list) else []
    normalized: list[str] = []
    for value in options:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    merged = normalized + [v for v in _distinct_profile_values(db, field.key or "") if v not in normalized]
    return merged


def _build_conditional_field_catalog(db: Session, menu_type: str | None = None) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    allow_map = {
        "Песок": set(CONDITIONAL_COMMON_KEYS + CONDITIONAL_PESOK_KEYS),
    }
    allow_keys = allow_map.get((menu_type or "").strip(), set(CONDITIONAL_COMMON_KEYS))

    fields: list[ClientCardFieldConfig] = db.execute(
        select(ClientCardFieldConfig).where(ClientCardFieldConfig.is_visible.is_(True))
    ).scalars().all()
    for field in fields:
        key = (field.key or "").strip()
        if not key:
            continue
        if key.startswith("custom_"):
            continue
        if key not in allow_keys:
            continue
        field_type = CONDITIONAL_FIELD_TYPE_BY_CARD.get(field.field_type, "text")
        entry = {
            "field": key,
            "type": field_type,
            "label": field.label or key,
            "is_custom": False,
        }
        if field_type == "select":
            preset = CONDITIONAL_SELECT_PRESETS.get(key) or []
            options = list(dict.fromkeys([*preset, *_field_options(db, field)]))
            entry["values"] = options
        catalog[key] = entry

    for field in fields:
        if field.is_system:
            continue
        key = (field.key or "").strip()
        if not key.startswith("custom_"):
            continue
        field_type = CONDITIONAL_FIELD_TYPE_BY_CARD.get(field.field_type, "text")
        entry = {
            "field": f"custom_fields.{key}",
            "type": field_type,
            "label": field.label or key,
            "is_custom": True,
            "custom_key": key,
        }
        if field_type == "select":
            entry["values"] = _field_options(db, field)
        catalog[entry["field"]] = entry

    catalog["telegram_username"] = {
        "field": "telegram_username",
        "type": "text",
        "label": "Telegram username",
        "is_custom": False,
    }
    catalog["telegram_display_name"] = {
        "field": "telegram_display_name",
        "type": "text",
        "label": "Telegram имя",
        "is_custom": False,
    }
    catalog["telegram_user_id"] = {
        "field": "telegram_user_id",
        "type": "text",
        "label": "Telegram user id",
        "is_custom": False,
    }
    catalog["telegram_chat_id"] = {
        "field": "telegram_chat_id",
        "type": "text",
        "label": "Telegram chat id",
        "is_custom": False,
    }
    return catalog


@router.get("/bot-buttons/item")
def get_bot_button_item(
    button_key: str,
    menu_type: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    txt_client_text = _fallback_client_text((menu_type or (row.menu_type if row else "")), button_key)
    txt_system_text = _fallback_system_text((menu_type or (row.menu_type if row else "")), button_key)
    txt_automation_key = _fallback_automation_key((menu_type or (row.menu_type if row else "")), button_key)
    automation_registry = get_automation_registry()
    db_automation_key = (row.automation_key or "").strip() if row is not None else ""
    automation_overridden = bool(row.automation_overridden) if row is not None else False
    effective_automation_key = db_automation_key if automation_overridden else txt_automation_key
    automation_meta = automation_registry.get(effective_automation_key) if effective_automation_key else None
    txt_files = _fallback_direct_files((menu_type or (row.menu_type if row else "")), button_key)
    effective_menu_type = menu_type or (row.menu_type if row else "")
    txt_conditional_files = _fallback_conditional_files(db, effective_menu_type, button_key)
    if row is None:
        return {
            "button_key": button_key,
            "stable_button_id": None,
            "legacy_button_key": None,
            "menu_type": effective_menu_type,
            "workflow_type": "",
            "button_path": "",
            "button_label": "",
            "label_override": None,
            "label_overridden": False,
            "button_label_effective": "",
            "button_label_from": "txt",
            "removed": False,
            "currently_present": True,
            "instruction_text": None,
            "client_text": None,
            "client_text_overridden": False,
            "client_text_effective": txt_client_text,
            "client_text_from": "txt",
            "system_text": None,
            "system_text_overridden": False,
            "system_text_effective": txt_system_text,
            "system_text_from": "txt",
            "automation_key_effective": txt_automation_key,
            "automation_key": None,
            "automation_overridden": False,
            "automation_from": "txt" if txt_automation_key else "none",
            "automation_title": (automation_meta.get("title") if automation_meta else None),
            "automation_description": (automation_meta.get("description") if automation_meta else None),
            "automation_available": bool(automation_meta.get("available")) if automation_meta else False,
            "automation_handler": (automation_meta.get("handler") if automation_meta else None),
            "automation_status": ("not_set" if not effective_automation_key else ("available" if automation_meta else "not_found")),
            "fixed_assignee_enabled": False,
            "fixed_assignee_user_id": None,
            "freeze_on_create": False,
            "confirmation_enabled": False,
            "confirmation_text": None,
            "confirmation_overridden": False,
            "files_overridden": False,
            "files": [],
            "files_effective": txt_files,
            "files_from": "txt",
            "conditional_files_overridden": False,
            "conditional_files": [],
            "conditional_files_effective": txt_conditional_files,
            "conditional_files_from": "txt",
            "is_enabled": True,
            "visibility_overridden": False,
        }
    effective_system_text = row.system_text if row.system_text_overridden else txt_system_text
    effective_client_text = row.client_text if row.client_text_overridden else ((row.client_text or txt_client_text))
    db_files_raw = row.files_json if isinstance(row.files_json, list) else []
    db_files = [p for p in (_normalize_relative_button_file_path(str(v)) for v in db_files_raw) if p]
    effective_files = db_files if bool(row.files_overridden) else txt_files
    db_conditional_raw = row.conditional_files_json if isinstance(row.conditional_files_json, list) else []
    db_conditional = [rule for rule in db_conditional_raw if isinstance(rule, dict)]
    effective_conditional = db_conditional if bool(row.conditional_files_overridden) else txt_conditional_files
    return {
        "button_key": row.button_key,
        "stable_button_id": row.stable_button_id,
        "legacy_button_key": row.legacy_button_key,
        "menu_type": row.menu_type,
        "workflow_type": row.workflow_type,
        "button_path": row.button_path,
        "button_label": row.button_label,
        "label_override": row.label_override,
        "label_overridden": bool(row.label_overridden),
        "button_label_effective": ((row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label),
        "button_label_from": ("db" if bool(row.label_overridden) and (row.label_override or "").strip() else "txt"),
        "removed": not bool(row.currently_present),
        "currently_present": bool(row.currently_present),
        "is_db_created": bool(row.is_db_created),
        "is_archived": bool(row.is_archived),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "deleted_by_user_id": row.deleted_by_user_id,
        "instruction_text": row.instruction_text,
        "client_text": row.client_text,
        "client_text_overridden": bool(row.client_text_overridden),
        "client_text_effective": effective_client_text,
        "client_text_from": "db" if bool(row.client_text_overridden) else "txt",
        "system_text": row.system_text,
        "system_text_overridden": bool(row.system_text_overridden),
        "system_text_effective": effective_system_text,
        "system_text_from": "db" if row.system_text_overridden else "txt",
        "automation_key_effective": effective_automation_key,
        "automation_key": db_automation_key or None,
        "automation_overridden": automation_overridden,
        "automation_from": ("db" if automation_overridden else ("txt" if txt_automation_key else "none")),
        "automation_title": (automation_meta.get("title") if automation_meta else None),
        "automation_description": (automation_meta.get("description") if automation_meta else None),
        "automation_available": bool(automation_meta.get("available")) if automation_meta else False,
        "automation_handler": (automation_meta.get("handler") if automation_meta else None),
        "automation_status": ("not_set" if not effective_automation_key else ("available" if automation_meta else "not_found")),
        "fixed_assignee_enabled": bool(row.fixed_assignee_enabled),
        "fixed_assignee_user_id": row.fixed_assignee_user_id,
        "freeze_on_create": bool(row.freeze_on_create),
        "confirmation_enabled": bool(row.confirmation_enabled),
        "confirmation_text": row.confirmation_text,
        "confirmation_overridden": bool(row.confirmation_overridden),
        "files_overridden": bool(row.files_overridden),
        "files": db_files,
        "files_effective": effective_files,
        "files_from": "db" if row.files_overridden else "txt",
        "conditional_files_overridden": bool(row.conditional_files_overridden),
        "conditional_files": db_conditional,
        "conditional_files_effective": effective_conditional,
        "conditional_files_from": "db" if row.conditional_files_overridden else "txt",
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
    }


@router.get("/bot-buttons/files")
def get_bot_button_files(
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return {"items": _list_available_button_files()}


@router.get("/bot-buttons/automations")
def get_bot_button_automations(
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    registry = get_automation_registry()
    return {"items": list(registry.values())}


@router.get("/bot-buttons/conditional-fields")
def get_bot_button_conditional_fields(
    menu_type: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    items: list[dict] = []
    for key, base in catalog.items():
        field_type = str(base.get("type") or "text")
        item = {
            "field": key,
            "type": field_type,
            "label": str(base.get("label") or key),
            "is_custom": bool(base.get("is_custom")),
        }
        if field_type == "select":
            item["operators"] = ["eq", "ne", "empty", "not_empty"]
            item["values"] = list(base.get("values") or [])
        elif field_type == "number":
            item["operators"] = ["eq", "ne", "gt", "lt", "gte", "lte", "empty", "not_empty"]
        elif field_type == "bool":
            item["operators"] = ["eq"]
            item["values"] = [
                {"value": "true", "label": "Да"},
                {"value": "false", "label": "Нет"},
            ]
        else:
            item["operators"] = ["eq", "ne", "contains", "not_contains", "empty", "not_empty"]
        items.append(item)
    return {"items": items}


@router.put("/bot-buttons/item/instruction")
def save_bot_button_instruction(
    payload: BotButtonInstructionUpdate,
    button_key: str,
    menu_type: str,
    workflow_type: str,
    button_path: str,
    button_label: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    override_label_value = (payload.label_override or "").strip() if payload.label_override is not None else None
    override_label_enabled = bool(payload.label_overridden) if payload.label_overridden is not None else None
    if override_label_enabled is True and not override_label_value:
        _error(422, "label_override_required", "Название в боте не может быть пустым")

    fixed_enabled = bool(payload.fixed_assignee_enabled)
    fixed_user_id = (payload.fixed_assignee_user_id or "").strip() or None
    if fixed_enabled and not fixed_user_id:
        _error(422, "fixed_assignee_required", "Выберите фиксированного ответственного")
    if fixed_user_id:
        target_user = db.execute(select(User).where(User.id == fixed_user_id)).scalar_one_or_none()
        if target_user is None or not target_user.is_active:
            _error(422, "fixed_assignee_invalid", "Выбранный фиксированный ответственный недоступен")
        if target_user.role not in {"admin", "agent", "trainee"}:
            _error(422, "fixed_assignee_role_invalid", "Можно выбрать только admin/agent/trainee")

    text_value = (payload.instruction_text or "").strip() or None
    client_text_value = (payload.client_text or "").strip() or None if payload.client_text is not None else None
    system_value = (payload.system_text or "").strip() or None if payload.system_text is not None else None
    automation_key_value = (payload.automation_key or "").strip() or None if payload.automation_key is not None else None
    if automation_key_value is not None:
        registry = get_automation_registry()
        if automation_key_value and automation_key_value not in registry:
            _error(422, "automation_not_found", "Указанная автоматизация не найдена в реестре")
    available_files = set(_list_available_button_files())
    payload_files = payload.files if payload.files is not None else None
    normalized_files: list[str] = []
    if payload_files is not None:
        for raw in payload_files:
            normalized = _normalize_relative_button_file_path(str(raw))
            if not normalized:
                _error(422, "invalid_file_path", "Некорректный путь файла в настройке кнопки")
            if normalized not in available_files:
                _error(422, "file_not_allowed", f"Файл недоступен для выбора: {normalized}")
            normalized_files.append(normalized)
        normalized_files = list(dict.fromkeys(normalized_files))
    payload_conditional = payload.conditional_files if payload.conditional_files is not None else None
    normalized_conditional: list[dict] = []
    conditional_catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    if payload_conditional is not None:
        normalized_conditional = _normalize_conditional_rules(payload_conditional, available_files, conditional_catalog)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    now = datetime.utcnow()
    if row is None:
        row = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=str(uuid.uuid4()),
            button_key=button_key,
            legacy_button_key=None,
            menu_type=menu_type,
            workflow_type=workflow_type,
            button_path=button_path,
            button_label=button_label,
            label_override=override_label_value if override_label_enabled else None,
            label_overridden=bool(override_label_enabled),
            currently_present=True,
            instruction_text=text_value,
            client_text=client_text_value,
            client_text_overridden=bool(payload.client_text_overridden),
            system_text=system_value,
            system_text_overridden=bool(payload.system_text is not None or payload.system_text_overridden),
            fixed_assignee_enabled=fixed_enabled,
            fixed_assignee_user_id=fixed_user_id,
            freeze_on_create=bool(payload.freeze_on_create),
            confirmation_enabled=bool(payload.confirmation_enabled),
            confirmation_text=((payload.confirmation_text or "").strip() or None),
            confirmation_overridden=bool(payload.confirmation_overridden),
            files_overridden=bool(payload.files_overridden),
            files_json=normalized_files if payload.files is not None else None,
            conditional_files_overridden=bool(payload.conditional_files_overridden),
            conditional_files_json=normalized_conditional if payload.conditional_files is not None else None,
            automation_key=automation_key_value,
            automation_overridden=bool(payload.automation_overridden),
            is_enabled=bool(payload.is_enabled) if payload.is_enabled is not None else True,
            visibility_overridden=bool(payload.visibility_overridden),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.menu_type = menu_type
        row.workflow_type = workflow_type
        row.button_path = button_path
        if not bool(row.label_overridden):
            row.button_label = button_label
        row.currently_present = True
        if not row.stable_button_id:
            row.stable_button_id = str(uuid.uuid4())
        if payload.label_override is not None:
            row.label_override = override_label_value
        if payload.label_overridden is not None:
            row.label_overridden = bool(payload.label_overridden)
            if not row.label_overridden:
                row.label_override = None
                row.button_label = button_label
            elif not (row.label_override or "").strip():
                _error(422, "label_override_required", "Название в боте не может быть пустым")
        row.instruction_text = text_value
        if payload.client_text is not None:
            row.client_text = client_text_value
        if payload.client_text_overridden is not None:
            row.client_text_overridden = bool(payload.client_text_overridden)
            if not row.client_text_overridden:
                row.client_text = None
        if payload.system_text is not None:
            row.system_text = system_value
            row.system_text_overridden = True
        if payload.system_text_overridden is not None:
            row.system_text_overridden = bool(payload.system_text_overridden)
            if not row.system_text_overridden:
                row.system_text = None
        if payload.fixed_assignee_enabled is not None:
            row.fixed_assignee_enabled = fixed_enabled
            if not row.fixed_assignee_enabled:
                row.fixed_assignee_user_id = None
        if payload.fixed_assignee_user_id is not None:
            row.fixed_assignee_user_id = fixed_user_id
        if payload.freeze_on_create is not None:
            row.freeze_on_create = bool(payload.freeze_on_create)
        if payload.confirmation_enabled is not None:
            row.confirmation_enabled = bool(payload.confirmation_enabled)
        if payload.confirmation_text is not None:
            row.confirmation_text = (payload.confirmation_text or "").strip() or None
        if payload.confirmation_overridden is not None:
            row.confirmation_overridden = bool(payload.confirmation_overridden)
            if not row.confirmation_overridden:
                row.confirmation_enabled = False
                row.confirmation_text = None
        if payload.files is not None:
            row.files_json = normalized_files
            row.files_overridden = True
        if payload.files_overridden is not None:
            row.files_overridden = bool(payload.files_overridden)
            if not row.files_overridden:
                row.files_json = None
        if payload.conditional_files is not None:
            row.conditional_files_json = normalized_conditional
            row.conditional_files_overridden = True
        if payload.conditional_files_overridden is not None:
            row.conditional_files_overridden = bool(payload.conditional_files_overridden)
            if not row.conditional_files_overridden:
                row.conditional_files_json = None
        if payload.automation_key is not None:
            row.automation_key = automation_key_value
            row.automation_overridden = True
        if payload.automation_overridden is not None:
            row.automation_overridden = bool(payload.automation_overridden)
            if not row.automation_overridden:
                row.automation_key = None
        if payload.is_enabled is not None:
            row.is_enabled = bool(payload.is_enabled)
            row.visibility_overridden = True
        if payload.visibility_overridden is not None:
            row.visibility_overridden = bool(payload.visibility_overridden)
            if not row.visibility_overridden:
                row.is_enabled = True
        row.updated_at = now
    db.commit()
    return {
        "ok": True,
        "button_key": button_key,
        "has_instruction": bool(text_value),
        "client_text": row.client_text,
        "client_text_overridden": bool(row.client_text_overridden),
        "system_text_overridden": bool(row.system_text_overridden),
        "fixed_assignee_enabled": bool(row.fixed_assignee_enabled),
        "fixed_assignee_user_id": row.fixed_assignee_user_id,
        "freeze_on_create": bool(row.freeze_on_create),
        "confirmation_enabled": bool(row.confirmation_enabled),
        "confirmation_text": row.confirmation_text,
        "confirmation_overridden": bool(row.confirmation_overridden),
        "files_overridden": bool(row.files_overridden),
        "files": row.files_json if isinstance(row.files_json, list) else [],
        "conditional_files_overridden": bool(row.conditional_files_overridden),
        "conditional_files": row.conditional_files_json if isinstance(row.conditional_files_json, list) else [],
        "automation_key": (row.automation_key or "").strip() or None,
        "automation_overridden": bool(row.automation_overridden),
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
        "label_override": row.label_override,
        "label_overridden": bool(row.label_overridden),
        "button_label_effective": ((row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label),
        "button_label_from": ("db" if bool(row.label_overridden) and (row.label_override or "").strip() else "txt"),
    }


@router.delete("/bot-buttons/item")
def delete_bot_button_setting(
    button_key: str,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    if row is None:
        return {"ok": True, "deleted": False}
    if hard:
        db.delete(row)
        db.commit()
        return {"ok": True, "deleted": True, "hard": True}
    if not bool(row.is_db_created):
        _error(422, "cannot_delete_txt_button", "Txt-кнопки нельзя удалить из меню, используйте только отключение активности.")
    now = datetime.utcnow()
    row.currently_present = False
    row.is_archived = True
    row.deleted_at = now
    row.deleted_by_user_id = user.id
    row.updated_at = now
    row_kind = (row.button_kind or "").strip().lower()
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
                BotButtonSetting.is_db_created.is_(True),
                BotButtonSetting.button_path.like(f"{prefix}%"),
            )
        ).scalars().all()
        for child in descendants:
            child.currently_present = False
            child.is_archived = True
            child.deleted_at = now
            child.deleted_by_user_id = user.id
            child.updated_at = now
    db.commit()
    return {"ok": True, "deleted": True, "hard": False}


@router.post("/bot-buttons/item/restore")
def restore_bot_button_setting(
    button_key: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    if row is None:
        _error(404, "button_setting_not_found", "Запись кнопки не найдена")
    if not bool(row.is_db_created):
        _error(422, "cannot_restore_txt_button", "Восстановление применяется только к элементам, созданным через UI.")
    now = datetime.utcnow()
    row.currently_present = True
    row.is_archived = False
    row.deleted_at = None
    row.deleted_by_user_id = None
    row.updated_at = now
    row_kind = (row.button_kind or "").strip().lower()
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
                BotButtonSetting.is_db_created.is_(True),
                BotButtonSetting.button_path.like(f"{prefix}%"),
            )
        ).scalars().all()
        for child in descendants:
            child.currently_present = True
            child.is_archived = False
            child.deleted_at = None
            child.deleted_by_user_id = None
            child.updated_at = now
    db.commit()
    return {"ok": True, "restored": True}
