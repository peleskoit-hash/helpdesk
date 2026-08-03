from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientCardSourceConfig, ClientProfile, Ticket
from . import client_card_sources
from .client_card_sources import ClientCardSourceError, MISSING
from .client_card_derivation import SYSTEM_PROFILE_FIELDS, apply_client_card_profile_local_save_rules


CALL_CENTER_EMPLOYEE_SOURCE_KEY = "call_center_employee_by_internal_number"
CALL_CENTER_DESTINATION_VALUE = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"

_LOGGER = logging.getLogger(__name__)

_MAPPING_TARGETS = {
    "contact_name": ("call_center_employee_first_name", "contact_name"),
    "last_name": ("call_center_employee_last_name", "last_name"),
    "bitrix_id": ("call_center_employee_id", "bitrix_id"),
    "external_user_id": ("call_center_employee_id", "bitrix_id"),
    "bitrix_login": ("call_center_employee_bitrix_login", "bitrix_login"),
}


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _is_call_center_profile(profile: ClientProfile | None) -> bool:
    if profile is None:
        return False
    destination = _normalized_text(profile.destination)
    return bool(destination and destination.casefold() == CALL_CENTER_DESTINATION_VALUE.casefold())


def _ticket_has_snapshot(ticket: Ticket) -> bool:
    return any(
        bool(_normalized_text(getattr(ticket, field_name, None)))
        for field_name in [
            "call_center_internal_number",
            "call_center_employee_first_name",
            "call_center_employee_last_name",
            "call_center_employee_id",
            "call_center_employee_bitrix_login",
        ]
    ) or ticket.call_center_employee_lookup_at is not None or bool(ticket.call_center_profile_snapshot_json)


def _snapshot_context(profile: ClientProfile) -> dict[str, Any]:
    context = {
        "contact_name": profile.contact_name,
        "last_name": profile.last_name,
        "destination": profile.destination,
        "department": profile.department,
        "group_name": profile.group_name,
        "internal_number": profile.internal_number,
        "external_user_id": profile.external_user_id,
        "bitrix_login": profile.bitrix_login,
        "bitrix_id": profile.bitrix_id,
    }
    context.update(profile.custom_fields or {})
    return context


def _mapped_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value).strip() or None
    return None


def _profile_snapshot_payload(profile: ClientProfile, changed_keys: set[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    custom_fields: dict[str, Any] = {}
    current_custom_fields = profile.custom_fields or {}
    for key in changed_keys:
        if key in SYSTEM_PROFILE_FIELDS and key != "bitrix_password":
            value = getattr(profile, key, None)
            if value is not None:
                fields[key] = value
        elif key in current_custom_fields:
            value = current_custom_fields.get(key)
            if value is not None:
                custom_fields[key] = value
    snapshot = dict(fields)
    if custom_fields:
        snapshot["custom_fields"] = custom_fields
    return snapshot


def apply_call_center_employee_snapshot_on_ticket_create(db: Session, ticket: Ticket) -> bool:
    if _ticket_has_snapshot(ticket):
        return False

    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    if not _is_call_center_profile(profile):
        return False

    internal_number = _normalized_text(profile.internal_number)
    if not internal_number:
        return False

    ticket.call_center_employee_lookup_at = datetime.utcnow()
    ticket.call_center_internal_number = internal_number

    source = db.execute(
        select(ClientCardSourceConfig).where(
            ClientCardSourceConfig.technical_key == CALL_CENTER_EMPLOYEE_SOURCE_KEY,
            ClientCardSourceConfig.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if source is None:
        _LOGGER.info("Call-center employee lookup source is not configured for ticket %s", ticket.id)
        return False

    context = _snapshot_context(profile)

    try:
        result = client_card_sources.execute_source(source, context)
    except ClientCardSourceError as exc:
        _LOGGER.warning(
            "Call-center employee lookup failed for ticket %s via source %s: %s",
            ticket.id,
            source.technical_key or source.id,
            type(exc).__name__,
        )
        return False

    try:
        applied = False
        profile_delta: dict[str, str] = {}
        for item in source.mapping_json or []:
            target_key = item.get("target_key")
            target_fields = _MAPPING_TARGETS.get(target_key)
            if not target_fields:
                continue
            value = client_card_sources.read_response_path(result.payload, item.get("response_path"))
            if value is MISSING:
                continue
            text = _mapped_scalar(value)
            if text is None:
                continue
            ticket_field, profile_field = target_fields
            setattr(ticket, ticket_field, text)
            setattr(profile, profile_field, text)
            profile_delta[profile_field] = text
            applied = True
        if applied:
            result = apply_client_card_profile_local_save_rules(
                db,
                profile,
                profile_delta,
                dict(profile.custom_fields or {}),
            )
            ticket.call_center_profile_snapshot_json = _profile_snapshot_payload(profile, result.changed_keys)
    except ClientCardSourceError as exc:
        _LOGGER.warning(
            "Call-center employee lookup mapping failed for ticket %s via source %s: %s",
            ticket.id,
            source.technical_key or source.id,
            type(exc).__name__,
        )
        return False

    if not applied:
        _LOGGER.info(
            "Call-center employee lookup returned no persistable employee fields for ticket %s via source %s",
            ticket.id,
            source.technical_key or source.id,
        )
    return applied
