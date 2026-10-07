from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import (
    Client,
    ClientProfile,
    Company,
    IntegrationLog,
    InternalTaskOccurrence,
    PendingTaskIntent,
    StatsCompletion,
    StatsWorkInterval,
    TelegramContact,
    Ticket,
    TicketAttachment,
    TicketAttachmentPreload,
    TicketFeedback,
    TicketFeedbackInput,
    TicketMessage,
    TicketReview,
    TicketReviewCycle,
    TicketView,
)
from .client_card_derivation import apply_client_card_profile_local_save_rules
from .encryption import decrypt_secret, encrypt_secret
from .internal_tasks import INTERNAL_TASK_CLIENT_UUID_NAMESPACE

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - exercised by runtime dependency checks.
    load_workbook = None


REQUIRED_COLUMNS = {
    "helpdeskeddy_id",
    "matched_bitrix_id",
    "matched_bitrix_login",
    "lastname",
    "Верификация пройдена",
    "Anydesk",
    "Руководитель",
    "Chat_id сотрудника",
    "Имя сотрудника",
    "{'ru': 'Отдел'}",
    "Тип сотрудника",
    "Внутренний номер",
    "ID пользователя",
    "Логин Bitrix",
    "Пароль Bitrix",
}

PRESERVED_DEV_CHAT_IDS = {"453920478", "346731919", "5098350035", "534385940"}
LEGACY_EXCLUDED_USERS = {
    ("287", "814689011"),
    ("11468", "606742675"),
}
CURRENT_LEADER_CHAT_IDS = {
    "969283110",
    "400438608",
    "814968925",
    "536426070",
    "1476152160",
}
CURRENT_HR_CHAT_OVERRIDES: dict[str, dict[str, str | None]] = {
    "1197577312": {},
    "7545713567": {},
    "922502736": {},
    "1085928274": {},
    "799332654": {},
}

OFFICE_EMPLOYEE = ("Офисный", "Сотрудник")
REMOTE_EMPLOYEE = ("Удалённый", "Сотрудник")
OPTIONAL_STRING_FIELD_LIMITS = {
    "anydesk": 100,
}

DEPARTMENT_MAP: dict[str, dict[str, str | None]] = {
    "Отдел продаж; Первый департамент; Группа Власова": {"destination": "Отдел Продаж", "sales_group": "группа Власова"},
    "Отдел продаж; Первый департамент; Группа Сайдаралиева": {"destination": "Отдел Продаж", "sales_group": "группа Сайдаралиева"},
    "Отдел продаж; Первый департамент; Группа Скрылевой": {"destination": "Отдел Продаж", "sales_group": "группа Скрылёвой"},
    "Отдел продаж; Первый департамент; Группа Макаренко": {"destination": "Отдел Продаж", "sales_group": "группа Макаренко"},
    "Отдел продаж; Второй отдел; Группа Шахалиевой": {"destination": "Отдел Продаж", "sales_group": "группа Шахалиевой"},
    "Отдел продаж; Второй отдел; Группа Кочуры": {"destination": "Отдел Продаж", "sales_group": "группа Кочуры"},
    "Отдел продаж; Третий отдел": {"destination": "Отдел Продаж", "sales_group": "группа Калинина"},
    "Колл центр": {"destination": "Колл Центр"},
    "Стажировка ОП; Стажировка Власова": {"destination": "Песок", "sand_owner": "песок Власова"},
    "С" + "тажировка ОП; " + "С" + "тажировка " + "С" + "крылевой": {"destination": "Песок", "sand_owner": "песок " + "С" + "крылёвой"},
    "С" + "тажировка ОП; " + "С" + "тажировка " + "С" + "айдаралиева": {"destination": "Песок", "sand_owner": "песок " + "С" + "айдаралиева"},
    "Стажировка ОП; Стажировка Макаренко": {"destination": "Песок", "sand_owner": "песок Макаренко"},
    "Стажировка ОП; Стажировка Третий отд": {"destination": "Песок", "sand_owner": "песок Калинина"},
    "Стажировка ОП; Начало Стажировки": {"destination": "Песок"},
}

SPECIAL_OVERRIDES = {
    ("11701", "1683865341"): {"sales_group": "группа Скрылёвой", "location_type": "Офисный"},
    ("12196", "740163370"): {"sales_group": "группа Макаренко", "location_type": "Удалённый"},
    ("12524", "1837201620"): {"sales_group": "группа Макаренко", "location_type": "Удалённый"},
    ("12075", "5255910625"): {"sales_group": "группа Макаренко", "location_type": "Удалённый"},
}


class LegacyClientImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyClientRow:
    row_number: int
    chat_id: str
    first_name: str | None
    last_name: str | None
    is_verified: bool
    department_raw: str | None
    is_leader: bool
    location_type: str | None
    employee_type: str | None
    anydesk: str | None
    internal_number: str | None
    bitrix_id: str | None
    bitrix_login: str | None
    bitrix_password: str | None

    @property
    def display_name(self) -> str:
        name = " ".join(part for part in (self.first_name, self.last_name) if part).strip()
        return name or f"Telegram {self.chat_id}"

    @property
    def is_excluded(self) -> bool:
        return (self.bitrix_id or "", self.chat_id) in LEGACY_EXCLUDED_USERS


def _cell_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return str(value.to_integral())
        return str(value).strip()
    text = str(value).strip()
    return text or None


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "да"}


def _employee_types(raw: str | None) -> tuple[str | None, str | None]:
    value = (raw or "").strip().lower()
    if not value:
        return None, None
    if value == "удаленный сотрудник":
        return REMOTE_EMPLOYEE
    if value == "штатный сотрудник":
        return OFFICE_EMPLOYEE
    return None, None


def _read_sheet_rows(path: str | Path) -> list[dict[str, Any]]:
    if load_workbook is None:
        raise LegacyClientImportError("openpyxl is required to read .xlsx files.")
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = [_cell_to_string(cell) for cell in next(rows, ())]
        missing = sorted(REQUIRED_COLUMNS - {name for name in header if name})
        if missing:
            raise LegacyClientImportError(f"Missing required columns: {', '.join(missing)}")
        indexed = {name: index for index, name in enumerate(header) if name}
        result: list[dict[str, Any]] = []
        for row_number, row in enumerate(rows, start=2):
            if not any(_cell_to_string(cell) for cell in row):
                continue
            result.append(
                {
                    "row_number": row_number,
                    **{name: _cell_to_string(row[index] if index < len(row) else None) for name, index in indexed.items()},
                }
            )
        return result
    finally:
        workbook.close()


def read_legacy_client_rows(path: str | Path) -> list[LegacyClientRow]:
    raw_rows = _read_sheet_rows(path)
    parsed: list[LegacyClientRow] = []
    for raw in raw_rows:
        row_number = int(raw["row_number"])
        chat_id = _cell_to_string(raw.get("Chat_id сотрудника"))
        if not chat_id:
            raise LegacyClientImportError(f"Row {row_number}: Chat_id сотрудника is required.")
        location_type, employee_type = _employee_types(raw.get("Тип сотрудника"))
        parsed.append(
            LegacyClientRow(
                row_number=row_number,
                chat_id=chat_id,
                first_name=_cell_to_string(raw.get("Имя сотрудника")),
                last_name=_cell_to_string(raw.get("lastname")),
                is_verified=_flag(_cell_to_string(raw.get("Верификация пройдена"))),
                department_raw=_cell_to_string(raw.get("{'ru': 'Отдел'}")),
                is_leader=_flag(_cell_to_string(raw.get("Руководитель"))),
                location_type=location_type,
                employee_type=employee_type,
                anydesk=_cell_to_string(raw.get("Anydesk")),
                internal_number=_cell_to_string(raw.get("Внутренний номер")),
                bitrix_id=_cell_to_string(raw.get("matched_bitrix_id")),
                bitrix_login=_cell_to_string(raw.get("matched_bitrix_login")),
                bitrix_password=_cell_to_string(raw.get("Пароль Bitrix")),
            )
        )
    return parsed


def _row_warnings(row: LegacyClientRow) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for field, limit in OPTIONAL_STRING_FIELD_LIMITS.items():
        value = getattr(row, field)
        if value is not None and len(value) > limit:
            warnings.append(
                {
                    "type": "field_omitted",
                    "row": row.row_number,
                    "chat_id": row.chat_id,
                    "field": field,
                    "reason": f"value_length_exceeds_{limit}",
                    "length": len(value),
                    "max_length": limit,
                }
            )
    return warnings


def _row_profile_payload(row: LegacyClientRow, *, include_blank_ordinary: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "is_verified": row.is_verified,
    }
    ordinary = {
        "contact_name": row.first_name,
        "last_name": row.last_name,
        "employee_type": row.employee_type,
        "location_type": row.location_type,
        "anydesk": row.anydesk if row.anydesk is None or len(row.anydesk) <= OPTIONAL_STRING_FIELD_LIMITS["anydesk"] else None,
        "internal_number": row.internal_number,
        "external_user_id": row.bitrix_id,
        "bitrix_login": row.bitrix_login,
        "bitrix_id": row.bitrix_id,
    }
    payload.update({key: value for key, value in ordinary.items() if include_blank_ordinary or value is not None})
    if row.chat_id in CURRENT_LEADER_CHAT_IDS:
        payload.update({"destination": "Руководство", "sales_group": None, "sand_owner": None})
    elif row.chat_id in CURRENT_HR_CHAT_OVERRIDES:
        payload.update({"destination": "HR", "sales_group": None, "sand_owner": None})
        payload.update({key: value for key, value in CURRENT_HR_CHAT_OVERRIDES[row.chat_id].items() if value is not None})
    elif row.is_leader:
        payload.update({"destination": "Руководство", "sales_group": None, "sand_owner": None})
    elif (row.bitrix_id or "", row.chat_id) in SPECIAL_OVERRIDES:
        payload.update({"destination": "Отдел Продаж", "sand_owner": None})
        payload.update(SPECIAL_OVERRIDES[(row.bitrix_id or "", row.chat_id)])
    else:
        mapping = DEPARTMENT_MAP.get(row.department_raw or "")
        if mapping is None:
            raise LegacyClientImportError(f"Row {row.row_number}: unknown department {row.department_raw!r}.")
        payload.update(
            {
                "destination": mapping.get("destination"),
                "sales_group": mapping.get("sales_group"),
                "sand_owner": mapping.get("sand_owner"),
            }
        )
    return payload


def _validate_rows(rows: list[LegacyClientRow]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen_chat_ids: dict[str, int] = {}
    for row in rows:
        if row.chat_id in seen_chat_ids:
            errors.append(
                {
                    "row": row.row_number,
                    "chat_id": row.chat_id,
                    "code": "duplicate_chat_id",
                    "message": f"Duplicate Chat_id сотрудника {row.chat_id!r}; first seen in row {seen_chat_ids[row.chat_id]}.",
                }
            )
        else:
            seen_chat_ids[row.chat_id] = row.row_number
        if row.is_excluded:
            continue
        has_role_override = row.chat_id in CURRENT_LEADER_CHAT_IDS or row.chat_id in CURRENT_HR_CHAT_OVERRIDES
        has_special_override = (row.bitrix_id or "", row.chat_id) in SPECIAL_OVERRIDES
        if not has_role_override and not row.is_leader and not has_special_override and (row.department_raw or "") not in DEPARTMENT_MAP:
            errors.append(
                {
                    "row": row.row_number,
                    "chat_id": row.chat_id,
                    "code": "unknown_department",
                    "message": f"Unknown department {row.department_raw!r}.",
                }
            )
    return errors


def _default_company_id(db: Session) -> str:
    company_id = db.execute(select(Company.id).order_by(Company.created_at.asc(), Company.id.asc()).limit(1)).scalar_one_or_none()
    if not company_id:
        raise LegacyClientImportError("No company exists; seed companies before importing clients.")
    return company_id


def _profile_by_client_id(db: Session, client_id: str) -> ClientProfile | None:
    return db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()


def _existing_plain_password(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    return decrypt_secret(encrypted)


def _effective_display_name(profile: ClientProfile, row: LegacyClientRow) -> str:
    name = " ".join(part for part in (profile.contact_name, profile.last_name) if part).strip()
    return name or f"Telegram {row.chat_id}"


PROFILE_CHANGE_FIELDS = [
    "contact_name",
    "last_name",
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
    "bitrix_id",
]


def _changed_fields(before: dict[str, Any], after: ClientProfile) -> list[str]:
    return [field for field in PROFILE_CHANGE_FIELDS if before.get(field) != getattr(after, field)]


def _apply_row(db: Session, row: LegacyClientRow, company_id: str) -> dict[str, Any]:
    contact = db.execute(select(TelegramContact).where(TelegramContact.external_chat_id == row.chat_id)).scalar_one_or_none()
    created_client = False
    created_contact = False
    created_profile = False
    now = datetime.utcnow()
    if contact is None:
        client = Client(id=str(uuid.uuid4()), company_id=company_id, display_name=row.display_name)
        db.add(client)
        db.flush()
        contact = TelegramContact(
            id=str(uuid.uuid4()),
            company_id=company_id,
            client_id=client.id,
            external_user_id=row.chat_id,
            external_chat_id=row.chat_id,
            external_display_name=row.display_name,
            is_blocked=False,
            created_at=now,
            updated_at=now,
        )
        db.add(contact)
        created_client = True
        created_contact = True
    else:
        client = db.get(Client, contact.client_id)
        if client is None:
            raise LegacyClientImportError(f"Telegram contact {row.chat_id!r} references a missing client.")
    profile = _profile_by_client_id(db, client.id)
    if profile is None:
        profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, custom_fields={}, created_at=now, updated_at=now)
        db.add(profile)
        db.flush()
        created_profile = True
    payload = _row_profile_payload(row, include_blank_ordinary=created_profile)
    before_client_name = client.display_name
    before_contact_name = contact.external_display_name
    before_profile = {field: getattr(profile, field, None) for field in PROFILE_CHANGE_FIELDS}
    before_password = _existing_plain_password(profile.bitrix_password)

    apply_client_card_profile_local_save_rules(db, profile, payload, dict(profile.custom_fields or {}))
    effective_display_name = _effective_display_name(profile, row)
    if client.display_name != effective_display_name:
        client.display_name = effective_display_name
    if contact.external_display_name != effective_display_name:
        contact.external_display_name = effective_display_name
        contact.updated_at = now
    password_changed = False
    if row.bitrix_password and before_password != row.bitrix_password:
        profile.bitrix_password = encrypt_secret(row.bitrix_password)
        profile.updated_at = datetime.utcnow()
        password_changed = True
    profile_changes = _changed_fields(before_profile, profile)
    if password_changed:
        profile_changes.append("bitrix_password")
    changed = bool(
        created_client
        or created_contact
        or created_profile
        or before_client_name != client.display_name
        or before_contact_name != contact.external_display_name
        or profile_changes
    )
    return {
        "row": row.row_number,
        "chat_id": row.chat_id,
        "client_id": client.id,
        "status": "created" if created_client else ("updated" if changed else "unchanged"),
        "created": {"client": created_client, "contact": created_contact, "profile": created_profile},
        "changed_fields": sorted(set(profile_changes + (["display_name"] if before_client_name != client.display_name else []))),
    }


def _duplicate_warnings(rows: list[LegacyClientRow]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for attr, label in (("bitrix_id", "matched_bitrix_id"), ("bitrix_login", "matched_bitrix_login")):
        seen: dict[str, list[str]] = {}
        for row in rows:
            value = getattr(row, attr)
            if value:
                seen.setdefault(value, []).append(row.chat_id)
        for value, chat_ids in sorted(seen.items()):
            if len(set(chat_ids)) > 1:
                warnings.append({"type": f"duplicate_{label}", "value": value, "chat_ids": sorted(set(chat_ids))})
    return warnings


def _import_warnings(rows: list[LegacyClientRow]) -> list[dict[str, Any]]:
    warnings = _duplicate_warnings(rows)
    for row in rows:
        if not row.is_excluded:
            warnings.extend(_row_warnings(row))
    return warnings


def import_legacy_clients(db: Session, rows: list[LegacyClientRow], *, apply: bool = False) -> dict[str, Any]:
    errors = _validate_rows(rows)
    skipped = [
        {"row": row.row_number, "chat_id": row.chat_id, "bitrix_id": row.bitrix_id, "status": "skipped_excluded"}
        for row in rows
        if row.is_excluded
    ]
    importable_rows = [row for row in rows if not row.is_excluded and not any(error["row"] == row.row_number for error in errors)]
    if apply and errors:
        db.rollback()
        raise LegacyClientImportError(f"Cannot apply legacy client import: {len(errors)} validation error(s).")
    company_id = _default_company_id(db)
    row_results = [_apply_row(db, row, company_id) for row in importable_rows]
    destination_distribution: dict[str, int] = {}
    sales_group_distribution: dict[str, int] = {}
    sand_owner_distribution: dict[str, int] = {}
    leadership_count = 0
    for row in importable_rows:
        payload = _row_profile_payload(row)
        for key, target in (
            ("destination", destination_distribution),
            ("sales_group", sales_group_distribution),
            ("sand_owner", sand_owner_distribution),
        ):
            value = payload.get(key)
            if value:
                target[value] = target.get(value, 0) + 1
        if payload.get("destination") == "Руководство":
            leadership_count += 1
    summary = {
        "mode": "apply" if apply else "dry-run",
        "source_rows": len(rows),
        "valid_rows": len(importable_rows),
        "skipped_excluded": len(skipped),
        "create": sum(1 for item in row_results if item["status"] == "created"),
        "update": sum(1 for item in row_results if item["status"] == "updated"),
        "noop": sum(1 for item in row_results if item["status"] == "unchanged"),
        "errors": errors,
        "distribution": {
            "destination": dict(sorted(destination_distribution.items())),
            "sales_group": dict(sorted(sales_group_distribution.items())),
            "sand_owner": dict(sorted(sand_owner_distribution.items())),
        },
        "leadership_count": leadership_count,
        "warnings": _import_warnings(rows),
        "items": skipped + row_results,
    }
    db.commit() if apply else db.rollback()
    return summary


def _count(db: Session, model: type, where: Any | None = None) -> int:
    stmt = select(func.count()).select_from(model)
    if where is not None:
        stmt = stmt.where(where)
    return int(db.execute(stmt).scalar_one())


def _cleanup_scope(db: Session) -> dict[str, list[str]]:
    preserved_client_ids = set(
        db.execute(select(TelegramContact.client_id).where(TelegramContact.external_chat_id.in_(PRESERVED_DEV_CHAT_IDS))).scalars().all()
    )
    company_ids = set(db.execute(select(Client.company_id)).scalars().all())
    system_client_ids = {str(uuid.uuid5(INTERNAL_TASK_CLIENT_UUID_NAMESPACE, company_id)) for company_id in company_ids}
    existing_system_client_ids = set(
        db.execute(select(Client.id).where(Client.id.in_(system_client_ids))).scalars().all()
    )
    preserved_client_ids.update(existing_system_client_ids)
    all_client_ids = set(db.execute(select(Client.id)).scalars().all())
    client_ids = sorted(all_client_ids - preserved_client_ids)
    ticket_ids = db.execute(select(Ticket.id).where(Ticket.client_id.in_(client_ids))).scalars().all() if client_ids else []
    message_ids = db.execute(select(TicketMessage.id).where(TicketMessage.ticket_id.in_(ticket_ids))).scalars().all() if ticket_ids else []
    feedback_ids = (
        db.execute(select(TicketFeedback.id).where((TicketFeedback.ticket_id.in_(ticket_ids)) | (TicketFeedback.client_id.in_(client_ids)))).scalars().all()
        if client_ids or ticket_ids
        else []
    )
    feedback_input_ids = (
        db.execute(select(TicketFeedbackInput.id).where((TicketFeedbackInput.ticket_id.in_(ticket_ids)) | (TicketFeedbackInput.client_id.in_(client_ids)))).scalars().all()
        if client_ids or ticket_ids
        else []
    )
    review_ids = (
        db.execute(select(TicketReview.id).where((TicketReview.ticket_id.in_(ticket_ids)) | (TicketReview.client_id.in_(client_ids)))).scalars().all()
        if client_ids or ticket_ids
        else []
    )
    cycle_ids = (
        db.execute(select(TicketReviewCycle.id).where((TicketReviewCycle.ticket_id.in_(ticket_ids)) | (TicketReviewCycle.client_id.in_(client_ids)))).scalars().all()
        if client_ids or ticket_ids
        else []
    )
    return {
        "preserved_client_ids": sorted(preserved_client_ids),
        "preserved_system_client_ids": sorted(existing_system_client_ids),
        "client_ids": client_ids,
        "ticket_ids": list(ticket_ids),
        "message_ids": list(message_ids),
        "feedback_ids": list(feedback_ids),
        "feedback_input_ids": list(feedback_input_ids),
        "review_ids": list(review_ids),
        "cycle_ids": list(cycle_ids),
    }


def _cleanup_counts(db: Session, scope: dict[str, list[str]]) -> dict[str, int]:
    client_ids = scope["client_ids"]
    ticket_ids = scope["ticket_ids"]
    message_ids = scope["message_ids"]
    feedback_ids = scope["feedback_ids"]
    feedback_input_ids = scope["feedback_input_ids"]
    review_ids = scope["review_ids"]
    cycle_ids = scope["cycle_ids"]
    stats_object_ids = list(client_ids) + list(ticket_ids)
    return {
        "clients": len(client_ids),
        "preserved_clients": len(scope["preserved_client_ids"]),
        "preserved_system_clients": len(scope["preserved_system_client_ids"]),
        "telegram_contacts": _count(db, TelegramContact, TelegramContact.client_id.in_(client_ids)) if client_ids else 0,
        "client_profiles": _count(db, ClientProfile, ClientProfile.client_id.in_(client_ids)) if client_ids else 0,
        "tickets": len(ticket_ids),
        "ticket_messages": len(message_ids),
        "ticket_attachments": _count(db, TicketAttachment, (TicketAttachment.ticket_id.in_(ticket_ids)) | (TicketAttachment.message_id.in_(message_ids))) if ticket_ids else 0,
        "ticket_attachment_preloads": _count(db, TicketAttachmentPreload, TicketAttachmentPreload.ticket_id.in_(ticket_ids)) if ticket_ids else 0,
        "ticket_views": _count(db, TicketView, TicketView.ticket_id.in_(ticket_ids)) if ticket_ids else 0,
        "ticket_feedback": len(feedback_ids),
        "ticket_feedback_inputs": len(feedback_input_ids),
        "ticket_reviews": len(review_ids),
        "ticket_review_cycles": len(cycle_ids),
        "integration_logs": _count(db, IntegrationLog, IntegrationLog.task_id.in_(ticket_ids)) if ticket_ids else 0,
        "internal_task_occurrences": _count(db, InternalTaskOccurrence, InternalTaskOccurrence.ticket_id.in_(ticket_ids)) if ticket_ids else 0,
        "pending_task_intents": _count(db, PendingTaskIntent, PendingTaskIntent.client_id.in_(client_ids)) if client_ids else 0,
        "stats_completions": _count(db, StatsCompletion, StatsCompletion.object_id.in_(stats_object_ids)) if stats_object_ids else 0,
        "stats_work_intervals": _count(db, StatsWorkInterval, StatsWorkInterval.object_id.in_(stats_object_ids)) if stats_object_ids else 0,
    }


def _execute_cleanup_deletes(db: Session, scope: dict[str, list[str]]) -> None:
    client_ids = scope["client_ids"]
    ticket_ids = scope["ticket_ids"]
    message_ids = scope["message_ids"]
    feedback_ids = scope["feedback_ids"]
    feedback_input_ids = scope["feedback_input_ids"]
    review_ids = scope["review_ids"]
    cycle_ids = scope["cycle_ids"]
    stats_object_ids = list(client_ids) + list(ticket_ids)
    if feedback_ids:
        db.execute(delete(TicketFeedback).where(TicketFeedback.id.in_(feedback_ids)))
    if feedback_input_ids:
        db.execute(delete(TicketFeedbackInput).where(TicketFeedbackInput.id.in_(feedback_input_ids)))
    if cycle_ids:
        db.execute(update(TicketReviewCycle).where(TicketReviewCycle.id.in_(cycle_ids)).values(review_id=None))
    if review_ids:
        db.execute(update(TicketReview).where(TicketReview.previous_version_id.in_(review_ids)).values(previous_version_id=None))
        db.execute(delete(TicketReview).where(TicketReview.id.in_(review_ids)))
    if cycle_ids:
        db.execute(delete(TicketReviewCycle).where(TicketReviewCycle.id.in_(cycle_ids)))
    if ticket_ids:
        db.execute(delete(TicketAttachment).where((TicketAttachment.ticket_id.in_(ticket_ids)) | (TicketAttachment.message_id.in_(message_ids))))
        db.execute(delete(TicketAttachmentPreload).where(TicketAttachmentPreload.ticket_id.in_(ticket_ids)))
        db.execute(delete(TicketView).where(TicketView.ticket_id.in_(ticket_ids)))
        db.execute(delete(IntegrationLog).where(IntegrationLog.task_id.in_(ticket_ids)))
        db.execute(delete(InternalTaskOccurrence).where(InternalTaskOccurrence.ticket_id.in_(ticket_ids)))
        db.execute(delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids)))
        db.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    if client_ids:
        db.execute(delete(PendingTaskIntent).where(PendingTaskIntent.client_id.in_(client_ids)))
        db.execute(delete(TelegramContact).where(TelegramContact.client_id.in_(client_ids)))
        db.execute(delete(ClientProfile).where(ClientProfile.client_id.in_(client_ids)))
        db.execute(delete(StatsCompletion).where(StatsCompletion.object_id.in_(stats_object_ids)))
        db.execute(delete(StatsWorkInterval).where(StatsWorkInterval.object_id.in_(stats_object_ids)))
        db.execute(delete(Client).where(Client.id.in_(client_ids)))


def cleanup_dev_clients(db: Session, *, apply: bool = False) -> dict[str, Any]:
    if settings.app_env != "dev":
        raise LegacyClientImportError("Dev cleanup is allowed only when APP_ENV=dev.")
    scope = _cleanup_scope(db)
    counts = _cleanup_counts(db, scope)
    if not apply:
        db.rollback()
        return {"mode": "dry-run", "deleted": counts}
    try:
        _execute_cleanup_deletes(db, scope)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return {"mode": "apply", "deleted": counts}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import legacy HelpDeskEddy clients into ES DESK.")
    parser.add_argument("--file", type=Path, help="Path to legacy .xlsx export.")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Defaults to dry-run.")
    parser.add_argument("--cleanup-dev", action="store_true", help="Delete DEV test clients except preserved Telegram chat IDs.")
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.file and not args.cleanup_dev:
        raise SystemExit("--file or --cleanup-dev is required")
    with SessionLocal() as db:
        try:
            if args.cleanup_dev:
                result = cleanup_dev_clients(db, apply=args.apply)
            else:
                rows = read_legacy_client_rows(args.file)
                result = import_legacy_clients(db, rows, apply=args.apply)
        except Exception:
            db.rollback()
            raise
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
