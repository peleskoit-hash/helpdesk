from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BotButtonSetting, BotMenuLevelLayout, BotMenuProfile, BotMenuDestinationBinding, MessageType, Ticket, TicketStatus, TicketTaskKind, TicketWorkflowType
from .automatic_messages import render_automatic_message_text
from .button_config import BASIC_MENU_NAME, ButtonConfig, MenuSpec, ButtonSpec, build_level_layout_key
from .statistics_lifecycle import sync_work_interval_after_unfreeze
from .ticket_messages import persist_client_delivery_failure
from .telegram_text_format import strip_telegram_html
from .system_tickets import is_system_menu_type


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _http_error_code(exc: HTTPException) -> str | None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code else None


def menu_name_for_ticket_client(
    db: Session,
    ticket: Ticket,
    *,
    get_or_create_client_profile_fn,
) -> str:
    profiles = db.execute(select(BotMenuProfile)).scalars().all()
    inactive = {row.menu_type for row in profiles if not row.is_active}
    available = {row[0] for row in db.execute(select(BotButtonSetting.menu_type).where(
        BotButtonSetting.currently_present.is_(True), BotButtonSetting.is_archived.is_(False),
    )).all()} - inactive
    available.update(row.menu_type for row in profiles if row.is_active)
    available = {name for name in available if not is_system_menu_type(name)}
    fallback = next((row.menu_type for row in profiles if row.is_active and row.is_fallback and row.menu_type in available), BASIC_MENU_NAME)
    for key in (ticket.instruction_button_key, ticket.button_config_key):
        if not key:
            continue
        row = db.execute(select(BotButtonSetting).where(
            (BotButtonSetting.button_key == key) | (BotButtonSetting.legacy_button_key == key),
        )).scalar_one_or_none()
        if row is not None and row.menu_type in available:
            return row.menu_type

    profile = get_or_create_client_profile_fn(db, ticket.client_id)
    if profile.is_verified and profile.destination:
        destination = profile.destination.strip()
        binding = db.execute(select(BotMenuDestinationBinding).where(
            BotMenuDestinationBinding.destination_value == destination,
        )).scalar_one_or_none()
        if binding is not None:
            return binding.menu_type if binding.menu_type in available else fallback
        if destination in available:
            return destination
    return fallback


def _db_button_config(db: Session, menu_name: str) -> ButtonConfig:
    rows = db.execute(select(BotButtonSetting).where(
        BotButtonSetting.menu_type == menu_name,
        BotButtonSetting.currently_present.is_(True),
        BotButtonSetting.is_archived.is_(False),
    ).order_by(BotButtonSetting.updated_at, BotButtonSetting.button_label)).scalars().all()
    by_parent: dict[str, list[BotButtonSetting]] = {}
    for row in rows:
        if row.visibility_overridden and not row.is_enabled:
            continue
        if row.workflow_type == "group_task_child":
            continue
        by_parent.setdefault((row.parent_path or "").strip(), []).append(row)
    layouts = {row.level_key: row.layout_json for row in db.execute(select(BotMenuLevelLayout).where(
        BotMenuLevelLayout.menu_type == menu_name,
    )).scalars().all()}
    config = ButtonConfig()

    def build(parent: str, menu_key: str, ancestors: set[str]) -> None:
        items: dict[str, BotButtonSetting] = {}
        for row in by_parent.get(parent, []):
            kind = "submenu" if row.button_kind == "section" and row.workflow_type != "group_task" else row.workflow_type
            if kind == "submenu":
                if row.button_path in ancestors:
                    continue
                build(row.button_path, row.button_key, ancestors | {row.button_path})
                if not config.menus[row.button_key].rows:
                    continue
            label = row.label_override if row.label_overridden and (row.label_override or "").strip() else row.button_label
            config.buttons[row.button_key] = ButtonSpec(
                key=row.button_key, label=label, kind=kind,
                submenu_key=row.button_key if kind == "submenu" else None,
            )
            items[row.button_key] = row
        menu_rows = []
        used = set()
        for layout_row in layouts.get(build_level_layout_key(menu_name, parent)) or []:
            if not isinstance(layout_row, dict) or not isinstance(layout_row.get("button_keys"), list):
                continue
            keys = []
            for key in layout_row["button_keys"]:
                if key in items and key not in used:
                    keys.append(key)
                    used.add(key)
            if keys:
                menu_rows.append(tuple(keys))
        menu_rows.extend((key,) for key in items if key not in used)
        config.menus[menu_key] = MenuSpec(key=menu_key, title=menu_name, rows=tuple(menu_rows))

    build("", config.root_menu_key, {""})
    return config


def button_tree_for_menu(config: ButtonConfig, menu: MenuSpec) -> list[dict]:
    tree: list[dict] = []
    for row in menu.rows:
        row_items = []
        for button_key in row:
            button = config.buttons.get(button_key)
            if button is None:
                continue
            item = {
                "key": button.key,
                "label": button.label,
                "kind": button.kind,
                "children": [],
            }
            if button.kind == "submenu" and button.submenu_key and button.submenu_key in config.menus:
                item["children"] = button_tree_for_menu(config, config.menus[button.submenu_key])
            row_items.append(item)
        if row_items:
            tree.append({"buttons": row_items})
    return tree


def filter_problem_tree_excluding_current(
    rows: list[dict],
    *,
    exclude_key: str | None,
    exclude_label: str | None,
) -> list[dict]:
    if not exclude_key and not exclude_label:
        return rows

    exclude_label_norm = (exclude_label or "").strip().lower()
    filtered_rows: list[dict] = []
    for row in rows:
        kept_buttons: list[dict] = []
        for item in row.get("buttons", []):
            key = item.get("key")
            label_norm = (str(item.get("label", ""))).strip().lower()
            if (exclude_key and key == exclude_key) or (exclude_label_norm and label_norm == exclude_label_norm):
                continue

            if item.get("kind") == "submenu":
                children = filter_problem_tree_excluding_current(
                    item.get("children", []),
                    exclude_key=exclude_key,
                    exclude_label=exclude_label,
                )
                if not children:
                    continue
                next_item = dict(item)
                next_item["children"] = children
                kept_buttons.append(next_item)
            else:
                kept_buttons.append(item)
        if kept_buttons:
            filtered_rows.append({"buttons": kept_buttons})
    return filtered_rows


def button_full_path_label(config: ButtonConfig, target_key: str) -> str | None:
    if not target_key:
        return None
    root = config.menus.get(config.root_menu_key) if config.root_menu_key else None
    if root is None:
        return None

    def walk(menu: MenuSpec, trail: list[str]) -> str | None:
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                next_trail = [*trail, button.label]
                if button.key == target_key:
                    return " → ".join(next_trail)
                if button.kind == "submenu" and button.submenu_key and button.submenu_key in config.menus:
                    found = walk(config.menus[button.submenu_key], next_trail)
                    if found:
                        return found
        return None

    return walk(root, [])


def get_problem_menu_for_ticket(
    db: Session,
    ticket: Ticket,
    *,
    get_or_create_client_profile_fn,
) -> dict:
    menu_name = menu_name_for_ticket_client(
        db,
        ticket,
        get_or_create_client_profile_fn=get_or_create_client_profile_fn,
    )
    config = _db_button_config(db, menu_name)
    root_menu = config.menus[config.root_menu_key]
    rows = button_tree_for_menu(config, root_menu)
    rows = filter_problem_tree_excluding_current(
        rows,
        exclude_key=(ticket.instruction_button_key or ticket.button_config_key or "").strip() or None,
        exclude_label=(ticket.subject or "").strip() or None,
    )
    return {
        "menu_name": menu_name,
        "text": root_menu.text or "Выберите тип проблемы",
        "rows": rows,
    }


def apply_wrong_problem(
    db: Session,
    ticket: Ticket,
    payload,
    *,
    actor_user_id: str | None,
    actor_role: str | None,
    actor_display_name: str | None,
    wrong_problem_text_template: str,
    get_or_create_client_profile_fn,
    send_to_telegram_client_fn,
    telegram_message_id_from_response_fn,
    add_message_fn,
    unfreeze_ticket_on_message_fn,
    remove_client_close_button_fn,
) -> dict:
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    if ticket.wrong_problem_used_at is not None:
        _error(409, "wrong_problem_already_used", "Подсказка по неверной проблеме уже использована для этой заявки")
    if (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.GROUP_CHILD.value:
        _error(409, "wrong_problem_not_allowed", "Для подзадач эта функция недоступна")

    menu_name = menu_name_for_ticket_client(
        db,
        ticket,
        get_or_create_client_profile_fn=get_or_create_client_profile_fn,
    )
    config = _db_button_config(db, menu_name)
    button = config.buttons.get(payload.button_key)
    if button is None:
        _error(404, "button_not_found", "Кнопка меню не найдена")
    if button.kind == "submenu":
        _error(409, "submenu_not_final", "Выберите конечную кнопку, а не подменю")

    target_label = button_full_path_label(config, button.key) or button.label
    fallback_hint_text = wrong_problem_text_template.format(label=target_label)
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    task_kind = ticket.task_kind or TicketTaskKind.NORMAL.value
    use_automatic_template = (
        workflow_type == TicketWorkflowType.TICKET.value
        or (workflow_type == TicketWorkflowType.TASK.value and task_kind == TicketTaskKind.NORMAL.value)
    )
    if use_automatic_template:
        try:
            hint_text = render_automatic_message_text(
                db,
                "telegram.wrong_problem.hint.client",
                fallback_hint_text,
                ticket=ticket,
                extra={
                    "correct_problem_label": target_label,
                },
            )
        except Exception:
            hint_text = fallback_hint_text
    else:
        hint_text = fallback_hint_text
    ticket.wrong_problem_used_at = datetime.utcnow()
    ticket.wrong_problem_button_key = button.key
    ticket.wrong_problem_label = target_label

    if workflow_type == TicketWorkflowType.TASK.value:
        ticket.wrong_problem_hint = hint_text
        add_message_fn(
            db,
            ticket,
            MessageType.SYSTEM,
            f"После завершения задачи клиенту будет отправлена подсказка: {target_label}",
            None,
        )
    else:
        try:
            response = send_to_telegram_client_fn(db, ticket, hint_text)
        except HTTPException as exc:
            if _http_error_code(exc) == "telegram_send_failed":
                persist_client_delivery_failure(
                    db=db,
                    ticket_id=ticket.id,
                    body="Подсказку по неверной проблеме не удалось отправить клиенту в Telegram.",
                )
            raise
        was_frozen = bool(ticket.is_frozen)
        unfreeze_ticket_on_message_fn(ticket)
        if was_frozen and not ticket.is_frozen:
            sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
        system_body = (
            f"Администратор {actor_display_name} подсказал клиенту правильный пункт меню: {target_label}"
            if actor_role == "admin"
            else f"Вы подсказали клиенту правильный пункт меню: {target_label}"
        )
        add_message_fn(
            db,
            ticket,
            MessageType.SYSTEM,
            system_body,
            actor_user_id,
            agent_visible=True,
        )
        add_message_fn(
            db,
            ticket,
            MessageType.SYSTEM,
            f"Клиенту ушёл текст в бота:\n{strip_telegram_html(hint_text)}",
            actor_user_id,
            agent_visible=False,
            telegram_message_id=telegram_message_id_from_response_fn(response),
        )
        remove_client_close_button_fn(db, ticket)
        if ticket.status == TicketStatus.NEW.value:
            ticket.status = TicketStatus.PENDING.value

    return {"ok": True, "label": target_label}
