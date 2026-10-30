"""Read-only WEB projection of the existing Telegram menu configuration."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, BotMenuDestinationBinding, BotMenuLevelLayout, ClientProfile
from .button_config import BASIC_MENU_NAME, available_menu_names, build_level_layout_key
from .temporary_rop import temporary_rop_destination
# Reuse the pure Telegram presentation helpers without changing its runtime.
from ..routers.telegram import _apply_layout_to_markup_rows, _normalize_layout_rows, _button_style_value


def menu_name_for_client(db: Session, client_id: str) -> str:
    profile = db.scalar(select(ClientProfile).where(ClientProfile.client_id == client_id))
    destination = temporary_rop_destination(profile) if profile is not None else None
    if profile is not None and profile.is_verified and destination and destination.strip():
        destination = destination.strip()
        binding = db.scalar(select(BotMenuDestinationBinding).where(
            BotMenuDestinationBinding.destination_value == destination))
        names = available_menu_names(db)
        if binding is not None and binding.menu_type in names:
            return binding.menu_type
        if destination in names:
            return destination
    return BASIC_MENU_NAME


def read_client_menu(db: Session, client_id: str) -> dict:
    name = menu_name_for_client(db, client_id)
    by_parent = {}
    for row in db.scalars(select(BotButtonSetting).where(BotButtonSetting.menu_type == name)):
        if row.currently_present and not row.is_archived:
            by_parent.setdefault((row.parent_path or "").strip(), []).append(row)
    for bucket in by_parent.values():
        bucket.sort(key=lambda row: (row.updated_at or row.created_at, row.button_label))
    layouts = {row.level_key: _normalize_layout_rows(row.layout_json) for row in db.scalars(
        select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == name))}

    def level(parent_path, ancestors):
        result = []
        for row in by_parent.get(parent_path, []):
            if row.visibility_overridden and not row.is_enabled:
                continue
            kind = (row.button_kind or "terminal").strip().lower()
            workflow = (row.workflow_type or "").strip().lower()
            children = None
            # Telegram treats group tasks as actions even when stored as sections.
            if workflow != "group_task" and kind in {"section", "group_parent"}:
                if row.button_path in ancestors:
                    continue
                children = level(row.button_path, ancestors | {row.button_path})
                if not children:
                    continue
            label = (row.label_override or "").strip() if row.label_overridden else ""
            result.append([{"id": row.id, "label": label or row.button_label,
                            "workflow_type": workflow,
                            "style": _button_style_value(row), "children": children,
                            "__layout_key": row.button_key}])
        arranged = _apply_layout_to_markup_rows(result, layouts.get(build_level_layout_key(name, parent_path)))
        for buttons in arranged:
            for button in buttons:
                button.pop("__layout_key", None)
        return arranged

    return {"menu_type": name, "rows": level("", {""})}
