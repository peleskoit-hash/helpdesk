from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientCardSourceConfig, ClientProfile
from ..schemas import ClientCardBlockConfigOut, ClientCardFieldConfigOut, ClientCardRuleConfigOut, ClientCardRuntimeConfigResponse, ClientCardSourceRuntimeOut, ClientProfileOut

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
    if isinstance(raw_options, list) and raw_options:
        return raw_options
    if bool(field.is_system):
        return list(SYSTEM_SELECT_FIELD_OPTIONS.get(field.key, [])) or None
    return None


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
    return ClientProfileOut(
        telegram_username=telegram_username,
        telegram_display_name=telegram_display_name,
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
                is_editable=bool(field.is_editable),
                is_required=bool(field.is_required),
                is_system=bool(field.is_system),
                options=_field_options_out(field),
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
        options = field.options_json or []
        if options and value_str not in options:
            _error(422, "invalid_custom_field_value", f"Поле '{field.label}' содержит недопустимое значение")
        return value_str or None
    return str(value).strip() or None
