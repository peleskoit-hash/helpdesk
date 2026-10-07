from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClientProfile, Ticket
from . import call_center_employee_resolver
from .client_card_derivation import SYSTEM_PROFILE_FIELDS, apply_client_card_profile_local_save_rules


CALL_CENTER_EMPLOYEE_SOURCE_KEY = "call_center_employee_by_internal_number"
CALL_CENTER_DESTINATION_VALUE = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440"
_LOGGER = logging.getLogger(__name__)


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


def _empty_resolver_profile_delta(internal_number: str) -> dict[str, Any]:
    return {
        "contact_name": "",
        "last_name": "",
        "bitrix_id": "",
        "external_user_id": "",
        "bitrix_login": "",
        "internal_number": internal_number,
        "custom_fields": {"custom_fio": ""},
    }


def _build_ticket_profile_delta(
    *,
    internal_number: str,
    first_name: str | None,
    last_name: str | None,
    bitrix_id: str | None,
    bitrix_login: str | None,
    custom_fio: str | None = None,
) -> dict[str, Any]:
    delta = {
        "contact_name": first_name or "",
        "last_name": last_name or "",
        "bitrix_id": bitrix_id or "",
        "external_user_id": bitrix_id or "",
        "bitrix_login": bitrix_login or "",
        "internal_number": internal_number,
        "custom_fields": {"custom_fio": custom_fio or ""},
    }
    return delta


def start_call_center_employee_refresh_if_needed(db: Session, client_id: str) -> bool:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    if not _is_call_center_profile(profile):
        return False
    internal_number = _normalized_text(profile.internal_number)
    if not internal_number:
        return False
    try:
        return call_center_employee_resolver.start_refresh_if_needed(internal_number)
    except Exception as exc:
        _LOGGER.warning(
            "Call-center employee early refresh failed safely for client %s: %s",
            client_id,
            type(exc).__name__,
        )
        return False


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

    try:
        result = call_center_employee_resolver.resolve(db, internal_number)
    except Exception as exc:
        _LOGGER.warning(
            "Call-center employee resolver failed safely for ticket %s: %s",
            ticket.id,
            type(exc).__name__,
        )
        ticket.call_center_profile_snapshot_json = {
            "internal_number": internal_number,
            "bitrix_id": "",
            "first_name": "",
            "last_name": "",
            "full_name": "",
            "department_ids": [],
            "resolution_status": call_center_employee_resolver.STATUS_TECHNICAL_ERROR,
            "used_stale_fallback": False,
            "bitrix_login": None,
            "error_code": type(exc).__name__,
            "profile_delta": _empty_resolver_profile_delta(internal_number),
        }
        return False
    payload = result.employee_payload()
    ticket.call_center_profile_snapshot_json = {
        **payload,
        "profile_delta": _empty_resolver_profile_delta(internal_number),
    }
    if not result.found:
        _LOGGER.info(
            "Call-center employee resolver returned status %s for ticket %s",
            result.status,
            ticket.id,
        )
        return False

    profile_delta: dict[str, str] = {}
    first_name = _mapped_scalar(payload.get("first_name"))
    last_name = _mapped_scalar(payload.get("last_name"))
    bitrix_id = _mapped_scalar(payload.get("bitrix_id"))
    bitrix_login = _mapped_scalar(payload.get("bitrix_login"))
    if first_name:
        ticket.call_center_employee_first_name = first_name
        profile.contact_name = first_name
        profile_delta["contact_name"] = first_name
    if last_name:
        ticket.call_center_employee_last_name = last_name
        profile.last_name = last_name
        profile_delta["last_name"] = last_name
    if bitrix_id:
        ticket.call_center_employee_id = bitrix_id
        profile.bitrix_id = bitrix_id
        profile.external_user_id = bitrix_id
        profile_delta["bitrix_id"] = bitrix_id
        profile_delta["external_user_id"] = bitrix_id
    if bitrix_login:
        ticket.call_center_employee_bitrix_login = bitrix_login
        profile.bitrix_login = bitrix_login
        profile_delta["bitrix_login"] = bitrix_login

    custom_fio = ""
    if profile_delta:
        derivation_result = apply_client_card_profile_local_save_rules(
            db,
            profile,
            profile_delta,
            dict(profile.custom_fields or {}),
        )
        derived_payload = _profile_snapshot_payload(profile, derivation_result.changed_keys)
        custom_fio = str((derived_payload.get("custom_fields") or {}).get("custom_fio") or "").strip()
        ticket.call_center_profile_snapshot_json = {
            **payload,
            **derived_payload,
            "profile_delta": _build_ticket_profile_delta(
                internal_number=internal_number,
                first_name=first_name,
                last_name=last_name,
                bitrix_id=bitrix_id,
                bitrix_login=bitrix_login,
                custom_fio=custom_fio,
            ),
        }
        return True

    ticket.call_center_profile_snapshot_json = {
        **payload,
        "profile_delta": _build_ticket_profile_delta(
            internal_number=internal_number,
            first_name=first_name,
            last_name=last_name,
            bitrix_id=bitrix_id,
            bitrix_login=bitrix_login,
            custom_fio=custom_fio,
        ),
    }
    _LOGGER.info("Call-center employee resolver found no persistable employee fields for ticket %s", ticket.id)
    return False
