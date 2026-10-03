import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, ClientCardFieldConfig, User
from ..schemas import BotButtonInstructionUpdate
from ..services.button_automation_types import (
    AUTOMATION_TYPE_NONE,
    AUTOMATION_TYPE_SEND_FILES,
    TARGET_WORKFLOW_TYPES,
    automation_type_from_key,
    canonical_automation_key_from_type,
    get_button_automation_meta,
    is_automation_allowed_for_workflow,
    is_known_button_automation_key,
    list_button_automation_types,
    normalize_automation_type,
    normalize_workflow_type,
)
from ..services.button_config import (
    BUTTON_CONFIG_DIR,
    find_button,
    list_terminal_buttons,
    load_button_config,
)
from ..services.telegram_automation import get_automation_registry
from ..services.system_tickets import (
    FEEDBACK_SYSTEM_BUTTONS,
    FIRST_CONNECTION_BUTTON_KEY,
    FirstConnectionDefaults,
    REVIEW_SYSTEM_BUTTONS,
    SYSTEM_MENU_TYPE,
    first_connection_values,
    materialize_first_connection_row,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ALLOWED_BUTTON_STYLES = {"primary", "success", "danger"}


class FirstConnectionSystemUpdate(BaseModel):
    subject: str | None = None
    client_text: str | None = None
    system_text: str | None = None
    instruction_text: str | None = None
    is_enabled: bool | None = None


class SystemButtonUpdate(BaseModel):
    key: str
    text: str | None = None
    button_style: str | None = None


class SystemButtonGroupUpdate(BaseModel):
    buttons: list[SystemButtonUpdate] = []


def _first_connection_defaults() -> FirstConnectionDefaults:
    from .telegram import FIRST_CONNECTION_AGENT_TEXT, FIRST_CONNECTION_CLIENT_TEXT, FIRST_CONNECTION_SUBJECT

    return FirstConnectionDefaults(
        subject=FIRST_CONNECTION_SUBJECT,
        client_text=FIRST_CONNECTION_CLIENT_TEXT,
        system_text=FIRST_CONNECTION_AGENT_TEXT,
    )

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
EDITABLE_EXISTING_WORKFLOW_TYPES = {"ticket", "task", "auto"}
ERROR_SYSTEM_BUTTON_TYPE_CHANGE_FORBIDDEN = "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0439 \u0437\u0430\u044f\u0432\u043a\u0435 \u043d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f"
ERROR_SUBMENU_TYPE_CHANGE_FORBIDDEN = "\u041d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f \u043f\u043e\u0434\u043c\u0435\u043d\u044e \u0447\u0435\u0440\u0435\u0437 \u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u043a\u043d\u043e\u043f\u043a\u0438"
ERROR_GROUP_TASK_TYPE_CHANGE_FORBIDDEN = "\u041d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f \u0433\u0440\u0443\u043f\u043f\u043e\u0432\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 \u0447\u0435\u0440\u0435\u0437 \u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u043a\u043d\u043e\u043f\u043a\u0438"
ERROR_GROUP_TASK_CHILD_TYPE_CHANGE_FORBIDDEN = "\u041d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f \u0434\u043e\u0447\u0435\u0440\u043d\u0435\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 \u0447\u0435\u0440\u0435\u0437 \u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u043a\u043d\u043e\u043f\u043a\u0438"
ERROR_UNKNOWN_BUTTON_TYPE_CHANGE_FORBIDDEN = "\u041d\u0435\u043b\u044c\u0437\u044f \u043c\u0435\u043d\u044f\u0442\u044c \u0442\u0438\u043f \u044d\u0442\u043e\u0439 \u043a\u043d\u043e\u043f\u043a\u0438"
ERROR_DISALLOWED_WORKFLOW_TYPE_CHANGE = "\u0420\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0430 \u0441\u043c\u0435\u043d\u0430 \u0442\u0438\u043f\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u043c\u0435\u0436\u0434\u0443 \u0417\u0430\u044f\u0432\u043a\u043e\u0439, \u0417\u0430\u0434\u0430\u0447\u0435\u0439 \u0438 \u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0435\u0439"


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _fallback_system_text(menu_type: str, button_key: str) -> str | None:
    button = _fallback_source_button(menu_type, button_key)
    if button is None:
        return None
    return button.system_text


def _fallback_source_button(menu_type: str, button_key: str):
    menu_name = (menu_type or "").strip()
    key = (button_key or "").strip()
    if not menu_name or not key:
        return None
    try:
        refs = list_terminal_buttons(menu_name)
    except Exception:
        refs = ()
    source_button_key = None
    for ref in refs:
        if ref.technical_key == key:
            source_button_key = ref.source_button_key
            break
    if source_button_key:
        return find_button(source_button_key)

    # group_task parent is not part of list_terminal_buttons, resolve it from encoded technical key
    # format: {menu_name}|group_task|{label_or_path}
    parts = key.split("|", 2)
    if len(parts) == 3 and parts[0] == menu_name and parts[1] == "group_task":
        target = parts[2]
        try:
            config = load_button_config(menu_name)
            root = config.menus.get(config.root_menu_key)
            if root is None:
                return None
            for row in root.rows:
                for bkey in row:
                    button = config.buttons.get(bkey)
                    if button is None or button.kind != "group_task":
                        continue
                    if button.label == target:
                        return button
        except Exception:
            return None
    return None


def _fallback_client_text(menu_type: str, button_key: str) -> str | None:
    button = _fallback_source_button(menu_type, button_key)
    if button is None:
        return None
    return button.text


def _fallback_automation_key(menu_type: str, button_key: str) -> str | None:
    button = _fallback_source_button(menu_type, button_key)
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


def _payload_fields_set(payload: BotButtonInstructionUpdate) -> set[str]:
    return set(getattr(payload, "model_fields_set", set()) or set())


def _normalize_button_style(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in ALLOWED_BUTTON_STYLES:
        _error(422, "invalid_button_style", "Недопустимый цвет кнопки")
    return normalized


def _button_style_out(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ALLOWED_BUTTON_STYLES else None


def _system_button_defs(group: str) -> tuple[dict[str, str], ...]:
    if group == "review":
        return REVIEW_SYSTEM_BUTTONS
    if group == "feedback":
        return FEEDBACK_SYSTEM_BUTTONS
    _error(404, "system_button_group_not_found", "Группа системных кнопок не найдена")


def _system_button_row(db: Session, button_key: str) -> BotButtonSetting | None:
    return db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()


def _system_button_text(row: BotButtonSetting | None, default_text: str) -> str:
    override = (row.label_override or "").strip() if row is not None and bool(row.label_overridden) else ""
    return override or default_text


def _system_button_payload(db: Session, group: str) -> dict:
    items = []
    for item in _system_button_defs(group):
        row = _system_button_row(db, item["key"])
        default_text = item["text"]
        items.append(
            {
                "key": item["key"],
                "text": _system_button_text(row, default_text),
                "default_text": default_text,
                "button_style": _button_style_out(row.button_style if row is not None else None),
            }
        )
    return {"group": group, "buttons": items}


def _materialize_system_button_row(db: Session, *, button_key: str, default_text: str, group: str) -> BotButtonSetting:
    row = _system_button_row(db, button_key)
    if row is not None:
        changed = False
        if row.menu_type != SYSTEM_MENU_TYPE:
            row.menu_type = SYSTEM_MENU_TYPE
            changed = True
        if row.button_kind != "system":
            row.button_kind = "system"
            changed = True
        if not (row.workflow_type or "").strip():
            row.workflow_type = group
            changed = True
        if not (row.button_path or "").strip():
            row.button_path = default_text
            changed = True
        if not (row.button_label or "").strip():
            row.button_label = default_text
            changed = True
        if changed:
            row.updated_at = datetime.utcnow()
        return row
    now = datetime.utcnow()
    row = BotButtonSetting(
        id=str(uuid.uuid4()),
        stable_button_id=str(uuid.uuid4()),
        button_key=button_key,
        legacy_button_key=None,
        menu_type=SYSTEM_MENU_TYPE,
        workflow_type=group,
        button_path=default_text,
        button_label=default_text,
        label_override=None,
        label_overridden=False,
        currently_present=True,
        is_enabled=True,
        visibility_overridden=False,
        is_db_created=True,
        button_kind="system",
        parent_path="",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _get_system_button_group(group: str, db: Session, user: CurrentUser) -> dict:
    _require_admin(user)
    return _system_button_payload(db, group)


def _save_system_button_group(group: str, payload: SystemButtonGroupUpdate, db: Session, user: CurrentUser) -> dict:
    _require_admin(user)
    defs = {item["key"]: item for item in _system_button_defs(group)}
    seen: set[str] = set()
    for button in payload.buttons or []:
        if button.key not in defs:
            _error(422, "system_button_key_invalid", "Неизвестная системная кнопка")
        if button.key in seen:
            _error(422, "system_button_key_duplicate", "Системная кнопка указана дважды")
        seen.add(button.key)
        text_value = str(button.text or "").strip()
        if not text_value:
            _error(422, "system_button_text_required", "Текст кнопки не может быть пустым")
        row = _materialize_system_button_row(db, button_key=button.key, default_text=defs[button.key]["text"], group=group)
        row.label_override = text_value
        row.label_overridden = text_value != defs[button.key]["text"]
        row.button_style = _normalize_button_style(button.button_style)
        row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, **_system_button_payload(db, group)}


def _validate_existing_button_workflow_transition(row: BotButtonSetting, requested_workflow_type: str) -> str:
    current_workflow = normalize_workflow_type(row.workflow_type)
    requested_workflow = normalize_workflow_type(requested_workflow_type)
    row_kind = (row.button_kind or "").strip().lower()
    if row.button_key == FIRST_CONNECTION_BUTTON_KEY or row_kind == "system":
        if requested_workflow and requested_workflow != current_workflow:
            _error(422, "system_button_type_change_forbidden", ERROR_SYSTEM_BUTTON_TYPE_CHANGE_FORBIDDEN)
        return current_workflow
    if current_workflow == "submenu" or row_kind == "section":
        if requested_workflow and requested_workflow != "submenu":
            _error(422, "button_type_change_forbidden", ERROR_SUBMENU_TYPE_CHANGE_FORBIDDEN)
        return "submenu"
    if current_workflow == "group_task" or row_kind == "group_parent":
        if requested_workflow and requested_workflow != current_workflow:
            _error(422, "button_type_change_forbidden", ERROR_GROUP_TASK_TYPE_CHANGE_FORBIDDEN)
        return current_workflow
    if current_workflow == "group_task_child":
        if requested_workflow and requested_workflow != current_workflow:
            _error(422, "button_type_change_forbidden", ERROR_GROUP_TASK_CHILD_TYPE_CHANGE_FORBIDDEN)
        return current_workflow
    if current_workflow not in EDITABLE_EXISTING_WORKFLOW_TYPES:
        if requested_workflow and requested_workflow != current_workflow:
            _error(422, "button_type_change_forbidden", ERROR_UNKNOWN_BUTTON_TYPE_CHANGE_FORBIDDEN)
        return current_workflow
    if requested_workflow not in EDITABLE_EXISTING_WORKFLOW_TYPES:
        _error(422, "button_type_change_forbidden", ERROR_DISALLOWED_WORKFLOW_TYPE_CHANGE)
    return requested_workflow


def _resolve_requested_automation_key(
    payload: BotButtonInstructionUpdate,
) -> tuple[bool, str | None]:
    fields_set = _payload_fields_set(payload)
    type_provided = "automation_type" in fields_set
    key_provided = "automation_key" in fields_set
    if not type_provided and not key_provided:
        return False, None

    type_key = canonical_automation_key_from_type(payload.automation_type) if type_provided else None
    legacy_key = canonical_automation_key_from_type(payload.automation_key) if key_provided else None
    if type_provided and key_provided and normalize_automation_type(type_key) != normalize_automation_type(legacy_key):
        _error(422, "automation_conflict", "automation_type и automation_key конфликтуют между собой")
    return True, type_key if type_provided else legacy_key


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


@router.get("/bot-buttons/system/first-connection")
def get_first_connection_system_ticket(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    defaults = _first_connection_defaults()
    row = materialize_first_connection_row(db, defaults)
    values = first_connection_values(db, defaults)
    db.commit()
    return {
        "button_key": FIRST_CONNECTION_BUTTON_KEY,
        "workflow_type": row.workflow_type,
        "button_label": row.button_label,
        "button_path": row.button_path,
        "subject": row.label_override,
        "subject_effective": values["subject"],
        "subject_overridden": bool(row.label_overridden),
        "client_text": row.client_text,
        "client_text_effective": values["client_text"],
        "client_text_overridden": bool(row.client_text_overridden),
        "system_text": row.system_text,
        "system_text_effective": values["system_text"],
        "system_text_overridden": bool(row.system_text_overridden),
        "instruction_text": row.instruction_text,
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
    }


@router.put("/bot-buttons/system/first-connection")
def save_first_connection_system_ticket(
    payload: FirstConnectionSystemUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    defaults = _first_connection_defaults()
    row = materialize_first_connection_row(db, defaults)
    fields_set = set(getattr(payload, "model_fields_set", set()) or set())

    if "subject" in fields_set and payload.subject is not None:
        value = payload.subject.strip()
        if value:
            row.label_override = value
            row.label_overridden = True
    if "client_text" in fields_set and payload.client_text is not None:
        value = payload.client_text.strip()
        if value:
            row.client_text = value
            row.client_text_overridden = True
    if "system_text" in fields_set and payload.system_text is not None:
        value = payload.system_text.strip()
        if value:
            row.system_text = value
            row.system_text_overridden = True
            row.instruction_text = value
    if "instruction_text" in fields_set and payload.instruction_text is not None:
        value = payload.instruction_text.strip()
        if value:
            row.instruction_text = value
    if "is_enabled" in fields_set and payload.is_enabled is not None:
        row.is_enabled = bool(payload.is_enabled)
        row.visibility_overridden = True

    row.updated_at = datetime.utcnow()
    values = first_connection_values(db, defaults)
    db.commit()
    return {
        "ok": True,
        "button_key": FIRST_CONNECTION_BUTTON_KEY,
        "subject_effective": values["subject"],
        "client_text_effective": values["client_text"],
        "system_text_effective": values["system_text"],
    }


@router.get("/bot-buttons/system/review-buttons")
def get_review_system_buttons(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _get_system_button_group("review", db, user)


@router.put("/bot-buttons/system/review-buttons")
def save_review_system_buttons(
    payload: SystemButtonGroupUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _save_system_button_group("review", payload, db, user)


@router.get("/bot-buttons/system/feedback-buttons")
def get_feedback_system_buttons(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _get_system_button_group("feedback", db, user)


@router.put("/bot-buttons/system/feedback-buttons")
def save_feedback_system_buttons(
    payload: SystemButtonGroupUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _save_system_button_group("feedback", payload, db, user)


@router.get("/bot-buttons/item")
def get_bot_button_item(
    button_key: str,
    menu_type: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    automation_registry = get_automation_registry()
    db_automation_key = (row.automation_key or "").strip() if row is not None else ""
    automation_type = automation_type_from_key(db_automation_key)
    effective_automation_key = db_automation_key or None
    effective_menu_type = menu_type or (row.menu_type if row else "")
    if row is None:
        none_meta = get_button_automation_meta(AUTOMATION_TYPE_NONE, automation_registry)
        return {
            "button_key": button_key,
            "stable_button_id": None,
            "legacy_button_key": None,
            "menu_type": effective_menu_type,
            "workflow_type": "",
            "button_path": "",
            "button_label": "",
            "button_style": None,
            "label_override": None,
            "label_overridden": False,
            "button_label_effective": "",
            "button_label_from": "txt",
            "removed": False,
            "currently_present": True,
            "instruction_text": None,
            "client_text": None,
            "client_text_overridden": False,
            "client_text_effective": None,
            "client_text_from": "db",
            "system_text": None,
            "system_text_overridden": False,
            "system_text_effective": None,
            "system_text_from": "db",
            "automation_type": AUTOMATION_TYPE_NONE,
            "automation_key_effective": None,
            "automation_key": None,
            "automation_overridden": False,
            "automation_from": "none",
            "automation_title": (none_meta.get("title") if none_meta else None),
            "automation_description": (none_meta.get("description") if none_meta else None),
            "automation_available": bool(none_meta.get("available")) if none_meta else False,
            "automation_handler": (none_meta.get("handler") if none_meta else None),
            "automation_status": ("available" if none_meta else "not_found"),
            "fixed_assignee_enabled": False,
            "fixed_assignee_user_id": None,
            "freeze_on_create": False,
            "confirmation_enabled": False,
            "confirmation_text": None,
            "confirmation_overridden": False,
            "files_overridden": False,
            "files": [],
            "files_effective": [],
            "files_from": "db",
            "conditional_files_overridden": False,
            "conditional_files": [],
            "conditional_files_effective": [],
            "conditional_files_from": "db",
            "is_enabled": True,
            "visibility_overridden": False,
        }
    effective_system_text = row.system_text
    effective_client_text = row.client_text
    db_files_raw = row.files_json if isinstance(row.files_json, list) else []
    db_files = [p for p in (_normalize_relative_button_file_path(str(v)) for v in db_files_raw) if p]
    db_conditional_raw = row.conditional_files_json if isinstance(row.conditional_files_json, list) else []
    db_conditional = [rule for rule in db_conditional_raw if isinstance(rule, dict)]
    has_legacy_files = bool(db_files or db_conditional)
    if effective_automation_key:
        effective_automation_type = automation_type
        automation_from = "db"
    elif has_legacy_files:
        effective_automation_type = AUTOMATION_TYPE_SEND_FILES
        automation_from = "legacy_files"
    else:
        effective_automation_type = AUTOMATION_TYPE_NONE
        automation_from = "none"
    automation_meta = get_button_automation_meta(effective_automation_type, automation_registry)
    return {
        "button_key": row.button_key,
        "stable_button_id": row.stable_button_id,
        "legacy_button_key": row.legacy_button_key,
        "menu_type": row.menu_type,
        "workflow_type": row.workflow_type,
        "button_path": row.button_path,
        "button_label": row.button_label,
        "button_style": _button_style_out(row.button_style),
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
        "client_text_from": "db",
        "system_text": row.system_text,
        "system_text_overridden": bool(row.system_text_overridden),
        "system_text_effective": effective_system_text,
        "system_text_from": "db",
        "automation_type": effective_automation_type,
        "automation_key_effective": effective_automation_key,
        "automation_key": db_automation_key or None,
        "automation_overridden": bool(row.automation_overridden),
        "automation_from": automation_from,
        "automation_title": (automation_meta.get("title") if automation_meta else None),
        "automation_description": (automation_meta.get("description") if automation_meta else None),
        "automation_available": bool(automation_meta.get("available")) if automation_meta else False,
        "automation_handler": (automation_meta.get("handler") if automation_meta else None),
        "automation_status": ("available" if automation_meta else "not_found"),
        "fixed_assignee_enabled": bool(row.fixed_assignee_enabled),
        "fixed_assignee_user_id": row.fixed_assignee_user_id,
        "freeze_on_create": bool(row.freeze_on_create),
        "confirmation_enabled": bool(row.confirmation_enabled),
        "confirmation_text": row.confirmation_text,
        "confirmation_overridden": bool(row.confirmation_overridden),
        "files_overridden": bool(row.files_overridden),
        "files": db_files,
        "files_effective": db_files,
        "files_from": "db",
        "conditional_files_overridden": bool(row.conditional_files_overridden),
        "conditional_files": db_conditional,
        "conditional_files_effective": db_conditional,
        "conditional_files_from": "db",
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
    return {"items": list_button_automation_types(registry)}


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
    requested_workflow_type = (workflow_type or "").strip()
    is_submenu_request = requested_workflow_type == "submenu"
    effective_workflow_type = "submenu" if is_submenu_request else requested_workflow_type
    button_path_value = (button_path or "").strip()
    parent_path_value = button_path_value.rsplit("/", 1)[0] if "/" in button_path_value else ""
    override_label_value = (payload.label_override or "").strip() if payload.label_override is not None else None
    override_label_enabled = bool(payload.label_overridden) if payload.label_overridden is not None else None
    fields_set = _payload_fields_set(payload)
    button_style_provided = "button_style" in fields_set
    button_style_value = _normalize_button_style(payload.button_style) if button_style_provided else None
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
    if payload.client_text is not None:
        _raw_client_text = str(payload.client_text).replace("\r\n", "\n").replace("\r", "\n")
        client_text_value = _raw_client_text if _raw_client_text.strip() else None
    else:
        client_text_value = None
    if payload.confirmation_text is not None:
        _raw_confirmation_text = str(payload.confirmation_text).replace("\r\n", "\n").replace("\r", "\n")
        confirmation_text_value = _raw_confirmation_text if _raw_confirmation_text.strip() else None
    else:
        confirmation_text_value = None
    system_value = (payload.system_text or "").strip() or None if payload.system_text is not None else None
    automation_type_requested, automation_key_value = _resolve_requested_automation_key(payload)
    if automation_type_requested:
        registry = get_automation_registry()
        if not is_known_button_automation_key(automation_key_value, registry):
            _error(422, "automation_not_found", "Указанная автоматизация не найдена в реестре")
        normalized_workflow = normalize_workflow_type(effective_workflow_type)
        if normalized_workflow in TARGET_WORKFLOW_TYPES and not is_automation_allowed_for_workflow(
            normalized_workflow,
            automation_key_value,
            allow_auto_none=True,
        ):
            _error(422, "automation_not_allowed_for_workflow", "Эта автоматизация недоступна для выбранного типа кнопки")
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
    workflow_type_changed = False
    if row is not None:
        effective_workflow_type = _validate_existing_button_workflow_transition(row, requested_workflow_type)
        is_submenu_request = effective_workflow_type == "submenu"
        workflow_type_changed = normalize_workflow_type(row.workflow_type) != normalize_workflow_type(effective_workflow_type)
    if row is None:
        row = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=str(uuid.uuid4()),
            button_key=button_key,
            legacy_button_key=None,
            menu_type=menu_type,
            workflow_type=effective_workflow_type,
            button_path=button_path,
            button_label=button_label,
            button_style=button_style_value,
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
            confirmation_text=confirmation_text_value,
            confirmation_overridden=bool(payload.confirmation_overridden),
            files_overridden=bool(payload.files_overridden),
            files_json=normalized_files if payload.files is not None else None,
            conditional_files_overridden=bool(payload.conditional_files_overridden),
            conditional_files_json=normalized_conditional if payload.conditional_files is not None else None,
            automation_key=automation_key_value if automation_type_requested else None,
            automation_overridden=bool(payload.automation_overridden),
            is_enabled=bool(payload.is_enabled) if payload.is_enabled is not None else True,
            visibility_overridden=bool(payload.visibility_overridden),
            button_kind="section" if is_submenu_request else "terminal",
            parent_path=parent_path_value,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.menu_type = menu_type
        row.workflow_type = effective_workflow_type
        row.button_path = button_path
        if effective_workflow_type == "submenu":
            row.button_kind = "section"
        elif effective_workflow_type in EDITABLE_EXISTING_WORKFLOW_TYPES:
            row.button_kind = "terminal"
        row.parent_path = parent_path_value
        if not bool(row.label_overridden):
            row.button_label = button_label
        row.currently_present = True
        if not row.stable_button_id:
            row.stable_button_id = str(uuid.uuid4())
        if button_style_provided:
            row.button_style = button_style_value
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
            row.confirmation_text = confirmation_text_value
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
        if automation_type_requested:
            row.automation_key = automation_key_value
            row.automation_overridden = True
        elif workflow_type_changed and not is_automation_allowed_for_workflow(
            effective_workflow_type,
            row.automation_key,
            allow_auto_none=True,
        ):
            row.automation_key = None
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
        "automation_type": automation_type_from_key((row.automation_key or "").strip() or None),
        "automation_key": (row.automation_key or "").strip() or None,
        "automation_overridden": bool(row.automation_overridden),
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
        "label_override": row.label_override,
        "label_overridden": bool(row.label_overridden),
        "button_style": _button_style_out(row.button_style),
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
    if button_key == FIRST_CONNECTION_BUTTON_KEY:
        _error(422, "system_button_not_deletable", "System ticket cannot be deleted through regular bot button endpoint")
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    if row is None:
        return {"ok": True, "deleted": False}
    if hard:
        db.delete(row)
        db.commit()
        return {"ok": True, "deleted": True, "hard": True}
    row_kind = (row.button_kind or "").strip().lower()
    if not bool(row.is_db_created) and row_kind != "section":
        _error(422, "cannot_delete_txt_button", "Txt-кнопки нельзя удалить из меню, используйте только отключение активности.")
    now = datetime.utcnow()
    row.currently_present = False
    row.is_archived = True
    row.deleted_at = now
    row.deleted_by_user_id = user.id
    row.updated_at = now
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
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
    row_kind = (row.button_kind or "").strip().lower()
    if not bool(row.is_db_created) and row_kind != "section":
        _error(422, "cannot_restore_txt_button", "Восстановление применяется только к элементам, созданным через UI.")
    now = datetime.utcnow()
    row.currently_present = True
    row.is_archived = False
    row.deleted_at = None
    row.deleted_by_user_id = None
    row.updated_at = now
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
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
