import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, BotMenuLevelLayout
from ..schemas import BotButtonGroupOut, BotButtonMenuStructureOut, BotButtonStructureLeafOut, BotButtonMovePayload
from ..services.button_config import build_level_layout_key, build_visibility_button_key, build_terminal_button_key
from ..services.bot_button_keys import lock_button_keys, check_button_key_collisions, change_button_workflow_key
from ..services.bot_button_moves import move_button_between_levels


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
EDITABLE_WORKFLOW_TYPES = {"ticket", "task", "auto"}
MAX_LAYOUT_ROW_BUTTONS = 3


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "Настройки доступны только администратору")


def _normalize_level_layout(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        cols_raw = row.get("columns", 1)
        try:
            cols = int(cols_raw)
        except Exception:
            cols = 1
        cols = cols if cols in {1, 2, 3} else 1
        keys_raw = row.get("button_keys")
        keys: list[str] = []
        if isinstance(keys_raw, list):
            for key in keys_raw:
                text = str(key or "").strip()
                if text and text not in keys:
                    keys.append(text)
        normalized.append({"columns": cols, "button_keys": keys[:MAX_LAYOUT_ROW_BUTTONS]})
    return normalized


def _apply_level_layout(rows: list[dict], layout_rows: list[dict] | None) -> list[dict]:
    if not layout_rows:
        return rows
    node_by_key: dict[str, dict] = {}
    source_order: list[str] = []
    for row in rows:
        for node in (row.get("buttons") or []):
            key = str(node.get("technical_key") or "").strip()
            if not key or key in node_by_key:
                continue
            node_by_key[key] = node
            source_order.append(key)
    if not node_by_key:
        return rows

    result: list[dict] = []
    used: set[str] = set()
    for row in layout_rows:
        if not isinstance(row, dict):
            continue
        cols_raw = row.get("columns", 1)
        try:
            cols = int(cols_raw)
        except Exception:
            cols = 1
        cols = cols if cols in {1, 2, 3} else 1
        keys = row.get("button_keys")
        if not isinstance(keys, list):
            continue
        picked: list[dict] = []
        for raw in keys:
            key = str(raw or "").strip()
            if not key or key in used:
                continue
            node = node_by_key.get(key)
            if node is None:
                continue
            picked.append(node)
            used.add(key)
            if len(picked) >= MAX_LAYOUT_ROW_BUTTONS:
                break
        if picked:
            result.append({"columns": cols, "buttons": picked})

    for key in source_order:
        if key in used:
            continue
        node = node_by_key.get(key)
        if node is not None:
            result.append({"columns": 1, "buttons": [node]})
    return result


def _find_level_rows_in_tree(rows: list[dict], target_level_key: str) -> list[dict] | None:
    for row in rows or []:
        for node in row.get("buttons") or []:
            if str(node.get("level_key") or "") == target_level_key:
                return node.get("children_rows") if isinstance(node.get("children_rows"), list) else []
            found = _find_level_rows_in_tree(node.get("children_rows") or [], target_level_key)
            if found is not None:
                return found
    return None


def _level_button_keys(rows: list[dict]) -> set[str]:
    keys: set[str] = set()
    for row in rows or []:
        for node in row.get("buttons") or []:
            key = str(node.get("technical_key") or "").strip()
            if key and not bool(node.get("removed")) and node.get("kind") != "removed_folder":
                keys.add(key)
    return keys


def _rows_for_level_key(db: Session, menu_type: str, level_key: str) -> list[dict]:
    structure = _build_bot_menu_structure(db, menu_type)
    if level_key == (structure.root_level_key or build_level_layout_key(menu_type, "")):
        return structure.tree_rows
    rows = _find_level_rows_in_tree(structure.tree_rows, level_key)
    if rows is None:
        _error(404, "level_not_found", "Level not found")
    return rows


def _normalize_layout_payload_rows(raw_rows: object, allowed_keys: set[str]) -> list[dict]:
    if not isinstance(raw_rows, list):
        _error(422, "layout_rows_required", "Layout rows must be a list")
    normalized: list[dict] = []
    seen: set[str] = set()
    for row_index, raw_row in enumerate(raw_rows):
        if isinstance(raw_row, dict):
            raw_keys = raw_row.get("button_keys")
            raw_columns = raw_row.get("columns")
        elif isinstance(raw_row, list):
            raw_keys = raw_row
            raw_columns = len(raw_row)
        else:
            _error(422, "invalid_layout_row", "Layout row must be an object or list")
        if not isinstance(raw_keys, list):
            _error(422, "invalid_layout_row_keys", "Layout row button_keys must be a list")
        keys: list[str] = []
        for raw_key in raw_keys:
            key = str(raw_key or "").strip()
            if key:
                keys.append(key)
        if not keys:
            continue
        if len(keys) > MAX_LAYOUT_ROW_BUTTONS:
            _error(422, "layout_row_too_wide", "A layout row can contain at most 3 buttons")
        for key in keys:
            if key in seen:
                _error(422, "duplicate_button_key", "Duplicate button_key in layout")
            if key not in allowed_keys:
                _error(422, "unknown_button_key", "button_key is not present in this menu level")
            seen.add(key)
        try:
            columns = int(raw_columns if raw_columns is not None else len(keys))
        except Exception:
            columns = len(keys)
        columns = columns if columns in {1, 2, 3} else len(keys)
        columns = max(1, min(MAX_LAYOUT_ROW_BUTTONS, columns, len(keys)))
        normalized.append({"columns": columns, "button_keys": keys})
    return normalized


def _model_to_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _build_bot_menu_structure(db: Session, menu_name: str) -> BotButtonMenuStructureOut:
    rows = db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)
                      .order_by(BotButtonSetting.updated_at, BotButtonSetting.button_label)).scalars().all()
    result = BotButtonMenuStructureOut(menu_name=menu_name, root_level_key=build_level_layout_key(menu_name, ""),
                                       tree_rows=_build_db_only_tree_rows(db, menu_name))
    groups = {}
    for row in rows:
        if row.workflow_type == "group_task" and row.currently_present and not row.is_archived:
            group = BotButtonGroupOut(label=row.label_override if row.label_overridden and row.label_override else row.button_label,
                                      full_path=row.button_path, technical_key=row.button_key,
                                      button_id=row.id, parent_path=row.parent_path or "")
            groups[row.button_path] = group
            result.group_tasks.append(group)
    for row in rows:
        if row.workflow_type not in {"ticket", "task", "auto", "group_task_child"}:
            continue
        removed = not row.currently_present or bool(row.is_archived)
        leaf = BotButtonStructureLeafOut(
            label=row.label_override if row.label_overridden and row.label_override else row.button_label,
            workflow_type=row.workflow_type, full_path=row.button_path, technical_key=row.button_key,
            source_button_key=row.button_key, has_instruction=bool((row.instruction_text or "").strip()),
            button_id=row.id, parent_path=row.parent_path or "",
            removed=removed, is_enabled=True if not row.visibility_overridden else bool(row.is_enabled),
            stable_button_id=row.stable_button_id,
            is_archived=bool(row.is_archived), status="removed_changed" if removed else "actual",
        )
        if removed:
            result.removed_items.append(leaf)
        elif row.workflow_type == "group_task_child":
            if row.parent_path in groups:
                groups[row.parent_path].children.append(leaf)
        else:
            {"ticket": result.tickets, "task": result.tasks, "auto": result.autos}[row.workflow_type].append(leaf)
    if result.removed_items:
        result.tree_rows.append({"buttons": [{"label": "Удалённые/изменённые", "kind": "removed_folder", "children_rows": [
            {"buttons": [{**_model_to_dict(leaf), "kind": "terminal", "children_rows": []}]} for leaf in result.removed_items
        ]}]})
    return result


def _build_db_only_tree_rows(db: Session, menu_name: str) -> list[dict]:
    now = datetime.utcnow()
    layout_rows_by_level: dict[str, list[dict]] = {}
    for row in db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == menu_name)).scalars().all():
        layout_rows_by_level[row.level_key] = _normalize_level_layout(row.layout_json)
    rows = [
        row
        for row in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all()
        if bool(row.currently_present) and not bool(row.is_archived)
    ]
    by_parent: dict[str, list[BotButtonSetting]] = {}
    for row in rows:
        by_parent.setdefault((row.parent_path or "").strip(), []).append(row)

    def sort_key(row: BotButtonSetting):
        value = row.updated_at or row.created_at or now
        utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return utc_value, row.button_label

    for bucket in by_parent.values():
        bucket.sort(key=sort_key)

    def _effective_label(row: BotButtonSetting) -> str:
        if bool(row.label_overridden):
            text = (row.label_override or "").strip()
            if text:
                return text
        return row.button_label

    def build_rows(parent_path: str) -> list[dict]:
        level_key = build_level_layout_key(menu_name, parent_path)
        result: list[dict] = []
        for row in by_parent.get(parent_path, []):
            kind = (row.button_kind or "terminal").strip().lower()
            workflow = (row.workflow_type or "").strip().lower()
            node: dict = {
                "label": _effective_label(row),
                "kind": "terminal",
                "technical_key": row.button_key,
                "button_id": row.id,
                "parent_path": row.parent_path or "",
                "workflow_type": workflow,
                "full_path": row.button_path,
                "has_instruction": bool((row.instruction_text or "").strip()),
                "stable_button_id": row.stable_button_id,
                "removed": False,
                "status": "actual",
                "is_enabled": (True if not row.visibility_overridden else bool(row.is_enabled)),
                "children_rows": [],
                "is_archived": bool(row.is_archived),
            }
            if kind == "section" and workflow != "group_task":
                node["kind"] = "submenu"
                node["level_key"] = build_level_layout_key(menu_name, row.button_path)
                node["children_rows"] = build_rows(row.button_path)
            elif kind == "group_parent" or workflow == "group_task":
                node["kind"] = "group_task_parent"
                node["workflow_type"] = "group_task"
                node["level_key"] = build_level_layout_key(menu_name, row.button_path)
                node["children_rows"] = build_rows(row.button_path)
            result.append({"buttons": [node]})
        return _apply_level_layout(result, layout_rows_by_level.get(level_key))

    return build_rows("")


@router.get("/bot-buttons/menu-types/{menu_name}/structure", response_model=BotButtonMenuStructureOut)
def get_bot_button_menu_structure(menu_name: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return _build_bot_menu_structure(db, menu_name)


@router.post("/bot-buttons/item/create")
def create_bot_button_item(
    payload: dict,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    menu_type = str(payload.get("menu_type") or "").strip()
    parent_path = str(payload.get("parent_path") or "").strip()
    label = str(payload.get("label") or "").strip()
    kind = str(payload.get("button_kind") or "").strip().lower()
    workflow_type = str(payload.get("workflow_type") or "").strip().lower()
    is_enabled = bool(payload.get("is_enabled", True))

    if not menu_type:
        _error(422, "menu_type_required", "Не указан тип меню")
    if not label:
        _error(422, "label_required", "Название кнопки обязательно")
    if kind not in {"section", "ticket", "task", "auto", "group_task", "group_task_child"}:
        _error(422, "invalid_button_kind", "Недопустимый тип элемента")
    if kind == "section":
        workflow_type = "submenu"
    elif kind == "group_task":
        workflow_type = "group_task"
    elif kind == "group_task_child":
        workflow_type = "group_task_child"
    elif workflow_type not in {"ticket", "task", "auto"}:
        _error(422, "invalid_workflow_type", "Недопустимый workflow_type")

    full_path = f"{parent_path}/{label}" if parent_path else label
    ordinary = workflow_type in {"ticket", "task", "auto"}
    button_key = (build_terminal_button_key(menu_type, workflow_type, full_path) if ordinary
                  else build_visibility_button_key(menu_type, "db", full_path))
    lock_button_keys(db)
    candidates = db.execute(select(BotButtonSetting).where(
        BotButtonSetting.menu_type == menu_type, BotButtonSetting.button_path == full_path,
    )).scalars().all()
    if len(candidates) > 1:
        _error(409, "ambiguous_button_key", "По этому пути найдено несколько кнопок")
    exists = candidates[0] if candidates else None
    if exists is not None and bool(exists.currently_present):
        _error(409, "button_already_exists", "Кнопка с таким названием уже существует на этом уровне")

    now = datetime.utcnow()
    if exists is None:
        row = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=str(uuid.uuid4()),
            button_key=button_key,
            legacy_button_key=None,
            menu_type=menu_type,
            workflow_type=workflow_type,
            button_path=full_path,
            button_label=label,
            label_override=None,
            label_overridden=False,
            currently_present=True,
            button_kind="section" if kind == "section" else ("group_parent" if kind == "group_task" else "terminal"),
            parent_path=parent_path,
            is_archived=False,
            deleted_at=None,
            deleted_by_user_id=None,
            is_enabled=is_enabled,
            visibility_overridden=True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        if ordinary and exists.workflow_type in {"ticket", "task", "auto"} and exists.workflow_type != workflow_type:
            change_button_workflow_key(db, exists, workflow_type)
        exists.menu_type = menu_type
        exists.workflow_type = workflow_type
        exists.button_path = full_path
        exists.button_label = label
        exists.currently_present = True
        exists.is_archived = False
        exists.deleted_at = None
        exists.deleted_by_user_id = None
        exists.button_kind = "section" if kind == "section" else ("group_parent" if kind == "group_task" else "terminal")
        exists.parent_path = parent_path
        exists.is_enabled = is_enabled
        exists.visibility_overridden = True
        if not exists.stable_button_id:
            exists.stable_button_id = str(uuid.uuid4())
        exists.updated_at = now
        row = exists

    check_button_key_collisions(db, row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _error(409, "button_key_collision", "Ключ кнопки уже занят")
    return {
        "ok": True,
        "button_key": row.button_key,
        "stable_button_id": row.stable_button_id,
        "menu_type": row.menu_type,
        "parent_path": row.parent_path,
        "button_path": row.button_path,
        "workflow_type": row.workflow_type,
        "button_kind": row.button_kind,
        "is_enabled": bool(row.is_enabled),
    }


@router.get("/bot-buttons/layout")
def get_bot_button_level_layout(
    menu_type: str,
    level_key: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(
        select(BotMenuLevelLayout).where(
            BotMenuLevelLayout.menu_type == menu_type,
            BotMenuLevelLayout.level_key == level_key,
        )
    ).scalar_one_or_none()
    return {
        "menu_type": menu_type,
        "level_key": level_key,
        "rows": _normalize_level_layout(row.layout_json if row is not None else []),
        "has_layout": row is not None,
    }


@router.post("/bot-buttons/item/move")
def move_bot_button_item(
    payload: BotButtonMovePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)

    def load_layout(menu, level_key):
        rows = _rows_for_level_key(db, menu, level_key)
        allowed = _level_button_keys(rows)
        return _normalize_layout_payload_rows([
            {"columns": item.get("columns"), "button_keys": [node["technical_key"]
             for node in item.get("buttons", []) if node.get("technical_key") in allowed]}
            for item in rows], allowed)

    try:
        row = move_button_between_levels(db, payload, load_layout)
        db.flush()
        structure = _model_to_dict(_build_bot_menu_structure(db, payload.menu_type))
        db.commit()
    except IntegrityError:
        db.rollback()
        _error(409, "button_move_conflict", "Button move conflicts with existing data")
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "button_id": row.id, "button_key": row.button_key,
            "stable_button_id": row.stable_button_id, "parent_path": row.parent_path,
            "button_path": row.button_path, "structure": structure}


@router.put("/bot-buttons/layout")
def save_bot_button_level_layout(
    menu_type: str,
    level_key: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    lock_button_keys(db)
    if not menu_type.strip():
        _error(422, "menu_type_required", "menu_type is required")
    if not level_key.strip():
        _error(422, "level_key_required", "level_key is required")
    level_rows = _rows_for_level_key(db, menu_type, level_key)
    allowed_keys = _level_button_keys(level_rows)
    rows = _normalize_layout_payload_rows(payload.get("rows"), allowed_keys)
    now = datetime.utcnow()
    row = db.execute(
        select(BotMenuLevelLayout).where(
            BotMenuLevelLayout.menu_type == menu_type,
            BotMenuLevelLayout.level_key == level_key,
        )
    ).scalar_one_or_none()
    if row is None:
        row = BotMenuLevelLayout(
            id=str(uuid.uuid4()),
            menu_type=menu_type,
            level_key=level_key,
            layout_json=rows,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.layout_json = rows
        row.updated_at = now
    db.commit()
    structure = _build_bot_menu_structure(db, menu_type)
    return {"ok": True, "menu_type": menu_type, "level_key": level_key, "rows": rows, "structure": _model_to_dict(structure)}
