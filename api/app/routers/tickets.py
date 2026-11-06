from datetime import datetime, time, timedelta
from functools import partial
import logging
from pathlib import Path
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, asc, desc, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from ..auth import (
    CurrentUser,
    can_comment,
    can_edit_client_profile,
    can_manage_ticket,
    can_reply,
    get_current_user,
    is_admin,
    is_readonly_chiki_piki,
    is_support_user,
)
from ..config import settings
from ..db import get_db
from ..models import BotButtonSetting, BotMenuDestinationBinding, ClientProfile, MessageType, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketIntakeState, TicketMessage, TicketStatus, TicketTaskKind, TicketView, TicketWorkflowType, User
from ..schemas import AdminBulkTicketsPayload, AdminCloseResult, AdminDeleteResult, AdminFreezeResult, AdminUnfreezeResult, AssignTicketsPayload, BitrixPasswordRevealOut, ClientCardRuntimeConfigResponse, ClientCardSourceRunRequest, ClientProfileOut, ClientProfileUpdate, FreezePayload, TaskPanelItem, TextPayload, TicketAutoClosePayload, TicketCard, TicketCountsOut, TicketDraftOut, TicketDraftPayload, TicketListResponse, WrongProblemPayload
from ..services.assignment import append_system_message, assign_ticket_by_admin, assignee_change_message, assignee_label, notify_client_about_assignee, schedule_employee_assignment_notification
from ..services.auto_close import AUTO_CLOSE_MINUTES, can_use_auto_close_controls, clear_ticket_auto_close_timer, refresh_ticket_auto_close_timer
from ..services.telegram_bot import TelegramApiError, delete_message, get_file, remove_inline_keyboard, send_document, send_message
from ..services.telegram_post_close import cleanup_feedback_flow_for_ticket_reopen, cleanup_review_flow_for_ticket_reopen
from ..services.telegram_outbound_delivery import (
    _discard_preload,
    _best_effort_delete_preload_archive,
    claim_attachment_preload,
    cleanup_stale_attachment_preloads,
    recover_ticket_uploads,
    deliver_attachment_preload,
    deliver_ticket_attachment,
)
from ..services.button_config import DEFAULT_MENU_NAME, resolve_button_row
from ..services.ticket_attachments import (
    _attachment_out,
    _ensure_local_attachment_file,
    _message_attachments,
    _save_attachment_preload,
    _safe_filename,
    _stream_sha256,
    _save_upload_attachment,
    _send_attachment_to_telegram_client,
)
from ..services.ticket_admin_actions import _delete_ticket_hard, delete_preliminary_telegram_ticket, is_preliminary_telegram_ticket
from ..services.client_card import _client_card_view_config, _custom_field_map, _normalize_custom_field_value, _profile_out
from ..services.client_card_derivation import apply_client_card_profile_save_pipeline, apply_client_card_source_manual_run, validate_client_card_direct_user_changes
from ..services.encryption import SecretEncryptionConfigurationError, SecretEncryptionError, decrypt_secret
from ..services import telegram_upload_state
from ..services import web_credentials
from ..schemas import WebCredentialsGeneratePayload
from fastapi.responses import JSONResponse
from ..services.ticket_special import _apply_move_to_special, _display_assignee_name, _handle_special_assignment
from ..services.statistics_lifecycle import (
    sync_completion_after_reopen,
    sync_work_interval_after_assignment_change,
    sync_work_interval_after_close,
    sync_work_interval_after_reopen,
    sync_work_interval_after_unfreeze,
)
from ..services.template_fields import render_template_text
from ..services.ticket_messages import add_message as add_ticket_message
from ..services.ticket_messages import comment_on_ticket, delete_agent_message, edit_agent_message, first_task_assignment_id, initial_task_description_ids, initial_task_message_ids, persist_client_delivery_failure, record_client_delivery_failure, reply_to_ticket
from ..services.ticket_list import apply_group_parent_visibility_filter, build_ticket_counts, internal_task_requester_display_name, list_special_tickets, list_tickets_paginated
from ..services.ticket_lifecycle import (
    close_ticket_by_actor,
    ensure_task_uploads_finished,
    task_has_active_attachment_uploads,
    format_task_closed_without_answer_text,
    finalize_group_parent_if_ready,
    freeze_ticket,
    refresh_ticket_auto_close,
    unfreeze_ticket,
    unfreeze_ticket_on_message,
    update_ticket_auto_close,
)
from ..services.ticket_numbering import ensure_ticket_number
from ..services.ticket_problem_flow import (
    apply_wrong_problem,
    get_problem_menu_for_ticket,
    menu_name_for_ticket_client,
)
from ..services.ticket_subject import build_ticket_subject_map, resolve_ticket_subject
from ..services.telegram_text_format import prepare_telegram_html_message, strip_telegram_html
from ..services.automatic_messages import render_automatic_message_text


router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
logger = logging.getLogger(__name__)

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
    "Сотрудник подсказал клиенту правильный пункт меню:",
    "Клиенту ушёл текст в бота:",
    "После завершения задачи клиенту будет отправлена подсказка:",
    "Клиент уведомлен об ограничении размера файлов Telegram Bot API",
)

def _ticket_closed_client_text(db: Session, ticket: Ticket | None = None) -> str:
    return render_automatic_message_text(db, "telegram.ticket.close.client", TICKET_CLOSED_CLIENT_TEXT, ticket=ticket)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _content_disposition_attachment(filename: str) -> str:
    normalized = (filename or "").strip() or "file"
    ascii_filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized).strip() or "file"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    encoded_filename = quote(normalized, safe="")
    return f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'


def _get_ticket_or_404(db: Session, ticket_id: str, user: CurrentUser, *, allow_preliminary_close: bool = False) -> Ticket:
    if ticket_id.isdigit():
        stmt = select(Ticket).where(Ticket.ticket_number == int(ticket_id))
    else:
        stmt = select(Ticket).where(Ticket.id == ticket_id)
    ticket = db.execute(apply_group_parent_visibility_filter(stmt, user)).scalar_one_or_none()
    if not ticket:
        _error(404, "ticket_not_found", "Тикет не найден")
    if ticket.ticket_number is None and (ticket.intake_state or TicketIntakeState.READY.value) == TicketIntakeState.READY.value:
        ensure_ticket_number(db, ticket)
        db.commit()
    if is_support_user(user):
        if ticket.status != TicketStatus.CLOSED.value and (
            ticket.agent_visible is False
            or (ticket.assignee_id != user.id and not (allow_preliminary_close and is_preliminary_telegram_ticket(db, ticket)))
        ):
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


def _normalize_draft_mode(value: str | None) -> str:
    return "comment" if str(value or "").strip().lower() == "comment" else "reply"


def _draft_out(ticket: Ticket) -> TicketDraftOut:
    return TicketDraftOut(
        text=ticket.draft_text or "",
        mode=_normalize_draft_mode(ticket.draft_mode),
        revision=int(ticket.draft_revision or 0),
    )


def _ensure_draft_write_allowed(user: CurrentUser, mode: str) -> None:
    if mode == "comment":
        if not can_comment(user):
            _error(403, "readonly_role", "Нет прав на внутренний комментарий")
    elif not can_reply(user):
        _error(403, "readonly_role", "Нет прав на отправку ответа")


def _save_ticket_draft(db: Session, ticket: Ticket, text_value: str, mode: str) -> TicketDraftOut:
    db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id)
        .values(
            draft_text=text_value,
            draft_mode=_normalize_draft_mode(mode),
            draft_revision=func.coalesce(Ticket.draft_revision, 0) + 1,
            updated_at=datetime.utcnow(),
        )
    )
    db.flush()
    db.refresh(ticket)
    return _draft_out(ticket)


def _call_center_profile_overrides(ticket: Ticket) -> dict:
    def meaningful(value) -> bool:
        return value is not None and str(value).strip() != ""

    def put(overrides: dict, key: str, value) -> None:
        if meaningful(value):
            overrides[key] = value

    def meaningful_custom_fields(value) -> dict:
        if not isinstance(value, dict):
            return {}
        return {key: item for key, item in value.items() if meaningful(item)}

    snapshot = ticket.call_center_profile_snapshot_json or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    profile_delta = snapshot.get("profile_delta")
    if isinstance(profile_delta, dict):
        overrides = {}
        for key in ("contact_name", "last_name", "bitrix_id", "external_user_id", "bitrix_login", "internal_number"):
            put(overrides, key, profile_delta.get(key))
        custom_fields = meaningful_custom_fields(profile_delta.get("custom_fields"))
        if custom_fields:
            overrides["custom_fields"] = custom_fields
        return overrides

    overrides = {}
    custom_fields = meaningful_custom_fields(snapshot.get("custom_fields"))
    if custom_fields:
        overrides["custom_fields"] = custom_fields
    put(overrides, "internal_number", snapshot.get("internal_number"))
    if ticket.call_center_employee_first_name is not None:
        put(overrides, "contact_name", ticket.call_center_employee_first_name)
    elif "first_name" in snapshot:
        put(overrides, "contact_name", snapshot.get("first_name"))
    if ticket.call_center_employee_last_name is not None:
        put(overrides, "last_name", ticket.call_center_employee_last_name)
    elif "last_name" in snapshot:
        put(overrides, "last_name", snapshot.get("last_name"))
    if ticket.call_center_employee_id is not None:
        put(overrides, "bitrix_id", ticket.call_center_employee_id)
        put(overrides, "external_user_id", ticket.call_center_employee_id)
    elif "bitrix_id" in snapshot:
        put(overrides, "bitrix_id", snapshot.get("bitrix_id"))
        put(overrides, "external_user_id", snapshot.get("bitrix_id"))
    if ticket.call_center_employee_bitrix_login is not None:
        put(overrides, "bitrix_login", ticket.call_center_employee_bitrix_login)
    elif "bitrix_login" in snapshot:
        put(overrides, "bitrix_login", snapshot.get("bitrix_login"))
    return overrides


def _drop_call_center_snapshot_payload(data: dict, ticket: Ticket) -> dict:
    overrides = _call_center_profile_overrides(ticket)
    if not overrides:
        return data
    sanitized = dict(data)
    snapshot_custom_fields = overrides.get("custom_fields") if isinstance(overrides.get("custom_fields"), dict) else {}
    for key in ("contact_name", "last_name", "bitrix_id", "external_user_id", "bitrix_login", "internal_number"):
        if key in overrides:
            sanitized.pop(key, None)
    if "custom_fields" in sanitized and isinstance(sanitized["custom_fields"], dict):
        custom_fields = dict(sanitized["custom_fields"])
        for key in snapshot_custom_fields:
            custom_fields.pop(key, None)
        sanitized["custom_fields"] = custom_fields
    return sanitized


def _current_client_profile_out(profile: ClientProfile, ticket: Ticket, contact) -> ClientProfileOut:
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else (ticket.client.display_name if ticket.client else None)
    telegram_username = contact.external_username if contact else None
    return _profile_out(
        profile,
        telegram_username,
        telegram_display_name,
    ).model_copy(update={
        "telegram_avatar_present": bool(contact and contact.avatar_thumbnail),
        "telegram_avatar_updated_at": contact.avatar_updated_at if contact and contact.avatar_thumbnail else None,
    })


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
    old_is_special = bool(ticket.is_special)
    old_status = ticket.status
    actor_user = db.get(User, actor.id) if actor.id else None
    actor_name = actor_user.full_name if actor_user and actor_user.full_name else "Администратор"

    if ticket.is_special and assignee is None:
        _error(409, "special_requires_admin_assignee", "У особой заявки должен быть ответственный администратор")

    if assignee is None:
        now = datetime.utcnow()
        ticket.assignee_id = None
        if ticket.status != TicketStatus.CLOSED.value:
            ticket.status = TicketStatus.UNASSIGNED.value
        ticket.updated_at = now
        sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee.id if old_assignee else None, new_assignee_id=None, now=now)
        append_system_message(db, ticket, assignee_change_message(old_assignee, None))
        schedule_employee_assignment_notification(
            db,
            ticket,
            old_assignee=old_assignee,
            new_assignee=None,
            old_is_special=old_is_special,
            new_is_special=bool(ticket.is_special),
            actor_user_id=actor_user.id if actor_user is not None else None,
        )
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
        sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee.id if old_assignee else None, new_assignee_id=ticket.assignee_id, now=ticket.updated_at)
        schedule_employee_assignment_notification(
            db,
            ticket,
            old_assignee=old_assignee,
            new_assignee=assignee,
            old_is_special=old_is_special,
            new_is_special=bool(ticket.is_special),
            actor_user_id=actor_user.id if actor_user is not None else None,
        )
        return

    assign_ticket_by_admin(
        db,
        ticket,
        assignee,
        actor_user_id=actor_user.id if actor_user is not None else None,
        old_is_special=old_is_special,
        notify_client=True,
    )


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
    reply_markup: dict | None = None,
    reply_to_message_id: str | None = None,
) -> dict | None:
    if ticket.channel == "web":
        return None
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is None:
        if required:
            _error(409, "telegram_contact_not_found", "У клиента нет Telegram-контакта")
        return None
    try:
        payload_text, parse_mode = prepare_telegram_html_message(text)
        return send_message(
            contact.external_chat_id,
            payload_text,
            parse_mode=parse_mode,
            remove_keyboard=remove_keyboard,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramApiError as exc:
        # Fallback: if HTML parse failed, send plain text without formatting.
        if "parse" in str(exc).lower() or "entity" in str(exc).lower() or "can't parse" in str(exc).lower():
            plain_text = strip_telegram_html(text)
            try:
                return send_message(
                    contact.external_chat_id,
                    plain_text,
                    remove_keyboard=remove_keyboard,
                    reply_markup=reply_markup,
                    reply_to_message_id=reply_to_message_id,
                )
            except TelegramApiError:
                pass
        if required:
            _error(502, "telegram_send_failed", f"Telegram не принял сообщение: {exc}")
        return None


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def _format_greeting_text(greeting_text: str, contact_name: str | None, telegram_username: str | None) -> str:
    name = (contact_name or "").strip()
    if name:
        return f"{greeting_text}, {name}!"

    username = (telegram_username or "").strip()
    if username:
        if not username.startswith("@"):
            username = f"@{username}"
        return f"{greeting_text}, {username}!"

    return greeting_text


def _send_problem_menu_to_telegram_client(db: Session, ticket: Ticket):
    contact = _telegram_contact_for_client(db, ticket.client_id)
    if contact is not None:
        try:
            from .telegram import _send_problem_menu_with_visibility
            _send_problem_menu_with_visibility(
                db, contact.external_chat_id,
                menu_name=menu_name_for_ticket_client(
                    db,
                    ticket,
                    get_or_create_client_profile_fn=_get_or_create_client_profile,
                ),
            )
        except TelegramApiError:
            pass


def _instruction_for_ticket(db: Session, ticket: Ticket) -> tuple[str | None, str | None]:
    key = (ticket.instruction_button_key or "").strip()
    if not key:
        return None, None
    row = resolve_button_row(db, key, include_inactive=True)
    if row is None:
        return None, None
    text = (row.instruction_text or "").strip()
    if not text:
        return None, None
    return row.button_label, text


def _group_parent_client_text_override(db: Session, ticket: Ticket) -> str | None:
    keys: list[str] = []
    instruction_key = (ticket.instruction_button_key or "").strip()
    button_key = (ticket.button_config_key or "").strip()
    if instruction_key:
        keys.append(instruction_key)
    if button_key and button_key not in keys:
        keys.append(button_key)
    if not keys:
        return None

    rows = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key.in_(keys))).scalars().all()
    rows_by_key = {row.button_key: row for row in rows}
    for key in keys:
        row = rows_by_key.get(key)
        if row is None or not bool(row.client_text_overridden):
            continue
        raw = (row.client_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not raw.strip():
            return None
        rendered = render_template_text(raw, db, ticket=ticket) or raw
        return rendered.replace("\r\n", "\n").replace("\r", "\n")

    # Fallback for group parent rows: in hybrid mode instruction_button_key can be empty
    # and button_config_key may differ from the DB settings key for parent group_task.
    if (
        (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TASK.value
        and (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.GROUP_PARENT.value
    ):
        menu_hint = ""
        if button_key and "|" in button_key:
            menu_hint = button_key.split("|", 1)[0].strip()
        subject = (ticket.subject or "").strip()
        if subject:
            stmt = select(BotButtonSetting).where(
                and_(
                    BotButtonSetting.workflow_type == "group_task",
                    BotButtonSetting.currently_present.is_(True),
                    BotButtonSetting.is_archived.is_(False),
                    BotButtonSetting.button_label == subject,
                )
            )
            if menu_hint:
                stmt = stmt.where(BotButtonSetting.menu_type == menu_hint)
            candidates = db.execute(stmt).scalars().all()
            for row in candidates:
                if not bool(row.client_text_overridden):
                    continue
                raw = (row.client_text or "").replace("\r\n", "\n").replace("\r", "\n")
                if not raw.strip():
                    return None
                rendered = render_template_text(raw, db, ticket=ticket) or raw
                return rendered.replace("\r\n", "\n").replace("\r", "\n")
    return None


def _remove_client_close_button(db: Session, ticket: Ticket):
    if ticket.channel == "web":
        return
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
    if (
        message.body == "Клиент нажал /start и пытается создать новое обращение"
        and message.agent_visible is True
    ):
        return True
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
    query: str | None = Query(default=None),
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
        query=query,
        admin_assignee_ids=assignee_ids,
        ensure_ticket_number_fn=ensure_ticket_number,
    )


@router.get("/unassigned", response_model=TicketListResponse)
def list_unassigned(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
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
        query=query,
        admin_assignee_ids=None,
        filter_admin_assignees=False,
        require_admin_or_chiki=True,
        ensure_ticket_number_fn=ensure_ticket_number,
    )


@router.get("/frozen", response_model=TicketListResponse)
def list_frozen(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
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
        query=query,
        admin_assignee_ids=assignee_ids,
        ensure_ticket_number_fn=ensure_ticket_number,
    )


@router.get("/closed", response_model=TicketListResponse)
def list_closed(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
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
        query=query,
        admin_assignee_ids=assignee_ids,
        filter_admin_assignees=False,
        include_special_open=False,
        include_support_special_closed=True,
        include_support_all_closed=True,
        ensure_ticket_number_fn=ensure_ticket_number,
    )


@router.get("/special", response_model=TicketListResponse)
def list_special(
    sort: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = Query(default=None),
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
        query=query,
        ensure_ticket_number_fn=ensure_ticket_number,
    )


@router.get("/counts", response_model=TicketCountsOut, response_model_exclude_none=True)
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

    for ticket in tickets:
        ensure_task_uploads_finished(db, ticket)
    closed = 0
    already_closed = 0
    for ticket in tickets:
        result = close_ticket_by_actor(
            db,
            ticket,
            user,
            force_without_agent_reply_for_task=True,
            skip_client_notification=payload.skip_client_notification,
            is_admin_fn=is_admin,
            get_or_create_client_profile_fn=_get_or_create_client_profile,
            last_agent_message_fn=_last_agent_message,
            first_client_message_fn=_first_client_message,
            send_to_telegram_client_fn=_send_to_telegram_client,
            actor_display_name_fn=_actor_display_name,
            add_message_fn=_add_message,
            message_attachments_fn=_message_attachments,
            send_attachment_to_telegram_client_fn=_send_attachment_to_telegram_client,
            onsite_close_reminder_text=ONSITE_CLOSE_REMINDER_TEXT,
            ticket_closed_client_text=TICKET_CLOSED_CLIENT_TEXT,
            resolve_group_parent_client_text_fn=_group_parent_client_text_override,
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


@router.post("/admin/freeze", response_model=AdminFreezeResult)
def admin_freeze_tickets(payload: AdminBulkTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)

    ticket_ids = list(dict.fromkeys(payload.ticket_ids))
    tickets = db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    not_found = [ticket_id for ticket_id in ticket_ids if ticket_id not in found_ids]

    frozen = 0
    already_frozen = 0
    invalid = 0
    closed = 0
    for ticket in tickets:
        if ticket.status == TicketStatus.CLOSED.value:
            closed += 1
            continue
        if ticket.is_frozen:
            already_frozen += 1
            continue
        if ticket.status == TicketStatus.UNASSIGNED.value or not ticket.assignee_id or ticket.is_special:
            invalid += 1
            continue
        freeze_ticket(
            db,
            ticket,
            freeze_until=None,
            add_message_fn=_add_message,
        )
        frozen += 1

    db.commit()
    return AdminFreezeResult(
        ok=True,
        frozen=frozen,
        already_frozen=already_frozen,
        invalid=invalid,
        closed=closed,
        not_found=not_found,
    )


@router.post("/admin/unfreeze", response_model=AdminUnfreezeResult)
def admin_unfreeze_tickets(payload: AdminBulkTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)

    ticket_ids = list(dict.fromkeys(payload.ticket_ids))
    tickets = db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids))).scalars().all()
    found_ids = {ticket.id for ticket in tickets}
    not_found = [ticket_id for ticket_id in ticket_ids if ticket_id not in found_ids]

    unfrozen = 0
    already_unfrozen = 0
    closed = 0
    for ticket in tickets:
        if ticket.status == TicketStatus.CLOSED.value:
            closed += 1
            continue
        if not ticket.is_frozen:
            already_unfrozen += 1
            continue
        unfreeze_ticket(
            db,
            ticket,
            add_message_fn=_add_message,
            refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
        )
        unfrozen += 1

    db.commit()
    return AdminUnfreezeResult(
        ok=True,
        unfrozen=unfrozen,
        already_unfrozen=already_unfrozen,
        closed=closed,
        not_found=not_found,
    )


@router.post("/admin/delete", response_model=AdminDeleteResult)
def admin_delete_tickets(payload: AdminBulkTicketsPayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    tickets = db.execute(select(Ticket).where(Ticket.id.in_(payload.ticket_ids))).scalars().all()
    selected_ids = set(payload.ticket_ids)
    found_ids = {ticket.id for ticket in tickets}
    not_found = [ticket_id for ticket_id in payload.ticket_ids if ticket_id not in found_ids]

    notifications = []

    def defer_notification(sender, db_session, ticket, content, **kwargs):
        if ticket.channel == "web":
            return {}
        # Transient column snapshots survive deletion and commit expiration. They
        # are never added to the session; delivery cannot recreate correspondence.
        ticket_snapshot = Ticket(**{column.key: getattr(ticket, column.key) for column in Ticket.__table__.columns})
        if isinstance(content, TicketAttachment):
            content = TicketAttachment(**{column.key: getattr(content, column.key) for column in TicketAttachment.__table__.columns})
        notifications.append((sender, ticket_snapshot, content, kwargs))
        return {}  # Accepted for delivery after commit; do not record a failure now.

    warning_batches: list[list[str]] = []
    deleted = 0
    deleted_ids: set[str] = set()
    try:
        for ticket in tickets:
            try:
                ticket_id = ticket.id
                ticket_parent_id = ticket.parent_ticket_id
                ticket_task_kind = ticket.task_kind
                workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
                ticket_status = ticket.status
            except ObjectDeletedError:
                # Ticket row was hard-deleted earlier in this batch (for example by parent delete).
                continue

            if ticket_id in deleted_ids:
                continue
            # If parent was already deleted in this batch, its children are already hard-deleted too.
            if ticket_parent_id and ticket_parent_id in deleted_ids:
                deleted_ids.add(ticket_id)
                continue
            # Skip stale ORM rows that may already be removed by previous parent delete.
            still_exists = db.execute(select(Ticket.id).where(Ticket.id == ticket_id)).scalar_one_or_none()
            if still_exists is None:
                deleted_ids.add(ticket_id)
                continue

            parent_ticket_id = ticket_parent_id if ticket_task_kind == TicketTaskKind.GROUP_CHILD.value else None
            if ticket_status != TicketStatus.CLOSED.value and not payload.skip_client_notification:
                try:
                    if workflow_type == TicketWorkflowType.TICKET.value:
                        defer_notification(_send_to_telegram_client, db, ticket,
                            TICKET_CLOSED_CLIENT_TEXT, required=False, remove_keyboard=True)
                    elif workflow_type == TicketWorkflowType.TASK.value and ticket_task_kind != TicketTaskKind.GROUP_CHILD.value:
                        client_message = _first_client_message(db, ticket)
                        defer_notification(_send_to_telegram_client, db, ticket,
                            format_task_closed_without_answer_text(db, ticket, client_message),
                            required=False, remove_keyboard=True)
                except SQLAlchemyError:
                    raise
                except Exception:
                    logger.exception("Client notification preparation for admin deletion failed ticket=%s", ticket_id)
            warning_batches.append(_delete_ticket_hard(db, ticket))
            deleted_ids.add(ticket_id)
            deleted += 1
            if parent_ticket_id:
                # Parent is being deleted in the same bulk action:
                # do not finalize it via child path (avoids duplicate client notice and
                # avoids creating parent system messages that can conflict with hard-delete flush).
                if parent_ticket_id in selected_ids:
                    continue
                parent = db.get(Ticket, parent_ticket_id)
                if parent is not None:
                    finalize_group_parent_if_ready(
                        db,
                        parent,
                        closed_by_user_id=user.id,
                        skip_client_notification=payload.skip_client_notification or ticket_status == TicketStatus.CLOSED.value,
                        system_note="Групповая задача завершена системой (возможен неполный ответ)",
                        first_client_message_fn=_first_client_message,
                        send_to_telegram_client_fn=partial(defer_notification, _send_to_telegram_client),
                        send_attachment_to_telegram_client_fn=partial(defer_notification, _send_attachment_to_telegram_client),
                        add_message_fn=_add_message,
                        resolve_group_parent_client_text_fn=_group_parent_client_text_override,
                    )
        db.commit()
    except Exception:
        db.rollback()
        raise
    for sender, ticket_snapshot, content, kwargs in notifications:
        try:
            sender(db, ticket_snapshot, content, **kwargs)
        except Exception:
            logger.exception("Client notification after admin deletion failed ticket=%s", ticket_snapshot.id)
    return AdminDeleteResult(
        ok=True,
        deleted=deleted,
        not_found=not_found,
        file_delete_warnings=[warning for batch in warning_batches for warning in batch],
    )


@router.get("/task-panel", response_model=list[TaskPanelItem])
def get_task_panel(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    latest_message = (
        select(
            TicketMessage.ticket_id.label("ticket_id"),
            func.max(TicketMessage.created_at).label("last_message_at"),
        )
        .where(or_(TicketMessage.author_user_id.is_(None), TicketMessage.author_user_id != user.id))
        .group_by(TicketMessage.ticket_id)
        .subquery()
    )
    rows = db.execute(
        apply_group_parent_visibility_filter(select(
            Ticket,
            TicketView.last_opened_at,
            latest_message.c.last_message_at,
        )
        .join(TicketView, and_(TicketView.ticket_id == Ticket.id, TicketView.user_id == user.id))
        .join(latest_message, latest_message.c.ticket_id == Ticket.id, isouter=True)
        .where(
            and_(
                Ticket.assignee_id == user.id,
                Ticket.agent_visible.is_(True),
                Ticket.status.in_([TicketStatus.NEW.value, TicketStatus.PENDING.value]),
                Ticket.is_special.is_(False),
            )
        )
        .order_by(desc(TicketView.first_opened_at)), user)
    ).all()
    tickets = [ticket for ticket, _last_opened_at, _last_message_at in rows]
    subject_map = build_ticket_subject_map(db, tickets)
    return [
        TaskPanelItem(
            id=ticket.id,
            subject=subject_map.get(ticket.id, ticket.subject),
            has_unread=bool(last_message_at and last_opened_at and last_message_at > last_opened_at),
        )
        for ticket, last_opened_at, last_message_at in rows
    ]


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
        refresh_ticket_auto_close(
            db,
            t,
            restart_from_now=False,
            refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
        )
        db.commit()
    if is_support_user(user):
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
    visible_client_name = internal_task_requester_display_name(db, t) or (t.client.display_name if t.client else "")
    telegram_display_name = contact.external_display_name if contact and contact.external_display_name else visible_client_name
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
    description_ids = initial_task_description_ids(t, messages, attachments_by_message)
    initial_message_ids = initial_task_message_ids(t, messages, attachments_by_message)
    initial_assignment_id = first_task_assignment_id(messages) if initial_message_ids else None
    recover_ticket_uploads(db, t.id)
    active_preload_threshold = datetime.utcnow() - timedelta(hours=24)
    active_attachment_preloads = [
        {
            "id": preload.id,
            "original_filename": preload.original_filename,
            "file_size": preload.file_size,
            "status": preload.status,
            "claimed": bool(preload.final_attachment_id),
            "error": (preload.telegram_upload_state_json or {}).get("error"),
            "uncertain": bool((preload.telegram_upload_state_json or {}).get("uncertain")),
            "created_at": preload.created_at,
        }
        for preload in db.execute(
            select(TicketAttachmentPreload)
            .where(
                TicketAttachmentPreload.ticket_id == t.id,
                TicketAttachmentPreload.user_id == user.id,
                or_(TicketAttachmentPreload.final_attachment_id.is_(None),
                    and_(t.workflow_type == TicketWorkflowType.TASK.value, TicketAttachmentPreload.status == "failed")),
                TicketAttachmentPreload.status.in_(["uploading", "ready", "failed"]),
                or_(TicketAttachmentPreload.status == "failed", TicketAttachmentPreload.created_at >= active_preload_threshold),
            )
            .order_by(asc(TicketAttachmentPreload.created_at), asc(TicketAttachmentPreload.id))
        ).scalars().all()
        if preload.final_attachment_id is None or telegram_upload_state.recoverable_task_attachment(db, preload) is not None
    ]
    instruction_title, instruction_text = _instruction_for_ticket(db, t)

    return TicketCard(
        id=t.id,
        has_active_attachment_uploads=task_has_active_attachment_uploads(db, t),
        ticket_number=t.ticket_number,
        subject=resolve_ticket_subject(db, t),
        workflow_type=t.workflow_type or TicketWorkflowType.TICKET.value,
        task_kind=t.task_kind,
        parent_ticket_id=t.parent_ticket_id,
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
        quick_actions_used={
            "greeting": bool((t.quick_action_state_json or {}).get("greeting")),
            "clarification": bool((t.quick_action_state_json or {}).get("clarification")),
            "onsite": bool((t.quick_action_state_json or {}).get("onsite")),
        },
        auto_close_disabled=bool(t.auto_close_disabled),
        auto_close_started_at=t.auto_close_started_at,
        auto_close_due_at=t.auto_close_due_at,
        auto_closed_at=t.auto_closed_at,
        closed_by_user_id=t.closed_by_user_id,
        onsite_triggered=bool(t.onsite_triggered),
        call_center_employee_lookup_at=t.call_center_employee_lookup_at,
        call_center_internal_number=t.call_center_internal_number,
        call_center_employee_first_name=t.call_center_employee_first_name,
        call_center_employee_last_name=t.call_center_employee_last_name,
        call_center_employee_id=t.call_center_employee_id,
        call_center_employee_bitrix_login=t.call_center_employee_bitrix_login,
        draft_text=t.draft_text or "",
        draft_mode=_normalize_draft_mode(t.draft_mode),
        draft_revision=int(t.draft_revision or 0),
        client_name=visible_client_name,
        client_profile=_profile_out(
            profile,
            telegram_username,
            telegram_display_name,
            _call_center_profile_overrides(t),
        ).model_copy(update={
            "telegram_avatar_present": bool(contact and contact.avatar_thumbnail),
            "telegram_avatar_updated_at": contact.avatar_updated_at if contact and contact.avatar_thumbnail else None,
        }),
        active_attachment_preloads=active_attachment_preloads,
        messages=[
            {
                "id": m.id,
                "is_initial_task_description": m.id in description_ids,
                "is_initial_task_message": m.id in initial_message_ids,
                "is_initial_task_assignment": m.id == initial_assignment_id,
                "message_type": m.message_type,
                "body": m.body,
                "author_user_id": m.author_user_id,
                "author_name": authors.get(m.author_user_id) if m.author_user_id else None,
                "telegram_message_id": m.telegram_message_id,
                "message_status": m.message_status or "normal",
                "status_note": m.status_note,
                "replaced_message_id": m.replaced_message_id,
                "quote_message_id": m.quote_message_id,
                "created_at": m.created_at,
                "attachments": [_attachment_out(t.id, attachment) for attachment in attachments_by_message.get(m.id, [])],
            }
            for m in messages
        ],
    )


@router.patch("/{ticket_id}/draft", response_model=TicketDraftOut)
def update_ticket_draft(
    ticket_id: str,
    payload: TicketDraftPayload,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    mode = _normalize_draft_mode(payload.mode)
    _ensure_draft_write_allowed(user, mode)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    result = _save_ticket_draft(db, ticket, payload.text or "", mode)
    db.commit()
    return result


@router.get("/{ticket_id}/client-card-view-config", response_model=ClientCardRuntimeConfigResponse)
def get_client_card_view_config(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _get_ticket_or_404(db, ticket_id, user)
    return _client_card_view_config(db)


@router.get("/{ticket_id}/client-profile", response_model=ClientProfileOut)
def get_current_client_profile(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = _get_ticket_or_404(db, ticket_id, user)
    profile = _get_or_create_client_profile(db, t.client_id)
    contact = _telegram_contact_for_client(db, t.client_id)
    return _current_client_profile_out(profile, t, contact)


@router.get("/{ticket_id}/client-profile/web-password")
def reveal_ticket_web_password(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    web_credentials.authorize_fields(db, user, {"custom_web_password"})
    return JSONResponse(web_credentials.reveal(web_credentials.require_profile(db, ticket.client_id)), headers={"Cache-Control": "no-store"})


@router.post("/{ticket_id}/client-profile/web-credentials/generate")
def generate_ticket_web_credentials(ticket_id: str, payload: WebCredentialsGeneratePayload,
                                    db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_edit_client_profile(user):
        _error(403, "readonly_role", "Нет прав на редактирование карточки клиента")
    web_credentials.authorize_fields(db, user, web_credentials.WEB_KEYS, write=True)
    result = web_credentials.generate_draft(db, web_credentials.require_profile(db, ticket.client_id), payload)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/{ticket_id}/client-profile/bitrix-password", response_model=BitrixPasswordRevealOut)
def reveal_ticket_client_bitrix_password(
    ticket_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if is_readonly_chiki_piki(user):
        _error(403, "password_reveal_forbidden", "Нет прав на раскрытие пароля")
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    if profile is None:
        _error(404, "client_profile_not_found", "Профиль клиента не найден")
    encrypted_password = profile.bitrix_password
    if not encrypted_password or not str(encrypted_password).strip():
        return {"bitrix_password": None}
    try:
        password = decrypt_secret(encrypted_password)
    except SecretEncryptionConfigurationError:
        logger.warning("Bitrix password reveal failed because encryption is not configured correctly.")
        _error(503, "bitrix_password_decrypt_unavailable", "Пароль Bitrix временно недоступен")
    except SecretEncryptionError:
        logger.warning("Bitrix password reveal failed because stored value could not be decrypted.")
        _error(409, "bitrix_password_decrypt_failed", "Пароль Bitrix не удалось расшифровать")
    return {"bitrix_password": password}


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
    headers = {"Content-Disposition": _content_disposition_attachment(filename)}

    if path.exists() and path.is_file():
        return FileResponse(path, media_type=media_type, headers=headers)

    file_path = (attachment.telegram_file_path or "").strip()
    if not file_path:
        file_id = (attachment.telegram_file_id or "").strip()
        if not file_id:
            _error(404, "attachment_file_missing", "Файл недоступен для скачивания")
        contact = _telegram_contact_for_client(db, ticket.client_id) if ticket.channel != "web" else None
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
    try:
        telegram_response = urlopen(request, timeout=120)
    except HTTPError as exc:
        if exc.code == 404:
            _error(404, "attachment_file_missing", "Telegram file is not available")
        _error(502, "attachment_download_failed", f"Telegram file download failed: HTTP {exc.code}")
    except URLError as exc:
        _error(502, "attachment_download_failed", f"Файл временно недоступен: {exc}")

    def _iter_stream():
        try:
            while True:
                chunk = telegram_response.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            telegram_response.close()

    telegram_content_length = telegram_response.headers.get("Content-Length")
    if telegram_content_length:
        headers["Content-Length"] = telegram_content_length
    elif attachment.file_size:
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
        _error(403, "readonly_role", "\u041d\u0435\u0442 \u043f\u0440\u0430\u0432 \u043d\u0430 \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438 \u043a\u043b\u0438\u0435\u043d\u0442\u0430")
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == t.client_id)).scalar_one_or_none()
    contact = _telegram_contact_for_client(db, t.client_id)

    data = payload.model_dump(exclude_unset=True)
    web_keys = web_credentials.WEB_KEYS.intersection(data.get("custom_fields") or {})
    if data.get("clear_web_password"):
        web_keys.add("custom_web_password")
    if web_keys:
        web_credentials.authorize_fields(db, user, web_keys, write=True)
    merged_custom_fields = dict(profile.custom_fields or {}) if profile is not None else {}
    if "custom_fields" in data and data["custom_fields"] is not None:
        custom_fields_map = _custom_field_map(db)
        incoming = data["custom_fields"] or {}
        if not isinstance(incoming, dict):
            _error(422, "invalid_custom_fields_payload", "custom_fields должен быть объектом")
        unknown_keys = [key for key in incoming.keys() if key not in custom_fields_map]
        if unknown_keys:
            _error(400, "unknown_custom_field_keys", f"Неизвестные custom поля: {', '.join(unknown_keys)}")
        normalized_custom: dict = dict(merged_custom_fields)
        for key, value in incoming.items():
            field = custom_fields_map[key]
            if not field.is_editable:
                _error(403, "readonly_custom_field", f"Поле '{field.label}' недоступно для редактирования")
            if value is None:
                normalized_custom.pop(key, None)
                continue
            normalized = _normalize_custom_field_value(field, value)
            if normalized is None:
                normalized_custom.pop(key, None)
            else:
                normalized_custom[key] = normalized
        merged_custom_fields = normalized_custom
    if not is_admin(user):
        validate_client_card_direct_user_changes(db, profile, data, merged_custom_fields, user=user)
    result = apply_client_card_profile_save_pipeline(db, profile, data, merged_custom_fields, client_id=t.client_id, user=user)
    profile = result.profile

    if t.client and "contact_name" in result.changed_keys and profile.contact_name:
        t.client.display_name = profile.contact_name

    db.commit()
    db.refresh(profile)
    return _current_client_profile_out(profile, t, contact)


@router.post("/{ticket_id}/client-profile/source-run", response_model=ClientProfileOut)
def run_client_profile_source(
    ticket_id: str,
    payload: ClientCardSourceRunRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    t = _get_ticket_or_404(db, ticket_id, user)
    if not can_edit_client_profile(user):
        _error(403, "readonly_role", "\u041d\u0435\u0442 \u043f\u0440\u0430\u0432 \u043d\u0430 \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438 \u043a\u043b\u0438\u0435\u043d\u0442\u0430")
    contact = _telegram_contact_for_client(db, t.client_id)
    result = apply_client_card_source_manual_run(db, client_id=t.client_id, source_id=payload.source_id)
    profile = result.profile
    if t.client and "contact_name" in result.changed_keys and profile.contact_name:
        t.client.display_name = profile.contact_name
    db.commit()
    db.refresh(profile)
    return _current_client_profile_out(profile, t, contact)


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
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                or_(TicketMessage.message_status.is_(None), TicketMessage.message_status != "deleted"),
            )
        )
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


@router.get("/{ticket_id}/problem-menu")
def problem_menu_for_ticket(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return get_problem_menu_for_ticket(
        db,
        ticket,
        get_or_create_client_profile_fn=_get_or_create_client_profile,
    )


@router.post("/{ticket_id}/reply")
async def reply(
    ticket_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
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
        send_attachment_to_telegram_client=lambda db_session, t, a, required, **kwargs: _send_attachment_to_telegram_client(
            db_session, t, a, required=required, **kwargs
        ),
        claim_attachment_preload=lambda db_session, preload_id, expected_ticket_id, expected_user_id: claim_attachment_preload(
            db_session,
            preload_id=preload_id,
            ticket_id=expected_ticket_id,
            user_id=expected_user_id,
        ),
        schedule_attachment_delivery=lambda **kwargs: background_tasks.add_task(deliver_ticket_attachment, **kwargs),
        unfreeze_ticket_on_message=unfreeze_ticket_on_message,
        remove_client_close_button=_remove_client_close_button,
        refresh_ticket_auto_close=lambda db_session, t, restart_from_now: refresh_ticket_auto_close(
            db_session, t, restart_from_now=restart_from_now, refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer
        ),
    )


@router.post("/{ticket_id}/attachment-preloads")
async def create_attachment_preload(
    ticket_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    archive_group_id: str | None = Form(default=None),
    upload_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на отправку ответа")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    if workflow_type not in {TicketWorkflowType.TICKET.value, TicketWorkflowType.TASK.value}:
        _error(409, "attachment_preload_unsupported_workflow", "Предварительная подготовка доступна только для заявки или задачи")
    if not user.id:
        _error(409, "user_id_required", "Для предзагрузки файла нужен корректный пользователь")

    if isinstance(upload_id, str):
        try:
            upload_id = str(uuid.UUID(upload_id))
        except ValueError:
            _error(422, "attachment_upload_id_invalid", "Некорректный идентификатор загрузки")
        telegram_upload_state.lock_row(db, Ticket, ticket.id)
        existing = telegram_upload_state.lock_row(db, TicketAttachmentPreload, upload_id)
        claimed = False
        if existing is None:
            archived = db.execute(select(TicketAttachment, TicketMessage.author_user_id).join(
                TicketMessage, TicketAttachment.message_id == TicketMessage.id).where(
                TicketAttachment.telegram_upload_state_json["upload_id"].as_string() == upload_id)).first()
            if archived is not None:
                if archived[0].ticket_id != ticket.id or archived[1] != user.id:
                    _error(409, "attachment_upload_id_conflict", "Идентификатор загрузки уже используется")
                existing = telegram_upload_state.lock_row(db, TicketAttachment, archived[0].id)
                claimed = existing is not None
        if existing is not None:
            if existing.ticket_id != ticket.id or (not claimed and existing.user_id != user.id) or existing.original_filename != _safe_filename(file.filename):
                _error(409, "attachment_upload_id_conflict", "Идентификатор загрузки уже используется")
            digest = telegram_upload_state.state(existing).get("content_sha256")
            if not digest and Path(existing.local_path).is_file():
                with Path(existing.local_path).open("rb") as source:
                    digest = _stream_sha256(source)
            if not digest or digest != _stream_sha256(file.file) or existing.mime_type != (file.content_type or "application/octet-stream"):
                _error(409, "attachment_upload_id_conflict", "Идентификатор загрузки связан с другим содержимым файла")
            telegram_upload_state.patch(existing, content_sha256=digest)
            db.commit()
            if claimed:
                return {"id": upload_id, "status": existing.telegram_delivery_status or "ready", "claimed": True}
            if existing.status == "uploading":
                background_tasks.add_task(deliver_attachment_preload, preload_id=existing.id)
            return telegram_upload_state.preload_payload(existing)
    else:
        upload_id = None
    cleanup_stale_attachment_preloads(db)
    ensure_ticket_number(db, ticket)
    preload = _save_attachment_preload(db, ticket, user.id, file, archive_group_id, preload_id=upload_id)
    db.commit()
    background_tasks.add_task(deliver_attachment_preload, preload_id=preload.id)
    return telegram_upload_state.preload_payload(preload)


@router.get("/{ticket_id}/attachment-preloads/{preload_id}")
def get_attachment_preload_status(
    ticket_id: str,
    preload_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    preload = db.get(TicketAttachmentPreload, preload_id)
    if preload is None or preload.ticket_id != ticket.id or preload.user_id != user.id:
        _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
    preload = telegram_upload_state.expire_attempt(db, preload)
    if preload is None:
        _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
    return telegram_upload_state.preload_payload(preload)


@router.post("/{ticket_id}/attachment-preloads/{preload_id}/retry")
def retry_attachment_preload(ticket_id: str, preload_id: str, background_tasks: BackgroundTasks,
                             confirm_uncertain: bool = Form(False), db: Session = Depends(get_db),
                             user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на отправку ответа")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    preload = db.get(TicketAttachmentPreload, preload_id)
    if preload is None or preload.ticket_id != ticket.id or preload.user_id != user.id:
        _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
    preload, schedule = telegram_upload_state.retry_preload(db, preload, confirm_uncertain is True)
    if schedule:
        background_tasks.add_task(deliver_attachment_preload, preload_id=preload.id)
    return telegram_upload_state.preload_payload(preload)


@router.delete("/{ticket_id}/attachment-preloads/{preload_id}")
def delete_attachment_preload(
    ticket_id: str,
    preload_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    cleanup_stale_attachment_preloads(db)
    preload = telegram_upload_state.lock_row(db, TicketAttachmentPreload, preload_id)
    if preload is None:
        db.commit()
        return {"ok": True}
    if preload.ticket_id != ticket.id or preload.user_id != user.id:
        _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
    if preload.final_attachment_id:
        attachment = telegram_upload_state.recoverable_task_attachment(db, preload)
        if attachment is None:
            _error(409, "attachment_preload_already_claimed", "Предзагруженный файл уже отправляется")
        message = db.get(TicketMessage, attachment.message_id)
        db.delete(attachment)
        db.flush()
        if message is not None and not (message.body or "").strip() and not db.scalar(select(TicketAttachment.id).where(TicketAttachment.message_id == message.id)):
            message.message_status = "deleted"
        preload.final_attachment_id = None
    if preload.status in {"ready", "failed"}:
        active = (telegram_upload_state.state(preload).get("staging_attempt") or {}).get("active")
        _discard_preload(db, preload, delete_row=not active, unlink_local=not active)
    elif preload.status == "uploading":
        _discard_preload(db, preload, delete_row=False, unlink_local=False)
    else:
        db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/comment")
async def comment(
    ticket_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    return await reply_to_ticket(
        request=request,
        db=db,
        user=user,
        ticket=ticket,
        message_type=MessageType.INTERNAL,
        send_to_telegram_client=_send_to_telegram_client,
        telegram_message_id_from_response=_telegram_message_id,
        save_upload_attachment=_save_upload_attachment,
        send_attachment_to_telegram_client=lambda db_session, t, a, required, **kwargs: _send_attachment_to_telegram_client(
            db_session, t, a, required=required, **kwargs
        ),
        claim_attachment_preload=lambda db_session, preload_id, expected_ticket_id, expected_user_id: claim_attachment_preload(
            db_session,
            preload_id=preload_id,
            ticket_id=expected_ticket_id,
            user_id=expected_user_id,
        ),
        schedule_attachment_delivery=lambda **kwargs: background_tasks.add_task(deliver_ticket_attachment, **kwargs),
        unfreeze_ticket_on_message=unfreeze_ticket_on_message,
        remove_client_close_button=_remove_client_close_button,
        refresh_ticket_auto_close=lambda db_session, t, restart_from_now: refresh_ticket_auto_close(
            db_session, t, restart_from_now=restart_from_now, refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer
        ),
    )


@router.post("/{ticket_id}/greeting")
def greeting(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    ticket = db.execute(
        select(Ticket)
        .where(Ticket.id == ticket.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) in {TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value}:
        _error(409, "greeting_not_allowed", "Приветствие доступно только для обычной заявки")
    if bool((ticket.quick_action_state_json or {}).get("greeting")):
        _error(409, "greeting_already_sent", "Приветствие уже отправлялось по этой заявке")

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
    contact = _telegram_contact_for_client(db, ticket.client_id)
    text = _format_greeting_text(greet, profile.contact_name, contact.external_username if contact else None)
    quick_state = dict(ticket.quick_action_state_json or {})
    quick_state["greeting"] = True
    ticket.quick_action_state_json = quick_state

    try:
        response = _send_to_telegram_client(db, ticket, text)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error = detail.get("error") if isinstance(detail, dict) else {}
        if error.get("code") == "telegram_send_failed":
            persist_client_delivery_failure(
                db=db,
                ticket_id=ticket.id,
                body="Не удалось отправить клиенту приветствие в Telegram.",
            )
        raise
    _add_message(db, ticket, MessageType.AGENT, text, user.id, telegram_message_id=_telegram_message_id(response))
    was_frozen = bool(ticket.is_frozen)
    unfreeze_ticket_on_message(ticket)
    if was_frozen and not ticket.is_frozen:
        sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
    _remove_client_close_button(db, ticket)
    if ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.PENDING.value
    refresh_ticket_auto_close(
        db,
        ticket,
        restart_from_now=True,
        refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
    )
    db.commit()
    return {"ok": True, "text": text}


@router.post("/{ticket_id}/freeze")
def freeze(ticket_id: str, payload: FreezePayload, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "freeze_closed_ticket", "Нельзя заморозить закрытую заявку")
    if ticket.status == TicketStatus.UNASSIGNED.value or not ticket.assignee_id:
        _error(409, "freeze_unassigned_ticket", "Нельзя заморозить нераспределённую заявку")

    freeze_ticket(
        db,
        ticket,
        freeze_until=payload.freeze_until,
        add_message_fn=_add_message,
    )
    db.commit()
    return {"ok": True}


@router.post("/{ticket_id}/unfreeze")
def unfreeze(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if not ticket.is_frozen:
        _error(409, "unfreeze_not_frozen", "Заявка не заморожена")

    unfreeze_ticket(
        db,
        ticket,
        add_message_fn=_add_message,
        refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
    )
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

    update_ticket_auto_close(
        db,
        ticket,
        auto_close_disabled=bool(payload.auto_close_disabled),
        refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
    )
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
    result = apply_wrong_problem(
        db,
        ticket,
        payload,
        actor_user_id=user.id,
        actor_role=user.role,
        actor_display_name=_actor_display_name(db, user),
        wrong_problem_text_template=WRONG_PROBLEM_TEXT_TEMPLATE,
        get_or_create_client_profile_fn=_get_or_create_client_profile,
        send_to_telegram_client_fn=_send_to_telegram_client,
        telegram_message_id_from_response_fn=_telegram_message_id,
        add_message_fn=_add_message,
        unfreeze_ticket_on_message_fn=unfreeze_ticket_on_message,
        remove_client_close_button_fn=_remove_client_close_button,
    )
    db.commit()
    return result


@router.post("/{ticket_id}/special")
def move_to_special(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    ticket = _get_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытую заявку нельзя перевести в особые")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) not in {TicketWorkflowType.TICKET.value, TicketWorkflowType.TASK.value}:
        _error(409, "special_only_for_ticket", "В особые можно переводить только заявки и задачи")
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
    sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee.id if old_assignee else None, new_assignee_id=admin_user.id, now=ticket.updated_at)
    schedule_employee_assignment_notification(
        db,
        ticket,
        old_assignee=old_assignee,
        new_assignee=admin_user,
        old_is_special=False,
        new_is_special=True,
        actor_user_id=admin_user.id,
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
    ticket = _get_ticket_or_404(db, ticket_id, user, allow_preliminary_close=True)
    if not can_manage_ticket(user):
        _error(403, "readonly_role", "Нет прав на управление заявкой")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_already_closed", "Заявка уже закрыта")
    close_ticket_by_actor(
        db,
        ticket,
        user,
        force_without_agent_reply_for_task=False,
        is_admin_fn=is_admin,
        get_or_create_client_profile_fn=_get_or_create_client_profile,
        last_agent_message_fn=_last_agent_message,
        first_client_message_fn=_first_client_message,
        send_to_telegram_client_fn=_send_to_telegram_client,
        actor_display_name_fn=_actor_display_name,
        add_message_fn=_add_message,
        message_attachments_fn=_message_attachments,
        send_attachment_to_telegram_client_fn=_send_attachment_to_telegram_client,
        onsite_close_reminder_text=ONSITE_CLOSE_REMINDER_TEXT,
        ticket_closed_client_text=_ticket_closed_client_text(db, ticket),
        resolve_group_parent_client_text_fn=_group_parent_client_text_override,
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

    contact = (
        db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
        if ticket.channel != "web" else None
    )
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
        if is_preliminary_telegram_ticket(db, collecting):
            delete_preliminary_telegram_ticket(db, collecting)
            continue
        collecting.intake_state = TicketIntakeState.CANCELLED.value
        collecting_closed_at = datetime.utcnow()
        collecting.status = TicketStatus.CLOSED.value
        collecting.closed_at = collecting_closed_at
        collecting.updated_at = collecting_closed_at
        sync_work_interval_after_close(db, collecting, now=collecting_closed_at)

    if ticket.is_special:
        ticket.status = TicketStatus.NEW.value if ticket.assignee_id else TicketStatus.UNASSIGNED.value
        if ticket.special_assignee_id and not ticket.assignee_id:
            ticket.assignee_id = ticket.special_assignee_id
    else:
        ticket.status = TicketStatus.NEW.value if ticket.assignee_id else TicketStatus.UNASSIGNED.value
    reopened_at = datetime.utcnow()
    ticket.closed_by_user_id = None
    ticket.closed_at = None
    ticket.completed_by_user_id = None
    ticket.completed_counted_at = None
    ticket.onsite_triggered = False
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = reopened_at
    sync_completion_after_reopen(db, ticket)
    sync_work_interval_after_reopen(db, ticket, now=reopened_at)
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
    restore_fallback_text = f"Вашу заявку «{ticket.subject}» от {created_at_text} вернули в работу."
    try:
        restore_client_text = render_automatic_message_text(
            db,
            "telegram.ticket.restore.client",
            restore_fallback_text,
            ticket=ticket,
            contact=contact,
            extra={"ticket_created_at": created_at_text},
        )
    except Exception:
        restore_client_text = restore_fallback_text
    _add_message(db, ticket, MessageType.SYSTEM, f"{restore_client_text} Инициатор: {actor_name}", None, agent_visible=True)
    cleanup_review_flow_for_ticket_reopen(
        db,
        ticket=ticket,
        contact=contact,
        delete_message_fn=delete_message,
    )
    cleanup_feedback_flow_for_ticket_reopen(
        db,
        ticket=ticket,
        contact=contact,
        delete_message_fn=delete_message,
    )
    if contact is not None:
        try:
            response = _send_to_telegram_client(db, ticket, restore_client_text, required=False, remove_keyboard=True)
            if response is None:
                record_client_delivery_failure(
                    db=db,
                    ticket=ticket,
                    body="Заявка восстановлена, но уведомление о восстановлении не удалось отправить клиенту в Telegram.",
                )
        except Exception:
            pass

    refresh_ticket_auto_close(
        db,
        ticket,
        restart_from_now=True,
        refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer,
    )
    db.commit()
    return {"ok": True}
