"""Relocate ordinary buttons without changing identity or settings."""
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, BotMenuLevelLayout
from ..schemas import BotButtonMovePayload
from .bot_button_keys import lock_button_keys, check_button_key_collisions
from .button_config import ORDINARY_WORKFLOW_TYPES, build_level_layout_key
from .system_tickets import is_system_menu_type


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def move_button_between_levels(db: Session, payload: BotButtonMovePayload, load_layout) -> BotButtonSetting:
    """Stage a move using the existing structure reader's effective level order.

    The caller commits the move, its layouts and the refreshed response together.
    """
    lock_button_keys(db)
    menu = payload.menu_type
    if is_system_menu_type(menu):
        _error(422, "invalid_move_menu", "System buttons cannot be moved")
    buttons = db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu)
                         .execution_options(populate_existing=True)).scalars().all()
    row = next((item for item in buttons if item.id == payload.button_id), None)
    if row is None or not row.currently_present or row.is_archived:
        _error(404, "button_not_found", "Active button not found in this menu")
    if row.button_kind != "terminal" or row.workflow_type not in ORDINARY_WORKFLOW_TYPES:
        _error(422, "invalid_move_kind", "Only ordinary buttons can be moved")
    if any(item.parent_path == row.button_path for item in buttons):
        _error(422, "button_has_children", "A button with children cannot be moved")

    source_parent = row.parent_path or ""
    target_parent = payload.parent_path
    if source_parent == target_parent:
        _error(422, "same_move_level", "Use layout to reorder within one level")
    for path in (source_parent, target_parent):
        visited = {row.button_path}
        while path:
            if path in visited:
                _error(409, "button_parent_cycle", "Cyclic button parent")
            visited.add(path)
            parents = [item for item in buttons if item.button_path == path]
            if len(parents) != 1:
                _error(422, "invalid_move_parent", "Parent must exist and be unambiguous")
            parent = parents[0]
            if (parent.button_kind != "section" or parent.workflow_type != "submenu"
                    or not parent.currently_present or parent.is_archived):
                _error(422, "invalid_move_parent", "Parent must be an active ordinary submenu")
            path = parent.parent_path or ""

    next_path = f"{target_parent}/{row.button_label}" if target_parent else row.button_label
    level_keys = [build_level_layout_key(menu, path) for path in (source_parent, target_parent)]
    if len(next_path) > 1000 or any(len(key) > 600 for key in level_keys):
        _error(422, "move_path_too_long", "Button path or layout key exceeds storage limit")
    if any(item.id != row.id and item.button_path == next_path for item in buttons):
        _error(409, "button_path_collision", "A button already occupies the target path")

    layouts = [load_layout(menu, key) for key in level_keys]
    source, target = layouts
    for item in source:
        item["button_keys"] = [key for key in item["button_keys"] if key != row.button_key]
        item["columns"] = min(item["columns"], len(item["button_keys"]))
    source[:] = [item for item in source if item["button_keys"]]
    index = len(target) if payload.target_row is None else payload.target_row
    if index > len(target) or (not payload.new_row and index == len(target)):
        _error(422, "invalid_target_position", "Target row does not exist")
    if payload.new_row:
        if payload.target_index != 0:
            _error(422, "invalid_target_position", "A new row starts at index zero")
        target.insert(index, {"columns": 1, "button_keys": [row.button_key]})
    else:
        keys = target[index]["button_keys"]
        if payload.target_index > len(keys):
            _error(422, "invalid_target_position", "Target index is outside the row")
        if len(keys) >= 3:
            _error(422, "layout_row_too_wide", "A layout row can contain at most 3 buttons")
        keys.insert(payload.target_index, row.button_key)
        target[index]["columns"] = len(keys)
    now = datetime.utcnow()
    row.parent_path, row.button_path, row.updated_at = target_parent, next_path, now
    check_button_key_collisions(db, row)
    for level_key, rows in zip(level_keys, layouts):
        layout = db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.level_key == level_key)
                            .execution_options(populate_existing=True)).scalar_one_or_none()
        if layout is None:
            layout = BotMenuLevelLayout(id=str(uuid.uuid4()), menu_type=menu, level_key=level_key)
            db.add(layout)
        layout.layout_json, layout.updated_at = rows, now
    return row
