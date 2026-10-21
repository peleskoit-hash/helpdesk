from __future__ import annotations

from copy import deepcopy

AUTOMATION_TYPE_NONE = "none"
AUTOMATION_TYPE_SEND_FILES = "send_files"
AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP = "bitrix_deal_phone_lookup"
AUTOMATION_TYPE_MESSAGE_ONLY = "message_only"
AUTOMATION_TYPE_TELEGRAM_GROUP = "telegram_group"

WORKFLOW_TYPE_TICKET = "ticket"
WORKFLOW_TYPE_TASK = "task"
WORKFLOW_TYPE_AUTO = "auto"

TARGET_WORKFLOW_TYPES = {
    WORKFLOW_TYPE_TICKET,
    WORKFLOW_TYPE_TASK,
    WORKFLOW_TYPE_AUTO,
}

_AUTOMATION_TYPE_DEFS: dict[str, dict] = {
    AUTOMATION_TYPE_NONE: {
        "key": AUTOMATION_TYPE_NONE,
        "title": "Не использовать",
        "description": "Автоматизация не применяется.",
        "workflow_types": [WORKFLOW_TYPE_TICKET, WORKFLOW_TYPE_TASK, WORKFLOW_TYPE_AUTO],
        "behavior_kind": "builtin",
        "ui_visible": True,
        "handler": None,
    },
    AUTOMATION_TYPE_SEND_FILES: {
        "key": AUTOMATION_TYPE_SEND_FILES,
        "title": "Отправка файлов",
        "description": "Отправляет настроенные файлы клиенту.",
        "workflow_types": [WORKFLOW_TYPE_TICKET, WORKFLOW_TYPE_TASK, WORKFLOW_TYPE_AUTO],
        "behavior_kind": "builtin",
        "ui_visible": True,
        "handler": None,
    },
    AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP: {
        "key": AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP,
        "title": "Расшифровка номера Bitrix",
        "description": "Ищет сделку/контакт/лид в Bitrix по номеру и возвращает клиенту найденный телефон.",
        "workflow_types": [WORKFLOW_TYPE_TASK],
        "behavior_kind": "registry",
        "ui_visible": True,
        "handler": "_process_bitrix_deal_phone_lookup_task",
    },
    AUTOMATION_TYPE_MESSAGE_ONLY: {
        "key": AUTOMATION_TYPE_MESSAGE_ONLY,
        "title": "Только сообщение",
        "description": "Отправляет текст клиенту и завершает автоматическую задачу.",
        "workflow_types": [WORKFLOW_TYPE_AUTO],
        "behavior_kind": "builtin",
        "ui_visible": True,
        "handler": None,
    },
    AUTOMATION_TYPE_TELEGRAM_GROUP: {
        "key": AUTOMATION_TYPE_TELEGRAM_GROUP,
        "title": "Отправка в Telegram-группу",
        "description": "Регистрация назначения. Пересылка пока не реализована; обращение передаётся сотруднику.",
        "workflow_types": [WORKFLOW_TYPE_TICKET, WORKFLOW_TYPE_TASK, WORKFLOW_TYPE_AUTO],
        "behavior_kind": "builtin",
        "ui_visible": True,
        "available": False,
        "handler": None,
    },
}


def normalize_workflow_type(value: str | None) -> str:
    return str(value or "").strip().lower()


def normalize_automation_type(value: str | None) -> str:
    return str(value or "").strip()


def canonical_automation_key_from_type(automation_type: str | None) -> str | None:
    normalized = normalize_automation_type(automation_type)
    if not normalized or normalized == AUTOMATION_TYPE_NONE:
        return None
    return normalized


def automation_type_from_key(automation_key: str | None) -> str:
    normalized = normalize_automation_type(automation_key)
    if not normalized:
        return AUTOMATION_TYPE_NONE
    return normalized


def get_automation_type_definitions() -> dict[str, dict]:
    return {key: deepcopy(value) for key, value in _AUTOMATION_TYPE_DEFS.items()}


def list_button_automation_types(
    registry: dict[str, dict] | None = None,
    *,
    include_hidden: bool = False,
) -> list[dict]:
    registry = registry or {}
    items: list[dict] = []
    for key, base in _AUTOMATION_TYPE_DEFS.items():
        if not include_hidden and not bool(base.get("ui_visible")):
            continue
        item = deepcopy(base)
        if item.get("behavior_kind") == "registry":
            meta = registry.get(key) or {}
            item["title"] = str(meta.get("title") or item.get("title") or key)
            item["description"] = str(meta.get("description") or item.get("description") or "")
            item["available"] = bool(meta.get("available"))
            item["enabled"] = bool(meta.get("enabled", True))
            item["handler"] = meta.get("handler") or item.get("handler")
        else:
            item["available"] = bool(item.get("available", True))
            item["enabled"] = True
        items.append(item)
    return items


def get_button_automation_meta(
    automation_key: str | None,
    registry: dict[str, dict] | None = None,
) -> dict | None:
    automation_type = automation_type_from_key(automation_key)
    definitions = {
        item["key"]: item
        for item in list_button_automation_types(registry, include_hidden=True)
    }
    return definitions.get(automation_type)


def is_builtin_button_automation_key(automation_key: str | None) -> bool:
    meta = _AUTOMATION_TYPE_DEFS.get(automation_type_from_key(automation_key))
    if meta is None:
        return False
    return meta.get("behavior_kind") == "builtin"


def is_known_button_automation_key(
    automation_key: str | None,
    registry: dict[str, dict] | None = None,
) -> bool:
    normalized = normalize_automation_type(automation_key)
    if not normalized:
        return True
    if normalized in _AUTOMATION_TYPE_DEFS:
        return True
    if registry and normalized in registry:
        return True
    return False


def is_automation_allowed_for_workflow(
    workflow_type: str | None,
    automation_key: str | None,
    *,
    allow_auto_none: bool = True,
) -> bool:
    workflow = normalize_workflow_type(workflow_type)
    if workflow not in TARGET_WORKFLOW_TYPES:
        return True
    normalized_key = canonical_automation_key_from_type(automation_key)
    if normalized_key is None:
        return workflow != WORKFLOW_TYPE_AUTO or allow_auto_none
    if normalized_key == AUTOMATION_TYPE_TELEGRAM_GROUP:
        return True
    if workflow == WORKFLOW_TYPE_TICKET:
        return normalized_key == AUTOMATION_TYPE_SEND_FILES
    if workflow == WORKFLOW_TYPE_TASK:
        return normalized_key in {
            AUTOMATION_TYPE_SEND_FILES,
            AUTOMATION_TYPE_BITRIX_DEAL_PHONE_LOOKUP,
        }
    if workflow == WORKFLOW_TYPE_AUTO:
        return normalized_key in {
            AUTOMATION_TYPE_SEND_FILES,
            AUTOMATION_TYPE_MESSAGE_ONLY,
        }
    return False
