"""Shared button values and stable key helpers. Button data lives in the database."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, literal_column, func
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, BotMenuProfile, BotMenuDestinationBinding
from .system_tickets import is_system_menu_type

# The directory remains the storage root for existing user attachments.
BUTTON_CONFIG_DIR = Path(os.getenv("BUTTON_CONFIG_DIR", "/button-config"))
DEFAULT_MENU_NAME = "Руководство"
BASIC_MENU_NAME = "Базовое"

@dataclass(frozen=True)
class ButtonSpec:
    key: str
    label: str
    kind: str
    text: str | None = None
    confirmation_text: str | None = None
    system_text: str | None = None
    instruction_text: str | None = None
    automation_key: str | None = None
    forced_assignee_type: str | None = None
    forced_assignee_value: str | None = None
    suppress_client_answer: bool = False
    file_path: str | None = None
    file_rules: tuple[dict[str, str], ...] = ()
    submenu_key: str | None = None
    subtasks: tuple["ButtonSpec", ...] = ()


@dataclass(frozen=True)
class TerminalButtonRef:
    source_button_key: str
    label: str
    workflow_type: str
    full_path: str
    technical_key: str
    instruction_text: str | None = None


@dataclass(frozen=True)
class MenuSpec:
    key: str
    title: str
    text: str | None = None
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ButtonConfig:
    menus: dict[str, MenuSpec] = field(default_factory=dict)
    buttons: dict[str, ButtonSpec] = field(default_factory=dict)
    root_menu_key: str = "root"

    def menu_markup(self, menu_key: str | None = None) -> dict:
        key = menu_key or self.root_menu_key
        menu = self.menus[key]
        inline_keyboard = []
        for row in menu.rows:
            rendered_row = []
            for button_key in row:
                button = self.buttons.get(button_key)
                if button is None:
                    continue
                callback_prefix = "menu" if button.kind == "submenu" else "btn"
                callback_key = button.submenu_key if button.kind == "submenu" and button.submenu_key else button.key
                rendered_row.append({"text": button.label, "callback_data": f"{callback_prefix}:{callback_key}"})
            if rendered_row:
                inline_keyboard.append(rendered_row)
        return {"inline_keyboard": inline_keyboard}

def build_terminal_button_key(menu_name: str, workflow_type: str, full_path: str) -> str:
    return f"{menu_name}|{workflow_type}|{full_path}"


def build_visibility_button_key(menu_name: str, node_kind: str, full_path: str) -> str:
    return f"{menu_name}|{node_kind}|{full_path}"


def build_level_layout_key(menu_name: str, full_path: str | None = None) -> str:
    normalized = (full_path or "").strip() or "__root__"
    return f"{menu_name}|level|{normalized}"



def available_menu_names(db: Session) -> tuple[str, ...]:
    names = set(db.execute(select(BotButtonSetting.menu_type).distinct()).scalars().all())
    names.update(db.execute(select(BotMenuDestinationBinding.menu_type).distinct()).scalars().all())
    profiles = db.execute(select(BotMenuProfile)).scalars().all()
    names.update(row.menu_type for row in profiles if row.is_active)
    names.difference_update(row.menu_type for row in profiles if not row.is_active)
    return tuple(sorted(name for name in names if name and not is_system_menu_type(name)))


ORDINARY_WORKFLOW_TYPES = ("ticket", "task", "auto")


def button_identity_path(row: BotButtonSetting) -> str:
    """A move changes location, not the path embedded in the stored key."""
    if row.button_kind != "terminal" or row.workflow_type not in ORDINARY_WORKFLOW_TYPES:
        return row.button_path
    for kind in (*ORDINARY_WORKFLOW_TYPES, "db"):
        prefix = f"{row.menu_type}|{kind}|"
        if row.button_key.startswith(prefix):
            return row.button_key[len(prefix):]
    return row.button_path


def ordinary_button_keys(row: BotButtonSetting) -> set[str]:
    if row.button_kind != "terminal" or row.workflow_type not in ORDINARY_WORKFLOW_TYPES or is_system_menu_type(row.menu_type):
        return set()
    return {build_terminal_button_key(row.menu_type, workflow, button_identity_path(row)) for workflow in ORDINARY_WORKFLOW_TYPES}


def button_key_aliases(row: BotButtonSetting, *, submenu: bool = False) -> set[str]:
    aliases = {row.button_key}
    if row.legacy_button_key:
        aliases.add(row.legacy_button_key)
    typed = ordinary_button_keys(row)
    if row.button_key in typed | {build_visibility_button_key(row.menu_type, "db", button_identity_path(row))}:
        aliases.update(typed)
    return aliases


def button_rows_by_key(rows: list[BotButtonSetting], *, submenu: bool = False) -> dict[str, BotButtonSetting | None]:
    result = {}
    for row in rows:
        for key in button_key_aliases(row, submenu=submenu):
            if key in result and result[key] is not row:
                result[key] = None
            else:
                result[key] = row
    return result


def resolve_button_row(db: Session, key: str, menu_name: str | None = None, *, submenu: bool = False,
                       include_inactive: bool = False, strict: bool = False) -> BotButtonSetting | None:
    if not key:
        return None
    query = select(BotButtonSetting)
    if menu_name:
        query = query.where(BotButtonSetting.menu_type == menu_name)
    by_key = button_rows_by_key(db.execute(query).scalars().all(), submenu=submenu)
    row = by_key.get(key)
    if strict and key in by_key and row is None:
        raise HTTPException(409, detail={"error": {"code": "ambiguous_button_key", "message": "Ключ кнопки неоднозначен"}})
    if row is not None and not include_inactive and (not row.currently_present or row.is_archived):
        return None
    return row


def button_key_matches(key):
    """SQL equivalent for historical ticket search and access filters."""
    ordinary = and_(BotButtonSetting.button_kind == "terminal",
                    BotButtonSetting.workflow_type.in_([literal_column(f"'{workflow}'") for workflow in ORDINARY_WORKFLOW_TYPES]),
                    ~BotButtonSetting.menu_type.startswith("__system__", autoescape=True))
    variants = []
    for stored_kind in (*ORDINARY_WORKFLOW_TYPES, "db"):
        prefix = BotButtonSetting.menu_type + f"|{stored_kind}|"
        path = func.substr(BotButtonSetting.button_key, func.length(prefix) + 1)
        variants.append(and_(func.substr(BotButtonSetting.button_key, 1, func.length(prefix)) == prefix,
            or_(*(key == BotButtonSetting.menu_type + f"|{workflow}|" + path
                  for workflow in ORDINARY_WORKFLOW_TYPES))))
    return or_(BotButtonSetting.button_key == key, BotButtonSetting.legacy_button_key == key,
               and_(ordinary, or_(*variants)))
