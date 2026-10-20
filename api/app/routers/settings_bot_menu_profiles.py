import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, BotMenuDestinationBinding, BotMenuProfile, ClientCardFieldConfig, ClientProfile
from ..schemas import BotButtonMenuTypeOut, BotMenuCreatePayload, BotMenuDestinationPayload
from ..services.button_config import BASIC_MENU_NAME, available_menu_names
from ..services.system_tickets import is_system_menu_type


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

# Keep aligned with client card destination selector defaults in ticket UI.
DEFAULT_DESTINATION_VALUES = [
    "Песок",
    "Отдел Продаж",
    "Колл Центр",
    "HR",
    "Руководство",
    "Отдел Коучей",
    "Контроль Качества",
]


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _destination_options(db: Session) -> list[str]:
    field = db.execute(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key == "destination")).scalar_one_or_none()
    values: list[object] = []
    if field is not None and isinstance(field.options_json, list):
        values.extend(field.options_json)

    values.extend(
        row[0]
        for row in db.execute(select(ClientProfile.destination).distinct()).all()
        if row[0]
    )
    values.extend(
        row[0]
        for row in db.execute(select(BotMenuDestinationBinding.destination_value).distinct()).all()
        if row[0]
    )
    values.extend(DEFAULT_DESTINATION_VALUES)
    values.extend(name for name in available_menu_names(db) if name != BASIC_MENU_NAME)
    result = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or set(value) == {"?"} or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _menu_profile_map(db: Session) -> dict[str, BotMenuProfile]:
    return {row.menu_type: row for row in db.execute(select(BotMenuProfile)).scalars().all()}


def _menu_bindings_map(db: Session) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in db.execute(select(BotMenuDestinationBinding).order_by(BotMenuDestinationBinding.destination_value.asc())).scalars().all():
        if is_system_menu_type(row.menu_type):
            continue
        result.setdefault(row.menu_type, []).append(row.destination_value)
    return result


def _ensure_menu_profile(db: Session, menu_type: str, *, display_name: str | None = None, is_fallback: bool = False) -> BotMenuProfile:
    row = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_type)).scalar_one_or_none()
    now = datetime.utcnow()
    if row is None:
        row = BotMenuProfile(
            id=str(uuid.uuid4()),
            menu_type=menu_type,
            display_name=(display_name or menu_type).strip() or menu_type,
            is_fallback=is_fallback,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        return row
    changed = False
    if not (row.display_name or "").strip():
        row.display_name = (display_name or menu_type).strip() or menu_type
        changed = True
    if bool(row.is_fallback) != bool(is_fallback):
        row.is_fallback = bool(is_fallback)
        changed = True
    if not bool(row.is_active):
        row.is_active = True
        changed = True
    if changed:
        row.updated_at = now
    return row


def _unique_menu_type(db: Session, display_name: str) -> str:
    base = re.sub(r"\s+", " ", display_name.strip()) or "Меню"
    candidate = base
    idx = 2
    while db.execute(select(BotMenuProfile.id).where(BotMenuProfile.menu_type == candidate)).first() is not None:
        candidate = f"{base} {idx}"
        idx += 1
    return candidate


@router.get("/bot-buttons/menu-types", response_model=list[BotButtonMenuTypeOut])
def list_bot_button_menu_types(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    profiles = _menu_profile_map(db)
    bindings = _menu_bindings_map(db)
    result = []
    for menu_name in available_menu_names(db):
        profile = profiles.get(menu_name)
        rows = db.execute(select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu_name, BotButtonSetting.currently_present.is_(True),
            BotButtonSetting.is_archived.is_(False),
        )).scalars().all()
        result.append(BotButtonMenuTypeOut(menu_name=menu_name,
            display_name=profile.display_name if profile else menu_name, destinations=bindings.get(menu_name, []),
            source_file="DB", ok=True, buttons_count=len(rows),
            menus_count=1 + sum(row.button_kind == "section" for row in rows)))
    return result


@router.get("/bot-buttons/menu-types/archived", response_model=list[BotButtonMenuTypeOut])
def list_archived_bot_button_menu_types(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    profiles = (
        db.execute(select(BotMenuProfile).where(BotMenuProfile.is_active.is_(False)).order_by(BotMenuProfile.display_name.asc()))
        .scalars()
        .all()
    )
    bindings = _menu_bindings_map(db)
    result: list[BotButtonMenuTypeOut] = []
    for profile in profiles:
        if is_system_menu_type(profile.menu_type):
            continue
        buttons_count = db.execute(
            select(BotButtonSetting.id).where(BotButtonSetting.menu_type == profile.menu_type)
        ).all()
        result.append(
            BotButtonMenuTypeOut(
                menu_name=profile.menu_type,
                display_name=(profile.display_name or profile.menu_type).strip() or profile.menu_type,
                destinations=bindings.get(profile.menu_type, []),
                source_file="DB",
                ok=True,
                buttons_count=len(buttons_count),
                menus_count=0,
            )
        )
    return result


@router.post("/bot-buttons/menu-types", response_model=BotButtonMenuTypeOut)
def create_bot_button_menu_type(
    payload: BotMenuCreatePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    display_name = (payload.display_name or "").strip()
    if not display_name:
        _error(422, "display_name_required", "Название меню обязательно")
    destination_options = set(_destination_options(db))
    requested_destinations = [str(v or "").strip() for v in (payload.destinations or [])]
    requested_destinations = [v for v in requested_destinations if v]
    if not requested_destinations:
        _error(422, "destinations_required", "Выберите минимум один тип клиента")
    for destination in requested_destinations:
        if destination not in destination_options:
            _error(422, "destination_unknown", f"Неизвестный тип клиента: {destination}")
        existing = db.execute(
            select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
        ).scalar_one_or_none()
        if existing is not None:
            _error(409, "destination_already_bound", f"Тип клиента уже привязан к меню: {destination}")

    menu_type = _unique_menu_type(db, display_name)
    profile = BotMenuProfile(
        id=str(uuid.uuid4()),
        menu_type=menu_type,
        display_name=display_name,
        is_fallback=False,
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(profile)
    for destination in requested_destinations:
        db.add(
            BotMenuDestinationBinding(
                id=str(uuid.uuid4()),
                destination_value=destination,
                menu_type=menu_type,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
    db.commit()
    return BotButtonMenuTypeOut(
        menu_name=menu_type,
        display_name=display_name,
        destinations=sorted(requested_destinations),
        source_file="DB",
        ok=True,
        buttons_count=0,
        menus_count=0,
    )


@router.delete("/bot-buttons/menu-types/{menu_name}")
def delete_bot_button_menu_type(
    menu_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if menu_name == BASIC_MENU_NAME:
        _error(422, "basic_menu_forbidden", "Базовое меню нельзя удалить")
    profile = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_name)).scalar_one_or_none()
    if profile is None:
        _error(404, "menu_not_found", "Меню не найдено")
    profile.is_active = False
    profile.updated_at = datetime.utcnow()
    for row in db.execute(select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.menu_type == menu_name)).scalars().all():
        db.delete(row)
    now = datetime.utcnow()
    for setting in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all():
        setting.is_archived = True
        setting.deleted_at = now
        setting.deleted_by_user_id = user.id
        setting.updated_at = now
    db.commit()
    return {"ok": True, "menu_name": menu_name}


@router.post("/bot-buttons/menu-types/{menu_name}/restore")
def restore_bot_button_menu_type(
    menu_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    profile = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_name)).scalar_one_or_none()
    if profile is None:
        _error(404, "menu_not_found", "Меню не найдено")
    profile.is_active = True
    profile.updated_at = datetime.utcnow()
    for setting in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all():
        setting.is_archived = False
        setting.deleted_at = None
        setting.deleted_by_user_id = None
        setting.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "menu_name": menu_name}


@router.get("/bot-buttons/destination-options")
def bot_button_destination_options(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    options = _destination_options(db)
    busy = {
        row.destination_value: row.menu_type
        for row in db.execute(select(BotMenuDestinationBinding)).scalars().all()
    }
    free = [item for item in options if item not in busy]
    return {
        # Legacy + current aliases to keep frontend/backward compatibility.
        "items": options,
        "free": free,
        "options": options,
        "free_options": free,
        "bindings": busy,
    }


@router.post("/bot-buttons/menu-types/{menu_name}/destinations")
def add_bot_menu_destination(
    menu_name: str,
    payload: BotMenuDestinationPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    profile = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_name)).scalar_one_or_none()
    if profile is None:
        _error(404, "menu_not_found", "Меню не найдено")
    destination = (payload.destination or "").strip()
    if not destination:
        _error(422, "destination_required", "Тип клиента обязателен")
    if destination not in set(_destination_options(db)):
        _error(422, "destination_unknown", f"Неизвестный тип клиента: {destination}")
    existing = db.execute(
        select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
    ).scalar_one_or_none()
    if existing is not None and existing.menu_type != menu_name:
        _error(409, "destination_already_bound", f"Тип клиента уже привязан к меню: {destination}")
    if existing is None:
        db.add(
            BotMenuDestinationBinding(
                id=str(uuid.uuid4()),
                destination_value=destination,
                menu_type=menu_name,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        db.commit()
    return {"ok": True, "menu_name": menu_name, "destination": destination}


@router.delete("/bot-buttons/menu-types/{menu_name}/destinations/{destination}")
def remove_bot_menu_destination(
    menu_name: str,
    destination: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(
        select(BotMenuDestinationBinding).where(
            BotMenuDestinationBinding.menu_type == menu_name,
            BotMenuDestinationBinding.destination_value == destination,
        )
    ).scalar_one_or_none()
    if row is None:
        _error(404, "binding_not_found", "Привязка не найдена")
    db.delete(row)
    db.commit()
    return {"ok": True, "menu_name": menu_name, "destination": destination}
