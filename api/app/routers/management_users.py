from __future__ import annotations

from datetime import datetime
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import Client, ClientProfile, TelegramContact, Ticket
from ..schemas import ClientCardConfigResponse, ClientProfileOut, ClientProfileUpdate
from ..services.client_card import (
    _client_card_view_config,
    _custom_field_map,
    _normalize_custom_field_value,
    _profile_out,
)


router = APIRouter(prefix="/api/v1/management/users", tags=["management-users"])


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


def _row_out(contact: TelegramContact, client: Client | None, profile: ClientProfile | None, last_activity_at: datetime | None):
    activity = last_activity_at or contact.updated_at or contact.created_at
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
        "blocked": False,
        "blocked_supported": False,
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
    if blocked:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "blocked_supported": False,
        }

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
    page_rows = deduped_rows[offset:offset + limit]
    return {
        "items": [_row_out(contact, client, profile, last_activity_at) for contact, client, profile, last_activity_at in page_rows],
        "total": len(deduped_rows),
        "limit": limit,
        "offset": offset,
        "blocked_supported": False,
    }


@router.get("/client-card-view-config", response_model=ClientCardConfigResponse)
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
    profile = _get_or_create_client_profile(db, client_id)
    data = _payload_data(payload)
    for field in [
        "contact_name",
        "last_name",
        "email",
        "phone",
        "verification_status",
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
    ]:
        if field in data:
            setattr(profile, field, data[field])
    if "is_verified" in data:
        profile.is_verified = bool(data["is_verified"])
    if "custom_fields" in data and data["custom_fields"] is not None:
        custom_fields = dict(profile.custom_fields or {})
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
        profile.custom_fields = custom_fields
    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    contact = _telegram_contact_for_client(db, client_id)
    return _profile_out(
        profile,
        contact.external_username if contact else None,
        contact.external_display_name if contact else None,
    )
