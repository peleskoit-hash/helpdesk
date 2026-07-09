import re
import unicodedata
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import ClientCardBlockConfig, ClientCardFieldConfig
from ..schemas import (
    ClientCardBlockConfigOut,
    ClientCardBlockCreate,
    ClientCardBlockUpdate,
    ClientCardConfigResponse,
    ClientCardFieldConfigOut,
    ClientCardFieldCreate,
    ClientCardFieldUpdate,
    ClientCardMovePayload,
)


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
SYSTEM_BLOCK_KEYS = {"tg", "profile", "department", "work", "verification"}
SORT_STEP = 10
NAMED_COLOR_SCHEMES = {"blue", "green", "yellow", "red", "gray", "purple"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _slugify_title(value: str) -> str:
    lowered = value.strip().lower()
    transliterated = "".join(CYRILLIC_TO_LATIN.get(ch, ch) for ch in lowered)
    transliterated = unicodedata.normalize("NFKD", transliterated)
    transliterated = transliterated.encode("ascii", "ignore").decode("ascii")
    transliterated = re.sub(r"[^a-z0-9]+", "_", transliterated).strip("_")
    return transliterated or "block"


def _generate_custom_field_key(db: Session, label: str) -> str:
    base_slug = _slugify_title(label)
    if not base_slug:
        base_slug = "field"
    base_key = f"custom_{base_slug}"
    key = base_key
    index = 2
    while db.execute(select(ClientCardFieldConfig.id).where(ClientCardFieldConfig.key == key)).first() is not None:
        key = f"{base_key}_{index}"
        index += 1
    return key


def _normalize_color_scheme(value: str) -> str:
    color = (value or "").strip()
    named = color.lower()
    if named in NAMED_COLOR_SCHEMES:
        return named
    if HEX_COLOR_RE.fullmatch(color):
        return color.lower()
    _error(422, "color_scheme_invalid", "Color must be a named client card color or #RRGGBB")


def _block_out(block: ClientCardBlockConfig, fields: list[ClientCardFieldConfigOut] | None = None) -> ClientCardBlockConfigOut:
    return ClientCardBlockConfigOut(
        id=block.id,
        key=block.key,
        title=block.title,
        color_scheme=block.color_scheme,
        sort_order=block.sort_order,
        is_active=bool(block.is_active),
        fields=fields or [],
    )


def _field_out(field: ClientCardFieldConfig) -> ClientCardFieldConfigOut:
    return ClientCardFieldConfigOut(
        id=field.id,
        key=field.key,
        label=field.label,
        field_type=field.field_type,
        block_key=field.block_key,
        sort_order=field.sort_order,
        is_visible=bool(field.is_visible),
        is_editable=bool(field.is_editable),
        is_required=bool(field.is_required),
        is_system=bool(field.is_system),
        options=field.options_json or None,
    )


def _is_custom_block(block: ClientCardBlockConfig) -> bool:
    return block.key not in SYSTEM_BLOCK_KEYS and block.key.startswith("custom_block_")


def _reindex_blocks(blocks: list[ClientCardBlockConfig]) -> None:
    now = datetime.utcnow()
    for index, block in enumerate(blocks, start=1):
        block.sort_order = index * SORT_STEP
        block.updated_at = now


def _reindex_fields(fields: list[ClientCardFieldConfig]) -> None:
    now = datetime.utcnow()
    for index, field in enumerate(fields, start=1):
        field.sort_order = index * SORT_STEP
        field.updated_at = now


def _ordered_blocks(db: Session) -> list[ClientCardBlockConfig]:
    return db.execute(
        select(ClientCardBlockConfig).order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.created_at.asc())
    ).scalars().all()


def _ordered_fields_for_block(db: Session, block_key: str) -> list[ClientCardFieldConfig]:
    return db.execute(
        select(ClientCardFieldConfig)
        .where(ClientCardFieldConfig.block_key == block_key)
        .order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()


def _serialize_client_card_config(db: Session) -> ClientCardConfigResponse:
    blocks = db.execute(
        select(ClientCardBlockConfig).order_by(ClientCardBlockConfig.sort_order.asc(), ClientCardBlockConfig.created_at.asc())
    ).scalars().all()
    fields = db.execute(
        select(ClientCardFieldConfig).order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()
    fields_by_block: dict[str, list[ClientCardFieldConfigOut]] = {}
    for field in fields:
        fields_by_block.setdefault(field.block_key, []).append(_field_out(field))
    return ClientCardConfigResponse(
        blocks=[_block_out(block, fields_by_block.get(block.key, [])) for block in blocks]
    )


@router.get("/client-card", response_model=ClientCardConfigResponse)
def get_client_card_config(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return _serialize_client_card_config(db)


@router.patch("/client-card/blocks/{block_id}", response_model=ClientCardBlockConfigOut)
def update_client_card_block(
    block_id: str,
    payload: ClientCardBlockUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.get(ClientCardBlockConfig, block_id)
    if block is None:
        _error(404, "block_not_found", "Блок не найден")
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            _error(422, "title_required", "Название блока обязательно")
        block.title = title
    if payload.color_scheme is not None:
        block.color_scheme = _normalize_color_scheme(payload.color_scheme)
    if payload.sort_order is not None:
        block.sort_order = payload.sort_order
    if payload.is_active is not None:
        block.is_active = payload.is_active
    block.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(block)
    return _block_out(block)


@router.post("/client-card/blocks/{block_id}/move", response_model=ClientCardConfigResponse)
def move_client_card_block(
    block_id: str,
    payload: ClientCardMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    blocks = _ordered_blocks(db)
    index = next((i for i, block in enumerate(blocks) if block.id == block_id), None)
    if index is None:
        _error(404, "block_not_found", "Block not found")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if target_index < 0 or target_index >= len(blocks):
        _reindex_blocks(blocks)
        db.commit()
        return _serialize_client_card_config(db)
    blocks[index], blocks[target_index] = blocks[target_index], blocks[index]
    _reindex_blocks(blocks)
    db.commit()
    return _serialize_client_card_config(db)


@router.delete("/client-card/blocks/{block_id}", response_model=ClientCardConfigResponse)
def delete_client_card_block(
    block_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.get(ClientCardBlockConfig, block_id)
    if block is None:
        _error(404, "block_not_found", "Block not found")
    if not _is_custom_block(block):
        _error(403, "system_block_delete_forbidden", "System block cannot be deleted")
    fields = _ordered_fields_for_block(db, block.key)
    if any(field.is_system for field in fields):
        _error(403, "system_block_fields_delete_forbidden", "Block contains system fields")
    for field in fields:
        db.delete(field)
    db.delete(block)
    db.flush()
    _reindex_blocks(_ordered_blocks(db))
    db.commit()
    return _serialize_client_card_config(db)


@router.patch("/client-card/fields/{field_id}", response_model=ClientCardFieldConfigOut)
def update_client_card_field(
    field_id: str,
    payload: ClientCardFieldUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Поле не найдено")
    if field.is_system:
        if payload.field_type is not None and payload.field_type != field.field_type:
            _error(403, "readonly_system_field_type", "Тип системного поля нельзя изменять")
    if payload.label is not None:
        label = payload.label.strip()
        if not label:
            _error(422, "label_required", "Название поля обязательно")
        field.label = label
    if payload.field_type is not None and not field.is_system:
        field.field_type = payload.field_type
    if payload.block_key is not None:
        if payload.block_key != field.block_key:
            _error(400, "field_block_change_not_supported", "Moving an existing field to another block is not supported in this release")
    if payload.sort_order is not None:
        field.sort_order = payload.sort_order
    if payload.is_visible is not None:
        field.is_visible = payload.is_visible
    if payload.is_editable is not None:
        field.is_editable = payload.is_editable
    if payload.is_required is not None:
        field.is_required = payload.is_required
    if payload.options is not None:
        options = [item.strip() for item in payload.options if item and item.strip()]
        if field.field_type == "select" and not options:
            _error(422, "select_options_required", "Для поля select укажите варианты")
        field.options_json = options or None
    field.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(field)
    return _field_out(field)


@router.post("/client-card/fields/{field_id}/move", response_model=ClientCardConfigResponse)
def move_client_card_field(
    field_id: str,
    payload: ClientCardMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Field not found")
    fields = _ordered_fields_for_block(db, field.block_key)
    index = next((i for i, item in enumerate(fields) if item.id == field_id), None)
    if index is None:
        _error(404, "field_not_found", "Field not found")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if target_index < 0 or target_index >= len(fields):
        _reindex_fields(fields)
        db.commit()
        return _serialize_client_card_config(db)
    fields[index], fields[target_index] = fields[target_index], fields[index]
    _reindex_fields(fields)
    db.commit()
    return _serialize_client_card_config(db)


@router.delete("/client-card/fields/{field_id}", response_model=ClientCardConfigResponse)
def delete_client_card_field(
    field_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    field = db.get(ClientCardFieldConfig, field_id)
    if field is None:
        _error(404, "field_not_found", "Field not found")
    if field.is_system:
        _error(403, "system_field_delete_forbidden", "System field cannot be deleted")
    block_key = field.block_key
    db.delete(field)
    db.flush()
    _reindex_fields(_ordered_fields_for_block(db, block_key))
    db.commit()
    return _serialize_client_card_config(db)


@router.post("/client-card/blocks", response_model=ClientCardBlockConfigOut)
def create_client_card_block(
    payload: ClientCardBlockCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    title = payload.title.strip()
    if not title:
        _error(422, "title_required", "Название блока обязательно")
    base = f"custom_block_{_slugify_title(title)}"
    key = base
    index = 2
    while db.execute(select(ClientCardBlockConfig.id).where(ClientCardBlockConfig.key == key)).first() is not None:
        key = f"{base}_{index}"
        index += 1
    now = datetime.utcnow()
    block = ClientCardBlockConfig(
        id=str(uuid.uuid4()),
        key=key,
        title=title,
        color_scheme=_normalize_color_scheme(payload.color_scheme),
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        created_at=now,
        updated_at=now,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return _block_out(block)


@router.post("/client-card/fields", response_model=ClientCardFieldConfigOut)
def create_client_card_field(
    payload: ClientCardFieldCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    block = db.execute(select(ClientCardBlockConfig).where(ClientCardBlockConfig.key == payload.block_key)).scalar_one_or_none()
    if block is None:
        _error(422, "block_key_invalid", "Указан неизвестный блок")
    label = payload.label.strip()
    if not label:
        _error(422, "label_required", "Название поля обязательно")
    options = [item.strip() for item in (payload.options or []) if item and item.strip()]
    if payload.field_type == "select" and not options:
        _error(422, "select_options_required", "Для поля select укажите варианты")

    max_sort = db.execute(
        select(ClientCardFieldConfig.sort_order)
        .where(ClientCardFieldConfig.block_key == payload.block_key)
        .order_by(ClientCardFieldConfig.sort_order.desc())
        .limit(1)
    ).scalar_one_or_none()
    sort_order = payload.sort_order if payload.sort_order is not None else (max_sort + 10 if max_sort is not None else 10)

    now = datetime.utcnow()
    field = ClientCardFieldConfig(
        id=str(uuid.uuid4()),
        key=_generate_custom_field_key(db, label),
        label=label,
        field_type=payload.field_type,
        block_key=payload.block_key,
        sort_order=sort_order,
        is_visible=payload.is_visible,
        is_editable=payload.is_editable,
        is_required=payload.is_required,
        is_system=False,
        options_json=options or None,
        created_at=now,
        updated_at=now,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return _field_out(field)
