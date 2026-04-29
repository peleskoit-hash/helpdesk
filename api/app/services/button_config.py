from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


BUTTON_CONFIG_DIR = Path(os.getenv("BUTTON_CONFIG_DIR", "/button-config"))
DEFAULT_MENU_NAME = "Руководство"
BASIC_MENU_NAME = "Базовое"
ALLOWED_KINDS_RU = {
    "Задача",
    "Заявка",
    "Автоматическая заявка",
    "Подменю",
    "групповая задача",
}
BASIC_PROBLEM_TYPES = (
    "Проблема со сделкой",
    "Проблема с техникой",
    "Проблема с доступами",
    "Проблема с раздачей",
)


@dataclass(frozen=True)
class ButtonSpec:
    key: str
    label: str
    kind: str
    text: str | None = None
    system_text: str | None = None
    submenu_key: str | None = None
    subtasks: tuple["ButtonSpec", ...] = ()


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


def _key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _extract_quoted(segment: str, marker: str) -> str | None:
    marker_index = segment.find(marker)
    if marker_index < 0:
        return None
    after_marker = segment[marker_index + len(marker):]
    start = after_marker.find('"')
    if start < 0:
        return None
    pos = start + 1
    escaped = False
    while pos < len(after_marker):
        char = after_marker[pos]
        if char == "\\" and not escaped:
            escaped = True
            pos += 1
            continue
        if char == '"' and not escaped:
            return after_marker[start + 1:pos]
        escaped = False
        pos += 1
    return after_marker[start + 1:]


def _normalize_kind(raw_kind: str) -> str:
    value = raw_kind.strip().lower()
    if value == "задача":
        return "task"
    if value == "заявка":
        return "ticket"
    if value == "автоматическая заявка":
        return "auto"
    if value == "подменю":
        return "submenu"
    if value == "групповая задача":
        return "group_task"
    return value


def _top_menu_rows(raw: str, button_keys_by_label: dict[str, str]) -> tuple[tuple[str, ...], ...]:
    before_details = raw.split("Далее", 1)[0]
    rows: list[tuple[str, ...]] = []
    for line in before_details.splitlines()[1:]:
        labels = re.findall(r"\[([^\]]+)\]", line)
        keys = tuple(button_keys_by_label[label] for label in labels if label in button_keys_by_label)
        if keys:
            rows.append(keys)
    return tuple(rows)


def _parse_submenu_items(segment: str, menu_name: str, parent_label: str) -> tuple[ButtonSpec, ...]:
    if "Кнопки подменю:" not in segment:
        return ()
    sub_segment = segment.split("Кнопки подменю:", 1)[1]
    specs: list[ButtonSpec] = []
    for line in sub_segment.splitlines():
        match = re.match(r"\s*\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+),\s*текст\s*:\s*(.*)$", line)
        if not match:
            continue
        label, raw_kind, tail = match.groups()
        specs.append(
            ButtonSpec(
                key=_key(menu_name, parent_label, label),
                label=label,
                kind=_normalize_kind(raw_kind),
                text=_strip_quotes(tail),
            )
        )
    return tuple(specs)


def _parse_group_subtasks(segment: str, menu_name: str, parent_label: str) -> tuple[ButtonSpec, ...]:
    if "Подзадачи:" not in segment:
        return ()
    sub_segment = segment.split("Подзадачи:", 1)[1]
    if "Конец подзадач" in sub_segment:
        sub_segment = sub_segment.split("Конец подзадач", 1)[0]

    header_pattern = re.compile(r"(?m)^\s+\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+),\s*текст системный\s*:")
    matches = list(header_pattern.finditer(sub_segment))
    specs: list[ButtonSpec] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(sub_segment)
        child_segment = sub_segment[match.start():next_start]
        label, raw_kind = match.groups()
        specs.append(
            ButtonSpec(
                key=_key(menu_name, parent_label, label),
                label=label,
                kind=_normalize_kind(raw_kind),
                system_text=_extract_quoted(child_segment, "текст системный:"),
            )
        )
    return tuple(specs)


def _parse_buttons(raw: str, menu_name: str) -> tuple[dict[str, ButtonSpec], dict[str, str], dict[str, MenuSpec]]:
    header_pattern = re.compile(r"(?m)^\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+)(?:,\s*текст\s*:|\s*,?\s*)")
    matches = list(header_pattern.finditer(raw))
    buttons: dict[str, ButtonSpec] = {}
    button_keys_by_label: dict[str, str] = {}
    menus: dict[str, MenuSpec] = {}

    for index, match in enumerate(matches):
        label, raw_kind = match.groups()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        segment = raw[start:end]
        kind = _normalize_kind(raw_kind)
        key = _key(menu_name, label)
        text = _extract_quoted(segment, "текст:")
        system_text = _extract_quoted(segment, "Текст системный:") or _extract_quoted(segment, "текст системный:")
        submenu_key = None
        subtasks: tuple[ButtonSpec, ...] = ()

        if kind == "submenu":
            submenu_key = _key(menu_name, label, "submenu")
            submenu_items = _parse_submenu_items(segment, menu_name, label)
            subtasks = submenu_items
            for item in submenu_items:
                buttons[item.key] = item
                button_keys_by_label[item.label] = item.key
            menus[submenu_key] = MenuSpec(
                key=submenu_key,
                title=label,
                text=text,
                rows=tuple((item.key,) for item in submenu_items),
            )
        elif kind == "group_task":
            subtasks = _parse_group_subtasks(segment, menu_name, label)

        spec = ButtonSpec(
            key=key,
            label=label,
            kind=kind,
            text=text,
            system_text=system_text,
            submenu_key=submenu_key,
            subtasks=subtasks,
        )
        buttons[key] = spec
        button_keys_by_label[label] = key

    return buttons, button_keys_by_label, menus


def _config_path(menu_name: str) -> Path:
    return BUTTON_CONFIG_DIR / f"{menu_name}.txt"


def _basic_button_config() -> ButtonConfig:
    buttons = {
        _key(BASIC_MENU_NAME, label): ButtonSpec(
            key=_key(BASIC_MENU_NAME, label),
            label=label,
            kind="ticket",
        )
        for label in BASIC_PROBLEM_TYPES
    }
    keys = list(buttons.keys())
    root_menu = MenuSpec(
        key="root",
        title=BASIC_MENU_NAME,
        text="Выберите тип проблемы",
        rows=(tuple(keys[0:2]), tuple(keys[2:4])),
    )
    return ButtonConfig(menus={"root": root_menu}, buttons=buttons, root_menu_key="root")


@lru_cache(maxsize=16)
def load_button_config(menu_name: str = DEFAULT_MENU_NAME) -> ButtonConfig:
    if menu_name == BASIC_MENU_NAME:
        return _basic_button_config()

    path = _config_path(menu_name)
    if not path.exists():
        raise FileNotFoundError(f"Button config not found: {path}")

    raw = path.read_text(encoding="utf-8")
    buttons, button_keys_by_label, menus = _parse_buttons(raw, menu_name)
    root_rows = _top_menu_rows(raw, button_keys_by_label)
    root_menu = MenuSpec(key="root", title=menu_name, text="Выберите тип проблемы", rows=root_rows)
    menus = {"root": root_menu, **menus}
    return ButtonConfig(menus=menus, buttons=buttons, root_menu_key="root")


def find_button(button_key: str) -> ButtonSpec | None:
    for menu_name in (DEFAULT_MENU_NAME, BASIC_MENU_NAME):
        config = load_button_config(menu_name)
        button = config.buttons.get(button_key)
        if button is not None:
            return button
    return None


def find_menu(menu_key: str) -> tuple[str, MenuSpec] | None:
    for menu_name in (DEFAULT_MENU_NAME, BASIC_MENU_NAME):
        config = load_button_config(menu_name)
        menu = config.menus.get(menu_key)
        if menu is not None:
            return menu_name, menu
    return None


def clear_button_config_cache() -> None:
    load_button_config.cache_clear()
