import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin
from ..db import get_db
from ..models import BotButtonSetting, BotMenuDestinationBinding, BotMenuLevelLayout, BotMenuProfile, ClientCardFieldConfig, ClientProfile, User
from ..schemas import (
    BotButtonGroupOut,
    BotButtonInstructionUpdate,
    BotButtonItemOut,
    BotButtonItemsResponse,
    BotButtonMenuStructureOut,
    BotButtonMenuTypeOut,
    BotButtonStructureLeafOut,
    BotMenuCreatePayload,
    BotMenuDestinationPayload,
    ClientCardFieldConfigOut,
)
from ..services.button_config import BASIC_MENU_NAME, BUTTON_CONFIG_DIR, available_menu_names, build_level_layout_key, build_terminal_button_key, build_visibility_button_key, find_button, list_terminal_buttons, load_button_config
from ..services.telegram_automation import get_automation_registry


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

CONDITIONAL_COMMON_KEYS: list[str] = [
    "employee_type",
    "location_type",
    "is_verified",
    "internal_number",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
]
CONDITIONAL_PESOK_KEYS: list[str] = [
    "sand_owner",
    "sales_group",
    "group_name",
]

CONDITIONAL_FIELD_TYPE_BY_CARD: dict[str, str] = {
    "text": "text",
    "textarea": "text",
    "phone": "text",
    "url": "text",
    "password": "text",
    "readonly": "text",
    "number": "number",
    "select": "select",
    "checkbox": "bool",
    "date": "text",
}

CONDITIONAL_SELECT_PRESETS: dict[str, list[str]] = {
    "sand_owner": [
        "песок Власова",
        "песок Шабурова",
        "песок Сайдаралиева",
        "песок Макаренко",
        "песок Шахалиевой",
        "песок Кочуры",
        "песок Москвиченко",
    ],
    "sales_group": [
        "группа Власова",
        "группа Шабурова",
        "группа Сайдаралиева",
        "группа Макаренко",
        "группа Шахалиевой",
        "группа Кочуры",
        "группа Москвиченко",
    ],
    "location_type": ["Офисный", "Удалённый"],
    "employee_type": ["Стажёр", "Сотрудник"],
}

TEXT_OPERATORS = {"eq", "ne", "contains", "not_contains", "empty", "not_empty"}
SELECT_OPERATORS = {"eq", "ne", "empty", "not_empty"}
NUMBER_OPERATORS = {"eq", "ne", "gt", "lt", "gte", "lte", "empty", "not_empty"}
BOOL_OPERATORS = {"eq", "is_true", "is_false"}



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

    # Some existing installations do not have options_json filled for "Куда?",
    # so keep the UI usable by combining configured options with real known values.
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
    values.extend(name for name in available_menu_names() if name != BASIC_MENU_NAME)
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


def _bootstrap_menu_profiles(db: Session) -> None:
    for menu_name in available_menu_names():
        profile = _ensure_menu_profile(db, menu_name, is_fallback=(menu_name == BASIC_MENU_NAME))
        if menu_name == BASIC_MENU_NAME:
            continue
        exists = db.execute(
            select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == menu_name)
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                BotMenuDestinationBinding(
                    id=str(uuid.uuid4()),
                    destination_value=menu_name,
                    menu_type=profile.menu_type,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
    db.flush()


def _unique_menu_type(db: Session, display_name: str) -> str:
    base = re.sub(r"\s+", " ", display_name.strip()) or "Меню"
    candidate = base
    idx = 2
    while db.execute(select(BotMenuProfile.id).where(BotMenuProfile.menu_type == candidate)).first() is not None:
        candidate = f"{base} {idx}"
        idx += 1
    return candidate



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
        cols = 2 if cols == 2 else 1
        keys_raw = row.get("button_keys")
        keys: list[str] = []
        if isinstance(keys_raw, list):
            for key in keys_raw:
                text = str(key or "").strip()
                if text and text not in keys:
                    keys.append(text)
        normalized.append({"columns": cols, "button_keys": keys[:2]})
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
        cols = 2 if cols == 2 else 1
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
            if len(picked) >= 2:
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

@router.get("/bot-buttons/menu-types", response_model=list[BotButtonMenuTypeOut])
def list_bot_button_menu_types(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    db.commit()
    profiles = _menu_profile_map(db)
    bindings = _menu_bindings_map(db)
    result: list[BotButtonMenuTypeOut] = []
    menu_names: list[str] = []
    seen_menu_names: set[str] = set()
    for name in available_menu_names():
        if name and name not in seen_menu_names:
            menu_names.append(name)
            seen_menu_names.add(name)
    extra_names = set(profiles.keys())
    extra_names.update(row[0] for row in db.execute(select(BotButtonSetting.menu_type).distinct()).all() if row[0])
    for name in sorted(extra_names):
        if name and name not in seen_menu_names:
            menu_names.append(name)
            seen_menu_names.add(name)
    for menu_name in menu_names:
        profile = profiles.get(menu_name)
        if profile is not None and not bool(profile.is_active):
            continue
        display_name = profile.display_name if profile is not None else menu_name
        source_file = f"{menu_name}.txt"
        if menu_name == BASIC_MENU_NAME:
            source_file = "built-in"
        try:
            config = load_button_config(menu_name)
            result.append(
                BotButtonMenuTypeOut(
                    menu_name=menu_name,
                    display_name=display_name,
                    destinations=bindings.get(menu_name, []),
                    source_file=source_file,
                    ok=True,
                    buttons_count=len(config.buttons),
                    menus_count=len(config.menus),
                )
            )
        except FileNotFoundError:
            if profile is not None:
                buttons_count = db.execute(
                    select(BotButtonSetting.id).where(BotButtonSetting.menu_type == menu_name)
                ).all()
                result.append(
                    BotButtonMenuTypeOut(
                        menu_name=menu_name,
                        display_name=display_name,
                        destinations=bindings.get(menu_name, []),
                        source_file="DB",
                        ok=True,
                        buttons_count=len(buttons_count),
                        menus_count=0,
                    )
                )
                continue
            result.append(
                BotButtonMenuTypeOut(
                    menu_name=menu_name,
                    display_name=display_name,
                    destinations=bindings.get(menu_name, []),
                    source_file=source_file,
                    ok=False,
                    error=f"Файл не найден: {BUTTON_CONFIG_DIR / source_file}",
                )
            )
        except Exception as exc:  # pragma: no cover
            result.append(
                BotButtonMenuTypeOut(
                    menu_name=menu_name,
                    display_name=display_name,
                    destinations=bindings.get(menu_name, []),
                    source_file=source_file,
                    ok=False,
                    error=f"Ошибка парсинга: {exc}",
                )
            )
    return result



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
        if row is None:
            stable_id = str(uuid.uuid4())
            row = BotButtonSetting(
                id=str(uuid.uuid4()),
                stable_button_id=stable_id,
                button_key=button_key,
                legacy_button_key=None,
                menu_type=menu_name,
                workflow_type=workflow_type,
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
        if row.workflow_type != workflow_type:
            row.workflow_type = workflow_type
            changed = True
        if row.button_path != button_path:
            row.button_path = button_path
            changed = True
        if row.button_label != button_label and not bool(row.label_overridden):
            row.button_label = button_label
            changed = True
        if not row.currently_present:
            row.currently_present = True
            changed = True
        if bool(row.is_archived):
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
            workflow_type=ref.workflow_type,  # type: ignore[arg-type]
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

    current_keys = {ref.technical_key for ref in refs}

    terminal_by_source: dict[str, BotButtonStructureLeafOut] = {}
    for leaf in [*tickets, *tasks, *autos]:
        terminal_by_source[leaf.source_button_key] = leaf
    for group in group_tasks:
        for leaf in group.children:
            terminal_by_source[leaf.source_button_key] = leaf

    def leaf_node(leaf: BotButtonStructureLeafOut) -> dict:
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


def _fallback_system_text(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    return button.system_text


def _fallback_client_text(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    return button.text


def _fallback_automation_key(menu_type: str, button_key: str) -> str | None:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return None
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return None
    button = find_button(source_button_key)
    if button is None:
        return None
    value = (button.automation_key or "").strip()
    return value or None

def _normalize_relative_button_file_path(raw_path: str) -> str | None:
    value = (raw_path or "").strip().replace("\\", "/")
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return None
    normalized = Path(*[part for part in candidate.parts if part not in ("", ".")]).as_posix()
    if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return None
    return normalized


def _list_available_button_files() -> list[str]:
    files_root = BUTTON_CONFIG_DIR / "files"
    if not files_root.exists() or not files_root.is_dir():
        return []
    items: list[str] = []
    for path in files_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(files_root).as_posix()
        normalized = _normalize_relative_button_file_path(rel)
        if normalized:
            items.append(normalized)
    return sorted(set(items), key=lambda x: x.lower())


def _fallback_direct_files(menu_type: str, button_key: str) -> list[str]:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return []
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return []
    button = find_button(source_button_key)
    if button is None:
        return []
    raw = (button.file_path or "").strip()
    normalized = _normalize_relative_button_file_path(raw) if raw else None
    return [normalized] if normalized else []


def _fallback_conditional_files(db: Session, menu_type: str, button_key: str) -> list[dict]:
    try:
        refs = list_terminal_buttons(menu_type)
    except Exception:
        return []
    source_button_key = None
    for ref in refs:
        if ref.technical_key == button_key:
            source_button_key = ref.source_button_key
            break
    if not source_button_key:
        return []
    button = find_button(source_button_key)
    if button is None or not button.file_rules:
        return []
    catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    result: list[dict] = []
    for rule in button.file_rules:
        field_name = (rule.get("field") or "").strip()
        file_name = _normalize_relative_button_file_path((rule.get("file") or "").strip())
        equals = (rule.get("equals") or "").strip()
        if not field_name or not file_name:
            continue
        if field_name not in catalog:
            continue
        result.append(
            {
                "conditions": [{"field": field_name, "operator": "eq", "value": equals}],
                "files": [file_name],
            }
        )
    return result


@router.get("/bot-buttons/menu-types/archived", response_model=list[BotButtonMenuTypeOut])
def list_archived_bot_button_menu_types(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    db.commit()
    profiles = _menu_profile_map(db)
    bindings = _menu_bindings_map(db)
    result: list[BotButtonMenuTypeOut] = []
    menu_names: list[str] = []
    seen: set[str] = set()
    for name, profile in profiles.items():
        if not name or bool(profile.is_active):
            continue
        if name in seen:
            continue
        seen.add(name)
        menu_names.append(name)

    extra_names = set(
        row[0]
        for row in db.execute(select(BotButtonSetting.menu_type).distinct()).all()
        if row[0]
    )
    for name in sorted(extra_names):
        if not name or name in seen:
            continue
        profile = profiles.get(name)
        if profile is None or bool(profile.is_active):
            continue
        seen.add(name)
        menu_names.append(name)

    for menu_name in menu_names:
        profile = profiles.get(menu_name)
        display_name = profile.display_name if profile is not None else menu_name
        buttons_count = db.execute(
            select(BotButtonSetting.id).where(BotButtonSetting.menu_type == menu_name)
        ).all()
        result.append(
            BotButtonMenuTypeOut(
                menu_name=menu_name,
                display_name=display_name,
                destinations=bindings.get(menu_name, []),
                source_file="ARCHIVE",
                ok=True,
                buttons_count=len(buttons_count),
                menus_count=0,
            )
        )
    return result


@router.delete("/bot-buttons/menu-types/{menu_name}")
def delete_bot_button_menu_type(
    menu_name: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    profile = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_name)).scalar_one_or_none()
    if profile is None:
        _error(404, "menu_not_found", "Меню не найдено")
    if menu_name == BASIC_MENU_NAME or bool(profile.is_fallback):
        _error(409, "cannot_delete_fallback_menu", "Нельзя удалить базовое fallback-меню")

    now = datetime.utcnow()
    profile.is_active = False
    profile.updated_at = now

    for binding in db.execute(
        select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.menu_type == menu_name)
    ).scalars().all():
        db.delete(binding)

    for row in db.execute(
        select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)
    ).scalars().all():
        row.is_archived = True
        row.currently_present = False
        row.deleted_at = now
        row.deleted_by_user_id = user.user.id
        row.updated_at = now

    db.commit()
    return {"ok": True}


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

    now = datetime.utcnow()
    profile.is_active = True
    profile.updated_at = now
    for row in db.execute(
        select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)
    ).scalars().all():
        row.is_archived = False
        row.updated_at = now
    db.commit()
    return {"ok": True}


@router.get("/bot-buttons/destination-options")
def bot_button_destination_options(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    db.commit()
    used = {
        row.destination_value
        for row in db.execute(select(BotMenuDestinationBinding)).scalars().all()
    }
    all_values = _destination_options(db)
    return {
        "items": all_values,
        "free": [value for value in all_values if value not in used],
    }


@router.post("/bot-buttons/menu-types")
def create_bot_button_menu_type(
    payload: BotMenuCreatePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    display_name = payload.display_name.strip()
    menu_type = _unique_menu_type(db, display_name)
    now = datetime.utcnow()
    profile = BotMenuProfile(
        id=str(uuid.uuid4()),
        menu_type=menu_type,
        display_name=display_name,
        is_fallback=False,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    allowed = set(_destination_options(db))
    for raw in payload.destinations:
        destination = str(raw or "").strip()
        if not destination or destination not in allowed:
            continue
        existing = db.execute(select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)).scalar_one_or_none()
        if existing is not None:
            _error(409, "destination_already_bound", f"Тип клиента уже привязан: {destination}")
        db.add(
            BotMenuDestinationBinding(
                id=str(uuid.uuid4()),
                destination_value=destination,
                menu_type=menu_type,
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    return {"ok": True, "menu_name": menu_type, "display_name": display_name}


@router.post("/bot-buttons/menu-types/{menu_name}/destinations")
def add_bot_menu_destination(
    menu_name: str,
    payload: BotMenuDestinationPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    _bootstrap_menu_profiles(db)
    profile = db.execute(select(BotMenuProfile).where(BotMenuProfile.menu_type == menu_name)).scalar_one_or_none()
    if profile is None:
        _error(404, "menu_not_found", "Меню не найдено")
    destination = payload.destination.strip()
    if destination not in set(_destination_options(db)):
        _error(404, "destination_not_found", "Значение поля Куда? не найдено в настройках карточки клиента")
    existing = db.execute(select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)).scalar_one_or_none()
    if existing is not None:
        _error(409, "destination_already_bound", "Этот тип клиента уже привязан к другому меню")
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
    return {"ok": True}


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
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}


def _normalize_conditional_rules(raw_rules: list[dict], available_files: set[str], catalog: dict[str, dict]) -> list[dict]:
    normalized_rules: list[dict] = []
    for idx, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            _error(422, "invalid_conditional_rule", f"Некорректный формат правила #{idx + 1}")
        raw_conditions = raw_rule.get("conditions") or []
        raw_files = raw_rule.get("files") or []
        if not isinstance(raw_conditions, list):
            _error(422, "invalid_conditional_rule_conditions", f"conditions должен быть списком в правиле #{idx + 1}")
        if not isinstance(raw_files, list):
            _error(422, "invalid_conditional_rule_files", f"files должен быть списком в правиле #{idx + 1}")
        conditions: list[dict] = []
        for cidx, raw_condition in enumerate(raw_conditions):
            if not isinstance(raw_condition, dict):
                _error(422, "invalid_conditional_condition", f"Некорректное условие #{cidx + 1} в правиле #{idx + 1}")
            field_name = str(raw_condition.get("field") or "").strip()
            operator = str(raw_condition.get("operator") or "").strip()
            value = raw_condition.get("value")
            if field_name not in catalog:
                _error(422, "conditional_field_not_allowed", f"Поле недоступно для условий: {field_name}")
            field_type = str(catalog[field_name].get("type") or "text")
            if field_type == "text":
                allowed_ops = TEXT_OPERATORS
            elif field_type == "select":
                allowed_ops = SELECT_OPERATORS
            elif field_type == "number":
                allowed_ops = NUMBER_OPERATORS
            else:
                allowed_ops = BOOL_OPERATORS
            if operator not in allowed_ops:
                _error(422, "conditional_operator_not_allowed", f"Оператор '{operator}' недоступен для поля '{field_name}'")
            if field_type == "bool":
                if operator == "is_true":
                    operator = "eq"
                    value = True
                elif operator == "is_false":
                    operator = "eq"
                    value = False
                elif operator == "eq":
                    if isinstance(value, bool):
                        pass
                    else:
                        raw_bool = str(value or "").strip().lower()
                        if raw_bool in {"true", "1", "да", "yes"}:
                            value = True
                        elif raw_bool in {"false", "0", "нет", "no"}:
                            value = False
                        else:
                            _error(422, "conditional_bool_value_invalid", f"Для поля '{field_name}' допустимы только Да/Нет")
            elif operator in {"empty", "not_empty"}:
                value = None
            elif value is not None:
                value = str(value)
            else:
                value = ""
            conditions.append({"field": field_name, "operator": operator, "value": value})
        files: list[str] = []
        for raw_file in raw_files:
            normalized = _normalize_relative_button_file_path(str(raw_file))
            if not normalized:
                _error(422, "invalid_conditional_file_path", f"Некорректный файл в условии: {raw_file}")
            if normalized not in available_files:
                _error(422, "conditional_file_not_allowed", f"Файл недоступен для выбора: {normalized}")
            files.append(normalized)
        files = list(dict.fromkeys(files))
        normalized_rules.append({"conditions": conditions, "files": files})
    return normalized_rules


def _distinct_profile_values(db: Session, field_name: str) -> list[str]:
    if field_name == "sand_owner":
        rows = db.execute(select(ClientProfile.sand_owner).distinct()).all()
    else:
        column = getattr(ClientProfile, field_name, None)
        if column is None:
            return []
        rows = db.execute(select(column).distinct()).all()
    values: list[str] = []
    for row in rows:
        value = row[0]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values.append(text)
    return sorted(set(values), key=lambda x: x.lower())


def _field_options(db: Session, field: ClientCardFieldConfig) -> list[str]:
    raw = field.options_json if isinstance(field.options_json, list) else []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text and text not in seen:
            values.append(text)
            seen.add(text)

    for preset in CONDITIONAL_SELECT_PRESETS.get(field.key, []):
        text = str(preset or "").strip()
        if text and text not in seen:
            values.append(text)
            seen.add(text)

    for actual in _distinct_profile_values(db, field.key):
        text = str(actual or "").strip()
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    return values


def _build_conditional_field_catalog(db: Session, menu_type: str | None = None) -> dict[str, dict]:
    menu_name = str(menu_type or "").strip().lower()
    fields = db.execute(
        select(ClientCardFieldConfig)
        .where(ClientCardFieldConfig.is_visible.is_(True))
        .order_by(ClientCardFieldConfig.sort_order.asc(), ClientCardFieldConfig.created_at.asc())
    ).scalars().all()
    by_key = {field.key: field for field in fields}
    catalog: dict[str, dict] = {}

    keys = list(CONDITIONAL_COMMON_KEYS)
    if "песок" in menu_name:
        keys = [*CONDITIONAL_PESOK_KEYS, *keys]

    for key in keys:
        field = by_key.get(key)
        if field is None:
            continue
        field_type = CONDITIONAL_FIELD_TYPE_BY_CARD.get(field.field_type, "text")
        entry = {
            "field": key,
            "type": field_type,
            "label": field.label or key,
            "is_custom": False,
        }
        if field_type == "select":
            entry["values"] = _field_options(db, field)
        catalog[key] = entry

    for field in fields:
        if field.is_system:
            continue
        key = (field.key or "").strip()
        if not key.startswith("custom_"):
            continue
        field_type = CONDITIONAL_FIELD_TYPE_BY_CARD.get(field.field_type, "text")
        entry = {
            "field": f"custom_fields.{key}",
            "type": field_type,
            "label": field.label or key,
            "is_custom": True,
            "custom_key": key,
        }
        if field_type == "select":
            entry["values"] = _field_options(db, field)
        catalog[entry["field"]] = entry

    catalog["telegram_username"] = {
        "field": "telegram_username",
        "type": "text",
        "label": "Telegram username",
        "is_custom": False,
    }
    catalog["telegram_display_name"] = {
        "field": "telegram_display_name",
        "type": "text",
        "label": "Telegram имя",
        "is_custom": False,
    }
    catalog["telegram_user_id"] = {
        "field": "telegram_user_id",
        "type": "text",
        "label": "Telegram user id",
        "is_custom": False,
    }
    catalog["telegram_chat_id"] = {
        "field": "telegram_chat_id",
        "type": "text",
        "label": "Telegram chat id",
        "is_custom": False,
    }
    return catalog


@router.get("/bot-buttons/item")
def get_bot_button_item(
    button_key: str,
    menu_type: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    txt_client_text = _fallback_client_text((menu_type or (row.menu_type if row else "")), button_key)
    txt_system_text = _fallback_system_text((menu_type or (row.menu_type if row else "")), button_key)
    txt_automation_key = _fallback_automation_key((menu_type or (row.menu_type if row else "")), button_key)
    automation_registry = get_automation_registry()
    db_automation_key = (row.automation_key or "").strip() if row is not None else ""
    automation_overridden = bool(row.automation_overridden) if row is not None else False
    effective_automation_key = db_automation_key if automation_overridden else txt_automation_key
    automation_meta = automation_registry.get(effective_automation_key) if effective_automation_key else None
    txt_files = _fallback_direct_files((menu_type or (row.menu_type if row else "")), button_key)
    effective_menu_type = menu_type or (row.menu_type if row else "")
    txt_conditional_files = _fallback_conditional_files(db, effective_menu_type, button_key)
    if row is None:
        return {
            "button_key": button_key,
            "stable_button_id": None,
            "legacy_button_key": None,
            "menu_type": effective_menu_type,
            "workflow_type": "",
            "button_path": "",
            "button_label": "",
            "label_override": None,
            "label_overridden": False,
            "button_label_effective": "",
            "button_label_from": "txt",
            "removed": False,
            "currently_present": True,
            "instruction_text": None,
            "client_text": None,
            "client_text_overridden": False,
            "client_text_effective": txt_client_text,
            "client_text_from": "txt",
            "system_text": None,
            "system_text_overridden": False,
            "system_text_effective": txt_system_text,
            "system_text_from": "txt",
            "automation_key_effective": txt_automation_key,
            "automation_key": None,
            "automation_overridden": False,
            "automation_from": "txt" if txt_automation_key else "none",
            "automation_title": (automation_meta.get("title") if automation_meta else None),
            "automation_description": (automation_meta.get("description") if automation_meta else None),
            "automation_available": bool(automation_meta.get("available")) if automation_meta else False,
            "automation_handler": (automation_meta.get("handler") if automation_meta else None),
            "automation_status": ("not_set" if not effective_automation_key else ("available" if automation_meta else "not_found")),
            "fixed_assignee_enabled": False,
            "fixed_assignee_user_id": None,
            "freeze_on_create": False,
            "confirmation_enabled": False,
            "confirmation_text": None,
            "confirmation_overridden": False,
            "files_overridden": False,
            "files": [],
            "files_effective": txt_files,
            "files_from": "txt",
            "conditional_files_overridden": False,
            "conditional_files": [],
            "conditional_files_effective": txt_conditional_files,
            "conditional_files_from": "txt",
            "is_enabled": True,
            "visibility_overridden": False,
        }
    effective_system_text = row.system_text if row.system_text_overridden else txt_system_text
    effective_client_text = row.client_text if row.client_text_overridden else ((row.client_text or txt_client_text))
    db_files_raw = row.files_json if isinstance(row.files_json, list) else []
    db_files = [p for p in (_normalize_relative_button_file_path(str(v)) for v in db_files_raw) if p]
    effective_files = db_files if bool(row.files_overridden) else txt_files
    db_conditional_raw = row.conditional_files_json if isinstance(row.conditional_files_json, list) else []
    db_conditional = [rule for rule in db_conditional_raw if isinstance(rule, dict)]
    effective_conditional = db_conditional if bool(row.conditional_files_overridden) else txt_conditional_files
    return {
        "button_key": row.button_key,
        "stable_button_id": row.stable_button_id,
        "legacy_button_key": row.legacy_button_key,
        "menu_type": row.menu_type,
        "workflow_type": row.workflow_type,
        "button_path": row.button_path,
        "button_label": row.button_label,
        "label_override": row.label_override,
        "label_overridden": bool(row.label_overridden),
        "button_label_effective": ((row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label),
        "button_label_from": ("db" if bool(row.label_overridden) and (row.label_override or "").strip() else "txt"),
        "removed": not bool(row.currently_present),
        "currently_present": bool(row.currently_present),
        "is_db_created": bool(row.is_db_created),
        "is_archived": bool(row.is_archived),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "deleted_by_user_id": row.deleted_by_user_id,
        "instruction_text": row.instruction_text,
        "client_text": row.client_text,
        "client_text_overridden": bool(row.client_text_overridden),
        "client_text_effective": effective_client_text,
        "client_text_from": "db" if bool(row.client_text_overridden) else "txt",
        "system_text": row.system_text,
        "system_text_overridden": bool(row.system_text_overridden),
        "system_text_effective": effective_system_text,
        "system_text_from": "db" if row.system_text_overridden else "txt",
        "automation_key_effective": effective_automation_key,
        "automation_key": db_automation_key or None,
        "automation_overridden": automation_overridden,
        "automation_from": ("db" if automation_overridden else ("txt" if txt_automation_key else "none")),
        "automation_title": (automation_meta.get("title") if automation_meta else None),
        "automation_description": (automation_meta.get("description") if automation_meta else None),
        "automation_available": bool(automation_meta.get("available")) if automation_meta else False,
        "automation_handler": (automation_meta.get("handler") if automation_meta else None),
        "automation_status": ("not_set" if not effective_automation_key else ("available" if automation_meta else "not_found")),
        "fixed_assignee_enabled": bool(row.fixed_assignee_enabled),
        "fixed_assignee_user_id": row.fixed_assignee_user_id,
        "freeze_on_create": bool(row.freeze_on_create),
        "confirmation_enabled": bool(row.confirmation_enabled),
        "confirmation_text": row.confirmation_text,
        "confirmation_overridden": bool(row.confirmation_overridden),
        "files_overridden": bool(row.files_overridden),
        "files": db_files,
        "files_effective": effective_files,
        "files_from": "db" if row.files_overridden else "txt",
        "conditional_files_overridden": bool(row.conditional_files_overridden),
        "conditional_files": db_conditional,
        "conditional_files_effective": effective_conditional,
        "conditional_files_from": "db" if row.conditional_files_overridden else "txt",
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
    }


@router.get("/bot-buttons/files")
def get_bot_button_files(
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return {"items": _list_available_button_files()}


@router.get("/bot-buttons/automations")
def get_bot_button_automations(
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    registry = get_automation_registry()
    return {"items": list(registry.values())}


@router.get("/bot-buttons/conditional-fields")
def get_bot_button_conditional_fields(
    menu_type: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    items: list[dict] = []
    for key, base in catalog.items():
        field_type = str(base.get("type") or "text")
        item = {
            "field": key,
            "type": field_type,
            "label": str(base.get("label") or key),
            "is_custom": bool(base.get("is_custom")),
        }
        if field_type == "select":
            item["operators"] = ["eq", "ne", "empty", "not_empty"]
            item["values"] = list(base.get("values") or [])
        elif field_type == "number":
            item["operators"] = ["eq", "ne", "gt", "lt", "gte", "lte", "empty", "not_empty"]
        elif field_type == "bool":
            item["operators"] = ["eq"]
            item["values"] = [
                {"value": "true", "label": "Да"},
                {"value": "false", "label": "Нет"},
            ]
        else:
            item["operators"] = ["eq", "ne", "contains", "not_contains", "empty", "not_empty"]
        items.append(item)
    return {
        "items": items
    }


@router.put("/bot-buttons/item/instruction")
def save_bot_button_instruction(
    payload: BotButtonInstructionUpdate,
    button_key: str,
    menu_type: str,
    workflow_type: str,
    button_path: str,
    button_label: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    override_label_value = (payload.label_override or "").strip() if payload.label_override is not None else None
    override_label_enabled = bool(payload.label_overridden) if payload.label_overridden is not None else None
    if override_label_enabled is True and not override_label_value:
        _error(422, "label_override_required", "Название в боте не может быть пустым")

    fixed_enabled = bool(payload.fixed_assignee_enabled)
    fixed_user_id = (payload.fixed_assignee_user_id or "").strip() or None
    if fixed_enabled and not fixed_user_id:
        _error(422, "fixed_assignee_required", "Выберите фиксированного ответственного")
    if fixed_user_id:
        target_user = db.execute(select(User).where(User.id == fixed_user_id)).scalar_one_or_none()
        if target_user is None or not target_user.is_active:
            _error(422, "fixed_assignee_invalid", "Выбранный фиксированный ответственный недоступен")
        if target_user.role not in {"admin", "agent", "trainee"}:
            _error(422, "fixed_assignee_role_invalid", "Можно выбрать только admin/agent/trainee")

    text_value = (payload.instruction_text or "").strip() or None
    client_text_value = (payload.client_text or "").strip() or None if payload.client_text is not None else None
    system_value = (payload.system_text or "").strip() or None if payload.system_text is not None else None
    automation_key_value = (payload.automation_key or "").strip() or None if payload.automation_key is not None else None
    if automation_key_value is not None:
        registry = get_automation_registry()
        if automation_key_value and automation_key_value not in registry:
            _error(422, "automation_not_found", "Указанная автоматизация не найдена в реестре")
    available_files = set(_list_available_button_files())
    payload_files = payload.files if payload.files is not None else None
    normalized_files: list[str] = []
    if payload_files is not None:
        for raw in payload_files:
            normalized = _normalize_relative_button_file_path(str(raw))
            if not normalized:
                _error(422, "invalid_file_path", "Некорректный путь файла в настройке кнопки")
            if normalized not in available_files:
                _error(422, "file_not_allowed", f"Файл недоступен для выбора: {normalized}")
            normalized_files.append(normalized)
        normalized_files = list(dict.fromkeys(normalized_files))
    payload_conditional = payload.conditional_files if payload.conditional_files is not None else None
    normalized_conditional: list[dict] = []
    conditional_catalog = _build_conditional_field_catalog(db, menu_type=menu_type)
    if payload_conditional is not None:
        normalized_conditional = _normalize_conditional_rules(payload_conditional, available_files, conditional_catalog)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    now = datetime.utcnow()
    if row is None:
        row = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=str(uuid.uuid4()),
            button_key=button_key,
            legacy_button_key=None,
            menu_type=menu_type,
            workflow_type=workflow_type,
            button_path=button_path,
            button_label=button_label,
            label_override=override_label_value if override_label_enabled else None,
            label_overridden=bool(override_label_enabled),
            currently_present=True,
            instruction_text=text_value,
            client_text=client_text_value,
            client_text_overridden=bool(payload.client_text_overridden),
            system_text=system_value,
            system_text_overridden=bool(payload.system_text is not None or payload.system_text_overridden),
            fixed_assignee_enabled=fixed_enabled,
            fixed_assignee_user_id=fixed_user_id,
            freeze_on_create=bool(payload.freeze_on_create),
            confirmation_enabled=bool(payload.confirmation_enabled),
            confirmation_text=((payload.confirmation_text or "").strip() or None),
            confirmation_overridden=bool(payload.confirmation_overridden),
            files_overridden=bool(payload.files_overridden),
            files_json=normalized_files if payload.files is not None else None,
            conditional_files_overridden=bool(payload.conditional_files_overridden),
            conditional_files_json=normalized_conditional if payload.conditional_files is not None else None,
            automation_key=automation_key_value,
            automation_overridden=bool(payload.automation_overridden),
            is_enabled=bool(payload.is_enabled) if payload.is_enabled is not None else True,
            visibility_overridden=bool(payload.visibility_overridden),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.menu_type = menu_type
        row.workflow_type = workflow_type
        row.button_path = button_path
        if not bool(row.label_overridden):
            row.button_label = button_label
        row.currently_present = True
        if not row.stable_button_id:
            row.stable_button_id = str(uuid.uuid4())
        if payload.label_override is not None:
            row.label_override = override_label_value
        if payload.label_overridden is not None:
            row.label_overridden = bool(payload.label_overridden)
            if not row.label_overridden:
                row.label_override = None
                row.button_label = button_label
            elif not (row.label_override or "").strip():
                _error(422, "label_override_required", "Название в боте не может быть пустым")
        row.instruction_text = text_value
        if payload.client_text is not None:
            row.client_text = client_text_value
        if payload.client_text_overridden is not None:
            row.client_text_overridden = bool(payload.client_text_overridden)
            if not row.client_text_overridden:
                row.client_text = None
        if payload.system_text is not None:
            row.system_text = system_value
            row.system_text_overridden = True
        if payload.system_text_overridden is not None:
            row.system_text_overridden = bool(payload.system_text_overridden)
            if not row.system_text_overridden:
                row.system_text = None
        if payload.fixed_assignee_enabled is not None:
            row.fixed_assignee_enabled = fixed_enabled
            if not row.fixed_assignee_enabled:
                row.fixed_assignee_user_id = None
        if payload.fixed_assignee_user_id is not None:
            row.fixed_assignee_user_id = fixed_user_id
        if payload.freeze_on_create is not None:
            row.freeze_on_create = bool(payload.freeze_on_create)
        if payload.confirmation_enabled is not None:
            row.confirmation_enabled = bool(payload.confirmation_enabled)
        if payload.confirmation_text is not None:
            row.confirmation_text = (payload.confirmation_text or "").strip() or None
        if payload.confirmation_overridden is not None:
            row.confirmation_overridden = bool(payload.confirmation_overridden)
            if not row.confirmation_overridden:
                row.confirmation_enabled = False
                row.confirmation_text = None
        if payload.files is not None:
            row.files_json = normalized_files
            row.files_overridden = True
        if payload.files_overridden is not None:
            row.files_overridden = bool(payload.files_overridden)
            if not row.files_overridden:
                row.files_json = None
        if payload.conditional_files is not None:
            row.conditional_files_json = normalized_conditional
            row.conditional_files_overridden = True
        if payload.conditional_files_overridden is not None:
            row.conditional_files_overridden = bool(payload.conditional_files_overridden)
            if not row.conditional_files_overridden:
                row.conditional_files_json = None
        if payload.automation_key is not None:
            row.automation_key = automation_key_value
            row.automation_overridden = True
        if payload.automation_overridden is not None:
            row.automation_overridden = bool(payload.automation_overridden)
            if not row.automation_overridden:
                row.automation_key = None
        if payload.is_enabled is not None:
            row.is_enabled = bool(payload.is_enabled)
            row.visibility_overridden = True
        if payload.visibility_overridden is not None:
            row.visibility_overridden = bool(payload.visibility_overridden)
            if not row.visibility_overridden:
                row.is_enabled = True
        row.updated_at = now
    db.commit()
    return {
        "ok": True,
        "button_key": button_key,
        "has_instruction": bool(text_value),
        "client_text": row.client_text,
        "client_text_overridden": bool(row.client_text_overridden),
        "system_text_overridden": bool(row.system_text_overridden),
        "fixed_assignee_enabled": bool(row.fixed_assignee_enabled),
        "fixed_assignee_user_id": row.fixed_assignee_user_id,
        "freeze_on_create": bool(row.freeze_on_create),
        "confirmation_enabled": bool(row.confirmation_enabled),
        "confirmation_text": row.confirmation_text,
        "confirmation_overridden": bool(row.confirmation_overridden),
        "files_overridden": bool(row.files_overridden),
        "files": row.files_json if isinstance(row.files_json, list) else [],
        "conditional_files_overridden": bool(row.conditional_files_overridden),
        "conditional_files": row.conditional_files_json if isinstance(row.conditional_files_json, list) else [],
        "automation_key": (row.automation_key or "").strip() or None,
        "automation_overridden": bool(row.automation_overridden),
        "is_enabled": bool(row.is_enabled) if row.visibility_overridden else True,
        "visibility_overridden": bool(row.visibility_overridden),
        "label_override": row.label_override,
        "label_overridden": bool(row.label_overridden),
        "button_label_effective": ((row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label),
        "button_label_from": ("db" if bool(row.label_overridden) and (row.label_override or "").strip() else "txt"),
    }



@router.delete("/bot-buttons/item")
def delete_bot_button_setting(
    button_key: str,
    hard: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    if row is None:
        return {"ok": True, "deleted": False}
    if hard:
        db.delete(row)
        db.commit()
        return {"ok": True, "deleted": True, "hard": True}
    if not bool(row.is_db_created):
        _error(422, "cannot_delete_txt_button", "Txt-кнопки нельзя удалить из меню, используйте только отключение активности.")
    now = datetime.utcnow()
    row.currently_present = False
    row.is_archived = True
    row.deleted_at = now
    row.deleted_by_user_id = user.id
    row.updated_at = now
    row_kind = (row.button_kind or "").strip().lower()
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
                BotButtonSetting.is_db_created.is_(True),
                BotButtonSetting.button_path.like(f"{prefix}%"),
            )
        ).scalars().all()
        for child in descendants:
            child.currently_present = False
            child.is_archived = True
            child.deleted_at = now
            child.deleted_by_user_id = user.id
            child.updated_at = now
    db.commit()
    return {"ok": True, "deleted": True, "hard": False}


@router.post("/bot-buttons/item/restore")
def restore_bot_button_setting(
    button_key: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == button_key)).scalar_one_or_none()
    if row is None:
        _error(404, "button_setting_not_found", "Запись кнопки не найдена")
    if not bool(row.is_db_created):
        _error(422, "cannot_restore_txt_button", "Восстановление применяется только к элементам, созданным через UI.")
    now = datetime.utcnow()
    row.currently_present = True
    row.is_archived = False
    row.deleted_at = None
    row.deleted_by_user_id = None
    row.updated_at = now
    row_kind = (row.button_kind or "").strip().lower()
    if row_kind in {"section", "group_parent"}:
        prefix = f"{row.button_path}/"
        descendants = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == row.menu_type,
                BotButtonSetting.is_db_created.is_(True),
                BotButtonSetting.button_path.like(f"{prefix}%"),
            )
        ).scalars().all()
        for child in descendants:
            child.currently_present = True
            child.is_archived = False
            child.deleted_at = None
            child.deleted_by_user_id = None
            child.updated_at = now
    db.commit()
    return {"ok": True, "restored": True}


@router.post("/bot-buttons/item/rebind")
def rebind_bot_button_setting(
    old_button_key: str,
    new_button_key: str,
    menu_type: str,
    workflow_type: str,
    button_path: str,
    button_label: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    if old_button_key == new_button_key:
        return {"ok": True, "moved": False, "reason": "same_key"}

    source = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == old_button_key)).scalar_one_or_none()
    if source is None:
        _error(404, "button_setting_not_found", "Исходная запись кнопки не найдена")

    text_value = (source.instruction_text or "").strip() or None
    source_client_text = (source.client_text or "").strip() or None
    source_client_text_overridden = bool(source.client_text_overridden)
    source_system_text = (source.system_text or "").strip() or None
    source_system_overridden = bool(source.system_text_overridden)
    source_fixed_enabled = bool(source.fixed_assignee_enabled)
    source_fixed_user_id = source.fixed_assignee_user_id
    source_freeze_on_create = bool(source.freeze_on_create)
    source_confirmation_enabled = bool(source.confirmation_enabled)
    source_confirmation_text = (source.confirmation_text or "").strip() or None
    source_confirmation_overridden = bool(source.confirmation_overridden)
    source_files_overridden = bool(source.files_overridden)
    source_files_json = source.files_json if isinstance(source.files_json, list) else None
    source_conditional_overridden = bool(source.conditional_files_overridden)
    source_conditional_json = source.conditional_files_json if isinstance(source.conditional_files_json, list) else None
    source_is_enabled = bool(source.is_enabled)
    source_visibility_overridden = bool(source.visibility_overridden)
    source_label_override = (source.label_override or "").strip() or None
    source_label_overridden = bool(source.label_overridden)
    target = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == new_button_key)).scalar_one_or_none()
    now = datetime.utcnow()

    if target is None:
        target = BotButtonSetting(
            id=str(uuid.uuid4()),
            stable_button_id=source.stable_button_id or str(uuid.uuid4()),
            button_key=new_button_key,
            legacy_button_key=old_button_key,
            menu_type=menu_type,
            workflow_type=workflow_type,
            button_path=button_path,
            button_label=button_label,
            label_override=source_label_override if source_label_overridden else None,
            label_overridden=source_label_overridden,
            currently_present=True,
            instruction_text=text_value,
            client_text=source_client_text,
            client_text_overridden=source_client_text_overridden,
            system_text=source_system_text,
            system_text_overridden=source_system_overridden,
            fixed_assignee_enabled=source_fixed_enabled,
            fixed_assignee_user_id=source_fixed_user_id,
            freeze_on_create=source_freeze_on_create,
            confirmation_enabled=source_confirmation_enabled,
            confirmation_text=source_confirmation_text,
            confirmation_overridden=source_confirmation_overridden,
            files_overridden=source_files_overridden,
            files_json=source_files_json,
            conditional_files_overridden=source_conditional_overridden,
            conditional_files_json=source_conditional_json,
            is_enabled=source_is_enabled,
            visibility_overridden=source_visibility_overridden,
            created_at=now,
            updated_at=now,
        )
        db.add(target)
    else:
        if not target.stable_button_id:
            target.stable_button_id = source.stable_button_id or str(uuid.uuid4())
        if not target.legacy_button_key:
            target.legacy_button_key = old_button_key
        target.menu_type = menu_type
        target.workflow_type = workflow_type
        target.button_path = button_path
        target.button_label = button_label
        target.label_overridden = source_label_overridden
        target.label_override = source_label_override if source_label_overridden else None
        target.currently_present = True
        target.instruction_text = text_value
        target.client_text = source_client_text
        target.client_text_overridden = source_client_text_overridden
        target.system_text = source_system_text
        target.system_text_overridden = source_system_overridden
        target.fixed_assignee_enabled = source_fixed_enabled
        target.fixed_assignee_user_id = source_fixed_user_id
        target.freeze_on_create = source_freeze_on_create
        target.confirmation_enabled = source_confirmation_enabled
        target.confirmation_text = source_confirmation_text
        target.confirmation_overridden = source_confirmation_overridden
        target.files_overridden = source_files_overridden
        target.files_json = source_files_json
        target.conditional_files_overridden = source_conditional_overridden
        target.conditional_files_json = source_conditional_json
        target.is_enabled = source_is_enabled
        target.visibility_overridden = source_visibility_overridden
        target.updated_at = now

    db.delete(source)
    db.commit()
    return {"ok": True, "moved": True, "button_key": new_button_key}

@router.get("/bot-buttons/assignees")
def list_bot_button_assignees(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    users = db.execute(
        select(User)
        .where(User.is_active.is_(True), User.role.in_(["admin", "agent", "trainee"]))
        .order_by(User.role.asc(), User.full_name.asc())
    ).scalars().all()
    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "role": u.role,
            "role_label": ROLE_LABELS.get(u.role, u.role),
        }
        for u in users
    ]


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
    rows = _normalize_level_layout(payload.get("rows"))
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
    return {"ok": True, "menu_type": menu_type, "level_key": level_key, "rows": rows}


@router.post("/bot-buttons/migrate-instructions")
def migrate_bot_button_instructions(
    overwrite_existing: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    created = 0
    updated = 0
    skipped_existing = 0
    skipped_empty = 0
    errors: list[str] = []
    now = datetime.utcnow()

    for menu_name in available_menu_names():
        try:
            refs = list_terminal_buttons(menu_name)
        except Exception as exc:  # pragma: no cover
            errors.append(f"{menu_name}: {exc}")
            continue
        for ref in refs:
            text_value = (ref.instruction_text or "").strip()
            if not text_value:
                skipped_empty += 1
                continue
            row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == ref.technical_key)).scalar_one_or_none()
            if row is None:
                row = BotButtonSetting(
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
                    instruction_text=text_value,
                    system_text=None,
                    system_text_overridden=False,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                created += 1
                continue
            existing_text = (row.instruction_text or "").strip()
            if existing_text and not overwrite_existing:
                skipped_existing += 1
                continue
            if existing_text == text_value:
                skipped_existing += 1
                continue
            row.menu_type = menu_name
            row.workflow_type = ref.workflow_type
            row.button_path = ref.full_path
            row.button_label = ref.label
            row.currently_present = True
            if not row.stable_button_id:
                row.stable_button_id = str(uuid.uuid4())
            row.instruction_text = text_value
            row.updated_at = now
            updated += 1

    db.commit()
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_empty": skipped_empty,
        "errors": errors,
        "overwrite_existing": overwrite_existing,
    }


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
                level_rows.append({"columns": 2 if len(row_keys) > 1 else 1, "button_keys": row_keys[:2]})
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
            if row.menu_type != menu_name:
                row.menu_type = menu_name
                changed = True
            if row.workflow_type != ref.workflow_type:
                row.workflow_type = ref.workflow_type
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







