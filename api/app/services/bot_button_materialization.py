from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting
from .button_config import (
    ButtonConfig,
    ButtonSpec,
    available_menu_names,
    build_terminal_button_key,
    build_visibility_button_key,
    list_terminal_buttons,
    load_button_config,
)


@dataclass(frozen=True)
class MaterializationSource:
    menu_type: str
    button_key: str
    workflow_type: str
    button_kind: str
    button_path: str
    button_label: str
    source_button_key: str
    source: ButtonSpec
    menu_text: str | None = None


@dataclass
class MaterializationSummary:
    buttons_found: int = 0
    submenus_found: int = 0
    rows_to_create: int = 0
    submenu_rows_to_create: int = 0
    rows_created: int = 0
    rows_updated: int = 0
    submenu_text_changes: int = 0
    field_changes: dict[str, int] = field(
        default_factory=lambda: {
            "client_text": 0,
            "system_text": 0,
            "automation_key": 0,
            "files": 0,
            "conditional_files": 0,
            "confirmation_text": 0,
        }
    )
    skipped_overrides: int = 0
    submenu_skipped_overrides: int = 0
    skipped_db_created_or_archived: int = 0
    submenu_skipped_db_created: int = 0
    skipped_ambiguous: int = 0
    submenu_skipped_ambiguous: int = 0
    apply_blocked: bool = False
    warnings: list[str] = field(default_factory=list)
    warning_details: list[dict[str, Any]] = field(default_factory=list)


MATERIALIZED_FIELDS = (
    "client_text",
    "system_text",
    "automation_key",
    "files",
    "conditional_files",
    "confirmation_text",
)


def _is_submenu_source(source: MaterializationSource) -> bool:
    return source.workflow_type == "submenu" and source.button_kind == "section"


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.strip() else None


def _normalize_scalar(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


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


def _normalize_files(file_path: str | None) -> list[str]:
    normalized = _normalize_relative_button_file_path(file_path or "")
    return [normalized] if normalized else []


def _normalize_conditional_rules(file_rules: tuple[dict[str, str], ...] | list[dict] | None) -> list[dict]:
    if not file_rules:
        return []
    result: list[dict] = []
    for rule in file_rules:
        if not isinstance(rule, dict):
            continue
        field_name = str(rule.get("field") or "").strip()
        file_name = _normalize_relative_button_file_path(str(rule.get("file") or ""))
        equals = str(rule.get("equals") or "").strip()
        if not field_name or not file_name:
            continue
        result.append(
            {
                "conditions": [{"field": field_name, "operator": "eq", "value": equals}],
                "files": [file_name],
            }
        )
    return result


def _collect_group_task_sources(menu_name: str, config: ButtonConfig) -> list[MaterializationSource]:
    result: list[MaterializationSource] = []

    def walk(menu_key: str | None, parent_path: str = "") -> None:
        menu = config.menus.get(menu_key or config.root_menu_key)
        if menu is None:
            return
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                if button.kind == "submenu" and button.submenu_key:
                    walk(button.submenu_key, full_path)
                    continue
                if button.kind != "group_task":
                    continue
                result.append(
                    MaterializationSource(
                        menu_type=menu_name,
                        button_key=build_visibility_button_key(menu_name, "group_task", full_path),
                        workflow_type="group_task",
                        button_kind="group_parent",
                        button_path=full_path,
                        button_label=button.label,
                        source_button_key=button.key,
                        source=button,
                    )
                )

    walk(config.root_menu_key, "")
    return result


def _collect_submenu_sources(menu_name: str, config: ButtonConfig) -> list[MaterializationSource]:
    result: list[MaterializationSource] = []

    def walk(menu_key: str | None, parent_path: str = "") -> None:
        menu = config.menus.get(menu_key or config.root_menu_key)
        if menu is None:
            return
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None or button.kind != "submenu" or not button.submenu_key:
                    continue
                full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                submenu_menu = config.menus.get(button.submenu_key)
                result.append(
                    MaterializationSource(
                        menu_type=menu_name,
                        button_key=build_visibility_button_key(menu_name, "submenu", full_path),
                        workflow_type="submenu",
                        button_kind="section",
                        button_path=full_path,
                        button_label=button.label,
                        source_button_key=button.key,
                        source=button,
                        menu_text=submenu_menu.text if submenu_menu is not None else None,
                    )
                )
                walk(button.submenu_key, full_path)

    walk(config.root_menu_key, "")
    return result


def _resolve_group_task_child_source(
    menu_name: str,
    config: ButtonConfig,
    ref,
) -> tuple[ButtonSpec | None, dict[str, Any] | None]:
    matches: list[tuple[str, str, ButtonSpec]] = []

    def walk(menu_key: str | None, parent_path: str = "") -> None:
        menu = config.menus.get(menu_key or config.root_menu_key)
        if menu is None:
            return
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                if button.kind == "submenu" and button.submenu_key:
                    walk(button.submenu_key, full_path)
                    continue
                if button.kind != "group_task":
                    continue
                parent_key = build_visibility_button_key(menu_name, "group_task", full_path)
                for child in button.subtasks:
                    child_path = f"{full_path}/{child.label}"
                    child_key = build_terminal_button_key(menu_name, "group_task_child", child_path)
                    if child_key == ref.technical_key:
                        matches.append((parent_key, full_path, child))

    walk(config.root_menu_key, "")
    if len(matches) == 1:
        return matches[0][2], None

    parent_key = ref.full_path.rsplit("/", 1)[0] if "/" in ref.full_path else ""
    detail = {
        "menu_type": menu_name,
        "parent_key": build_visibility_button_key(menu_name, "group_task", parent_key) if parent_key else None,
        "child_button_key": ref.technical_key,
        "child_label": ref.label,
        "found_db_key": ref.technical_key,
        "reason": (
            "group_task_child source not found in TXT tree"
            if not matches
            else f"group_task_child mapping is ambiguous ({len(matches)} matches)"
        ),
    }
    return None, detail


def collect_materialization_sources() -> tuple[list[MaterializationSource], list[dict[str, Any]]]:
    sources: list[MaterializationSource] = []
    warnings: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, str], MaterializationSource] = {}

    for menu_name in available_menu_names():
        try:
            config = load_button_config(menu_name)
        except Exception as exc:
            warnings.append(
                {
                    "menu_type": menu_name,
                    "parent_key": None,
                    "child_button_key": None,
                    "child_label": None,
                    "found_db_key": None,
                    "reason": f"failed to load config ({type(exc).__name__})",
                }
            )
            continue

        for ref in list_terminal_buttons(menu_name):
            source_button = config.buttons.get(ref.source_button_key)
            warning_detail = None
            if source_button is None and ref.workflow_type == "group_task_child":
                source_button, warning_detail = _resolve_group_task_child_source(menu_name, config, ref)
            if source_button is None:
                warnings.append(
                    warning_detail
                    or {
                        "menu_type": menu_name,
                        "parent_key": None,
                        "child_button_key": ref.technical_key,
                        "child_label": ref.label,
                        "found_db_key": ref.technical_key,
                        "reason": f"source button not found for {ref.source_button_key}",
                    }
                )
                continue
            source = MaterializationSource(
                menu_type=menu_name,
                button_key=ref.technical_key,
                workflow_type=ref.workflow_type,
                button_kind="terminal",
                button_path=ref.full_path,
                button_label=ref.label,
                source_button_key=ref.source_button_key,
                source=source_button,
            )
            seen = seen_keys.get((menu_name, ref.technical_key))
            if seen is not None:
                warnings.append(
                    {
                        "menu_type": menu_name,
                        "parent_key": None,
                        "child_button_key": ref.technical_key,
                        "child_label": ref.label,
                        "found_db_key": ref.technical_key,
                        "reason": "duplicate terminal mapping",
                    }
                )
                continue
            seen_keys[(menu_name, ref.technical_key)] = source
            sources.append(source)

        for source in _collect_group_task_sources(menu_name, config):
            seen = seen_keys.get((menu_name, source.button_key))
            if seen is not None:
                warnings.append(
                    {
                        "menu_type": menu_name,
                        "parent_key": source.button_key,
                        "child_button_key": None,
                        "child_label": source.button_label,
                        "found_db_key": source.button_key,
                        "reason": "duplicate group_task mapping",
                    }
                )
                continue
            seen_keys[(menu_name, source.button_key)] = source
            sources.append(source)

        for source in _collect_submenu_sources(menu_name, config):
            seen = seen_keys.get((menu_name, source.button_key))
            if seen is not None:
                warnings.append(
                    {
                        "menu_type": menu_name,
                        "parent_key": None,
                        "child_button_key": None,
                        "child_label": source.button_label,
                        "found_db_key": source.button_key,
                        "submenu_button_key": source.button_key,
                        "button_path": source.button_path,
                        "reason": "duplicate submenu mapping",
                    }
                )
                continue
            seen_keys[(menu_name, source.button_key)] = source
            sources.append(source)

    return sources, warnings


def _materialized_targets(source: MaterializationSource) -> dict[str, object]:
    spec = source.source
    client_text_source = source.menu_text if source.workflow_type == "submenu" and source.button_kind == "section" else spec.text
    confirmation_text = _normalize_text(spec.confirmation_text)
    return {
        "client_text": _normalize_text(client_text_source),
        "client_text_overridden": True,
        "system_text": _normalize_text(spec.system_text),
        "system_text_overridden": True,
        "automation_key": _normalize_scalar(spec.automation_key),
        "automation_overridden": True,
        "files_json": _normalize_files(spec.file_path),
        "files_overridden": True,
        "conditional_files_json": _normalize_conditional_rules(spec.file_rules),
        "conditional_files_overridden": True,
        "confirmation_text": confirmation_text,
        "confirmation_enabled": bool(confirmation_text),
        "confirmation_overridden": True,
    }


def _create_row_from_source(source: MaterializationSource) -> BotButtonSetting:
    targets = _materialized_targets(source)
    now = datetime.utcnow()
    is_submenu = _is_submenu_source(source)
    return BotButtonSetting(
        id=str(uuid.uuid4()),
        stable_button_id=str(uuid.uuid4()),
        button_key=source.button_key,
        legacy_button_key=None,
        menu_type=source.menu_type,
        workflow_type=source.workflow_type,
        button_path=source.button_path,
        button_label=source.button_label,
        label_override=None,
        label_overridden=False,
        currently_present=True,
        is_db_created=False,
        button_kind=source.button_kind,
        parent_path=source.button_path.rsplit("/", 1)[0] if "/" in source.button_path else "",
        is_archived=False,
        deleted_at=None,
        deleted_by_user_id=None,
        client_text=targets["client_text"],
        client_text_overridden=True,
        system_text=None if is_submenu else targets["system_text"],
        system_text_overridden=False if is_submenu else True,
        confirmation_text=None if is_submenu else targets["confirmation_text"],
        confirmation_enabled=False if is_submenu else bool(targets["confirmation_enabled"]),
        confirmation_overridden=False if is_submenu else True,
        files_json=None if is_submenu else targets["files_json"],
        files_overridden=False if is_submenu else True,
        conditional_files_json=None if is_submenu else targets["conditional_files_json"],
        conditional_files_overridden=False if is_submenu else True,
        automation_key=None if is_submenu else targets["automation_key"],
        automation_overridden=False if is_submenu else True,
        created_at=now,
        updated_at=now,
    )


def _row_should_be_skipped(row: BotButtonSetting, source: MaterializationSource) -> bool:
    if bool(row.is_archived) or not bool(row.currently_present):
        return True
    if bool(row.is_db_created):
        return True
    if row.menu_type != source.menu_type:
        return True
    return False


def _set_field_if_needed(
    row: BotButtonSetting,
    *,
    summary_key: str,
    field_name: str,
    override_name: str,
    target_value: object,
    summary: MaterializationSummary,
) -> bool:
    if bool(getattr(row, override_name)):
        summary.skipped_overrides += 1
        return False
    current_value = getattr(row, field_name)
    changed = current_value != target_value or not bool(getattr(row, override_name))
    setattr(row, field_name, target_value)
    setattr(row, override_name, True)
    if changed:
        summary.field_changes[summary_key] += 1
    return changed


def _set_client_text_if_needed(
    row: BotButtonSetting,
    *,
    target_value: object,
    summary: MaterializationSummary,
    source: MaterializationSource,
) -> bool:
    if bool(row.client_text_overridden):
        summary.skipped_overrides += 1
        if _is_submenu_source(source):
            summary.submenu_skipped_overrides += 1
        return False
    current_value = getattr(row, "client_text")
    changed = current_value != target_value or not bool(getattr(row, "client_text_overridden"))
    setattr(row, "client_text", target_value)
    setattr(row, "client_text_overridden", True)
    if changed:
        summary.field_changes["client_text"] += 1
        if _is_submenu_source(source):
            summary.submenu_text_changes += 1
    return changed


def _set_confirmation_if_needed(
    row: BotButtonSetting,
    *,
    target_text: str | None,
    target_enabled: bool,
    summary: MaterializationSummary,
) -> bool:
    if bool(row.confirmation_overridden):
        summary.skipped_overrides += 1
        return False
    changed = (
        row.confirmation_text != target_text
        or bool(row.confirmation_enabled) != bool(target_enabled)
        or not bool(row.confirmation_overridden)
    )
    row.confirmation_text = target_text
    row.confirmation_enabled = bool(target_enabled)
    row.confirmation_overridden = True
    if changed:
        summary.field_changes["confirmation_text"] += 1
    return changed


def materialize_bot_button_settings(db: Session, *, apply: bool = False) -> MaterializationSummary:
    sources, warnings = collect_materialization_sources()
    summary = MaterializationSummary(
        buttons_found=len(sources),
        submenus_found=sum(1 for source in sources if _is_submenu_source(source)),
    )

    def process_rows() -> None:
        rows = {
            row.button_key: row
            for row in db.execute(select(BotButtonSetting)).scalars().all()
        }
        for warning in warnings:
            detail = dict(warning)
            db_row = None
            child_button_key = detail.get("child_button_key")
            if child_button_key:
                db_row = rows.get(str(child_button_key))
            if db_row is not None:
                detail["found_db_id"] = db_row.id
            summary.warning_details.append(detail)
            summary.warnings.append(
                "menu={menu_type} submenu_key={submenu_button_key} path={button_path} parent={parent_key} "
                "child_key={child_button_key} child_label={child_label} found_db_key={found_db_key} "
                "found_db_id={found_db_id} reason={reason}".format(
                    menu_type=detail.get("menu_type") or "-",
                    submenu_button_key=detail.get("submenu_button_key") or "-",
                    button_path=detail.get("button_path") or "-",
                    parent_key=detail.get("parent_key") or "-",
                    child_button_key=detail.get("child_button_key") or "-",
                    child_label=detail.get("child_label") or "-",
                    found_db_key=detail.get("found_db_key") or "-",
                    found_db_id=detail.get("found_db_id") or "-",
                    reason=detail.get("reason") or "-",
                )
            )
        if summary.warning_details:
            summary.skipped_ambiguous = len(summary.warning_details)
            summary.submenu_skipped_ambiguous = sum(1 for item in summary.warning_details if item.get("submenu_button_key"))
            if apply:
                summary.apply_blocked = True
                return

        for source in sources:
            row = rows.get(source.button_key)
            is_submenu = _is_submenu_source(source)
            if row is None:
                summary.rows_to_create += 1
                if is_submenu:
                    summary.submenu_rows_to_create += 1
                if not apply:
                    targets = _materialized_targets(source)
                    if is_submenu:
                        summary.field_changes["client_text"] += 1
                        summary.submenu_text_changes += 1
                    else:
                        for field_name in MATERIALIZED_FIELDS:
                            if field_name == "files":
                                summary.field_changes["files"] += 1
                            elif field_name == "conditional_files":
                                summary.field_changes["conditional_files"] += 1
                            elif field_name == "confirmation_text":
                                summary.field_changes["confirmation_text"] += 1
                            else:
                                summary.field_changes[field_name] += 1
                    continue
                row = _create_row_from_source(source)
                db.add(row)
                rows[source.button_key] = row
                summary.rows_created += 1
                if is_submenu:
                    summary.field_changes["client_text"] += 1
                    summary.submenu_text_changes += 1
                else:
                    for field_name in MATERIALIZED_FIELDS:
                        summary.field_changes[field_name] += 1
                continue

            if _row_should_be_skipped(row, source):
                summary.skipped_db_created_or_archived += 1
                if is_submenu:
                    summary.submenu_skipped_db_created += 1
                continue

            targets = _materialized_targets(source)
            changed_any = False
            changed_any |= _set_client_text_if_needed(
                row,
                target_value=targets["client_text"],
                summary=summary,
                source=source,
            )
            if not is_submenu:
                changed_any |= _set_field_if_needed(
                    row,
                    summary_key="system_text",
                    field_name="system_text",
                    override_name="system_text_overridden",
                    target_value=targets["system_text"],
                    summary=summary,
                )
                changed_any |= _set_field_if_needed(
                    row,
                    summary_key="automation_key",
                    field_name="automation_key",
                    override_name="automation_overridden",
                    target_value=targets["automation_key"],
                    summary=summary,
                )
                changed_any |= _set_field_if_needed(
                    row,
                    summary_key="files",
                    field_name="files_json",
                    override_name="files_overridden",
                    target_value=targets["files_json"],
                    summary=summary,
                )
                changed_any |= _set_field_if_needed(
                    row,
                    summary_key="conditional_files",
                    field_name="conditional_files_json",
                    override_name="conditional_files_overridden",
                    target_value=targets["conditional_files_json"],
                    summary=summary,
                )
                changed_any |= _set_confirmation_if_needed(
                    row,
                    target_text=targets["confirmation_text"],  # type: ignore[arg-type]
                    target_enabled=bool(targets["confirmation_enabled"]),
                    summary=summary,
                )
            if changed_any:
                row.updated_at = datetime.utcnow()
                summary.rows_updated += 1

    if apply:
        with db.begin():
            process_rows()
    else:
        process_rows()
    return summary
