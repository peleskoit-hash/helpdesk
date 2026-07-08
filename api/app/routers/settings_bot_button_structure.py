import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin
from ..db import get_db
from ..models import BotButtonSetting, BotMenuLevelLayout
from ..schemas import BotButtonGroupOut, BotButtonMenuStructureOut, BotButtonStructureLeafOut
from ..services.button_config import (
    BUTTON_CONFIG_DIR,
    available_menu_names,
    build_level_layout_key,
    build_terminal_button_key,
    build_visibility_button_key,
    find_button,
    list_terminal_buttons,
    load_button_config,
)


router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
EDITABLE_WORKFLOW_TYPES = {"ticket", "task", "auto"}
MAX_LAYOUT_ROW_BUTTONS = 3


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _require_admin(user: CurrentUser) -> None:
    if not is_admin(user):
        _error(403, "admin_only", "????????? ???????? ?????? ??????????????")


def _normalize_workflow_type(value: object) -> str:
    return str(value or "").strip().lower()


def _effective_registry_workflow_type(row: BotButtonSetting | None, fallback_workflow_type: str) -> str:
    fallback = _normalize_workflow_type(fallback_workflow_type)
    if row is None:
        return fallback
    current = _normalize_workflow_type(row.workflow_type)
    kind = str(row.button_kind or "terminal").strip().lower()
    if kind == "terminal" and current in EDITABLE_WORKFLOW_TYPES and fallback in EDITABLE_WORKFLOW_TYPES:
        return current
    return fallback or current


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
    try:
        config = load_button_config(menu_name)
        refs = list_terminal_buttons(menu_name)
        parent_map_by_source = _collect_source_parent_paths(config)
    except FileNotFoundError:
        config = None
        refs = []
        parent_map_by_source = {}
    now = datetime.utcnow()
    layout_rows_by_level: dict[str, list[dict]] = {}
    for row in db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == menu_name)).scalars().all():
        layout_rows_by_level[row.level_key] = _normalize_level_layout(row.layout_json)
    settings_rows = {
        row.button_key: row
        for row in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all()
    }
    db_created_rows = [
        row
        for row in settings_rows.values()
        if bool(row.is_db_created) and bool(row.currently_present) and not bool(row.is_archived)
    ]
    db_rows_by_parent: dict[str, list[BotButtonSetting]] = {}
    for row in db_created_rows:
        parent = (row.parent_path or "").strip()
        db_rows_by_parent.setdefault(parent, []).append(row)
    for bucket in db_rows_by_parent.values():
        bucket.sort(key=lambda r: ((r.updated_at or r.created_at or now), r.button_label))

    def _effective_label(row: BotButtonSetting | None, fallback: str) -> str:
        if row is not None and bool(row.label_overridden):
            text = (row.label_override or "").strip()
            if text:
                return text
        return fallback

    def ensure_registry_row(
        button_key: str,
        workflow_type: str,
        button_path: str,
        button_label: str,
        *,
        button_kind: str = "terminal",
        parent_path: str = "",
        is_db_created: bool = False,
    ) -> BotButtonSetting:
        row = settings_rows.get(button_key)
        effective_workflow_type = _effective_registry_workflow_type(row, workflow_type)
        if row is None:
            stable_id = str(uuid.uuid4())
            row = BotButtonSetting(
                id=str(uuid.uuid4()),
                stable_button_id=stable_id,
                button_key=button_key,
                legacy_button_key=None,
                menu_type=menu_name,
                workflow_type=effective_workflow_type,
                button_path=button_path,
                button_label=button_label,
                label_override=None,
                label_overridden=False,
                currently_present=True,
                is_db_created=is_db_created,
                button_kind=button_kind,
                parent_path=parent_path,
                is_archived=False,
                deleted_at=None,
                deleted_by_user_id=None,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            settings_rows[button_key] = row
            return row
        changed = False
        if not row.stable_button_id:
            row.stable_button_id = str(uuid.uuid4())
            changed = True
        if row.menu_type != menu_name:
            row.menu_type = menu_name
            changed = True
        if row.workflow_type != effective_workflow_type:
            row.workflow_type = effective_workflow_type
            changed = True
        if row.button_path != button_path:
            row.button_path = button_path
            changed = True
        if row.button_label != button_label and not bool(row.label_overridden):
            row.button_label = button_label
            changed = True
        if not row.currently_present and not bool(row.is_archived):
            row.currently_present = True
            changed = True
        if bool(row.is_archived) and bool(is_db_created):
            row.is_archived = False
            row.deleted_at = None
            row.deleted_by_user_id = None
            changed = True
        if bool(row.is_db_created) != bool(is_db_created):
            row.is_db_created = bool(is_db_created)
            changed = True
        if (row.button_kind or "terminal") != button_kind:
            row.button_kind = button_kind
            changed = True
        if (row.parent_path or "") != parent_path:
            row.parent_path = parent_path
            changed = True
        if changed:
            row.updated_at = now
        return row
    tickets: list[BotButtonStructureLeafOut] = []
    tasks: list[BotButtonStructureLeafOut] = []
    autos: list[BotButtonStructureLeafOut] = []
    group_tasks: list[BotButtonGroupOut] = []
    group_map: dict[str, BotButtonGroupOut] = {}

    for ref in refs:
        setting_row = ensure_registry_row(
            button_key=ref.technical_key,
            workflow_type=ref.workflow_type,
            button_path=ref.full_path,
            button_label=ref.label,
            button_kind="terminal",
            parent_path=parent_map_by_source.get(ref.source_button_key, (ref.full_path.rsplit("/", 1)[0] if "/" in ref.full_path else "")),
            is_db_created=False,
        )
        has_instruction = bool((setting_row.instruction_text if setting_row else "") or "")
        is_enabled = True if setting_row is None or not setting_row.visibility_overridden else bool(setting_row.is_enabled)
        leaf = BotButtonStructureLeafOut(
            label=_effective_label(setting_row, ref.label),
            workflow_type=_effective_registry_workflow_type(setting_row, ref.workflow_type),  # type: ignore[arg-type]
            full_path=ref.full_path,
            technical_key=ref.technical_key,
            source_button_key=ref.source_button_key,
            has_instruction=has_instruction,
            is_enabled=is_enabled,
            removed=False,
            stable_button_id=setting_row.stable_button_id,
            status="actual",
        )
        if ref.workflow_type == "ticket":
            tickets.append(leaf)
        elif ref.workflow_type == "task":
            tasks.append(leaf)
        elif ref.workflow_type == "auto":
            autos.append(leaf)
        else:
            parent_label = ref.full_path.split("/", 1)[0]
            group = group_map.get(parent_label)
            if group is None:
                group = BotButtonGroupOut(
                    label=parent_label,
                    full_path=parent_label,
                    technical_key=build_terminal_button_key(menu_name, "group_task", parent_label),
                    children=[],
                )
                group_map[parent_label] = group
                group_tasks.append(group)
            group.children.append(leaf)

    for row in db_created_rows:
        if (row.button_kind or "").strip().lower() == "section":
            continue
        wf = (row.workflow_type or "").strip().lower()
        if wf not in {"ticket", "task", "auto"}:
            continue
        leaf = BotButtonStructureLeafOut(
            label=_effective_label(row, row.button_label),
            workflow_type=wf,  # type: ignore[arg-type]
            full_path=row.button_path,
            technical_key=row.button_key,
            source_button_key=row.button_key,
            has_instruction=bool((row.instruction_text or "").strip()),
            is_enabled=(True if not row.visibility_overridden else bool(row.is_enabled)),
            removed=False,
            stable_button_id=row.stable_button_id,
            status="actual",
        )
        if wf == "ticket":
            tickets.append(leaf)
        elif wf == "task":
            tasks.append(leaf)
        else:
            autos.append(leaf)

    current_keys: set[str] = set()

    terminal_by_source: dict[str, BotButtonStructureLeafOut] = {}
    for leaf in [*tickets, *tasks, *autos]:
        terminal_by_source[leaf.source_button_key] = leaf
    for group in group_tasks:
        for leaf in group.children:
            terminal_by_source[leaf.source_button_key] = leaf

    def leaf_node(leaf: BotButtonStructureLeafOut) -> dict:
        current_keys.add(leaf.technical_key)
        return {
            "label": leaf.label,
            "kind": "terminal",
            "technical_key": leaf.technical_key,
            "level_key": None,
            "workflow_type": leaf.workflow_type,
            "full_path": leaf.full_path,
            "has_instruction": bool(leaf.has_instruction),
            "removed": bool(leaf.removed),
            "stable_button_id": leaf.stable_button_id,
            "status": leaf.status,
            "is_enabled": bool(getattr(leaf, "is_enabled", True)),
            "children_rows": [],
            "is_db_created": bool(getattr(leaf, "is_db_created", False)),
            "is_archived": bool(getattr(leaf, "is_archived", False)),
        }

    def build_rows_from_menu(menu_key: str | None, parent_path: str = "") -> list[dict]:
        menu = config.menus.get(menu_key) if menu_key else None
        current_level_key = build_level_layout_key(menu_name, parent_path)
        rows: list[dict] = []
        if menu is not None:
            for row in menu.rows:
                buttons: list[dict] = []
                for button_key in row:
                    button = config.buttons.get(button_key)
                    if button is None:
                        continue
                    if button.kind == "submenu" and button.submenu_key:
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        submenu_key = build_visibility_button_key(menu_name, "submenu", full_path)
                        submenu_row = ensure_registry_row(
                            button_key=submenu_key,
                            workflow_type="submenu",
                            button_path=full_path,
                            button_label=button.label,
                            button_kind="section",
                            parent_path=parent_path,
                            is_db_created=False,
                        )
                        if not bool(submenu_row.currently_present) or bool(submenu_row.is_archived):
                            continue
                        current_keys.add(submenu_key)
                        submenu_enabled = True if submenu_row is None or not submenu_row.visibility_overridden else bool(submenu_row.is_enabled)
                        buttons.append(
                            {
                                "label": _effective_label(submenu_row, button.label),
                                "kind": "submenu",
                                "technical_key": submenu_key,
                                "level_key": build_level_layout_key(menu_name, full_path),
                                "workflow_type": "submenu",
                                "full_path": full_path,
                                "is_enabled": submenu_enabled,
                                "children_rows": build_rows_from_menu(button.submenu_key, full_path),
                                "is_db_created": False,
                                "is_archived": False,
                            }
                        )
                        continue
                    if button.kind == "group_task":
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        group_key = build_visibility_button_key(menu_name, "group_task", full_path)
                        group_row = ensure_registry_row(
                            button_key=group_key,
                            workflow_type="group_task",
                            button_path=full_path,
                            button_label=button.label,
                            button_kind="section",
                            parent_path=parent_path,
                            is_db_created=False,
                        )
                        if not bool(group_row.currently_present) or bool(group_row.is_archived):
                            continue
                        current_keys.add(group_key)
                        group_enabled = True if group_row is None or not group_row.visibility_overridden else bool(group_row.is_enabled)
                        child_rows = [{"buttons": [leaf_node(leaf)]} for leaf in (group_map.get(button.label).children if group_map.get(button.label) else [])]
                        child_rows = _apply_level_layout(child_rows, layout_rows_by_level.get(build_level_layout_key(menu_name, full_path)))
                        buttons.append(
                            {
                                "label": _effective_label(group_row, button.label),
                                "kind": "group_task_parent",
                                "technical_key": group_key,
                                "level_key": build_level_layout_key(menu_name, full_path),
                                "workflow_type": "group_task",
                                "full_path": full_path,
                                "is_enabled": group_enabled,
                                "children_rows": child_rows,
                                "is_db_created": False,
                                "is_archived": False,
                            }
                        )
                        continue
                    leaf = terminal_by_source.get(button.key)
                    if leaf is not None:
                        buttons.append(leaf_node(leaf))
                if buttons:
                    rows.append({"buttons": buttons})
        for db_row in db_rows_by_parent.get(parent_path, []):
            key = db_row.button_key
            current_keys.add(key)
            kind = (db_row.button_kind or "").strip().lower()
            enabled = True if not db_row.visibility_overridden else bool(db_row.is_enabled)
            if kind == "section":
                full_path = db_row.button_path
                rows.append(
                    {
                        "buttons": [
                            {
                                "label": _effective_label(db_row, db_row.button_label),
                                "kind": "submenu",
                                "technical_key": key,
                                "level_key": build_level_layout_key(menu_name, full_path),
                                "workflow_type": "submenu",
                                "full_path": full_path,
                                "is_enabled": enabled,
                                "children_rows": build_rows_from_menu(None, full_path),
                                "is_db_created": True,
                                "is_archived": bool(db_row.is_archived),
                            }
                        ]
                    }
                )
                continue
            if kind == "group_parent":
                full_path = db_row.button_path
                rows.append(
                    {
                        "buttons": [
                            {
                                "label": _effective_label(db_row, db_row.button_label),
                                "kind": "group_task_parent",
                                "technical_key": key,
                                "level_key": build_level_layout_key(menu_name, full_path),
                                "workflow_type": "group_task",
                                "full_path": full_path,
                                "is_enabled": enabled,
                                "children_rows": build_rows_from_menu(None, full_path),
                                "is_db_created": True,
                                "is_archived": bool(db_row.is_archived),
                            }
                        ]
                    }
                )
                continue
            rows.append(
                {
                    "buttons": [
                        {
                            "label": _effective_label(db_row, db_row.button_label),
                            "kind": "terminal",
                            "technical_key": key,
                            "level_key": None,
                            "workflow_type": db_row.workflow_type,
                            "full_path": db_row.button_path,
                            "has_instruction": bool((db_row.instruction_text or "").strip()),
                            "removed": False,
                            "stable_button_id": db_row.stable_button_id,
                            "status": "actual",
                            "is_enabled": enabled,
                            "children_rows": [],
                            "is_db_created": True,
                            "is_archived": bool(db_row.is_archived),
                        }
                    ]
                }
            )
        return _apply_level_layout(rows, layout_rows_by_level.get(current_level_key))

    tree_rows = build_rows_from_menu(config.root_menu_key if config is not None else None, "")
    removed_items: list[BotButtonStructureLeafOut] = []
    allowed_removed_workflows = {"ticket", "task", "auto", "group_task_child"}
    saved_rows = db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all()
    for row in saved_rows:
        if not row.stable_button_id:
            row.stable_button_id = str(uuid.uuid4())
            row.updated_at = now
        is_present_now = row.button_key in current_keys
        if bool(row.currently_present) != bool(is_present_now):
            row.currently_present = bool(is_present_now)
            row.updated_at = now
        if is_present_now:
            continue
        has_any_settings = any(
            [
                bool((row.instruction_text or "").strip()),
                bool((row.system_text or "").strip()),
                bool(row.system_text_overridden),
                bool(row.files_overridden),
                bool(row.conditional_files_overridden),
                bool(row.fixed_assignee_enabled),
                bool(row.fixed_assignee_user_id),
                bool(row.freeze_on_create),
                bool(row.confirmation_enabled),
                bool((row.confirmation_text or "").strip()),
                bool(row.confirmation_overridden),
                bool(row.visibility_overridden),
                bool(row.is_enabled is False),
            ]
        )
        if not has_any_settings and not (bool(row.is_db_created) and bool(row.is_archived)):
            continue
        if (row.workflow_type or "").strip() not in allowed_removed_workflows:
            # Sections/group parents are not terminal buttons; skip them in removed leaf list.
            continue
        removed_leaf = BotButtonStructureLeafOut(
            label=_effective_label(row, row.button_label),
            workflow_type=row.workflow_type,  # type: ignore[arg-type]
            full_path=row.button_path,
            technical_key=row.button_key,
            source_button_key="",
            has_instruction=bool((row.instruction_text or "").strip()),
            removed=True,
            stable_button_id=row.stable_button_id,
            is_db_created=bool(row.is_db_created),
            is_archived=bool(row.is_archived),
            status="removed_changed",
        )
        removed_items.append(removed_leaf)
    db.commit()
    if removed_items:
        tree_rows.append(
            {
                "buttons": [
                    {
                        "label": "Удалённые/изменённые",
                        "kind": "removed_folder",
                        "children_rows": [{"buttons": [leaf_node(leaf)]} for leaf in removed_items],
                    }
                ]
            }
        )

    return BotButtonMenuStructureOut(
        menu_name=menu_name,
        ok=True,
        root_level_key=build_level_layout_key(menu_name, ""),
        tickets=tickets,
        tasks=tasks,
        autos=autos,
        group_tasks=group_tasks,
        removed_items=removed_items,
        tree_rows=tree_rows,
    )


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
    for bucket in by_parent.values():
        bucket.sort(key=lambda r: ((r.updated_at or r.created_at or now), r.button_label))

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
                "workflow_type": workflow,
                "full_path": row.button_path,
                "is_enabled": (True if not row.visibility_overridden else bool(row.is_enabled)),
                "children_rows": [],
                "is_db_created": bool(row.is_db_created),
                "is_archived": bool(row.is_archived),
            }
            if kind == "section":
                node["kind"] = "submenu"
                node["level_key"] = build_level_layout_key(menu_name, row.button_path)
                node["children_rows"] = build_rows(row.button_path)
            elif kind == "group_parent":
                node["kind"] = "group_task_parent"
                node["workflow_type"] = "group_task"
                node["level_key"] = build_level_layout_key(menu_name, row.button_path)
                node["children_rows"] = build_rows(row.button_path)
            result.append({"buttons": [node]})
        return _apply_level_layout(result, layout_rows_by_level.get(level_key))

    return build_rows("")


def _flatten_rows_for_compare(rows: list[dict]) -> tuple[list[str], dict[str, int]]:
    flat: list[str] = []
    counters = {
        "sections": 0,
        "terminals": 0,
        "rows": 0,
        "disabled": 0,
        "ticket": 0,
        "task": 0,
        "auto": 0,
        "group_task": 0,
        "group_task_child": 0,
    }

    def walk(level_rows: list[dict], prefix: str = ""):
        for r_idx, row in enumerate(level_rows or []):
            buttons = row.get("buttons") or []
            if buttons:
                counters["rows"] += 1
            for b_idx, node in enumerate(buttons):
                kind = str(node.get("kind") or "terminal")
                workflow = str(node.get("workflow_type") or "")
                key = str(node.get("technical_key") or "")
                enabled = bool(node.get("is_enabled", True))
                label = str(node.get("label") or "")
                sig = f"{prefix}{r_idx}:{b_idx}|{kind}|{workflow}|{key}|{1 if enabled else 0}|{label}"
                flat.append(sig)
                if not enabled:
                    counters["disabled"] += 1
                if kind in {"submenu", "group_task_parent"}:
                    counters["sections"] += 1
                else:
                    counters["terminals"] += 1
                if workflow in counters:
                    counters[workflow] += 1
                child_rows = node.get("children_rows") or []
                if child_rows:
                    walk(child_rows, prefix=f"{prefix}{r_idx}:{b_idx}/")

    walk(rows or [])
    return flat, counters


@router.post("/bot-buttons/db-only-readiness")
def check_db_only_readiness(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    menu_types = {name for name in available_menu_names() if name}
    db_menu_types = db.execute(select(BotButtonSetting.menu_type).distinct()).all()
    for row in db_menu_types:
        if row and row[0]:
            menu_types.add(str(row[0]))
    menu_types = sorted(menu_types)
    results: list[dict] = []
    ok_all = True
    for menu_name in menu_types:
        try:
            current = _build_bot_menu_structure(db, menu_name)
            db_only_rows = _build_db_only_tree_rows(db, menu_name)
            current_rows = [r for r in (current.tree_rows or []) if ((r.get("buttons") or [{}])[0].get("kind") != "removed_folder")]
            current_flat, current_counts = _flatten_rows_for_compare(current_rows)
            db_flat, db_counts = _flatten_rows_for_compare(db_only_rows)

            missing_in_db = [x for x in current_flat if x not in set(db_flat)]
            extra_in_db = [x for x in db_flat if x not in set(current_flat)]
            order_same = current_flat == db_flat
            row_count_same = current_counts["rows"] == db_counts["rows"]
            has_diff = bool(missing_in_db or extra_in_db or not order_same or not row_count_same)
            if has_diff:
                ok_all = False
            results.append(
                {
                    "menu_name": menu_name,
                    "ok": not has_diff,
                    "current_counts": current_counts,
                    "db_counts": db_counts,
                    "order_same": order_same,
                    "row_count_same": row_count_same,
                    "missing_in_db": missing_in_db[:50],
                    "extra_in_db": extra_in_db[:50],
                }
            )
        except Exception as exc:  # pragma: no cover
            ok_all = False
            results.append(
                {
                    "menu_name": menu_name,
                    "ok": False,
                    "error": str(exc),
                }
            )
    return {
        "ok": ok_all,
        "checked_menus": len(results),
        "results": results,
    }


@router.get("/bot-buttons/menu-types/{menu_name}/structure", response_model=BotButtonMenuStructureOut)
def get_bot_button_menu_structure(
    menu_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    try:
        return _build_bot_menu_structure(db, menu_name)
    except FileNotFoundError:
        return BotButtonMenuStructureOut(
            menu_name=menu_name,
            ok=False,
            root_level_key=build_level_layout_key(menu_name, ""),
            error=f"Файл не найден: {BUTTON_CONFIG_DIR / f'{menu_name}.txt'}",
        )
    except Exception as exc:  # pragma: no cover
        return BotButtonMenuStructureOut(
            menu_name=menu_name,
            ok=False,
            root_level_key=build_level_layout_key(menu_name, ""),
            error=f"Ошибка парсинга: {exc}",
        )


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
        _error(422, "label_required", "???????? ???? ???????????")
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
    button_key = build_visibility_button_key(menu_type, "db", full_path)
    exists = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
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
            is_db_created=True,
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
        exists.menu_type = menu_type
        exists.workflow_type = workflow_type
        exists.button_path = full_path
        exists.button_label = label
        exists.currently_present = True
        exists.is_archived = False
        exists.deleted_at = None
        exists.deleted_by_user_id = None
        exists.is_db_created = True
        exists.button_kind = "section" if kind == "section" else ("group_parent" if kind == "group_task" else "terminal")
        exists.parent_path = parent_path
        exists.is_enabled = is_enabled
        exists.visibility_overridden = True
        if not exists.stable_button_id:
            exists.stable_button_id = str(uuid.uuid4())
        exists.updated_at = now
        row = exists

    db.commit()
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


@router.put("/bot-buttons/layout")
def save_bot_button_level_layout(
    menu_type: str,
    level_key: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
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


def _normalize_txt_file_rules(button) -> list[dict]:
    rules: list[dict] = []
    for raw in (button.file_rules or ()):
        field = str((raw or {}).get("field") or "").strip()
        equals = str((raw or {}).get("equals") or "").strip()
        file_name = _normalize_relative_button_file_path(str((raw or {}).get("file") or "").strip())
        if not field or not file_name:
            continue
        rules.append({"conditions": [{"field": field, "operator": "eq", "value": equals}], "files": [file_name]})
    return rules


def _collect_txt_level_layouts(menu_name: str, config, refs_by_source: dict[str, object]) -> dict[str, list[dict]]:
    layouts: dict[str, list[dict]] = {}

    def walk(menu_key: str | None, parent_path: str = "") -> None:
        menu = config.menus.get(menu_key) if menu_key else None
        if menu is None:
            return
        level_key = build_level_layout_key(menu_name, parent_path)
        level_rows: list[dict] = []
        for row in menu.rows:
            row_keys: list[str] = []
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                if button.kind == "submenu" and button.submenu_key:
                    full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                    key = build_visibility_button_key(menu_name, "submenu", full_path)
                    row_keys.append(key)
                    walk(button.submenu_key, full_path)
                    continue
                if button.kind == "group_task":
                    full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                    key = build_visibility_button_key(menu_name, "group_task", full_path)
                    row_keys.append(key)
                    child_level_key = build_level_layout_key(menu_name, full_path)
                    child_rows: list[dict] = []
                    for child in (button.subtasks or ()):
                        child_ref = refs_by_source.get(child.key)
                        if child_ref is None:
                            continue
                        child_rows.append({"columns": 1, "button_keys": [child_ref.technical_key]})
                    if child_rows:
                        layouts[child_level_key] = child_rows
                    continue
                ref = refs_by_source.get(button.key)
                if ref is not None:
                    row_keys.append(ref.technical_key)
            if row_keys:
                level_rows.append({
                    "columns": max(1, min(MAX_LAYOUT_ROW_BUTTONS, len(row_keys))),
                    "button_keys": row_keys[:MAX_LAYOUT_ROW_BUTTONS],
                })
        layouts[level_key] = level_rows

    walk(config.root_menu_key, "")
    return layouts


def _collect_source_parent_paths(config) -> dict[str, str]:
    parent_map: dict[str, str] = {}

    def walk(menu_key: str | None, parent_path: str = "") -> None:
        menu = config.menus.get(menu_key) if menu_key else None
        if menu is None:
            return
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                if button.kind == "submenu" and button.submenu_key:
                    next_path = f"{parent_path}/{button.label}" if parent_path else button.label
                    walk(button.submenu_key, next_path)
                    continue
                if button.kind == "group_task":
                    group_path = f"{parent_path}/{button.label}" if parent_path else button.label
                    for child in (button.subtasks or ()):
                        parent_map[child.key] = group_path
                    continue
                parent_map[button.key] = parent_path

    walk(config.root_menu_key, "")
    return parent_map


def _sync_bot_buttons_from_txt(
    dry_run: bool = True,
    db: Session | None = None,
    user: CurrentUser | None = None,
):
    if db is None or user is None:
        _error(500, "sync_context_missing", "Контекст синхронизации не инициализирован")
    _require_admin(user)
    now = datetime.utcnow()
    created = 0
    updated = 0
    unchanged = 0
    layouts_created = 0
    layouts_skipped_existing = 0
    errors: list[str] = []
    parsed_menus = 0
    present_keys: set[str] = set()
    touched_menu_types: set[str] = set()

    for menu_name in available_menu_names():
        try:
            config = load_button_config(menu_name)
            refs = list_terminal_buttons(menu_name)
            refs_by_source = {ref.source_button_key: ref for ref in refs}
            parent_map_by_source = _collect_source_parent_paths(config)
            parsed_menus += 1
            touched_menu_types.add(menu_name)
        except Exception as exc:  # pragma: no cover
            errors.append(f"{menu_name}: {exc}")
            continue

        # structural import of terminal buttons
        for ref in refs:
            present_keys.add(ref.technical_key)
            button = find_button(ref.source_button_key)
            txt_system_text = (button.system_text or "").strip() if button is not None else ""
            txt_client_text = (button.text or "").strip() if button is not None else ""
            txt_confirmation = (button.confirmation_text or "").strip() if button is not None else ""
            txt_file = _normalize_relative_button_file_path((button.file_path or "").strip()) if button is not None else None
            txt_files = [txt_file] if txt_file else []
            txt_conditional = _normalize_txt_file_rules(button) if button is not None else []
            row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == ref.technical_key)).scalar_one_or_none()
            if row is None:
                created += 1
                if dry_run:
                    continue
                forced_enabled = bool(button.forced_assignee_type and button.forced_assignee_value) if button is not None else False
                forced_value = (button.forced_assignee_value or "").strip() if button is not None else ""
                db.add(
                    BotButtonSetting(
                        id=str(uuid.uuid4()),
                        stable_button_id=str(uuid.uuid4()),
                        button_key=ref.technical_key,
                        legacy_button_key=None,
                        menu_type=menu_name,
                        workflow_type=ref.workflow_type,
                        button_path=ref.full_path,
                        button_label=ref.label,
                        label_override=None,
                        label_overridden=False,
                        currently_present=True,
                        is_archived=False,
                        deleted_at=None,
                        deleted_by_user_id=None,
                        instruction_text=None,  # deliberately ignore txt instruction
                        client_text=(txt_client_text or None),
                        client_text_overridden=False,
                        system_text=(txt_system_text or None),
                        system_text_overridden=False,
                        fixed_assignee_enabled=forced_enabled,
                        fixed_assignee_user_id=(forced_value or None) if forced_enabled else None,
                        freeze_on_create=False,
                        confirmation_enabled=bool(txt_confirmation),
                        confirmation_text=(txt_confirmation or None),
                        confirmation_overridden=False,
                        files_overridden=False,
                        files_json=txt_files if txt_files else None,
                        conditional_files_overridden=False,
                        conditional_files_json=txt_conditional if txt_conditional else None,
                        is_enabled=True,
                        visibility_overridden=False,
                        is_db_created=False,
                        button_kind="terminal",
                        parent_path=parent_map_by_source.get(ref.source_button_key, (ref.full_path.rsplit("/", 1)[0] if "/" in ref.full_path else "")),
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue

            changed = False
            effective_workflow_type = _effective_registry_workflow_type(row, ref.workflow_type)
            if row.menu_type != menu_name:
                row.menu_type = menu_name
                changed = True
            if row.workflow_type != effective_workflow_type:
                row.workflow_type = effective_workflow_type
                changed = True
            if row.button_path != ref.full_path:
                row.button_path = ref.full_path
                changed = True
            if row.button_label != ref.label and not bool(row.label_overridden):
                row.button_label = ref.label
                changed = True
            if not row.currently_present:
                row.currently_present = True
                changed = True
            if bool(row.is_archived):
                row.is_archived = False
                row.deleted_at = None
                row.deleted_by_user_id = None
                changed = True
            if bool(row.is_db_created):
                row.is_db_created = False
                changed = True
            if (row.button_kind or "terminal") != "terminal":
                row.button_kind = "terminal"
                changed = True
            parent_path = parent_map_by_source.get(ref.source_button_key, (ref.full_path.rsplit("/", 1)[0] if "/" in ref.full_path else ""))
            if (row.parent_path or "") != parent_path:
                row.parent_path = parent_path
                changed = True

            if not bool(row.system_text_overridden):
                next_system = txt_system_text or None
                if (row.system_text or None) != next_system:
                    row.system_text = next_system
                    changed = True
            if not bool(row.client_text_overridden):
                next_client = txt_client_text or None
                if (row.client_text or None) != next_client:
                    row.client_text = next_client
                    changed = True
            if not bool(row.confirmation_overridden):
                next_confirmation = txt_confirmation or None
                next_confirmation_enabled = bool(txt_confirmation)
                if bool(row.confirmation_enabled) != next_confirmation_enabled:
                    row.confirmation_enabled = next_confirmation_enabled
                    changed = True
                if (row.confirmation_text or None) != next_confirmation:
                    row.confirmation_text = next_confirmation
                    changed = True
            if not bool(row.files_overridden):
                next_files = txt_files if txt_files else None
                if (row.files_json if isinstance(row.files_json, list) else None) != next_files:
                    row.files_json = next_files
                    changed = True
            if not bool(row.conditional_files_overridden):
                next_cond = txt_conditional if txt_conditional else None
                if (row.conditional_files_json if isinstance(row.conditional_files_json, list) else None) != next_cond:
                    row.conditional_files_json = next_cond
                    changed = True
            if changed:
                updated += 1
                if not dry_run:
                    row.updated_at = now
            else:
                unchanged += 1

        # import structural rows for sections/group parents
        for menu in config.menus.values():
            parent_path = ""
            level_key = build_level_layout_key(menu_name, parent_path)
            _ = level_key
        # reuse structure walk similar to builder
        def walk_structure(menu_key: str | None, parent_path: str = "") -> None:
            nonlocal created, updated, unchanged
            menu = config.menus.get(menu_key) if menu_key else None
            if menu is None:
                return
            for row_keys in menu.rows:
                for b_key in row_keys:
                    button = config.buttons.get(b_key)
                    if button is None:
                        continue
                    if button.kind == "submenu" and button.submenu_key:
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        key = build_visibility_button_key(menu_name, "submenu", full_path)
                        present_keys.add(key)
                        s_row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
                        if s_row is None:
                            created_local = True
                            if not dry_run:
                                db.add(
                                    BotButtonSetting(
                                        id=str(uuid.uuid4()),
                                        stable_button_id=str(uuid.uuid4()),
                                        button_key=key,
                                        legacy_button_key=None,
                                        menu_type=menu_name,
                                        workflow_type="submenu",
                                        button_path=full_path,
                                        button_label=button.label,
                                        label_override=None,
                                        label_overridden=False,
                                        currently_present=True,
                                        is_archived=False,
                                        deleted_at=None,
                                        deleted_by_user_id=None,
                                        instruction_text=None,
                                        client_text=None,
                                        client_text_overridden=False,
                                        system_text=None,
                                        system_text_overridden=False,
                                        fixed_assignee_enabled=False,
                                        fixed_assignee_user_id=None,
                                        freeze_on_create=False,
                                        confirmation_enabled=False,
                                        confirmation_text=None,
                                        confirmation_overridden=False,
                                        files_overridden=False,
                                        files_json=None,
                                        conditional_files_overridden=False,
                                        conditional_files_json=None,
                                        is_enabled=True,
                                        visibility_overridden=False,
                                        is_db_created=False,
                                        button_kind="section",
                                        parent_path=parent_path,
                                        created_at=now,
                                        updated_at=now,
                                    )
                                )
                            created += 1
                        else:
                            changed_local = False
                            if s_row.menu_type != menu_name:
                                s_row.menu_type = menu_name
                                changed_local = True
                            if s_row.workflow_type != "submenu":
                                s_row.workflow_type = "submenu"
                                changed_local = True
                            if s_row.button_path != full_path:
                                s_row.button_path = full_path
                                changed_local = True
                            if s_row.button_label != button.label and not bool(s_row.label_overridden):
                                s_row.button_label = button.label
                                changed_local = True
                            if not s_row.currently_present:
                                s_row.currently_present = True
                                changed_local = True
                            if bool(s_row.is_archived):
                                s_row.is_archived = False
                                s_row.deleted_at = None
                                s_row.deleted_by_user_id = None
                                changed_local = True
                            if bool(s_row.is_db_created):
                                s_row.is_db_created = False
                                changed_local = True
                            if (s_row.button_kind or "section") != "section":
                                s_row.button_kind = "section"
                                changed_local = True
                            if (s_row.parent_path or "") != parent_path:
                                s_row.parent_path = parent_path
                                changed_local = True
                            if changed_local:
                                updated += 1
                                if not dry_run:
                                    s_row.updated_at = now
                            else:
                                unchanged += 1
                        walk_structure(button.submenu_key, full_path)
                        continue
                    if button.kind == "group_task":
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        key = build_visibility_button_key(menu_name, "group_task", full_path)
                        present_keys.add(key)
                        g_row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
                        if g_row is None:
                            if not dry_run:
                                db.add(
                                    BotButtonSetting(
                                        id=str(uuid.uuid4()),
                                        stable_button_id=str(uuid.uuid4()),
                                        button_key=key,
                                        legacy_button_key=None,
                                        menu_type=menu_name,
                                        workflow_type="group_task",
                                        button_path=full_path,
                                        button_label=button.label,
                                        label_override=None,
                                        label_overridden=False,
                                        currently_present=True,
                                        is_archived=False,
                                        deleted_at=None,
                                        deleted_by_user_id=None,
                                        instruction_text=None,
                                        client_text=None,
                                        client_text_overridden=False,
                                        system_text=None,
                                        system_text_overridden=False,
                                        fixed_assignee_enabled=False,
                                        fixed_assignee_user_id=None,
                                        freeze_on_create=False,
                                        confirmation_enabled=False,
                                        confirmation_text=None,
                                        confirmation_overridden=False,
                                        files_overridden=False,
                                        files_json=None,
                                        conditional_files_overridden=False,
                                        conditional_files_json=None,
                                        is_enabled=True,
                                        visibility_overridden=False,
                                        is_db_created=False,
                                        button_kind="section",
                                        parent_path=parent_path,
                                        created_at=now,
                                        updated_at=now,
                                    )
                                )
                            created += 1
                        else:
                            changed_local = False
                            if g_row.menu_type != menu_name:
                                g_row.menu_type = menu_name
                                changed_local = True
                            if g_row.workflow_type != "group_task":
                                g_row.workflow_type = "group_task"
                                changed_local = True
                            if g_row.button_path != full_path:
                                g_row.button_path = full_path
                                changed_local = True
                            if g_row.button_label != button.label and not bool(g_row.label_overridden):
                                g_row.button_label = button.label
                                changed_local = True
                            if not g_row.currently_present:
                                g_row.currently_present = True
                                changed_local = True
                            if bool(g_row.is_archived):
                                g_row.is_archived = False
                                g_row.deleted_at = None
                                g_row.deleted_by_user_id = None
                                changed_local = True
                            if bool(g_row.is_db_created):
                                g_row.is_db_created = False
                                changed_local = True
                            if (g_row.button_kind or "section") != "section":
                                g_row.button_kind = "section"
                                changed_local = True
                            if (g_row.parent_path or "") != parent_path:
                                g_row.parent_path = parent_path
                                changed_local = True
                            if changed_local:
                                updated += 1
                                if not dry_run:
                                    g_row.updated_at = now
                            else:
                                unchanged += 1

        walk_structure(config.root_menu_key, "")

        # import txt row layouts for levels only when layout absent
        txt_layouts = _collect_txt_level_layouts(menu_name, config, refs_by_source)
        for level_key, level_rows in txt_layouts.items():
            existing_layout = db.execute(
                select(BotMenuLevelLayout).where(
                    BotMenuLevelLayout.menu_type == menu_name,
                    BotMenuLevelLayout.level_key == level_key,
                )
            ).scalar_one_or_none()
            if existing_layout is None:
                layouts_created += 1
                if not dry_run:
                    db.add(
                        BotMenuLevelLayout(
                            id=str(uuid.uuid4()),
                            menu_type=menu_name,
                            level_key=level_key,
                            layout_json=_normalize_level_layout(level_rows),
                            created_at=now,
                            updated_at=now,
                        )
                    )
            else:
                layouts_skipped_existing += 1

    removed_changed = 0
    currently_present_false = 0
    existing_rows = db.execute(select(BotButtonSetting)).scalars().all()
    for row in existing_rows:
        if row.menu_type not in set(available_menu_names()):
            continue
        if row.button_key not in present_keys and bool(row.currently_present):
            removed_changed += 1
            currently_present_false += 1
            if not dry_run and not bool(row.is_db_created):
                row.currently_present = False
                row.updated_at = now

    if not dry_run:
        db.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "parsed_menus": parsed_menus,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed_changed": removed_changed,
        "currently_present_false": currently_present_false,
        "layouts_created": layouts_created,
        "layouts_skipped_existing": layouts_skipped_existing,
        "touched_menu_types": sorted(touched_menu_types),
        "errors": errors,
    }


@router.post("/bot-buttons/import-from-txt")
def import_bot_buttons_from_txt(
    dry_run: bool = True,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _sync_bot_buttons_from_txt(dry_run=dry_run, db=db, user=user)


@router.post("/bot-buttons/sync-from-txt")
def sync_bot_buttons_from_txt(
    dry_run: bool = True,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return _sync_bot_buttons_from_txt(dry_run=dry_run, db=db, user=user)


