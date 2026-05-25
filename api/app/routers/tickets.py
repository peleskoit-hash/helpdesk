from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from ..auth import (
    CurrentUser,
    can_edit_client_profile,
    can_manage_ticket,
    get_current_user,
    is_admin,
    is_readonly_chiki_piki,
    is_support_user,
)
from ..config import settings
from ..db import get_db
from ..models import BotButtonSetting, BotMenuDestinationBinding, ClientProfile, MessageType, TelegramContact, Ticket, TicketAttachment, TicketIntakeState, TicketMessage, TicketStatus, TicketTaskKind, TicketView, TicketWorkflowType, User
from ..schemas import AdminBulkTicketsPayload, AdminCloseResult, AdminDeleteResult, AssignTicketsPayload, ClientCardConfigResponse, ClientProfileOut, ClientProfileUpdate, FreezePayload, TextPayload, TicketAutoClosePayload, TicketCard, TicketCountsOut, TicketListResponse, WrongProblemPayload
from ..services.assignment import append_system_message, assignee_change_message, assignee_label, notify_client_about_assignee, run_group_parent_assigned_hook
from ..services.auto_close import AUTO_CLOSE_MINUTES, can_use_auto_close_controls, clear_ticket_auto_close_timer, refresh_ticket_auto_close_timer
from ..services.telegram_bot import TelegramApiError, get_file, remove_inline_keyboard, send_document, send_message, send_problem_menu
from ..services.button_config import BASIC_MENU_NAME, DEFAULT_MENU_NAME, ButtonConfig, MenuSpec, available_menu_names, find_button, load_button_config
from ..services.ticket_attachments import (
    _attachment_out,
    _ensure_local_attachment_file,
    _message_attachments,
    _save_upload_attachment,
    _send_attachment_to_telegram_client,
)
from ..services.ticket_admin_actions import _delete_ticket_hard
from ..services.client_card import _client_card_view_config, _custom_field_map, _normalize_custom_field_value, _profile_out
from ..services.ticket_special import _apply_move_to_special, _display_assignee_name, _handle_special_assignment
from ..services.ticket_messages import add_message as add_ticket_message
from ..services.ticket_messages import comment_on_ticket, delete_agent_message, edit_agent_message, reply_to_ticket
from ..services.ticket_list import build_ticket_counts, list_special_tickets, list_tickets_paginated


router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])

TICKET_CLOSED_CLIENT_TEXT = "Ваше обращение закрыто. Если Вам снова будет необходима помощь технической поддержки, нажмите /start."
ONSITE_CLOSE_REMINDER_TEXT = 'Не забудьте самостоятельно изменить статус на "В сети" по завершению данной заявки'

WRONG_PROBLEM_TEXT_TEMPLATE = (
    "Вы выбрали неподходящий раздел меню для решения этой проблемы.\n\n"
    "В следующий раз для в такой же ситуации выберите:\n"
    "«{label}»\n\n\n"
    "Это поможет быстрее и точнее решить новую задачу."
)

SYSTEM_HIDDEN_PREFIXES_FOR_SUPPORT = (
    "Ответственный изменён:",
    "На распределении",
    "Клиент начал создание обращения",
    "Клиент выбрал кнопку:",
    "Клиент нажал /start",
    "Заявка заморожена",
    "Заявка разморожена",
    "Заявка возвращена в работу.",
    "Подсказка по неверной проблеме отправлена клиенту",
    "Сотрудник подсказал клиенту правильный пункт меню:",
    "После завершения задачи клиенту будет отправлена подсказка:",
    "Клиент уведомлен об ограничении размера файлов Telegram Bot API",
)

def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _ensure_ticket_number(db: Session, ticket: Ticket) -> int:
    if ticket.ticket_number is not None:
        return ticket.ticket_number
    max_number = db.execute(select(func.max(Ticket.ticket_number))).scalar_one() or 0
    ticket.ticket_number = int(max_number) + 1
    db.flush()
    return ticket.ticket_number


def _get_ticket_or_404(db: Session, ticket_id: str, user: CurrentUser) -> Ticket:
    if ticket_id.isdigit():
        stmt = select(Ticket).where(Ticket.ticket_number == int(ticket_id))
    else:
        stmt = select(Ticket).where(Ticket.id == ticket_id)
    ticket = db.execute(stmt).scalar_one_or_none()
    if not ticket:
        _error(404, "ticket_not_found", "Тикет не найден")
    if ticket.ticket_number is None:
        _ensure_ticket_number(db, ticket)
        db.commit()
    if is_support_user(user):
        if ticket.status != TicketStatus.CLOSED.value and (ticket.assignee_id != user.id or ticket.agent_visible is False):
            _error(403, "forbidden_ticket_access", "Нет доступа к заявке")
    if is_readonly_chiki_piki(user):
        profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
        destination = (profile.destination or "").strip().lower() if profile is not None else ""
        key = (ticket.button_config_key or "").strip()
        instruction_key = (ticket.instruction_button_key or "").strip()
        is_call_center_ticket = (
            destination == "колл центр"
            or key.startswith("Колл Центр|")
            or instruction_key.startswith("Колл Центр|")
        )
        if not is_call_center_ticket:
            _error(403, "forbidden_ticket_access", "Нет доступа к заявке")
    return ticket


def _require_admin(user: CurrentUser):
    if not is_admin(user):
        _error(403, "admin_only", "Действие доступно только администратору")


def _admin_actor_user(db: Session, user: CurrentUser) -> User:
    if not user.id:
        _error(409, "admin_user_id_required", "Для действия администратора нужен корректный пользователь")
    admin_user = db.execute(
        select(User).where(
            and_(
                User.id == user.id,
                User.role == "admin",
                User.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if admin_user is None:
        _error(409, "admin_user_not_found", "Активный администратор, выполнивший действие, не найден")
    return admin_user


def _ticket_word(ticket: Ticket) -> str:
    return "задачу" if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value else "заявку"


def _actor_display_name(db: Session, actor: CurrentUser) -> str:
    actor_user = db.get(User, actor.id) if actor.id else None
    if actor_user and actor_user.full_name:
        return actor_user.full_name
    return "Администратор" if is_admin(actor) else "Сотрудник"


def _assign_ticket_by_admin(db: Session, ticket: Ticket, assignee: User | None, actor: CurrentUser):
    old_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    old_status = ticket.status
    actor_user = db.get(User, actor.id) if actor.id else None
    actor_name = actor_user.full_name if actor_user and actor_user.full_name else "Администратор"

    if ticket.is_special and assignee is None:
        _error(409, "special_requires_admin_assignee", "У особой заявки должен быть ответственный администратор")

    if assignee is None:
        ticket.assignee_id = None
        if ticket.status != TicketStatus.CLOSED.value:
            ticket.status = TicketStatus.UNASSIGNED.value
        ticket.updated_at = datetime.utcnow()
        append_system_message(db, ticket, assignee_change_message(old_assignee, None))
        return

    if _handle_special_assignment(
        db,
        ticket,
        assignee,
        old_assignee,
        old_status,
        append_system_message_fn=append_system_message,
        assignee_change_message_fn=assignee_change_message,
        refresh_auto_close_timer_fn=refresh_ticket_auto_close_timer,
        notify_client_about_assignee_fn=notify_client_about_assignee,
        error_fn=_error,
    ):
        return

    ticket.assignee_id = assignee.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status == TicketStatus.UNASSIGNED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = datetime.utcnow()
    append_system_message(db, ticket, assignee_change_message(old_assignee, assignee))
    run_group_parent_assigned_hook(db, ticket, assignee)
    if is_admin(actor):
        # Admin took ticket from support queue.
        if assignee.id == actor.id and old_assignee is not None and old_assignee.id != actor.id:
            append_system_message(
                db,
                ticket,
                f"{actor_name} забрал {_ticket_word(ticket)}",
                agent_visible=True,
            )
        # Admin handed off ticket from self back to support.
        if old_assignee is not None and old_assignee.id == actor.id and assignee.id != actor.id:
            append_system_message(
                db,
                ticket,
                f"{actor_name} перевёл {_ticket_word(ticket)} на Вас",
                agent_visible=True,
            )
    if (
        ticket.status != TicketStatus.CLOSED.value
        and old_assignee is None
        and old_status == TicketStatus.UNASSIGNED.value
    ):
        notify_client_about_assignee(db, ticket, assignee)


def _get_or_create_client_profile(db: Session, client_id: str) -> ClientProfile:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()
    if profile is None:
        profile = ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _telegram_contact_for_client(db: Session, client_id: str) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == client_id)).scalar_one_or_none()


def _telegram_chat_id_for_client(db: Session, client_id: str) -> str | None:
    contact = _telegram_contact_for_client(db, client_id)
    return contact.external_chat_id if contact is not None else None


def _send_to_telegram_client(
    db: Session,
    ticket: Ticket,
    text: str,
    *,
    required: bool = True,
    remove_keyboard: bool = False,
    reply_to_message_id: str | None = None,
) -> dict | None:
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is None:
        if required:
            _error(409, "telegram_contact_not_found", "У клиента нет Telegram-контакта")
        return None
    try:
        return send_message(contact.external_chat_id, text, remove_keyboard=remove_keyboard, reply_to_message_id=reply_to_message_id)
    except TelegramApiError as exc:
        if required:
            _error(502, "telegram_send_failed", f"Telegram не принял сообщение: {exc}")
        return None


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def _send_problem_menu_to_telegram_client(db: Session, ticket: Ticket):
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is not None:
        try:
            send_problem_menu(contact.external_chat_id, menu_name=_menu_name_for_ticket_client(db, ticket))
        except TelegramApiError:
            pass


def _menu_name_for_ticket_client(db: Session, ticket: Ticket) -> str:
    button_key = (ticket.button_config_key or "").strip()
    if button_key:
        for menu_name in available_menu_names():
            config = load_button_config(menu_name)
            if button_key in config.buttons:
                return menu_name

    profile = _get_or_create_client_profile(db, ticket.client_id)
    if profile.is_verified and profile.destination:
        destination = profile.destination.strip()
        if destination:
            binding = db.execute(
                select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
            ).scalar_one_or_none()
            if binding is not None:
                try:
                    load_button_config(binding.menu_type)
                    return binding.menu_type
                except Exception:
                    return BASIC_MENU_NAME
            try:
                load_button_config(destination)
                return destination
            except FileNotFoundError:
                pass
            except Exception:
                pass
    return BASIC_MENU_NAME


def _button_tree_for_menu(config: ButtonConfig, menu: MenuSpec) -> list[dict]:
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
                item["children"] = _button_tree_for_menu(config, config.menus[button.submenu_key])
            row_items.append(item)
        if row_items:
            tree.append({"buttons": row_items})
    return tree


def _button_full_path_label(config: ButtonConfig, target_key: str) -> str | None:
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


def _instruction_for_ticket(db: Session, ticket: Ticket) -> tuple[str | None, str | None]:
    key = (ticket.instruction_button_key or "").strip()
    if not key:
        return None, None
    row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
    if row is None:
        return None, None
    text = (row.instruction_text or "").strip()
    if not text:
        return None, None
    return row.button_label, text


def _remove_client_close_button(db: Session, ticket: Ticket):
    if not ticket.client_close_message_id:
        return
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is None:
        ticket.client_close_message_id = None
        return
    try:
        remove_inline_keyboard(contact.external_chat_id, int(ticket.client_close_message_id))
    except Exception:
        pass
    ticket.client_close_message_id = None


def _is_support_visible_system_message(message: TicketMessage) -> bool:
    text = (message.body or "").strip()
    if not text:
        return False
    if text.startswith(SYSTEM_HIDDEN_PREFIXES_FOR_SUPPORT):
        return False
    return True


@router.get("/active", response_model=TicketListResponse)
def list_active(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.NEW.value, TicketStatus.PENDING.value]
    return list_tickets_paginated(
        db=db,
        user=user,
        statuses=statuses,
        is_frozen=False,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
        admin_assignee_ids=assignee_ids,
        ensure_ticket_number_fn=_ensure_ticket_number,
    )


@router.get("/unassigned", response_model=TicketListResponse)
def list_unassigned(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return list_tickets_paginated(
        db=db,
        user=user,
        statuses=[TicketStatus.UNASSIGNED.value],
        is_frozen=False,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
        admin_assignee_ids=None,
        filter_admin_assignees=False,
        require_admin_or_chiki=True,
        ensure_ticket_number_fn=_ensure_ticket_number,
    )


@router.get("/frozen", response_model=TicketListResponse)
def list_frozen(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.NEW.value, TicketStatus.PENDING.value]
    return list_tickets_paginated(
        db=db,
        user=user,
        statuses=statuses,
        is_frozen=True,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
        admin_assignee_ids=assignee_ids,
        ensure_ticket_number_fn=_ensure_ticket_number,
    )


@router.get("/closed", response_model=TicketListResponse)
def list_closed(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    statuses = [TicketStatus.CLOSED.value]
    return list_tickets_paginated(
        db=db,
        user=user,
        statuses=statuses,
        is_frozen=None,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
        admin_assignee_ids=assignee_ids,
        filter_admin_assignees=False,
        include_special_open=False,
        include_support_special_closed=True,
        include_support_all_closed=True,
        ensure_ticket_number_fn=_ensure_ticket_number,
    )


@router.get("/special", response_model=TicketListResponse)
def list_special(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return list_special_tickets(
        db=db,
        user=user,
        sort=sort,
        order=order,
        page=page,
        limit=limit,
        ensure_ticket_number_fn=_ensure_ticket_number,
    )


@router.get("/counts", response_model=TicketCountsOut)
def ticket_counts(
    assignee_ids: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return build_ticket_counts(
        db=db,
        user=user,
        assignee_ids=assignee_ids,
    )


@router.post("/assign")
def assign_tickets(payload: AssignTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)

    assignee = None
    if payload.assignee_id is not None:
        assignee = db.execute(
            select(User).where(
                and_(
                    User.id == payload.assignee_id,
                    User.role.in_(["admin", "agent", "trainee"]),
                    User.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if assignee is None:
            _error(404, "assignee_not_found", "Пользователь для назначения не найден")

    tickets = db.execute(select(Ticket).where(Ticket.id.in_(payload.ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    missing_ids = [ticket_id for ticket_id in payload.ticket_ids if ticket_id not in found_ids]
    if missing_ids:
        _error(404, "ticket_not_found", "Одна или несколько заявок не найдены")

    for ticket in tickets:
        if assignee is None and ticket.is_special:
            _error(409, "special_requires_admin_assignee", "У особой заявки должен быть ответственный администратор")
        _assign_ticket_by_admin(db, ticket, assignee, user)
    db.commit()
    return {"ok": True, "assigned": len(tickets)}


@router.post("/admin/close", response_model=AdminCloseResult)
def admin_close_tickets(payload: AdminBulkTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)

    tickets = db.execute(select(Ticket).where(Ticket.id.in_(payload.ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    not_found = [ticket_id for ticket_id in payload.ticket_ids if ticket_id not in found_ids]

    closed = 0
    already_closed = 0
    for ticket in tickets:
        result = _close_ticket_by_actor(
            db,
            ticket,
            user,
            force_without_agent_reply_for_task=True,
        )
        if result == "already_closed":
            already_closed += 1
        else:
            closed += 1

    db.commit()
    return AdminCloseResult(
        ok=True,
        closed=closed,
        already_closed=already_closed,
        not_found=not_found,
    )


@router.post("/admin/delete", response_model=AdminDeleteResult)
def admin_delete_tickets(payload: AdminBulkTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    tickets = db.execute(select(Ticket).where(Ticket.id.in_(payload.ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    not_found = [ticket_id for ticket_id in payload.ticket_ids if ticket_id not in found_ids]

    warnings: list[str] = []
    deleted = 0
    deleted_ids: set[str] = set()
    for ticket in tickets:
        if ticket.id in deleted_ids:
            continue
        # If parent was already deleted in this batch, its children are already hard-deleted too.
        if ticket.parent_ticket_id and ticket.parent_ticket_id in deleted_ids:
            deleted_ids.add(ticket.id)
            continue
        # Skip stale ORM rows that may already be removed by previous parent delete.
        still_exists = db.execute(select(Ticket.id).where(Ticket.id == ticket.id)).scalar_one_or_none()
        if still_exists is None:
            deleted_ids.add(ticket.id)
            continue

        workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
        parent_ticket_id = ticket.parent_ticket_id if ticket.task_kind == TicketTaskKind.GROUP_CHILD.value else None
        if workflow_type == TicketWorkflowType.TICKET.value:
            # For admin hard-delete of a regular ticket, notify client with the same text as normal close.
            try:
                _send_to_telegram_client(
                    db,
                    ticket,
                    TICKET_CLOSED_CLIENT_TEXT,
                    required=False,
                    remove_keyboard=True,
                )
            except Exception:
                # Deletion must not be blocked by Telegram delivery issues.
                pass
        elif workflow_type == TicketWorkflowType.TASK.value:
            try:
                # For group children we avoid client spam; parent summary/close handles client notification.
                if ticket.task_kind != TicketTaskKind.GROUP_CHILD.value:
                    client_message = _first_client_message(db, ticket)
                    _send_to_telegram_client(
                        db,
                        ticket,
                        _format_task_closed_without_answer_text(ticket, client_message),
                        required=False,
                        remove_keyboard=True,
                    )
            except Exception:
                # Deletion must not be blocked by Telegram delivery issues.
                pass
        warnings.extend(_delete_ticket_hard(db, ticket))
        deleted_ids.add(ticket.id)
        if parent_ticket_id:
            parent = db.get(Ticket, parent_ticket_id)
            if parent is not None:
                _finalize_group_parent_if_ready(
                    db,
                    parent,
                    closed_by_user_id=user.id,
                    system_note="Групповая задача завершена системой (возможен неполный ответ)",
                )
        deleted += 1
    db.commit()
    return AdminDeleteResult(
        ok=True,
        deleted=deleted,
        not_found=not_found,
        file_delete_warnings=warnings,
    )


@router.get("/{ticket_id}", response_model=TicketCard)
def get_ticket(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = _get_ticket_or_404(db, ticket_id, user)
    if (
        (t.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value
        and t.status in {TicketStatus.NEW.value, TicketStatus.PENDING.value}
        and not t.is_frozen
        and not t.is_special
        and not t.auto_close_disabled
        and (t.intake_state or TicketIntakeState.READY.value) != TicketIntakeState.COLLECTING.value
        and t.auto_close_due_at is None
    ):
        _refresh_ticket_auto_close(db, t, restart_from_now=False)
        db.commit()
    if user.role == "agent":
        view = db.execute(
            select(TicketView).where(and_(TicketView.ticket_id == t.id, TicketView.user_id == user.id))
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if view is None:
            view = TicketView(
                id=str(uuid.uuid4()),
                ticket_id=t.id,
                user_id=user.id,
                first_opened_at=now,
                last_opened_at=now,
            )
            db.add(view)
        else:
            view.last_opened_at = now
        db.commit()

    msg_stmt = select(TicketMessage).where(TicketMessage.ticket_id == t.id).order_by(asc(TicketMessage.created_at))
    messages = db.execute(msg_stmt).scalars().all()
    if user.role in {"agent", "trainee"}:
        messages = [
            message for message in messages
            if message.message_type != MessageType.SYSTEM.value
            or _is_support_visible_system_message(message)
        ]
    profile = _get_or_create_client_profile(db, t.client_id)
    contact = _telegram_contact_for_client(db, t.client_id)
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else (t.client.display_name if t.client else None)
    telegram_username = contact.external_username if contact else None
    assignee = db.get(User, t.assignee_id) if t.assignee_id else None
    special_assignee = db.get(User, t.special_assignee_id) if t.special_assignee_id else None
    author_ids = {m.author_user_id for m in messages if m.author_user_id}
    authors = {}
    if author_ids:
        authors = {
            author.id: author.full_name
            for author in db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
        }
    message_ids = [m.id for m in messages]
    attachments_by_message: dict[str, list[TicketAttachment]] = {message_id: [] for message_id in message_ids}
    if message_ids:
        attachments = db.execute(
            select(TicketAttachment)
            .where(TicketAttachment.message_id.in_(message_ids))
            .order_by(asc(TicketAttachment.created_at))
        ).scalars().all()
        for attachment in attachments:
            attachments_by_message.setdefault(attachment.message_id, []).append(attachment)
    instruction_title, instruction_text = _instruction_for_ticket(db, t)

    return TicketCard(
        id=t.id,
        ticket_number=t.ticket_number,
        subject=t.subject,
        workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
        intake_state=t.intake_state or TicketIntakeState.READY.value,
        status=t.status,
        is_frozen=t.is_frozen,
        freeze_until=t.freeze_until,
        closed_at=t.closed_at,
        assignee_id=t.assignee_id,
        assignee_name=assignee.full_name if assignee else None,
        is_special=bool(t.is_special),
        special_assignee_id=t.special_assignee_id,
        special_assignee_name=special_assignee.full_name if special_assignee else None,
        instruction_title=None if user.role == "chiki_piki" else instruction_title,
        instruction_text=None if user.role == "chiki_piki" else instruction_text,
        wrong_problem_used=bool(t.wrong_problem_used_at),
        wrong_problem_used_at=t.wrong_problem_used_at,
        wrong_problem_label=t.wrong_problem_label,
        auto_close_disabled=bool(t.auto_close_disabled),
        auto_close_started_at=t.auto_close_started_at,
        auto_close_due_at=t.auto_close_due_at,
        auto_closed_at=t.auto_closed_at,
        closed_by_user_id=t.closed_by_user_id,
        onsite_triggered=bool(t.onsite_triggered),
        client_name=t.client.display_name if t.client else "",
        client_profile=_profile_out(
            profile,
            telegram_username,
            telegram_display_name,
        ),
        messages=[
            {
                "id": m.id,
                "message_type": m.message_type,
                "body": m.body,
                "author_user_id": m.author_user_id,
                "author_name": authors.get(m.author_user_id) if m.author_user_id else None,
                "telegram_message_id": m.telegram_message_id,
                "message_status": m.message_status or "normal",
                "status_note": m.status_note,
                "replaced_message_id": m.replaced_message_id,
                "created_at": m.created_at,
                "attachments": [_attachment_out(t.id, attachment) for attachment in attachments_by_message.get(m.id, [])],
            }
            for m in messages
        ],
    )


@router.get("/{ticket_id}/client-card-view-config", response_model=ClientCardConfigResponse)
def get_client_card_view_config(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _get_ticket_or_404(db, ticket_id, user)
    return _client_card_view_config(db)


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
def download_attachment(
    ticket_id: str,
    attachment_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    attachment = db.get(TicketAttachment, attachment_id)
    if attachment is None or attachment.ticket_id != ticket.id:
        _error(404, "attachment_not_found", "Файл не найден")
    media_type = attachment.mime_type or "application/octet-stream"
    filename = attachment.original_filename or Path(attachment.local_path).name
    path = Path(attachment.local_path)

    if path.exists() and path.is_file():
        return FileResponse(path, media_type=media_type, filename=filename)

    file_path = (attachment.telegram_file_path or "").strip()
    if not file_path:
        file_id = (attachment.telegram_file_id or "").strip()
        if not file_id:
            _error(404, "attachment_file_missing", "Файл недоступен для скачивания")
        contact = _telegram_contact_for_client(db, ticket.client_id)
        chat_id = contact.external_chat_id if contact else None
        try:
            file_response = get_file(file_id, chat_id=chat_id)
            file_path = (((file_response or {}).get("result") or {}).get("file_path") or "").strip()
            if not file_path:
                _error(404, "attachment_file_missing", "Telegram не вернул путь к файлу")
            attachment.telegram_file_path = file_path
            db.commit()
        except TelegramApiError as exc:
            db.rollback()
            _error(404, "attachment_file_missing", f"Файл недоступен для скачивания: {exc}")

    if not settings.telegram_bot_token:
        _error(502, "telegram_token_missing", "Telegram bot token is not configured")

    stream_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    request = UrlRequest(stream_url, method="GET")

    def _iter_stream():
        try:
            with urlopen(request, timeout=120) as response:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(body or str(exc))
        except URLError as exc:
            raise RuntimeError(str(exc))

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if attachment.file_size:
        headers["Content-Length"] = str(attachment.file_size)
    return StreamingResponse(_iter_stream(), media_type=media_type, headers=headers)


@router.patch("/{ticket_id}/client-profile", response_model=ClientProfileOut)
def update_client_profile(
    ticket_id: str,
    payload: ClientProfileUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    t = _get_ticket_or_404(db, ticket_id, user)
    if not can_edit_client_profile(user):
        _error(403, "readonly_role", "Нет прав на редактирование карточки клиента")
    profile = _get_or_create_client_profile(db, t.client_id)
    contact = _telegram_contact_for_client(db, t.client_id)

    profile.contact_name = payload.contact_name
    profile.last_name = payload.last_name
    profile.email = payload.email
    profile.phone = payload.phone
    profile.verification_status = payload.verification_status
    profile.is_verified = bool(payload.is_verified)
    profile.destination = payload.destination
    profile.sand_owner = payload.sand_owner
    profile.sales_group = payload.sales_group
    profile.department = payload.department
    profile.first_department = payload.first_department
    profile.group_name = payload.group_name
    profile.employee_type = payload.employee_type
    profile.location_type = payload.location_type
    profile.anydesk = payload.anydesk
    profile.internal_number = payload.internal_number
    profile.external_user_id = payload.external_user_id
    profile.bitrix_login = payload.bitrix_login
    profile.bitrix_password = payload.bitrix_password
    profile.bitrix_id = payload.bitrix_id
    if payload.custom_fields is not None:
        custom_fields_map = _custom_field_map(db)
        incoming = payload.custom_fields or {}
        if not isinstance(incoming, dict):
            _error(422, "invalid_custom_fields_payload", "custom_fields должен быть объектом")
        unknown_keys = [key for key in incoming.keys() if key not in custom_fields_map]
        if unknown_keys:
            _error(400, "unknown_custom_field_keys", f"Неизвестные custom поля: {', '.join(unknown_keys)}")
        current_custom = dict(profile.custom_fields or {})
        normalized_custom: dict = dict(current_custom)
        for key, value in incoming.items():
            field = custom_fields_map[key]
            if not field.is_editable:
                _error(403, "readonly_custom_field", f"Поле '{field.label}' недоступно для редактирования")
            normalized_custom[key] = _normalize_custom_field_value(field, value)
        profile.custom_fields = normalized_custom
    profile.updated_at = datetime.utcnow()

    if t.client and payload.contact_name:
        t.client.display_name = payload.contact_name

    db.commit()
    db.refresh(profile)
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else (t.client.display_name if t.client else None)
    telegram_username = contact.external_username if contact else None
    return _profile_out(
        profile,
        telegram_username,
        telegram_display_name,
    )


def _add_message(
    db: Session,
    ticket: Ticket,
    message_type: MessageType,
    body: str,
    author_user_id: str | None,
    *,
    agent_visible: bool | None = None,
    telegram_message_id: str | None = None,
    message_status: str = "normal",
    status_note: str | None = None,
    replaced_message_id: str | None = None,
):
    return add_ticket_message(
        db=db,
        ticket=ticket,
        message_type=message_type,
        body=body,
        author_user_id=author_user_id,
        agent_visible=agent_visible,
        telegram_message_id=telegram_message_id,
        message_status=message_status,
        status_note=status_note,
        replaced_message_id=replaced_message_id,
    )


def _first_client_message(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value))
        .order_by(asc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _last_agent_message(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value))
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _last_client_visible_support_message_author_id(db: Session, ticket: Ticket) -> str | None:
    row = db.execute(
        select(TicketMessage.author_user_id)
        .join(User, User.id == TicketMessage.author_user_id)
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                TicketMessage.message_status != "deleted",
                User.role.in_(["agent", "trainee"]),
            )
        )
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).first()
    if row is None:
        return None
    return row[0]


def _has_client_visible_support_message_by_user(db: Session, ticket: Ticket, user_id: str) -> bool:
    if not user_id:
        return False
    return db.execute(
        select(TicketMessage.id)
        .join(User, User.id == TicketMessage.author_user_id)
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                TicketMessage.message_status != "deleted",
                TicketMessage.author_user_id == user_id,
                User.role.in_(["agent", "trainee"]),
            )
        )
        .limit(1)
    ).first() is not None


def _resolve_completed_by_user_id(db: Session, ticket: Ticket, actor: CurrentUser) -> str | None:
    if actor.role in {"agent", "trainee"}:
        return actor.id
    if not is_admin(actor):
        return None

    assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    if assignee and assignee.role in {"agent", "trainee"} and _has_client_visible_support_message_by_user(db, ticket, assignee.id):
        return assignee.id

    return _last_client_visible_support_message_author_id(db, ticket)


def _mark_ticket_completed_once(db: Session, ticket: Ticket, actor: CurrentUser) -> None:
    if ticket.completed_by_user_id or ticket.completed_counted_at:
        return
    completed_by_user_id = _resolve_completed_by_user_id(db, ticket, actor)
    if not completed_by_user_id:
        return
    ticket.completed_by_user_id = completed_by_user_id
    ticket.completed_counted_at = datetime.utcnow()


def _is_client_visible_message(message: TicketMessage) -> bool:
    return message.message_type in {MessageType.CLIENT.value, MessageType.AGENT.value}


def _refresh_ticket_auto_close(db: Session, ticket: Ticket, restart_from_now: bool = False):
    refresh_ticket_auto_close_timer(db, ticket, restart_from_now=restart_from_now)

def _format_task_completion_text(ticket: Ticket, client_message: TicketMessage, agent_message: TicketMessage) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    agent_text = (agent_message.body or "").strip()
    if agent_text:
        return (
            f"По Вашей задаче \"{ticket.subject}\" от {created_text}\n"
            f"\"{client_message.body}\"\n"
            "\nПолучен ответ:\n"
            f"{agent_text}"
        )
    return (
        f"По Вашей задаче \"{ticket.subject}\" от {created_text}\n"
        f"\"{client_message.body}\"\n"
        "\nПолучены файлы от сотрудника:"
    )


def _format_task_closed_without_answer_text(ticket: Ticket, client_message: TicketMessage | None) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    client_text = (client_message.body or "").strip() if client_message is not None else ""
    if not client_text:
        client_text = "(без текста)"
    return (
        "Ваша задача:\n"
        f"\"{ticket.subject}\" от {created_text}\n"
        "с текстом:\n"
        f"\"{client_text}\"\n"
        "закрыта без ответа по причине некорректных или неполных данных.\n\n"
        "Для создания новой нажмите\n"
        "/start"
    )



def _format_group_task_completion_text(ticket: Ticket, client_message: TicketMessage, answers: list[str]) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    if not answers:
        return (
            f"По Вашей задаче \"{ticket.subject}\" от {created_text}\n"
            f"\"{client_message.body}\"\n\n"
            "Получены файлы от сотрудников."
        )
    return (
        f"По Вашей задаче \"{ticket.subject}\" от {created_text}\n"
        f"\"{client_message.body}\"\n\n"
        "Получены ответы:\n"
        f"{chr(10).join(answers)}"
    )


def _group_parent_children_payload(db: Session, parent: Ticket) -> tuple[list[str], list[TicketAttachment]]:
    children = db.execute(
        select(Ticket)
        .where(
            and_(
                Ticket.parent_ticket_id == parent.id,
                Ticket.task_kind == TicketTaskKind.GROUP_CHILD.value,
            )
        )
        .order_by(asc(Ticket.created_at))
    ).scalars().all()

    answers: list[str] = []
    attachments: list[TicketAttachment] = []
    for child in children:
        last_agent_message = db.execute(
            select(TicketMessage)
            .where(
                and_(
                    TicketMessage.ticket_id == child.id,
                    TicketMessage.message_type == MessageType.AGENT.value,
                )
            )
            .order_by(desc(TicketMessage.created_at))
            .limit(1)
        ).scalar_one_or_none()
        if last_agent_message is None:
            continue
        text = (last_agent_message.body or "").strip()
        if text:
            answers.append(text)
        child_attachments = db.execute(
            select(TicketAttachment)
            .where(TicketAttachment.message_id == last_agent_message.id)
            .order_by(asc(TicketAttachment.created_at))
        ).scalars().all()
        attachments.extend(child_attachments)
    return answers, attachments


def _finalize_group_parent_if_ready(
    db: Session,
    parent: Ticket,
    *,
    closed_by_user_id: str | None = None,
    system_note: str = "Групповая задача завершена системой",
) -> bool:
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return False
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return False
    if parent.status == TicketStatus.CLOSED.value:
        return False

    open_children = db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.parent_ticket_id == parent.id,
                Ticket.status != TicketStatus.CLOSED.value,
            )
        )
        .limit(1)
    ).first()
    if open_children is not None:
        return False

    parent_client_message = _first_client_message(db, parent)
    if parent_client_message is not None:
        answers, attachments = _group_parent_children_payload(db, parent)
        _send_to_telegram_client(
            db,
            parent,
            _format_group_task_completion_text(parent, parent_client_message, answers),
            required=False,
        )
        for attachment in attachments:
            _send_attachment_to_telegram_client(db, parent, attachment, required=True)

    parent.status = TicketStatus.CLOSED.value
    parent.closed_by_user_id = closed_by_user_id
    parent.closed_at = datetime.utcnow()
    parent.is_frozen = False
    parent.freeze_until = None
    _add_message(db, parent, MessageType.SYSTEM, system_note, None, agent_visible=True)
    return True


def _notify_group_parent_completion_before_delete(db: Session, parent: Ticket) -> None:
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return
    parent_client_message = _first_client_message(db, parent)
    if parent_client_message is None:
        return
    answers, attachments = _group_parent_children_payload(db, parent)
    _send_to_telegram_client(
        db,
        parent,
        _format_group_task_completion_text(parent, parent_client_message, answers),
        required=False,
    )
    for attachment in attachments:
        _send_attachment_to_telegram_client(db, parent, attachment, required=True)


@router.get("/{ticket_id}/problem-menu")
def problem_menu_for_ticket(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    menu_name = _menu_name_for_ticket_client(db, ticket)
    config = load_button_config(menu_name)
    root_menu = config.menus[config.root_menu_key]
    return {
        "menu_name": menu_name,
        "text": root_menu.text or "Выберите тип проблемы",
        "rows": _button_tree_for_menu(config, root_menu),
    }


def _send_pending_wrong_problem_hint(db: Session, ticket: Ticket):
    if not ticket.wrong_problem_hint:
        return
    if _send_to_telegram_client(db, ticket, ticket.wrong_problem_hint, required=False):
        _add_message(db, ticket, MessageType.SYSTEM, "Подсказка по неверной проблеме отправлена клиенту", None)
        ticket.wrong_problem_hint = None


def _unfreeze_ticket_on_message(ticket: Ticket) -> None:
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TICKET.value:
        return
    if ticket.status == TicketStatus.CLOSED.value:
        return
    if not ticket.is_frozen:
        return
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()


def _close_group_child_if_ready(db: Session, child: Ticket, last_agent_message: TicketMessage, user: CurrentUser) -> bool:
    if child.task_kind != TicketTaskKind.GROUP_CHILD.value or not child.parent_ticket_id:
        return False

    parent = db.get(Ticket, child.parent_ticket_id)
    if parent is None:
        return False
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return False
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return False

    parent_agent_message = _add_message(db, parent, MessageType.AGENT, last_agent_message.body or "", user.id)
    child_attachments = db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == last_agent_message.id)
        .order_by(asc(TicketAttachment.created_at))
    ).scalars().all()
    for attachment in child_attachments:
        db.add(
            TicketAttachment(
                id=str(uuid.uuid4()),
                ticket_id=parent.id,
                message_id=parent_agent_message.id,
                source=attachment.source,
                telegram_file_id=attachment.telegram_file_id,
                telegram_file_unique_id=attachment.telegram_file_unique_id,
                telegram_file_path=attachment.telegram_file_path,
                telegram_message_id=attachment.telegram_message_id,
                original_filename=attachment.original_filename,
                mime_type=attachment.mime_type,
                file_size=attachment.file_size,
                local_path=attachment.local_path,
                created_at=datetime.utcnow(),
            )
        )

    _finalize_group_parent_if_ready(
        db,
        parent,
        closed_by_user_id=user.id,
        system_note="Групповая задача завершена системой",
    )
    return True


def _close_task_without_reply(db: Session, ticket: Ticket, actor: CurrentUser):
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = actor.id
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    _add_message(db, ticket, MessageType.SYSTEM, "Задача принудительно закрыта администратором без ответа", None, agent_visible=True)
    # For group child tasks, client should receive only parent-level finalization messages.
    if ticket.task_kind != TicketTaskKind.GROUP_CHILD.value:
        client_message = _first_client_message(db, ticket)
        _send_to_telegram_client(
            db,
            ticket,
            _format_task_closed_without_answer_text(ticket, client_message),
            required=False,
        )
    if ticket.task_kind == TicketTaskKind.GROUP_CHILD.value and ticket.parent_ticket_id:
        parent = db.get(Ticket, ticket.parent_ticket_id)
        if parent is not None:
            _finalize_group_parent_if_ready(
                db,
                parent,
                closed_by_user_id=actor.id,
                system_note="Групповая задача завершена системой (возможен неполный ответ)",
            )
    _mark_ticket_completed_once(db, ticket, actor)


def _close_ticket_by_actor(
    db: Session,
    ticket: Ticket,
    actor: CurrentUser,
    *,
    force_without_agent_reply_for_task: bool = False,
):
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    is_admin_action = is_admin(actor)

    if ticket.status == TicketStatus.CLOSED.value:
        return "already_closed"

    if ticket.subject == "Первое подключение к боту":
        profile = _get_or_create_client_profile(db, ticket.client_id)
        if not profile.is_verified and not is_admin_action:
            _error(409, "first_connection_not_verified", "Нельзя закрыть первое подключение, пока карточка клиента не верифицирована")

    last_agent_message = _last_agent_message(db, ticket)
    if last_agent_message is None and is_admin_action and force_without_agent_reply_for_task:
        if workflow_type == TicketWorkflowType.TASK.value:
            _close_task_without_reply(db, ticket, actor)
            return "closed"
        if workflow_type == TicketWorkflowType.TICKET.value:
            ticket.status = TicketStatus.CLOSED.value
            ticket.closed_by_user_id = actor.id
            ticket.closed_at = datetime.utcnow()
            ticket.is_frozen = False
            ticket.freeze_until = None
            _add_message(db, ticket, MessageType.SYSTEM, f"Заявка принудительно завершена администратором: {_actor_display_name(db, actor)}", None, agent_visible=True)
            _send_to_telegram_client(
                db,
                ticket,
                TICKET_CLOSED_CLIENT_TEXT,
                required=False,
                remove_keyboard=True,
            )
            _mark_ticket_completed_once(db, ticket, actor)
            return "closed"
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_by_user_id = actor.id
        ticket.closed_at = datetime.utcnow()
        ticket.is_frozen = False
        ticket.freeze_until = None
        _add_message(db, ticket, MessageType.SYSTEM, f"Автообращение принудительно завершено администратором: {_actor_display_name(db, actor)}", None, agent_visible=True)
        _mark_ticket_completed_once(db, ticket, actor)
        return "closed"

    if last_agent_message is None:
        action_name = "задачу" if workflow_type == TicketWorkflowType.TASK.value else "заявку"
        _error(409, "close_not_allowed", f"Нельзя завершить {action_name} без ответа сотрудника")

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = actor.id
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None

    if workflow_type == TicketWorkflowType.TASK.value:
        client_message = _first_client_message(db, ticket)
        if client_message is None:
            if force_without_agent_reply_for_task:
                _close_task_without_reply(db, ticket, actor)
                return "closed"
            _error(409, "task_client_message_required", "У задачи нет текста клиента")
        if ticket.task_kind == TicketTaskKind.GROUP_CHILD.value:
            _close_group_child_if_ready(db, ticket, last_agent_message, actor)
            close_text = "Подзадача завершена сотрудником" if not is_admin_action else f"Подзадача принудительно завершена администратором: {_actor_display_name(db, actor)}"
            _add_message(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        else:
            _send_to_telegram_client(
                db,
                ticket,
                _format_task_completion_text(ticket, client_message, last_agent_message),
                required=False,
            )
            for attachment in _message_attachments(db, last_agent_message.id):
                _send_attachment_to_telegram_client(db, ticket, attachment, required=True)
            _send_pending_wrong_problem_hint(db, ticket)
            close_text = "Задача завершена сотрудником" if not is_admin_action else f"Задача принудительно завершена администратором: {_actor_display_name(db, actor)}"
            _add_message(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    elif workflow_type == TicketWorkflowType.TICKET.value:
        close_text = "Заявка закрыта сотрудником" if not is_admin_action else f"Заявка принудительно завершена администратором: {_actor_display_name(db, actor)}"
        _add_message(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        if bool(ticket.onsite_triggered):
            _add_message(db, ticket, MessageType.SYSTEM, ONSITE_CLOSE_REMINDER_TEXT, None, agent_visible=True)
        _send_to_telegram_client(
            db,
            ticket,
            TICKET_CLOSED_CLIENT_TEXT,
            required=False,
            remove_keyboard=True,
        )
    else:
        close_text = "Автообращение завершено системой" if not is_admin_action else f"Автообращение принудительно завершено администратором: {_actor_display_name(db, actor)}"
        _add_message(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    _mark_ticket_completed_once(db, ticket, actor)
    return "closed"


@router.post("/{ticket_id}/reply")
async def reply(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return await reply_to_ticket(
        request=request,
        db=db,
        user=user,
        ticket=ticket,
        send_to_telegram_client=_send_to_telegram_client,
        telegram_message_id_from_response=_telegram_message_id,
        save_upload_attachment=_save_upload_attachment,
        send_attachment_to_telegram_client=lambda db_session, t, a, required: _send_attachment_to_telegram_client(
            db_session, t, a, required=required
        ),
        unfreeze_ticket_on_message=_unfreeze_ticket_on_message,
        remove_client_close_button=_remove_client_close_button,
        refresh_ticket_auto_close=lambda db_session, t, restart_from_now: _refresh_ticket_auto_close(
            db_session, t, restart_from_now=restart_from_now
        ),
    )


@router.post("/{ticket_id}/comment")
def comment(ticket_id: str, payload: TextPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return comment_on_ticket(
        db=db,
        user=user,
        ticket=ticket,
        payload=payload,
        unfreeze_ticket_on_message=_unfreeze_ticket_on_message,
    )


@router.post("/{ticket_id}/greeting")
def greeting(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) in {TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value}:
        _error(409, "greeting_not_allowed", "Приветствие доступно только для обычной заявки")

    # Greeting can be sent only once per ticket.
    existing_greeting_stmt = select(TicketMessage.id).where(
        and_(
            TicketMessage.ticket_id == ticket.id,
            TicketMessage.message_type == MessageType.AGENT.value,
            TicketMessage.body.like("Добр%")
        )
    )
    if db.execute(existing_greeting_stmt).first() is not None:
        _error(409, "greeting_already_sent", "Приветствие уже отправлялось по этой заявке")

    now = datetime.now(ZoneInfo(settings.app_timezone)).time()
    if now <= time(11, 59):
        greet = "Доброе утро"
    elif now <= time(16, 59):
        greet = "Добрый день"
    else:
        greet = "Добрый вечер"

    profile = _get_or_create_client_profile(db, ticket.client_id)
    name = (profile.contact_name or "").strip()
    if not name:
        _error(409, "client_name_required", "Поле 'Имя' в карточке клиента не заполнено")

    text = f"{greet}, {name}!"

    response = _send_to_telegram_client(db, ticket, text)
    _add_message(db, ticket, MessageType.AGENT, text, user.id, telegram_message_id=_telegram_message_id(response))
    _unfreeze_ticket_on_message(ticket)
    _remove_client_close_button(db, ticket)
    if ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.PENDING.value
    _refresh_ticket_auto_close(db, ticket, restart_from_now=True)
    db.commit()
    return {"ok": True, "text": text}


@router.post("/{ticket_id}/freeze")
def freeze(ticket_id: str, payload: FreezePayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "freeze_closed_ticket", "Нельзя заморозить закрытую заявку")

    ticket.is_frozen = True
    ticket.freeze_until = payload.freeze_until
    clear_ticket_auto_close_timer(ticket)
    _add_message(db, ticket, MessageType.SYSTEM, "Заявка заморожена", None)
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/unfreeze")
def unfreeze(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if not ticket.is_frozen:
        _error(409, "unfreeze_not_frozen", "Заявка не заморожена")

    ticket.is_frozen = False
    ticket.freeze_until = None
    _add_message(db, ticket, MessageType.SYSTEM, "Заявка разморожена", None)
    _refresh_ticket_auto_close(db, ticket, restart_from_now=True)
    db.commit()
    return {"ok": True}


@router.patch("/{ticket_id}/auto-close")
def update_auto_close(
    ticket_id: str,
    payload: TicketAutoClosePayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if is_readonly_chiki_piki(user) or not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление таймером автозакрытия")
    if not can_use_auto_close_controls(ticket):
        _error(409, "auto_close_not_available", "Таймер автозакрытия недоступен для этой заявки")

    ticket.auto_close_disabled = bool(payload.auto_close_disabled)
    if ticket.auto_close_disabled:
        clear_ticket_auto_close_timer(ticket)
    else:
        _refresh_ticket_auto_close(db, ticket, restart_from_now=True)
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/wrong-problem")
def wrong_problem(
    ticket_id: str,
    payload: WrongProblemPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    if ticket.wrong_problem_used_at is not None:
        _error(409, "wrong_problem_already_used", "Подсказка по неверной проблеме уже использована для этой заявки")

    config = load_button_config(_menu_name_for_ticket_client(db, ticket))
    button = config.buttons.get(payload.button_key) or find_button(payload.button_key)
    if button is None:
        _error(404, "button_not_found", "Кнопка меню не найдена")
    if button.kind == "submenu":
        _error(409, "submenu_not_final", "Выберите конечную кнопку, а не подменю")

    target_label = _button_full_path_label(config, button.key) or button.label
    hint_text = WRONG_PROBLEM_TEXT_TEMPLATE.format(label=target_label)
    ticket.wrong_problem_used_at = datetime.utcnow()
    ticket.wrong_problem_button_key = button.key
    ticket.wrong_problem_label = target_label
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value:
        ticket.wrong_problem_hint = hint_text
        _add_message(db, ticket, MessageType.SYSTEM, f"После завершения задачи клиенту будет отправлена подсказка: {target_label}", None)
    else:
        response = _send_to_telegram_client(db, ticket, hint_text)
        _add_message(db, ticket, MessageType.AGENT, hint_text, user.id, telegram_message_id=_telegram_message_id(response))
        _unfreeze_ticket_on_message(ticket)
        _add_message(db, ticket, MessageType.SYSTEM, f"Сотрудник подсказал клиенту правильный пункт меню: {target_label}", None)
        _remove_client_close_button(db, ticket)
        if ticket.status == TicketStatus.NEW.value:
            ticket.status = TicketStatus.PENDING.value

    db.commit()
    return {"ok": True, "label": target_label}


@router.post("/{ticket_id}/special")
def move_to_special(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытую заявку нельзя перевести в особые")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TICKET.value:
        _error(409, "special_only_for_ticket", "В особые можно переводить только обычные заявки")
    if ticket.is_special:
        return {"ok": True, "already_special": True}

    admin_user = _admin_actor_user(db, user)
    old_assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    _apply_move_to_special(
        db,
        ticket,
        admin_user,
        old_assignee,
        append_system_message_fn=append_system_message,
        clear_auto_close_timer_fn=clear_ticket_auto_close_timer,
    )
    db.commit()
    return {"ok": True}


@router.patch("/{ticket_id}/messages/{message_id}")
def edit_ticket_message(
    ticket_id: str,
    message_id: str,
    payload: TextPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return edit_agent_message(
        db=db,
        user=user,
        ticket=ticket,
        message_id=message_id,
        payload=payload,
        telegram_chat_id_by_client_id=_telegram_chat_id_for_client,
        actor_display_name=_actor_display_name,
    )


@router.delete("/{ticket_id}/messages/{message_id}")
def delete_ticket_message(
    ticket_id: str,
    message_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return delete_agent_message(
        db=db,
        user=user,
        ticket=ticket,
        message_id=message_id,
        telegram_chat_id_by_client_id=_telegram_chat_id_for_client,
        actor_display_name=_actor_display_name,
    )


@router.post("/{ticket_id}/close")
def close(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_already_closed", "Заявка уже закрыта")
    _close_ticket_by_actor(
        db,
        ticket,
        user,
        force_without_agent_reply_for_task=False,
    )
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/restore")
def restore(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status != TicketStatus.CLOSED.value:
        _error(409, "ticket_not_closed", "Заявка не закрыта")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) in {TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value}:
        _error(409, "restore_not_allowed", "Для задач и автообращений возврат в работу недоступен")
    if not is_admin(user) and ticket.closed_by_user_id != user.id:
        _error(403, "restore_forbidden", "Вернуть в работу может только сотрудник, который закрыл заявку")

    active_exists = db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.client_id == ticket.client_id,
                Ticket.id != ticket.id,
                Ticket.status != TicketStatus.CLOSED.value,
                or_(
                    Ticket.intake_state.is_(None),
                    Ticket.intake_state != TicketIntakeState.COLLECTING.value,
                ),
                or_(
                    Ticket.workflow_type.is_(None),
                    Ticket.workflow_type.notin_([TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]),
                ),
            )
        )
        .limit(1)
    ).first()
    if active_exists is not None:
        _error(409, "client_has_active_ticket", "У клиента уже есть активная заявка")

    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    collecting_tickets = db.execute(
        select(Ticket).where(
            and_(
                Ticket.client_id == ticket.client_id,
                Ticket.id != ticket.id,
                Ticket.intake_state == TicketIntakeState.COLLECTING.value,
            )
        )
    ).scalars().all()
    for collecting in collecting_tickets:
        if contact is not None:
            if collecting.menu_message_id and collecting.menu_message_id.isdigit():
                try:
                    remove_inline_keyboard(contact.external_chat_id, int(collecting.menu_message_id))
                except Exception:
                    pass
            if collecting.client_close_message_id and collecting.client_close_message_id.isdigit():
                try:
                    remove_inline_keyboard(contact.external_chat_id, int(collecting.client_close_message_id))
                except Exception:
                    pass
        collecting.menu_message_id = None
        collecting.client_close_message_id = None
        collecting.intake_state = TicketIntakeState.CANCELLED.value
        collecting.status = TicketStatus.CLOSED.value
        collecting.closed_at = datetime.utcnow()
        collecting.updated_at = datetime.utcnow()

    if ticket.is_special:
        ticket.status = TicketStatus.NEW.value if ticket.assignee_id else TicketStatus.UNASSIGNED.value
        if ticket.special_assignee_id and not ticket.assignee_id:
            ticket.assignee_id = ticket.special_assignee_id
    else:
        ticket.status = TicketStatus.NEW.value if ticket.assignee_id else TicketStatus.UNASSIGNED.value
    ticket.closed_by_user_id = None
    ticket.closed_at = None
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()
    assignee = db.get(User, ticket.assignee_id) if ticket.assignee_id else None
    if ticket.is_special:
        _add_message(db, ticket, MessageType.SYSTEM, "Особая заявка возвращена в работу администратором", None)
    else:
        _add_message(db, ticket, MessageType.SYSTEM, f"Заявка возвращена в работу. Ответственный: {assignee_label(assignee)}", None)

    actor_name = _actor_display_name(db, user)
    if ticket.created_at:
        created_at = ticket.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
        created_at_text = created_at.astimezone(ZoneInfo(settings.app_timezone)).strftime("%d.%m.%Y %H:%M")
    else:
        created_at_text = "неизвестно"
    restore_client_text = f"Вашу заявку «{ticket.subject}» от {created_at_text} вернули в работу."
    _add_message(db, ticket, MessageType.SYSTEM, f"{restore_client_text} Инициатор: {actor_name}", None, agent_visible=True)
    if contact is not None:
        try:
            send_message(contact.external_chat_id, restore_client_text, remove_keyboard=True)
        except Exception:
            pass

    _refresh_ticket_auto_close(db, ticket, restart_from_now=True)
    db.commit()
    return {"ok": True}
