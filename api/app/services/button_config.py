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
    "Автоматическая задача",
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


def _extract_line_value(segment: str, marker: str) -> str | None:
    pattern = re.compile(rf"(?mi)^\s*{re.escape(marker)}\s*(.+?)\s*$")
    match = pattern.search(segment)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _normalize_kind(raw_kind: str) -> str:
    value = raw_kind.strip().lower()
    if value == "задача":
        return "task"
    if value == "заявка":
        return "ticket"
    if value in {"автоматическая заявка", "автоматическая задача"}:
        return "auto"
    if value == "подменю":
        return "submenu"
    if value == "групповая задача":
        return "group_task"
    return value


def _parse_button_segment(
    segment: str,
    *,
    menu_name: str,
    label: str,
    raw_kind: str,
    parent_label: str | None = None,
) -> ButtonSpec:
    kind = _normalize_kind(raw_kind)
    key_parts = [menu_name, label] if parent_label is None else [menu_name, parent_label, label]
    key = _key(*key_parts)
    text = _extract_quoted(segment, "текст:")
    confirmation_text = _extract_quoted(segment, "Подтверждение:") or _extract_quoted(segment, "подтверждение:")
    system_text = _extract_quoted(segment, "Текст системный:") or _extract_quoted(segment, "текст системный:")
    instruction_text = _extract_quoted(segment, "Инструкция:") or _extract_quoted(segment, "инструкция:")
    automation_key = _extract_line_value(segment, "Автоматизация:")
    forced_assignee_type = None
    forced_assignee_value = None
    forced_raw = _extract_line_value(segment, "Ответственный:")
    if forced_raw:
        forced_match = re.match(r"^(login|user_id)\s*:\s*(.+)$", forced_raw.strip(), flags=re.IGNORECASE)
        if forced_match is None:
            raise ValueError(f"Некорректный формат 'Ответственный:' для кнопки [{label}]")
        forced_assignee_type = forced_match.group(1).lower()
        forced_assignee_value = forced_match.group(2).strip()
        if not forced_assignee_value:
            raise ValueError(f"Пустое значение 'Ответственный:' для кнопки [{label}]")
    direct_file = None
    for raw_line in segment.splitlines():
        line = raw_line.strip()
        direct_file_match = re.match(r'^(?:фаил|файл)\s*:\s*"([^"]+)"\s*$', line, flags=re.IGNORECASE)
        if direct_file_match:
            direct_file = direct_file_match.group(1).strip()
            break
    file_rules: list[dict[str, str]] = []
    for raw_line in segment.splitlines():
        line = raw_line.strip()
        match = re.match(
            r'^если\s+\{\{([a-zA-Z0-9_]+)\}\}\s*=\s*([^,]+)\s*,\s*(?:фаил|файл)\s*:\s*"([^"]+)"\s*$',
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        field_name, expected_value, file_name = match.groups()
        file_rules.append(
            {
                "field": field_name.strip(),
                "equals": expected_value.strip(),
                "file": file_name.strip(),
            }
        )

    return ButtonSpec(
        key=key,
        label=label,
        kind=kind,
        text=text,
        confirmation_text=confirmation_text,
        system_text=system_text,
        instruction_text=instruction_text,
        automation_key=automation_key,
        forced_assignee_type=forced_assignee_type,
        forced_assignee_value=forced_assignee_value,
        file_path=direct_file,
        file_rules=tuple(file_rules),
    )


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
    header_pattern = re.compile(r"(?m)^\s*\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+)(?:,\s*текст\s*:|\s*,?\s*)")
    matches = list(header_pattern.finditer(sub_segment))
    specs: list[ButtonSpec] = []
    for index, match in enumerate(matches):
        label, raw_kind = match.groups()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(sub_segment)
        child_segment = sub_segment[start:end]
        specs.append(
            _parse_button_segment(
                child_segment,
                menu_name=menu_name,
                label=label,
                raw_kind=raw_kind,
                parent_label=parent_label,
            )
        )
    return tuple(specs)


def _parse_group_subtasks(segment: str, menu_name: str, parent_label: str) -> tuple[ButtonSpec, ...]:
    if "Подзадачи:" not in segment:
        return ()
    sub_segment = segment.split("Подзадачи:", 1)[1]
    if "Конец подзадач" in sub_segment:
        sub_segment = sub_segment.split("Конец подзадач", 1)[0]

    header_pattern = re.compile(r"(?m)^\s+\[([^\]]+)\]\s*-\s*тип\s*([^,\n]+)(?:,\s*(?:инструкция|текст системный)\s*:|\s*,?\s*)", flags=re.IGNORECASE)
    matches = list(header_pattern.finditer(sub_segment))
    specs: list[ButtonSpec] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(sub_segment)
        child_segment = sub_segment[match.start():next_start]
        label, raw_kind = match.groups()
        forced_assignee_type = None
        forced_assignee_value = None
        forced_raw = _extract_line_value(child_segment, "Ответственный:")
        if forced_raw:
            forced_match = re.match(r"^(login|user_id)\s*:\s*(.+)$", forced_raw.strip(), flags=re.IGNORECASE)
            if forced_match is None:
                raise ValueError(f"Некорректный формат 'Ответственный:' для подзадачи [{label}]")
            forced_assignee_type = forced_match.group(1).lower()
            forced_assignee_value = forced_match.group(2).strip()
            if not forced_assignee_value:
                raise ValueError(f"Пустое значение 'Ответственный:' для подзадачи [{label}]")
        specs.append(
            ButtonSpec(
                key=_key(menu_name, parent_label, label),
                label=label,
                kind=_normalize_kind(raw_kind),
                system_text=(
                    _extract_quoted(child_segment, "Текст системный:")
                    or _extract_quoted(child_segment, "текст системный:")
                ),
                instruction_text=(
                    _extract_quoted(child_segment, "Инструкция:")
                    or _extract_quoted(child_segment, "инструкция:")
                ),
                forced_assignee_type=forced_assignee_type,
                forced_assignee_value=forced_assignee_value,
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
        parsed = _parse_button_segment(
            segment,
            menu_name=menu_name,
            label=label,
            raw_kind=raw_kind,
        )
        kind = parsed.kind
        key = parsed.key
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
                text=parsed.text,
                rows=tuple((item.key,) for item in submenu_items),
            )
        elif kind == "group_task":
            subtasks = _parse_group_subtasks(segment, menu_name, label)

        spec = ButtonSpec(
            key=key,
            label=label,
            kind=kind,
            text=parsed.text,
            confirmation_text=parsed.confirmation_text,
            system_text=parsed.system_text,
            instruction_text=parsed.instruction_text,
            automation_key=parsed.automation_key,
            forced_assignee_type=parsed.forced_assignee_type,
            forced_assignee_value=parsed.forced_assignee_value,
            file_path=parsed.file_path,
            file_rules=parsed.file_rules,
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


def available_menu_names() -> tuple[str, ...]:
    names: list[str] = [DEFAULT_MENU_NAME, BASIC_MENU_NAME]
    if BUTTON_CONFIG_DIR.exists():
        for path in BUTTON_CONFIG_DIR.glob("*.txt"):
            if not path.is_file() or path.parent != BUTTON_CONFIG_DIR:
                continue
            menu_name = path.stem.strip()
            if menu_name and menu_name not in names:
                names.append(menu_name)
    return tuple(names)


def find_button(button_key: str) -> ButtonSpec | None:
    for menu_name in available_menu_names():
        config = load_button_config(menu_name)
        button = config.buttons.get(button_key)
        if button is not None:
            return button
    return None


def find_menu(menu_key: str) -> tuple[str, MenuSpec] | None:
    for menu_name in available_menu_names():
        config = load_button_config(menu_name)
        menu = config.menus.get(menu_key)
        if menu is not None:
            return menu_name, menu
    return None


def clear_button_config_cache() -> None:
    load_button_config.cache_clear()


def build_terminal_button_key(menu_name: str, workflow_type: str, full_path: str) -> str:
    return f"{menu_name}|{workflow_type}|{full_path}"


def build_visibility_button_key(menu_name: str, node_kind: str, full_path: str) -> str:
    return f"{menu_name}|{node_kind}|{full_path}"


def build_level_layout_key(menu_name: str, full_path: str | None = None) -> str:
    normalized = (full_path or "").strip() or "__root__"
    return f"{menu_name}|level|{normalized}"


def list_terminal_buttons(menu_name: str) -> tuple[TerminalButtonRef, ...]:
    config = load_button_config(menu_name)
    root = config.menus.get(config.root_menu_key)
    if root is None:
        return ()
    result: list[TerminalButtonRef] = []
    for row in root.rows:
        for button_key in row:
            button = config.buttons.get(button_key)
            if button is None:
                continue
            if button.kind == "submenu":
                for child in button.subtasks:
                    if child.kind not in {"ticket", "task", "auto"}:
                        continue
                    full_path = f"{button.label}/{child.label}"
                    result.append(
                        TerminalButtonRef(
                            source_button_key=child.key,
                            label=child.label,
                            workflow_type=child.kind,
                            full_path=full_path,
                            technical_key=build_terminal_button_key(menu_name, child.kind, full_path),
                            instruction_text=child.instruction_text,
                        )
                    )
                continue
            if button.kind == "group_task":
                for child in button.subtasks:
                    full_path = f"{button.label}/{child.label}"
                    result.append(
                        TerminalButtonRef(
                            source_button_key=child.key,
                            label=child.label,
                            workflow_type="group_task_child",
                            full_path=full_path,
                            technical_key=build_terminal_button_key(menu_name, "group_task_child", full_path),
                            instruction_text=child.instruction_text,
                        )
                    )
                continue
            if button.kind in {"ticket", "task", "auto"}:
                result.append(
                    TerminalButtonRef(
                        source_button_key=button.key,
                        label=button.label,
                        workflow_type=button.kind,
                        full_path=button.label,
                        technical_key=build_terminal_button_key(menu_name, button.kind, button.label),
                        instruction_text=button.instruction_text,
                    )
                )
    return tuple(result)

