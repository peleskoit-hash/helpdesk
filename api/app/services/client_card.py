from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig, ClientProfile
from ..schemas import ClientCardBlockConfigOut, ClientCardFieldConfigOut, ClientCardRuleAction, ClientCardRuleConfigOut, ClientCardRuntimeConfigResponse, ClientCardSourceRuntimeOut, ClientProfileOut
from .temporary_rop import with_temporary_rop_suffix
from .web_credentials import WEB_COLUMNS, without_web_fields

_SEARCHABLE_FIELD_TYPES = {"text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "readonly"}
_PROTECTED_FIELD_NAME = re.compile(
    r"password|passwd|passphrase|pwd|secret|token|apikey|privatekey|encrypted|cipher|masked"
    r"|\u043f\u0430\u0440\u043e\u043b|\u0441\u0435\u043a\u0440\u0435\u0442|\u0442\u043e\u043a\u0435\u043d"
)
_TELEGRAM_ID_NAME = re.compile(
    r"(?:telegram|tg|external)(?:user|chat|contact|u)?id|(?:user|chat)id(?:telegram|tg)?|"
    r"id(?:telegram|tg|external)(?:user|chat|contact)?|id\u0447\u0430\u0442\u0430"
)


SYSTEM_PROFILE_FIELD_KEYS = {
    "contact_name",
    "last_name",
    "email",
    "phone",
    "verification_status",
    "is_verified",
    "destination",
    "sand_owner",
    "sales_group",
    "department",
    "first_department",
    "group_name",
    "employee_type",
    "location_type",
    "anydesk",
    "internal_number",
    "external_user_id",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
}

SYSTEM_FIELD_EDITOR_VISIBILITY_DEFAULTS = {
    "telegram_username": True,
    "telegram_display_name": True,
    "last_name": True,
    "contact_name": True,
    "destination": True,
    "sand_owner": False,
    "sales_group": False,
    "department": False,
    "first_department": False,
    "group_name": False,
    "location_type": True,
    "anydesk": False,
    "internal_number": True,
    "bitrix_login": True,
    "bitrix_password": True,
    "bitrix_id": True,
    "is_verified": True,
}

SYSTEM_SELECT_FIELD_OPTIONS = {
    "destination": [
        "Песок",
        "Отдел Продаж",
        "Колл Центр",
        "HR",
        "Руководство",
        "Отдел Коучей",
        "Контроль Качества",
    ],
    "sand_owner": [
        "песок Власова",
        "песок Скрылёвой",
        "песок Сайдаралиева",
        "песок Макаренко",
        "песок Шахалиевой",
        "песок Кочуры",
        "песок Калинина",
    ],
    "sales_group": [
        "группа Власова",
        "группа Скрылёвой",
        "группа Сайдаралиева",
        "группа Макаренко",
        "группа Шахалиевой",
        "группа Кочуры",
        "группа Калинина",
    ],
    "location_type": [
        "Офисный",
        "Удалённый",
    ],
}


def _field_editor_visibility_default(field: ClientCardFieldConfig) -> bool:
    if not bool(field.is_system):
        return bool(field.is_editable)
    return SYSTEM_FIELD_EDITOR_VISIBILITY_DEFAULTS.get(field.key, bool(field.is_editable))


def _field_options_out(field: ClientCardFieldConfig) -> list[str] | None:
    raw_options = field.options_json
    if isinstance(raw_options, str):
        try:
            parsed = json.loads(raw_options)
        except Exception:
            parsed = None
        raw_options = parsed
    if isinstance(raw_options, list) and (raw_options or not field.is_system):
        return raw_options
    if field.is_system:
        return list(SYSTEM_SELECT_FIELD_OPTIONS.get(field.key, [])) or None
    return None


def _field_admin_only_values_out(field: ClientCardFieldConfig) -> list[str] | None:
    if field.field_type != "select":
        return None
    raw_values = getattr(field, "admin_only_values_json", None)
    if not isinstance(raw_values, list):
        return None
    options = set(_field_options_out(field) or [])
    values = []
    seen = set()
    for item in raw_values:
        value = str(item or "").strip()
        if value and value in options and value not in seen:
            values.append(value)
            seen.add(value)
    return values or None


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _profile_out(
    profile: ClientProfile,
    telegram_username: str | None,
    telegram_display_name: str | None,
    field_overrides: dict[str, Any] | None = None,
) -> ClientProfileOut:
    overrides = field_overrides or {}
    custom_field_overrides = overrides.get("custom_fields") if isinstance(overrides.get("custom_fields"), dict) else {}

    def value(key: str):
        if key in overrides:
            return overrides[key]
        return getattr(profile, key)

    custom_fields = {
        key: item
        for key, item in dict(profile.custom_fields or {}).items()
        if key not in SYSTEM_PROFILE_FIELD_KEYS
    }
    if custom_field_overrides:
        custom_fields.update(
            {
                key: item
                for key, item in custom_field_overrides.items()
                if key not in SYSTEM_PROFILE_FIELD_KEYS
            }
        )
    custom_fields = without_web_fields(custom_fields)
    return ClientProfileOut(
        web_login=profile.web_login,
        web_password_set=bool(profile.web_password_encrypted),
        telegram_username=telegram_username,
        telegram_display_name=telegram_display_name,
        display_name=with_temporary_rop_suffix(
            " ".join(part for part in (value("last_name"), value("contact_name")) if part).strip()
            or telegram_display_name
            or "",
            profile,
        ) or None,
        contact_name=value("contact_name"),
        last_name=value("last_name"),
        email=profile.email,
        phone=profile.phone,
        verification_status=profile.verification_status,
        is_verified=bool(profile.is_verified),
        destination=profile.destination,
        sand_owner=profile.sand_owner,
        sales_group=profile.sales_group,
        department=profile.department,
        first_department=profile.first_department,
        group_name=profile.group_name,
        employee_type=profile.employee_type,
        location_type=profile.location_type,
        anydesk=profile.anydesk,
        internal_number=value("internal_number"),
        external_user_id=value("external_user_id"),
        bitrix_login=value("bitrix_login"),
        bitrix_password=None,
        bitrix_password_set=bool(profile.bitrix_password and str(profile.bitrix_password).strip()),
        bitrix_id=value("bitrix_id"),
        custom_fields=custom_fields,
    )


def _client_card_view_config(db: Session) -> ClientCardRuntimeConfigResponse:
    blocks = db.execute(
        select(ClientCardBlockConfig)
        .where(ClientCardBlockConfig.is_active.is_(True))
        .order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.created_at.asc())
    ).scalars().all()
    fields = db.execute(
        select(ClientCardFieldConfig).order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()
    fields_by_block: dict[str, list[ClientCardFieldConfigOut]] = {}
    for field in fields:
        field_visible_in_editor = getattr(field, "is_visible_in_editor", None)
        if field_visible_in_editor is None:
            field_visible_in_editor = _field_editor_visibility_default(field)
        fields_by_block.setdefault(field.block_key, []).append(
            ClientCardFieldConfigOut(
                id=field.id,
                key=field.key,
                label=field.label,
                field_type=field.field_type,
                block_key=field.block_key,
                sort_order=field.sort_order,
                is_visible=bool(field.is_visible),
                is_visible_in_editor=bool(field_visible_in_editor),
                show_when_false=getattr(field, "show_when_false", None) is not False,
                is_editable=bool(field.is_editable),
                admin_only_edit=bool(getattr(field, "admin_only_edit", False)),
                is_required=bool(field.is_required),
                is_system=bool(field.is_system),
                options=_field_options_out(field),
                admin_only_values=_field_admin_only_values_out(field),
            )
        )
    sources = db.execute(
        select(ClientCardSourceConfig)
        .where(ClientCardSourceConfig.is_active.is_(True))
        .order_by(ClientCardSourceConfig.created_at.asc())
    ).scalars().all()
    return ClientCardRuntimeConfigResponse(
        blocks=[
            ClientCardBlockConfigOut(
                id=block.id,
                key=block.key,
                title=block.title,
                color_scheme=block.color_scheme,
                sort_order=block.sort_order,
                is_active=bool(block.is_active),
                show_in_card=block.show_in_card is not False,
                show_in_editor=block.show_in_editor is not False,
                show_title=block.show_title is not False,
                fields=fields_by_block.get(block.key, []),
            )
            for block in blocks
        ],
        rules=[
            ClientCardRuleConfigOut(
                id=rule.id,
                technical_key=rule.technical_key,
                name=rule.name,
                is_active=bool(rule.is_active),
                is_system=bool(rule.is_system),
                sort_order=rule.sort_order,
                condition_field_key=rule.condition_field_key or None,
                condition_operator=rule.condition_operator,
                condition_value=rule.condition_value,
                actions=rule.actions_json or [],
            )
            for rule in db.execute(
                select(ClientCardRuleConfig)
                .where(ClientCardRuleConfig.is_active.is_(True))
                .order_by(ClientCardRuleConfig.sort_order.asc(), ClientCardRuleConfig.id.asc())
            ).scalars().all()
        ],
        sources=[
            ClientCardSourceRuntimeOut(
                id=source.id,
                technical_key=source.technical_key,
                name=source.name,
                is_active=bool(source.is_active),
            )
            for source in sources
        ],
    )


def _permitted_client_card_fields(
    config: ClientCardRuntimeConfigResponse, *, admin: bool, extra_system_keys: frozenset[str]
) -> list[ClientCardFieldConfigOut]:
    fields = []
    for block in config.blocks:
        if not block.is_active:
            continue
        for field in block.fields:
            if field.field_type not in _SEARCHABLE_FIELD_TYPES or (field.admin_only_edit and not admin):
                continue
            if field.key in WEB_COLUMNS:
                continue
            names = [re.sub(r"[\W_]+", "", name.casefold()) for name in (field.key, field.label)]
            if any(_PROTECTED_FIELD_NAME.search(name) for name in names):
                continue
            if field.is_system:
                if field.key not in SYSTEM_PROFILE_FIELD_KEYS and field.key not in extra_system_keys:
                    continue
            elif not field.key.startswith("custom_") or field.key in SYSTEM_PROFILE_FIELD_KEYS:
                continue
            fields.append(field)
    return fields


_VisibilityRules = list[tuple[ClientCardRuleConfigOut, list[ClientCardRuleAction]]]


def _client_card_visibility_rules(config: ClientCardRuntimeConfigResponse) -> _VisibilityRules:
    rules = []
    for rule in sorted(config.rules, key=lambda rule: (rule.sort_order, rule.id)):
        if not rule.is_active:
            continue
        actions = [
            action for action in rule.actions
            if action.action in {"show", "hide"} and action.scope in {"card", "editor", "both"}
        ]
        if actions:
            rules.append((rule, actions))
    return rules


def _visible_client_card_field_keys(config: ClientCardRuntimeConfigResponse, overrides=None) -> set[str]:
    overrides = overrides or {}
    visible = set()
    for block in config.blocks:
        if not block.is_active:
            continue
        for view, block_shown in (("card", block.show_in_card), ("editor", block.show_in_editor)):
            if overrides.get((view, "block", block.key), block_shown):
                visible.update(
                    field.key for field in block.fields
                    if overrides.get((view, "field", field.key), field.is_visible if view == "card" else field.is_visible_in_editor)
                )
    return visible


def _searchable_client_card_fields(
    config: ClientCardRuntimeConfigResponse, fields: list[ClientCardFieldConfigOut], rules: _VisibilityRules
) -> list[ClientCardFieldConfigOut]:
    hidden_targets = {
        (action.target_type, action.target_key)
        for _, actions in rules for action in actions if action.action == "hide"
    }
    visible = _visible_client_card_field_keys(config)
    return [
        field for field in fields
        if field.key in visible and ("field", field.key) not in hidden_targets
        and ("block", field.block_key) not in hidden_targets and not _is_client_card_identifier(field)
    ]


def _visibility_rule_matches(rule: ClientCardRuleConfigOut, values: dict[str, str]) -> bool | None:
    operator = rule.condition_operator
    if operator == "always":
        return True
    if operator not in {"equals", "not_equals", "is_empty", "is_not_empty"}:
        return False
    if rule.condition_field_key not in values:
        return None
    current = values[rule.condition_field_key]
    expected = (rule.condition_value or "").strip()
    if operator == "equals":
        return current == expected
    if operator == "not_equals":
        return current != expected
    if operator == "is_empty":
        return current == ""
    return current != ""


def _client_card_column_keys(
    config: ClientCardRuntimeConfigResponse, rules: _VisibilityRules, condition_values: dict[str, str] | None = None,
) -> set[str]:
    overrides = {}
    resolved = set()
    withheld = set()
    for rule, actions in rules:
        match = True if condition_values is None else _visibility_rule_matches(rule, condition_values)
        for action in actions:
            show = action.action == "show"
            target = (action.target_type, action.target_key)
            views = ("card", "editor") if action.scope == "both" else (action.scope,)
            if condition_values is None:  # Catalog includes targets that can become visible.
                if show:
                    overrides.update({(view, *target): True for view in views})
                continue
            conflict_key = (action.scope, *target)
            if match is None and not show:
                withheld.update((view, *target) for view in views)
            elif match and conflict_key not in resolved:
                resolved.add(conflict_key)
                overrides.update({(view, *target): show for view in views})
    overrides.update(dict.fromkeys(withheld, False))
    return _visible_client_card_field_keys(config, overrides)


def _is_client_card_identifier(field: ClientCardFieldConfigOut) -> bool:
    names = (re.sub(r"[\W_]+", "", name.casefold()) for name in (field.key, field.label))
    return any(_TELEGRAM_ID_NAME.search(name) for name in names)


def _client_card_raw_value(profile, field, contact_values):
    if field.is_system and field.key in contact_values:
        return contact_values[field.key]
    if profile is None:
        return None
    if field.key == "custom_web_login":
        return profile.web_login
    if field.is_system:
        return getattr(profile, field.key)
    return profile.custom_fields.get(field.key) if isinstance(profile.custom_fields, dict) else None


def _accessible_client_card_values(
    profile: ClientProfile | None, fields: list[ClientCardFieldConfigOut], *, admin: bool,
    contact_values: dict[str, str | None] | None = None, conditions: bool = False,
) -> dict[str, str]:
    values = {}
    for field in fields:
        value = _client_card_raw_value(profile, field, contact_values or {})
        if conditions and value is None:
            values[field.key] = ""
            continue
        if not isinstance(value, (str, int, float, bool)):
            continue
        if not conditions and field.field_type == "checkbox" and value is False and not field.show_when_false:
            continue
        text = str(value).strip()
        if not admin and text in (field.admin_only_values or ()):
            continue
        if conditions or text:
            values[field.key] = text.lower() if conditions and isinstance(value, bool) else text
    return values


def _custom_field_map(db: Session) -> dict[str, ClientCardFieldConfig]:
    fields = db.execute(
        select(ClientCardFieldConfig).where(ClientCardFieldConfig.is_system.is_(False))
    ).scalars().all()
    return {field.key: field for field in fields}


def _normalize_custom_field_value(field: ClientCardFieldConfig, value):
    if value in ("", None):
        return None
    field_type = (field.field_type or "text").lower()
    if field_type == "checkbox":
        return bool(value)
    if field_type == "number":
        try:
            if isinstance(value, str) and "." in value:
                return float(value)
            return int(value)
        except Exception:
            _error(422, "invalid_custom_field_value", f"Поле '{field.label}' должно быть числом")
    if field_type == "date":
        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                _error(422, "invalid_custom_field_value", f"Поле '{field.label}' должно быть в формате YYYY-MM-DD")
            return value
        _error(422, "invalid_custom_field_value", f"Поле '{field.label}' должно быть строкой даты YYYY-MM-DD")
    if field_type == "select":
        value_str = str(value).strip()
        options = _field_options_out(field) or []
        if options and value_str not in options:
            _error(422, "invalid_custom_field_value", f"Поле '{field.label}' содержит недопустимое значение")
        return value_str or None
    return str(value).strip() or None
