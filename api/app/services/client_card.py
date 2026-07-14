from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientCardBlockConfig, ClientCardFieldConfig, ClientCardRuleConfig, ClientProfile
from ..schemas import ClientCardBlockConfigOut, ClientCardConfigResponse, ClientCardFieldConfigOut, ClientCardRuleConfigOut, ClientProfileOut


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _profile_out(
    profile: ClientProfile,
    telegram_username: str | None,
    telegram_display_name: str | None,
) -> ClientProfileOut:
    return ClientProfileOut(
        telegram_username=telegram_username,
        telegram_display_name=telegram_display_name,
        contact_name=profile.contact_name,
        last_name=profile.last_name,
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
        internal_number=profile.internal_number,
        external_user_id=profile.external_user_id,
        bitrix_login=profile.bitrix_login,
        bitrix_password=profile.bitrix_password,
        bitrix_id=profile.bitrix_id,
        custom_fields=profile.custom_fields or {},
    )


def _client_card_view_config(db: Session) -> ClientCardConfigResponse:
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
            field_visible_in_editor = (not bool(field.is_system)) and bool(field.is_editable)
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
                options=field.options_json or None,
            )
        )
    return ClientCardConfigResponse(
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
                .order_by(ClientCardRuleConfig.sort_order.asc(), ClientCardRuleConfig.created_at.asc())
            ).scalars().all()
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
