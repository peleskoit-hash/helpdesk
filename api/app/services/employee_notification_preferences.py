from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EmployeeNotificationPreference, TelegramNotificationAccount
from .automatic_messages import (
    get_notification_bot_basic_definition,
    serialize_notification_bot_basic_template,
    validate_notification_bot_basic_template,
)
from .telegram_employee_binding import REQUEST_STATUS_APPROVED

PERSONAL_NOTIFICATION_TYPES = ("new_assignment", "new_message")


def _normalize_body(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.strip() else None


def _normalize_copied_base_template(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def get_employee_notification_preference(
    db: Session,
    *,
    user_id: str,
) -> EmployeeNotificationPreference | None:
    return db.execute(
        select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == user_id)
    ).scalar_one_or_none()


def get_or_create_employee_notification_preference(
    db: Session,
    *,
    user_id: str,
) -> EmployeeNotificationPreference:
    row = get_employee_notification_preference(db, user_id=user_id)
    if row is not None:
        return row
    now = datetime.utcnow()
    row = EmployeeNotificationPreference(
        id=str(uuid.uuid4()),
        user_id=user_id,
        use_base_new_assignment_settings=True,
        use_base_new_message_settings=True,
        receive_transfer_to_agent=True,
        receive_transfer_from_agent=True,
        receive_new_message=True,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _base_template(db: Session, notification_type: str) -> dict[str, Any]:
    definition = get_notification_bot_basic_definition(notification_type)
    if definition is None:
        raise KeyError(notification_type)
    return serialize_notification_bot_basic_template(db, definition, tag_context="personal")


def _has_personal_template(row: EmployeeNotificationPreference, notification_type: str) -> bool:
    if notification_type == "new_assignment":
        return row.personal_new_assignment_template is not None
    if notification_type == "new_message":
        return row.personal_new_message_template is not None
    raise KeyError(notification_type)


def _copy_base_to_personal(db: Session, row: EmployeeNotificationPreference, notification_type: str) -> None:
    base = _base_template(db, notification_type)
    if notification_type == "new_assignment":
        row.personal_new_assignment_template = _normalize_copied_base_template(base["template"])
        row.personal_new_assignment_html_enabled = bool(base["html_enabled"])
        row.personal_new_assignment_disable_link_preview = bool(base["disable_link_preview"])
        row.personal_new_assignment_send_silently = bool(base["send_silently"])
        return
    if notification_type == "new_message":
        row.personal_new_message_template = _normalize_copied_base_template(base["template"])
        row.personal_new_message_html_enabled = bool(base["html_enabled"])
        row.personal_new_message_disable_link_preview = bool(base["disable_link_preview"])
        row.personal_new_message_send_silently = bool(base["send_silently"])
        return
    raise KeyError(notification_type)


def _get_use_base_settings(row: EmployeeNotificationPreference | None, notification_type: str) -> bool:
    if row is None:
        return True
    if notification_type == "new_assignment":
        return bool(row.use_base_new_assignment_settings)
    if notification_type == "new_message":
        return bool(row.use_base_new_message_settings)
    raise KeyError(notification_type)


def _set_use_base_settings(row: EmployeeNotificationPreference, notification_type: str, value: bool) -> None:
    if notification_type == "new_assignment":
        row.use_base_new_assignment_settings = bool(value)
        return
    if notification_type == "new_message":
        row.use_base_new_message_settings = bool(value)
        return
    raise KeyError(notification_type)


def _personal_template(row: EmployeeNotificationPreference, notification_type: str) -> dict[str, Any]:
    if notification_type == "new_assignment":
        return {
            "template": row.personal_new_assignment_template or "",
            "html_enabled": bool(row.personal_new_assignment_html_enabled),
            "disable_link_preview": bool(row.personal_new_assignment_disable_link_preview),
            "send_silently": bool(row.personal_new_assignment_send_silently),
        }
    if notification_type == "new_message":
        return {
            "template": row.personal_new_message_template or "",
            "html_enabled": bool(row.personal_new_message_html_enabled),
            "disable_link_preview": bool(row.personal_new_message_disable_link_preview),
            "send_silently": bool(row.personal_new_message_send_silently),
        }
    raise KeyError(notification_type)


def _set_personal_template(
    row: EmployeeNotificationPreference,
    notification_type: str,
    *,
    template: str | None,
    html_enabled: bool,
    disable_link_preview: bool,
    send_silently: bool,
) -> None:
    body = validate_notification_bot_basic_template(enabled=True, template=template)
    if notification_type == "new_assignment":
        row.personal_new_assignment_template = body
        row.personal_new_assignment_html_enabled = bool(html_enabled)
        row.personal_new_assignment_disable_link_preview = bool(disable_link_preview)
        row.personal_new_assignment_send_silently = bool(send_silently)
        return
    if notification_type == "new_message":
        row.personal_new_message_template = body
        row.personal_new_message_html_enabled = bool(html_enabled)
        row.personal_new_message_disable_link_preview = bool(disable_link_preview)
        row.personal_new_message_send_silently = bool(send_silently)
        return
    raise KeyError(notification_type)


def _binding_active(db: Session, user_id: str) -> bool:
    account = db.execute(
        select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id == user_id)
    ).scalar_one_or_none()
    return bool(
        account is not None
        and account.request_status == REQUEST_STATUS_APPROVED
        and account.bound_user_id == user_id
    )


def serialize_employee_notification_preferences(
    db: Session,
    *,
    user_id: str,
) -> dict[str, Any]:
    row = get_employee_notification_preference(db, user_id=user_id)
    items: list[dict[str, Any]] = []
    for notification_type in PERSONAL_NOTIFICATION_TYPES:
        base = _base_template(db, notification_type)
        source = base
        use_base_settings = _get_use_base_settings(row, notification_type)
        if not use_base_settings and row is not None:
            source = {**base, **_personal_template(row, notification_type)}
        items.append(
            {
                "type": notification_type,
                "key": base["key"],
                "title": "Новая заявка / задача" if notification_type == "new_assignment" else base["title"],
                "description": base["description"],
                "template": source["template"],
                "default_template": base["default_template"],
                "html_enabled": bool(source["html_enabled"]),
                "disable_link_preview": bool(source["disable_link_preview"]),
                "send_silently": bool(source["send_silently"]),
                "tags": list(base["tags"]),
                "use_base_settings": use_base_settings,
                "readonly": use_base_settings,
            }
        )
    return {
        "items": items,
        "delivery": {
            "new_assignment": True,
            "transfer_to_agent": True if row is None else bool(row.receive_transfer_to_agent),
            "transfer_from_agent": True if row is None else bool(row.receive_transfer_from_agent),
            "new_message": True if row is None else bool(row.receive_new_message),
        },
        "telegram_binding": {
            "active": _binding_active(db, user_id),
        },
    }


def update_employee_notification_preferences(
    db: Session,
    *,
    user_id: str,
    items: list[dict[str, Any]] | None,
    receive_transfer_to_agent: bool,
    receive_transfer_from_agent: bool,
    receive_new_message: bool,
) -> EmployeeNotificationPreference:
    row = get_or_create_employee_notification_preference(db, user_id=user_id)
    row.receive_transfer_to_agent = bool(receive_transfer_to_agent)
    row.receive_transfer_from_agent = bool(receive_transfer_from_agent)
    row.receive_new_message = bool(receive_new_message)

    by_type = {str(item.get("type") or ""): item for item in (items or [])}
    unknown = sorted(set(by_type) - set(PERSONAL_NOTIFICATION_TYPES))
    if unknown:
        raise ValueError("Неизвестный тип уведомления")
    missing = [notification_type for notification_type in PERSONAL_NOTIFICATION_TYPES if notification_type not in by_type]
    if missing:
        raise ValueError("Передайте настройки для двух вкладок")
    for notification_type in PERSONAL_NOTIFICATION_TYPES:
        item = by_type[notification_type]
        use_base_settings = item.get("use_base_settings") is not False
        was_use_base_settings = _get_use_base_settings(row, notification_type)
        had_personal_template = _has_personal_template(row, notification_type)
        if not use_base_settings and not had_personal_template:
            _copy_base_to_personal(db, row, notification_type)
        _set_use_base_settings(row, notification_type, use_base_settings)
        should_update_template = not use_base_settings and not was_use_base_settings
        if should_update_template:
            _set_personal_template(
                row,
                notification_type,
                template=item.get("template"),
                html_enabled=item.get("html_enabled") is not False,
                disable_link_preview=bool(item.get("disable_link_preview")),
                send_silently=bool(item.get("send_silently")),
            )
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def restore_employee_notification_preferences_from_base(
    db: Session,
    *,
    user_id: str,
    notification_type: str,
) -> EmployeeNotificationPreference:
    if notification_type not in PERSONAL_NOTIFICATION_TYPES:
        raise KeyError(notification_type)
    row = get_or_create_employee_notification_preference(db, user_id=user_id)
    _copy_base_to_personal(db, row, notification_type)
    _set_use_base_settings(row, notification_type, False)
    row.updated_at = datetime.utcnow()
    db.flush()
    return row
