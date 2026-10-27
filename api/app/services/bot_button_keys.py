"""Change an ordinary button key without changing its identity or ticket history."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, BotMenuLevelLayout, Ticket
from .button_config import button_identity_path, button_key_aliases, build_terminal_button_key, build_visibility_button_key, ordinary_button_keys


def _conflict(code: str, message: str):
    raise HTTPException(409, detail={"error": {"code": code, "message": message}})


def lock_button_keys(db: Session):
    # ponytail: serialize infrequent admin key writes; use finer locks if write volume grows.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(684205731)))


def check_button_key_collisions(db: Session, row: BotButtonSetting):
    if ordinary_button_keys(row) and len(row.button_key) > Ticket.__table__.c.button_config_key.type.length:
        _conflict("button_key_requires_migration", "Ключ не помещается в ссылки обращений; требуется миграция")
    aliases = button_key_aliases(row) | button_key_aliases(row, submenu=True)
    with db.no_autoflush:
        others = db.execute(select(BotButtonSetting)).scalars().all()
    for other in others:
        if other.id == row.id:
            continue
        if aliases & (button_key_aliases(other) | button_key_aliases(other, submenu=True)):
            _conflict("button_key_collision", "Новый ключ или алиас уже принадлежит другой кнопке")


def change_button_workflow_key(db: Session, row: BotButtonSetting, workflow: str):
    typed = ordinary_button_keys(row)
    identity_path = button_identity_path(row)
    old_db = build_visibility_button_key(row.menu_type, "db", identity_path)
    if not typed or row.button_key not in typed | {old_db}:
        _conflict("button_key_requires_decision", "Нестандартный ключ требует отдельного решения")
    legacy = row.legacy_button_key
    if row.button_key == old_db:
        if legacy and legacy not in typed | {old_db}:
            _conflict("button_legacy_key_occupied", "Для сохранения старого db-ключа требуется миграция алиасов")
        legacy = old_db
    new_key = build_terminal_button_key(row.menu_type, workflow, identity_path)
    aliases = button_key_aliases(row)
    layouts = []
    for layout in db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == row.menu_type)).scalars().all():
        value = deepcopy(layout.layout_json)
        for item in value or []:
            if isinstance(item, dict) and isinstance(item.get("button_keys"), list):
                keys = [new_key if key in aliases else key for key in item["button_keys"]]
                item["button_keys"] = keys
        all_keys = [key for item in value or [] if isinstance(item, dict) for key in item.get("button_keys", [])]
        if all_keys.count(new_key) > 1:
            _conflict("button_layout_alias_duplicate", "В раскладке несколько ссылок на одну кнопку")
        if value != layout.layout_json:
            layouts.append((layout, value))
    # Validate the proposed aliases before mutating persistent records.
    candidate = BotButtonSetting(id=row.id, button_key=new_key, legacy_button_key=legacy,
        menu_type=row.menu_type, button_path=row.button_path, workflow_type=workflow,
        button_kind=row.button_kind, button_label=row.button_label)
    check_button_key_collisions(db, candidate)
    row.button_key = new_key
    row.legacy_button_key = legacy
    for layout, value in layouts:
        layout.layout_json = value
