from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import settings


DEAL_LINK_RE = re.compile(r"/crm/deal/show/(\d+)/?")


class BitrixLookupError(Exception):
    pass


class BitrixConfigError(BitrixLookupError):
    pass


class BitrixApiError(BitrixLookupError):
    pass


@dataclass
class BitrixDealPhoneLookupResult:
    deal_id: str
    contact_id: str | None = None
    lead_id: str | None = None
    client_name: str | None = None
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract_deal_id(text: str) -> str | None:
    value = (text or "").strip()
    if value.isdigit():
        return value
    match = DEAL_LINK_RE.search(value)
    if match:
        return match.group(1)
    return None


def validate_deal_id(deal_id: str) -> bool:
    return bool(re.fullmatch(r"\d{5,7}", deal_id or ""))


def _webhook_base() -> str:
    base = (settings.bitrix_webhook_base or "").strip()
    if not base:
        raise BitrixConfigError("BITRIX_WEBHOOK_BASE не задан")
    return base if base.endswith("/") else f"{base}/"


def bitrix_get(method: str, params: dict[str, Any]) -> dict[str, Any]:
    base = _webhook_base()
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{base}{method}"
    if query:
        url = f"{url}?{query}"
    request = Request(url, headers={"accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise BitrixApiError(f"HTTP {exc.code} при вызове {method}") from exc
    except URLError as exc:
        raise BitrixApiError(f"Ошибка сети при вызове {method}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise BitrixApiError(f"Таймаут при вызове {method}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BitrixApiError(f"Bitrix вернул не JSON для {method}") from exc

    if payload.get("error"):
        description = payload.get("error_description") or payload.get("error")
        raise BitrixApiError(f"{method}: {description}")
    return payload


def _first_multi_value(payload: dict[str, Any], field_name: str) -> str | None:
    values = payload.get(field_name)
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        return None
    value = first.get("VALUE")
    return str(value).strip() if value else None


def _full_name(payload: dict[str, Any]) -> str | None:
    parts = [payload.get("LAST_NAME"), payload.get("NAME")]
    name = " ".join(str(part).strip() for part in parts if part)
    return name or None


def get_deal_phone_lookup(deal_id: str) -> BitrixDealPhoneLookupResult:
    if not validate_deal_id(deal_id):
        raise BitrixLookupError("Некорректный deal_id")

    result = BitrixDealPhoneLookupResult(deal_id=deal_id)
    deal_payload = bitrix_get("crm.deal.get.json", {"ID": deal_id}).get("result") or {}
    if not isinstance(deal_payload, dict):
        raise BitrixApiError("crm.deal.get.json вернул неожиданный result")

    contact_id = deal_payload.get("CONTACT_ID")
    lead_id = deal_payload.get("LEAD_ID")
    result.contact_id = str(contact_id).strip() if contact_id else None
    result.lead_id = str(lead_id).strip() if lead_id else None

    lead_payload: dict[str, Any] | None = None
    if result.lead_id:
        lead_payload_raw = bitrix_get("crm.lead.get.json", {"ID": result.lead_id}).get("result") or {}
        if not isinstance(lead_payload_raw, dict):
            raise BitrixApiError("crm.lead.get.json вернул неожиданный result")
        lead_payload = lead_payload_raw
        lead_phone = _first_multi_value(lead_payload, "PHONE")
        lead_email = _first_multi_value(lead_payload, "EMAIL")
        lead_name = _full_name(lead_payload)
        if lead_phone:
            result.phone = lead_phone
            result.source = "lead"
        if lead_email:
            result.email = lead_email
        if lead_name:
            result.client_name = lead_name

    needs_contact_fallback = bool(
        result.contact_id
        and (
            not result.lead_id
            or not result.phone
            or not result.email
            or not result.client_name
        )
    )
    contact_payload: dict[str, Any] | None = None
    if needs_contact_fallback:
        contact_payload_raw = bitrix_get("crm.contact.get.json", {"ID": result.contact_id}).get("result") or {}
        if isinstance(contact_payload_raw, dict):
            contact_payload = contact_payload_raw
            contact_lead_id = contact_payload.get("LEAD_ID")
            if not result.lead_id and contact_lead_id:
                result.lead_id = str(contact_lead_id).strip()
            contact_phone = _first_multi_value(contact_payload, "PHONE")
            contact_email = _first_multi_value(contact_payload, "EMAIL")
            contact_name = _full_name(contact_payload)
            if not result.phone and contact_phone:
                result.phone = contact_phone
                result.source = "contact"
            if not result.email and contact_email:
                result.email = contact_email
            if not result.client_name and contact_name:
                result.client_name = contact_name
        else:
            result.warnings.append("crm.contact.get.json вернул неожиданный result")

    # contact fallback may provide LEAD_ID when deal had no lead; retry lead as primary source
    if result.lead_id and lead_payload is None:
        lead_payload_raw = bitrix_get("crm.lead.get.json", {"ID": result.lead_id}).get("result") or {}
        if not isinstance(lead_payload_raw, dict):
            raise BitrixApiError("crm.lead.get.json вернул неожиданный result")
        lead_payload = lead_payload_raw
        lead_phone = _first_multi_value(lead_payload, "PHONE")
        lead_email = _first_multi_value(lead_payload, "EMAIL")
        lead_name = _full_name(lead_payload)
        if lead_phone:
            result.phone = lead_phone
            result.source = "lead"
        if lead_email:
            result.email = lead_email
        if lead_name:
            result.client_name = lead_name

    if not result.source and result.phone:
        result.source = "fallback"
    if not result.phone:
        result.errors.append("phone_missing")
    return result
