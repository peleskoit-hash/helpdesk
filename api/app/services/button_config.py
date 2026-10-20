"""Shared button values and stable key helpers. Button data lives in the database."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
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


def resolve_button_row(db: Session, key: str, menu_name: str | None = None, *, submenu: bool = False) -> BotButtonSetting | None:
    query = select(BotButtonSetting).where(
        BotButtonSetting.currently_present.is_(True), BotButtonSetting.is_archived.is_(False),
    )
    if menu_name:
        query = query.where(BotButtonSetting.menu_type == menu_name)
    rows = db.execute(query).scalars().all()
    for row in rows:
        if key == row.button_key or key == row.legacy_button_key:
            return row
    # Old Telegram keyboards and old tickets may carry the original hashed key.
    # Reconstruct it from the unchanged DB path; never read a TXT configuration.
    matches = []
    for row in rows:
        if row.is_db_created:
            continue
        path = (row.button_path or row.button_label).split("/")
        parts = [row.menu_type, path[-1], "submenu"] if submenu else [row.menu_type, *path[-2:]]
        alias = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
        if key == alias:
            matches.append(row)
    return matches[0] if len(matches) == 1 else None
