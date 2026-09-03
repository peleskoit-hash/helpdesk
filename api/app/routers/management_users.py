from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import Client, ClientProfile, TelegramContact, Ticket
from ..schemas import BitrixPasswordRevealOut, ClientCardRuntimeConfigResponse, ClientCardSourceRunRequest, ClientProfileOut, ClientProfileUpdate
from ..services.client_card import (
    _client_card_view_config,
    _custom_field_map,
    _normalize_custom_field_value,
    _profile_out,
)
from ..services.client_card_derivation import apply_client_card_profile_save_pipeline, apply_client_card_source_manual_run
from ..services.encryption import SecretEncryptionConfigurationError, SecretEncryptionError, decrypt_secret
from ..services.management_user_reset import (
    ResetManagementUserDataActiveTicketError,
    reset_management_user_data,
)


router = APIRouter(prefix="/api/v1/management/users", tags=["management-users"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser):
    if not is_admin(user):
        _error(403, "admin_only", "\u0414\u043e\u0441\u0442\u0443\u043f \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430")


def _get_or_create_client_profile(db: Session, client_id: str) -> ClientProfile:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    if profile is None:
        profile = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _telegram_contact_for_client(db: Session, client_id: str) -> TelegramContact | None:
    return db.execute(
        select(TelegramContact)
        .where(TelegramContact.client_id == client_id)
        .order_by(TelegramContact.updated_at.desc(), TelegramContact.created_at.desc())
    ).scalar_one_or_none()


def _display_name(client: Client | None, profile: ClientProfile | None, contact: TelegramContact) -> str:
    profile_parts = [profile.last_name if profile else None, profile.contact_name if profile else None]
    profile_name = " ".join(str(part).strip() for part in profile_parts if part and str(part).strip())
    return profile_name or (client.display_name if client else None) or contact.external_display_name or ""


def _row_out(
    contact: TelegramContact,
    client: Client | None,
    profile: ClientProfile | None,
    last_activity_at: datetime | None,
    *,
    is_blocked: bool | None = None,
):
    activity = last_activity_at or contact.updated_at or contact.created_at
    blocked = bool(contact.is_blocked) if is_blocked is None else bool(is_blocked)
    return {
        "telegram_contact_id": contact.id,
        "client_id": contact.client_id,
        "telegram_user_id": contact.external_user_id,
        "telegram_chat_id": contact.external_chat_id,
        "telegram_username": contact.external_username,
        "telegram_display_name": contact.external_display_name,
        "display_name": _display_name(client, profile, contact),
        "contact_name": profile.contact_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "internal_number": profile.internal_number if profile else None,
        "destination": profile.destination if profile else None,
        "group_name": profile.group_name if profile else None,
        "sales_group": profile.sales_group if profile else None,
        "is_verified": bool(profile.is_verified) if profile else False,
        "is_blocked": blocked,
        "blocked": blocked,
        "blocked_supported": True,
        "first_connected_at": contact.created_at,
        "last_activity_at": activity,
    }


def _profile_completeness(profile: ClientProfile | None) -> int:
    if profile is None:
        return 0
    fields = [
        profile.contact_name,
        profile.last_name,
        profile.email,
        profile.phone,
        profile.destination,
        profile.sand_owner,
        profile.sales_group,
        profile.department,
        profile.first_department,
        profile.group_name,
        profile.employee_type,
        profile.location_type,
        profile.anydesk,
        profile.internal_number,
        profile.external_user_id,
        profile.bitrix_login,
        profile.bitrix_password,
        profile.bitrix_id,
    ]
    return sum(1 for value in fields if value is not None and str(value).strip())


def _timestamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


def _dedupe_key(contact: TelegramContact) -> str:
    user_id = (contact.external_user_id or "").strip()
    if user_id:
        return f"company:{contact.company_id}:user:{user_id}"
    chat_id = (contact.external_chat_id or "").strip()
    return f"contact:{contact.id or chat_id}"


def _canonical_sort_key(row) -> tuple:
    contact, _client, profile, last_activity_at = row
    activity = last_activity_at or contact.updated_at or contact.created_at
    return (
        1 if profile is not None and profile.is_verified else 0,
        _profile_completeness(profile),
        _timestamp(activity),
        -_timestamp(contact.created_at),
        str(contact.id or ""),
    )


def _dedupe_rows(rows) -> list:
    grouped: dict[str, object] = {}
    for row in rows:
        contact = row[0]
        key = _dedupe_key(contact)
        current = grouped.get(key)
        if current is None or _canonical_sort_key(row) > _canonical_sort_key(current):
            grouped[key] = row
    return sorted(
        grouped.values(),
        key=lambda row: _timestamp(row[3] or row[0].updated_at or row[0].created_at),
        reverse=True,
    )


def _blocked_state_by_dedupe_key(db: Session, rows) -> dict[str, bool]:
    state: dict[str, bool] = {}
    identities: dict[tuple[str, str], str] = {}
    for row in rows:
        contact = row[0]
        key = _dedupe_key(contact)
        user_id = (contact.external_user_id or "").strip()
        if user_id:
            identities[(contact.company_id, user_id)] = key
            state.setdefault(key, False)
        else:
            state[key] = bool(contact.is_blocked)

    if identities:
        blocked_rows = db.execute(
            select(
                TelegramContact.company_id,
                TelegramContact.external_user_id,
                TelegramContact.is_blocked,
            ).where(
                tuple_(TelegramContact.company_id, TelegramContact.external_user_id).in_(list(identities.keys()))
            )
        ).all()
        for company_id, external_user_id, is_blocked in blocked_rows:
            key = identities.get((company_id, external_user_id))
            if key is not None:
                state[key] = state.get(key, False) or bool(is_blocked)
    return state


def _contacts_for_dedupe_identity(db: Session, contact: TelegramContact) -> list[TelegramContact]:
    user_id = (contact.external_user_id or "").strip()
    if user_id:
        return db.execute(
            select(TelegramContact).where(
                TelegramContact.company_id == contact.company_id,
                TelegramContact.external_user_id == user_id,
            )
        ).scalars().all()
    return [contact]


def _set_contact_blocked_state(db: Session, client_id: str, blocked: bool) -> dict:
    contact = _telegram_contact_for_client(db, client_id)
    if contact is None:
        _error(404, "telegram_contact_not_found", "\u041a\u043e\u043d\u0442\u0430\u043a\u0442 Telegram \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
    now = datetime.utcnow()
    contacts = _contacts_for_dedupe_identity(db, contact)
    for item in contacts:
        item.is_blocked = blocked
        item.updated_at = now
    db.commit()
    return {
        "ok": True,
        "client_id": client_id,
        "telegram_contact_id": contact.id,
        "is_blocked": blocked,
        "updated_contacts": len(contacts),
    }


def _payload_data(payload: ClientProfileUpdate) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


@router.get("")
def list_management_users(
    q: Annotated[str, Query(max_length=120)] = "",
    blocked: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    last_ticket = (
        select(Ticket.client_id.label("client_id"), func.max(Ticket.updated_at).label("last_activity_at"))
        .group_by(Ticket.client_id)
        .subquery()
    )
    base = (
        select(TelegramContact, Client, ClientProfile, last_ticket.c.last_activity_at)
        .join(Client, Client.id == TelegramContact.client_id, isouter=True)
        .join(ClientProfile, ClientProfile.client_id == TelegramContact.client_id, isouter=True)
        .join(last_ticket, last_ticket.c.client_id == TelegramContact.client_id, isouter=True)
    )

    query = q.strip().lower()
    if query:
        pattern = f"%{query}%"
        base = base.where(
            or_(
                func.lower(TelegramContact.external_user_id).like(pattern),
                func.lower(TelegramContact.external_chat_id).like(pattern),
                func.lower(TelegramContact.external_username).like(pattern),
                func.lower(TelegramContact.external_display_name).like(pattern),
                func.lower(Client.display_name).like(pattern),
                func.lower(ClientProfile.contact_name).like(pattern),
                func.lower(ClientProfile.last_name).like(pattern),
                func.lower(ClientProfile.internal_number).like(pattern),
            )
        )

    rows = db.execute(
        base.order_by(func.coalesce(last_ticket.c.last_activity_at, TelegramContact.updated_at, TelegramContact.created_at).desc())
    ).all()
    deduped_rows = _dedupe_rows(rows)
    blocked_state = _blocked_state_by_dedupe_key(db, deduped_rows)
    if blocked:
        deduped_rows = [row for row in deduped_rows if blocked_state.get(_dedupe_key(row[0]), False)]
    else:
        deduped_rows = [row for row in deduped_rows if not blocked_state.get(_dedupe_key(row[0]), False)]
    page_rows = deduped_rows[offset:offset + limit]
    return {
        "items": [
            _row_out(
                contact,
                client,
                profile,
                last_activity_at,
                is_blocked=blocked_state.get(_dedupe_key(contact), False),
            )
            for contact, client, profile, last_activity_at in page_rows
        ],
        "total": len(deduped_rows),
        "limit": limit,
        "offset": offset,
        "blocked_supported": True,
    }


@router.get("/client-card-view-config", response_model=ClientCardRuntimeConfigResponse)
def get_client_card_config(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return _client_card_view_config(db)


@router.get("/{client_id}/profile", response_model=ClientProfileOut)
def get_management_user_profile(
    client_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        _error(404, "client_not_found", "\u041a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
    profile = _get_or_create_client_profile(db, client_id)
    contact = _telegram_contact_for_client(db, client_id)
    return _profile_out(
        profile,
        contact.external_username if contact else None,
        contact.external_display_name if contact else None,
    )


@router.get("/{client_id}/profile/bitrix-password", response_model=BitrixPasswordRevealOut)
def reveal_management_user_bitrix_password(
    client_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    if profile is None:
        _error(404, "client_profile_not_found", "Профиль клиента не найден")
    encrypted_password = profile.bitrix_password
    if not encrypted_password or not str(encrypted_password).strip():
        return {"bitrix_password": None}
    try:
        password = decrypt_secret(encrypted_password)
    except SecretEncryptionConfigurationError:
        logger.warning("Bitrix password reveal failed because encryption is not configured correctly.")
        _error(503, "bitrix_password_decrypt_unavailable", "Пароль Bitrix временно недоступен")
    except SecretEncryptionError:
        logger.warning("Bitrix password reveal failed because stored value could not be decrypted.")
        _error(409, "bitrix_password_decrypt_failed", "Пароль Bitrix не удалось расшифровать")
    return {"bitrix_password": password}


@router.post("/{client_id}/block")
def block_management_user(
    client_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return _set_contact_blocked_state(db, client_id, True)


@router.post("/{client_id}/unblock")
def unblock_management_user(
    client_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return _set_contact_blocked_state(db, client_id, False)


@router.post("/{client_id}/reset-data")
def reset_management_user_profile_data(
    client_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        _error(404, "client_not_found", "Клиент не найден")
    try:
        result = reset_management_user_data(db, client_id)
    except ResetManagementUserDataActiveTicketError:
        _error(
            409,
            "client_has_active_ticket",
            "\u041d\u0435\u043b\u044c\u0437\u044f \u0441\u0442\u0435\u0440\u0435\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f, \u043f\u043e\u043a\u0430 \u0443 \u043d\u0435\u0433\u043e \u0435\u0441\u0442\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u0438\u043b\u0438 \u0437\u0430\u0434\u0430\u0447\u0430. \u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u0435 \u0435\u0451."
        )
    except LookupError:
        _error(404, "telegram_contact_not_found", "Контакт Telegram не найден")
    profile = result.profile
    profile_payload = profile.model_dump() if hasattr(profile, "model_dump") else profile.dict()
    return {
        "ok": True,
        "client_id": result.client_id,
        "telegram_contact_id": result.telegram_contact_id,
        "is_blocked": result.is_blocked,
        "profile": profile_payload,
    }


@router.patch("/{client_id}/profile", response_model=ClientProfileOut)
def update_management_user_profile(
    client_id: str,
    payload: ClientProfileUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        _error(404, "client_not_found", "\u041a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    data = _payload_data(payload)
    merged_custom_fields = dict(profile.custom_fields or {}) if profile is not None else {}
    if "custom_fields" in data and data["custom_fields"] is not None:
        custom_fields = dict(merged_custom_fields)
        field_map = _custom_field_map(db)
        for key, value in data["custom_fields"].items():
            field = field_map.get(key)
            if not field:
                continue
            normalized = _normalize_custom_field_value(field, value)
            if normalized is None:
                custom_fields.pop(key, None)
            else:
                custom_fields[key] = normalized
        merged_custom_fields = custom_fields
    result = apply_client_card_profile_save_pipeline(db, profile, data, merged_custom_fields, client_id=client_id)
    profile = result.profile
    db.commit()
    db.refresh(profile)
    contact = _telegram_contact_for_client(db, client_id)
    return _profile_out(
        profile,
        contact.external_username if contact else None,
        contact.external_display_name if contact else None,
    )


@router.post("/{client_id}/profile/source-run", response_model=ClientProfileOut)
def run_management_user_profile_source(
    client_id: str,
    payload: ClientCardSourceRunRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    client = db.get(Client, client_id)
    if client is None:
        _error(404, "client_not_found", "\u041a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
    contact = _telegram_contact_for_client(db, client_id)
    result = apply_client_card_source_manual_run(db, client_id=client_id, source_id=payload.source_id)
    profile = result.profile
    if "contact_name" in result.changed_keys and profile.contact_name:
        client.display_name = profile.contact_name
    db.commit()
    db.refresh(profile)
    return _profile_out(
        profile,
        contact.external_username if contact else None,
        contact.external_display_name if contact else None,
    )
