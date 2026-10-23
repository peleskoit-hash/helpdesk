import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, asc, desc, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.telegram_client_avatar_sync import schedule_avatar_refresh
from ..models import (
    SavedFile,
    Client,
    Company,
    MessageType,
    PendingTaskIntent,
    TelegramContact,
    TelegramUpdate,
    Ticket,
    TicketAttachment,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
    ClientProfile,
    BotButtonSetting,
    BotMenuDestinationBinding,
    BotMenuLevelLayout,
)
from ..config import settings
from ..services.telegram_bot import (
    answer_callback_query,
    client_close_markup,
    delete_message,
    edit_message_reply_markup,
    remove_inline_keyboard,
    send_document,
    send_message,
    send_rich_message,
)
from ..services.button_config import BASIC_MENU_NAME, BUTTON_CONFIG_DIR, DEFAULT_MENU_NAME, ButtonSpec, build_level_layout_key, build_visibility_button_key, available_menu_names, resolve_button_row
from ..services.assignment import append_system_message, notify_client_about_assignee
from ..services.telegram_group_registry import is_group_registration, register_group
from ..services import telegram_group_automation
from ..services import telegram_client_origin, telegram_bitrix_phone
from ..services.assignment import assignee_change_message, assignee_label
from ..services.assignment import resolve_forced_assignee
from ..services.assignment import assign_ticket_to_forced_user
from ..services.assignment import assign_ticket_to_agent
from ..services.assignment import choose_online_agent
from ..services.assignment import schedule_employee_assignment_notification
from ..services.assignment import set_group_parent_assigned_hook
from ..services.employee_telegram_notifications import schedule_new_message_notification
from ..services.statistics_lifecycle import (
    sync_work_interval_after_assignment_change,
    sync_work_interval_after_close,
    sync_work_interval_after_freeze,
    sync_work_interval_after_unfreeze,
)
from ..services.saved_files import SavedFileStorageError, resolve_saved_file_path
from ..services.template_fields import render_template_text
from ..services.telegram_text_format import prepare_telegram_html_message, strip_telegram_html
from ..services.temporary_rop import temporary_rop_destination
from ..services.automatic_messages import render_automatic_message_for_telegram, render_automatic_message_text
from ..services.telegram_client_close import (
    CLIENT_CLOSE_MESSAGE_KEY,
    client_can_close_ticket,
    close_active_ticket_for_contact,
    process_client_close_callback,
    remove_client_close_button,
    send_client_close_notification,
    store_client_close_button,
)
from ..services.auto_close import refresh_ticket_auto_close_timer
from ..services.telegram_attachments import (
    _attachment_extension,
    _extract_attachment,
    _forbidden_attachment_type,
    _safe_filename,
    _store_telegram_attachment,
    start_background_archive_inbound_attachment,
)
from ..services.telegram_menu_flow import (
    _confirm_callback_data,
    _confirmation_markup,
    _parse_confirm_callback_data,
    _remove_menu_keyboard,
    _response_message_id,
    _safe_delete_message,
    _safe_remove_inline_keyboard,
    _store_menu_message_id,
)
from ..services.button_automation_types import (
    AUTOMATION_TYPE_MESSAGE_ONLY,
    AUTOMATION_TYPE_SEND_FILES,
)
from ..services.telegram_automation import (
    _handoff_automation_task_to_agent,
    AUTOMATION_FALLBACK_CLIENT_TEXT,
    process_collecting_ticket_automation,
    resolve_effective_automation_key,
    unsupported_automation_message,
)
from ..services.telegram_attachment_policy import (
    FORBIDDEN_ATTACHMENT_TEXT,
    attachment_is_too_large,
    forbidden_attachment_system_text,
    send_too_large_attachment_client_messages,
    too_large_attachment_system_text,
)
from ..services.telegram_onboarding import (
    get_or_create_contact_from_user,
    process_first_connection_start,
    telegram_contact_by_chat_id,
)
from ..services.telegram_client_access import evaluate_incoming_telegram_access
from ..services.telegram_post_close import (
    cleanup_feedback_flow_for_contact,
    cleanup_review_flow_for_contact,
    is_feedback_callback_data,
    is_review_callback_data,
    process_feedback_callback,
    process_feedback_incoming_message,
    process_review_incoming_message,
    process_review_callback,
)
from ..services.system_tickets import FirstConnectionDefaults, first_connection_values
from ..services.ticket_call_center_snapshot import (
    apply_call_center_employee_snapshot_on_ticket_create,
    start_call_center_employee_refresh_if_needed,
)

router = APIRouter(prefix="/api/v1/messenger", tags=["messenger"])
MAX_LAYOUT_ROW_BUTTONS = 3


FIRST_CONNECTION_SUBJECT = "Первое подключение к боту"
FIRST_CONNECTION_CLIENT_TEXT = (
    "Здравствуйте, Вы подключаетесь к нашему боту тех. поддержки в первый раз.\n"
    "Сейчас Ваша заявка будет распределена на свободного (освободившегося) сотрудника.\n"
    "Будьте готовы сообщить Ваши Фамилию и Имя, а так же отдел к которому Вы относитесь.\n"
    "Если Вы проходите стажировку, сразу сообщите об этом."
)
FIRST_CONNECTION_AGENT_TEXT = (
    "Клиент с данным {username_text} не найден в базе, необходимо спросить у клиента и занести его данные в карточку клиента слева в helpdesk:\n"
    "1. Фамилия и Имя\n"
    "2. Отдел в котором он работает/стажируется\n"
    "3. Офисный или удалённый сотрудник.\n\n"
    "Проверить создана ли его учётная запись, и если да, то данные вн. номера, логин\\пароль Bitrix и id тоже должны быть внесены в соответствующие поля карточки."
)
TICKET_CLOSED_CLIENT_TEXT = (
    "Ваше обращение закрыто. Если Вам снова будет необходима помощь технической поддержки, нажмите /start."
)
TASK_DESCRIPTION_REQUEST_TEXT = (
    "Опишите задачу одним сообщением.\n"
    "Если текст не будет отправлен в течение 5 минут, создание задачи будет отменено."
)
TASK_DESCRIPTION_TIMEOUT_MINUTES = 10
MENU_SELECTION_TIMEOUT_MINUTES = 10
TASK_CREATED_CLIENT_TEXT = "Заявка создана и передана в работу.\n\nДля создания другой нажмите\n /start"
TASK_EXPIRED_TEXT = "Создание заявки было прервано т.к. текст не был написан в течении 10 мин.\n\nДля создания новой нажмите\n/start"
AUTO_REQUEST_TEXT = (
    "Автоматическая заявка принята в обработку.\n"
    "Система обработает её автоматически."
)
AUTO_DONE_TEXT = "Для создания другой нажмите /start"
MATERIAL_UNAVAILABLE_TEXT = "Материал временно недоступен. Обращение передано сотруднику технической поддержки."
PROBLEM_NOT_SELECTED_TEXT = (
    "Сначала выберите конечную проблему в меню. Пока выбор не сделан, сообщение не дойдёт до технической поддержки.\n"
    "Если у Вас не открыто меню нажмите\n"
    "/start"
)
PROBLEM_SUBMENU_TEXT = "Вы остановились на промежуточном пункте меню. Выберите конечную проблему, иначе текст не будет передан в техническую поддержку."
MENU_SELECTION_TIMEOUT_TEXT = "Вы не выбрали нужное меню, создание заявки прервано.\nДля создания новой нажмите\n/start."
NO_ACTIVE_TICKET_TEXT = "Активное обращение не найдено. Для создания новой заявки нажмите /start."
ALLOWED_ATTACHMENT_TYPES = {"document", "photo", "audio", "video", "animation", "sticker"}
IMAGE_PREVIEW_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
SUBMENU_MARKER_PREFIX = "__submenu__:"


def _automatic_text(
    db: Session,
    key: str,
    fallback: str,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    try:
        return render_automatic_message_text(
            db,
            key,
            fallback,
            ticket=ticket,
            contact=contact,
            agent=agent,
            client_message=client_message,
            extra=extra,
        )
    except Exception:
        return fallback


def _first_connection_defaults(db: Session, contact: TelegramContact | None = None) -> FirstConnectionDefaults:
    return FirstConnectionDefaults(
        subject=FIRST_CONNECTION_SUBJECT,
        client_text=_automatic_text(db, "telegram.onboarding.first_connection.client", FIRST_CONNECTION_CLIENT_TEXT, contact=contact),
        system_text=FIRST_CONNECTION_AGENT_TEXT,
    )


def _automatic_telegram_text(
    db: Session,
    key: str,
    fallback: str,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
    extra: dict[str, object] | None = None,
) -> tuple[str, str | None]:
    try:
        return render_automatic_message_for_telegram(
            db,
            key,
            fallback,
            ticket=ticket,
            contact=contact,
            agent=agent,
            client_message=client_message,
            extra=extra,
        )
    except Exception:
        return fallback, None




def _first_company(db: Session) -> Company | None:
    return db.execute(select(Company).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()


def _is_private_telegram_chat(chat: dict | None) -> bool:
    if not isinstance(chat, dict):
        return False
    chat_type = str(chat.get("type") or "").strip().lower()
    return chat_type == "private"


def _now_for(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return datetime.now(value.tzinfo)
    return datetime.utcnow()


def _active_ticket_for_client(db: Session, company_id: str, client_id: str) -> Ticket | None:
    stmt = (
        select(Ticket)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.client_id == client_id,
                Ticket.status != TicketStatus.CLOSED.value,
                or_(
                    Ticket.workflow_type.is_(None),
                    Ticket.workflow_type.notin_([TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value]),
                ),
                or_(Ticket.intake_state.is_(None), Ticket.intake_state == TicketIntakeState.READY.value),
            )
        )
        .order_by(desc(Ticket.updated_at))
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _collecting_ticket_for_client(db: Session, company_id: str, client_id: str) -> Ticket | None:
    return db.execute(
        select(Ticket)
        .where(
            and_(
                Ticket.company_id == company_id,
                Ticket.client_id == client_id,
                Ticket.status == TicketStatus.UNASSIGNED.value,
                Ticket.assignee_id.is_(None),
                Ticket.intake_state == TicketIntakeState.COLLECTING.value,
            )
        )
        .order_by(desc(Ticket.updated_at))
        .limit(1)
    ).scalar_one_or_none()


def _task_for_client_album(db: Session, contact: TelegramContact, attachment_data: dict | None) -> Ticket | None:
    media_group_id = str((attachment_data or {}).get("media_group_id") or "").strip()
    if not media_group_id:
        return None
    tickets = db.execute(
        select(Ticket)
        .where(
            Ticket.company_id == contact.company_id,
            Ticket.client_id == contact.client_id,
            Ticket.workflow_type == TicketWorkflowType.TASK.value,
            select(TicketAttachment.id).where(
                TicketAttachment.ticket_id == Ticket.id,
                TicketAttachment.telegram_media_group_id == media_group_id,
            ).exists(),
        )
        .limit(2)
    ).scalars().all()
    return tickets[0] if len(tickets) == 1 else None


def _create_collecting_ticket(db: Session, company_id: str, client_id: str, subject: str = "Выбор типа проблемы") -> Ticket:
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        assignee_id=None,
        subject=subject,
        workflow_type=TicketWorkflowType.TICKET.value,
        intake_state=TicketIntakeState.COLLECTING.value,
        status=TicketStatus.UNASSIGNED.value,
        menu_message_id=None,
        is_frozen=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(ticket)
    db.flush()
    append_system_message(db, ticket, "Клиент начал создание обращения")
    schedule_avatar_refresh(db, ticket)
    return ticket


def _forced_assignee_label(button: ButtonSpec) -> str:
    if not button.forced_assignee_type or not button.forced_assignee_value:
        return ""
    return f"{button.forced_assignee_type}:{button.forced_assignee_value}"


def _try_forced_assignment(db: Session, ticket: Ticket, button: ButtonSpec | None) -> User | None:
    if button is None or not button.forced_assignee_type or not button.forced_assignee_value:
        return None
    forced_label = _forced_assignee_label(button)
    assignee, error, candidate = assign_ticket_to_forced_user(
        db,
        ticket,
        button.forced_assignee_type,
        button.forced_assignee_value,
    )
    if assignee is not None:
        return assignee
    if error == "not_found":
        append_system_message(db, ticket, f"{forced_label} не найден, заявка в общем распределении", agent_visible=True)
    elif error == "inactive" and candidate is not None:
        append_system_message(
            db,
            ticket,
            f"{assignee_label(candidate)} недоступен для назначения, заявка в общем распределении",
            agent_visible=True,
        )
    elif error == "invalid_role" and candidate is not None:
        append_system_message(
            db,
            ticket,
            f"{assignee_label(candidate)} не может быть ответственным по заявке, заявка в общем распределении",
            agent_visible=True,
        )
    else:
        append_system_message(
            db,
            ticket,
            f"{forced_label} не найден или недоступен, заявка в общем распределении",
            agent_visible=True,
        )
    return None



def _ready_ticket_for_distribution(db: Session, ticket: Ticket, button: ButtonSpec | None = None) -> User | None:
    ticket.intake_state = TicketIntakeState.READY.value
    apply_call_center_employee_snapshot_on_ticket_create(db, ticket)

    fixed_assignee = _apply_fixed_assignee_from_setting(db, ticket)
    if fixed_assignee is not None:
        _apply_button_creation_flags(db, ticket)
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        return fixed_assignee

    forced_assignee = _try_forced_assignment(db, ticket, button)
    if forced_assignee is not None:
        _apply_button_creation_flags(db, ticket)
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        return forced_assignee

    assignee = choose_online_agent(db, ticket.company_id)
    if assignee is None:
        append_system_message(db, ticket, "На распределении")
        _apply_button_creation_flags(db, ticket)
        return None
    if assign_ticket_to_agent(db, ticket, assignee, notify_client=False):
        _apply_button_creation_flags(db, ticket)
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        return assignee
    append_system_message(db, ticket, "На распределении")
    _apply_button_creation_flags(db, ticket)
    return None

def _complete_auto_ticket(db: Session, ticket: Ticket, external_chat_id: str | None, notify_client: bool):
    append_system_message(db, ticket, "Автоматическая заявка обработана системой")
    closed_at = datetime.utcnow()
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = closed_at
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.client_close_message_id = None
    ticket.menu_message_id = None
    ticket.updated_at = closed_at
    sync_work_interval_after_close(db, ticket, now=closed_at)
    if notify_client and external_chat_id:
        send_message(external_chat_id, AUTO_DONE_TEXT)


def _pending_task_intent_for_chat(db: Session, external_chat_id: str) -> PendingTaskIntent | None:
    return db.execute(
        select(PendingTaskIntent).where(PendingTaskIntent.external_chat_id == external_chat_id)
    ).scalar_one_or_none()


def _create_or_replace_pending_task_intent(
    db: Session,
    contact: TelegramContact,
    subject: str,
) -> PendingTaskIntent:
    existing = _pending_task_intent_for_chat(db, contact.external_chat_id)
    if existing is not None:
        db.delete(existing)
        db.flush()

    intent = PendingTaskIntent(
        id=str(uuid.uuid4()),
        company_id=contact.company_id,
        client_id=contact.client_id,
        external_chat_id=contact.external_chat_id,
        subject=subject,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(minutes=TASK_DESCRIPTION_TIMEOUT_MINUTES),
    )
    db.add(intent)
    db.flush()
    return intent


def _client_profile(db: Session, client_id: str) -> ClientProfile | None:
    return db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()


def _menu_name_for_contact(db: Session, contact: TelegramContact) -> str:
    profile = _client_profile(db, contact.client_id)
    destination_value = temporary_rop_destination(profile) if profile is not None else None
    if profile is not None and profile.is_verified and destination_value:
        destination = destination_value.strip()
        if destination:
            binding = db.execute(
                select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
            ).scalar_one_or_none()
            names = available_menu_names(db)
            if binding is not None and binding.menu_type in names:
                return binding.menu_type
            if destination in names:
                return destination
    return BASIC_MENU_NAME


def _menu_name_for_ticket_client(db: Session, ticket: Ticket) -> str:
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is not None:
        return _menu_name_for_contact(db, contact)
    profile = _client_profile(db, ticket.client_id)
    destination_value = temporary_rop_destination(profile) if profile is not None else None
    if profile is not None and profile.is_verified and destination_value:
        destination = destination_value.strip()
        if destination:
            binding = db.execute(
                select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
            ).scalar_one_or_none()
            names = available_menu_names(db)
            if binding is not None and binding.menu_type in names:
                return binding.menu_type
            if destination in names:
                return destination
    return BASIC_MENU_NAME


def _send_problem_menu_for_contact(db: Session, contact: TelegramContact, ticket: Ticket | None = None):
    menu_name = _menu_name_for_contact(db, contact)
    response = _send_problem_menu_with_visibility(db, contact.external_chat_id, menu_name=menu_name)
    if ticket is not None:
        _store_menu_message_id(ticket, response)
    _remember_contact_menu_message(contact, response)
    return response


def _contact_menu_message_ids(contact: TelegramContact) -> list[int]:
    raw = contact.menu_message_ids_json if isinstance(contact.menu_message_ids_json, list) else []
    result: list[int] = []
    seen: set[int] = set()
    for value in raw:
        try:
            msg_id = int(value)
        except Exception:
            continue
        if msg_id <= 0 or msg_id in seen:
            continue
        seen.add(msg_id)
        result.append(msg_id)
    return result


def _set_contact_menu_message_ids(contact: TelegramContact, ids: list[int]) -> None:
    clean: list[int] = []
    seen: set[int] = set()
    for value in ids:
        try:
            msg_id = int(value)
        except Exception:
            continue
        if msg_id <= 0 or msg_id in seen:
            continue
        seen.add(msg_id)
        clean.append(msg_id)
    # keep only recent small tail to avoid unbounded growth
    clean = clean[-20:]
    contact.menu_message_ids_json = clean
    contact.active_menu_message_id = str(clean[-1]) if clean else None


def _remember_contact_menu_message(contact: TelegramContact, response: dict) -> None:
    msg_id = _response_message_id(response)
    if msg_id is None:
        return
    try:
        new_id = int(msg_id)
    except Exception:
        return
    ids = _contact_menu_message_ids(contact)
    ids.append(new_id)
    _set_contact_menu_message_ids(contact, ids)


def _start_menu_cleanup_ids(contact: TelegramContact, *tickets: Ticket | None) -> list[int]:
    ids = _contact_menu_message_ids(contact)
    if not ids:
        for ticket in tickets:
            try:
                if ticket is not None and ticket.menu_message_id:
                    ids.append(int(ticket.menu_message_id))
            except Exception:
                continue
    return ids


def _clear_client_menu_keyboards(
    db: Session,
    contact: TelegramContact,
    old_message_ids: list[int] | None = None,
    *,
    keep_message_id: int | None = None,
) -> None:
    """Remove old tracked menu copies after a replacement /start response was sent."""
    ids = list(old_message_ids) if old_message_ids is not None else _contact_menu_message_ids(contact)
    dedup = []
    seen: set[int] = set()
    for mid in ids:
        if mid in seen or (keep_message_id is not None and mid == keep_message_id):
            continue
        seen.add(mid)
        dedup.append(mid)
    cleared_ids: set[int] = set()
    failed_ids: list[int] = []
    for mid in dedup:
        cleared = _safe_delete_message(contact.external_chat_id, mid)
        if cleared:
            cleared_ids.add(mid)
        else:
            failed_ids.append(mid)
    if cleared_ids:
        cleared_id_strings = {str(mid) for mid in cleared_ids}
        tickets = db.execute(
            select(Ticket).where(
                and_(
                    Ticket.client_id == contact.client_id,
                    Ticket.menu_message_id.in_(cleared_id_strings),
                )
            )
        ).scalars().all()
        for ticket in tickets:
            ticket.menu_message_id = None
    next_ids = failed_ids[:1]
    if keep_message_id is not None:
        next_ids.append(keep_message_id)
    else:
        next_ids.extend(mid for mid in _contact_menu_message_ids(contact) if mid not in cleared_ids and mid not in next_ids)
    _set_contact_menu_message_ids(contact, next_ids[-2:])


def _button_visibility_enabled(row: BotButtonSetting | None) -> bool:
    if row is None:
        return True
    if not bool(row.currently_present) or bool(row.is_archived):
        return False
    if not bool(row.visibility_overridden):
        return True
    return bool(row.is_enabled)


def _button_style_value(row: BotButtonSetting | None) -> str | None:
    value = str(getattr(row, "button_style", "") or "").strip().lower()
    return value if value in {"primary", "success", "danger"} else None


def _button_settings_map(db: Session, menu_name: str) -> dict[str, BotButtonSetting]:
    return {
        row.button_key: row
        for row in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all()
    }


def _normalize_layout_rows(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            cols = int(row.get("columns", 1))
        except Exception:
            cols = 1
        cols = cols if cols in {1, 2, 3} else 1
        keys: list[str] = []
        keys_raw = row.get("button_keys")
        if isinstance(keys_raw, list):
            for item in keys_raw:
                key = str(item or "").strip()
                if key and key not in keys:
                    keys.append(key)
        normalized.append({"columns": cols, "button_keys": keys[:MAX_LAYOUT_ROW_BUTTONS]})
    return normalized


def _apply_layout_to_markup_rows(rows: list[list[dict]], layout_rows: list[dict] | None) -> list[list[dict]]:
    if not layout_rows:
        return rows
    item_by_key: dict[str, dict] = {}
    source_order: list[str] = []
    for row in rows:
        for item in row:
            key = str(item.get("__layout_key") or "").strip()
            if not key or key in item_by_key:
                continue
            item_by_key[key] = item
            source_order.append(key)
    if not item_by_key:
        return rows

    result: list[list[dict]] = []
    used: set[str] = set()
    for layout_row in layout_rows:
        keys = layout_row.get("button_keys") if isinstance(layout_row, dict) else None
        if not isinstance(keys, list):
            continue
        row_items: list[dict] = []
        for raw in keys:
            key = str(raw or "").strip()
            if not key or key in used:
                continue
            item = item_by_key.get(key)
            if item is None:
                continue
            row_items.append(item)
            used.add(key)
            if len(row_items) >= MAX_LAYOUT_ROW_BUTTONS:
                break
        if row_items:
            result.append(row_items)
    for key in source_order:
        if key in used:
            continue
        item = item_by_key.get(key)
        if item is not None:
            result.append([item])
    return result


def _send_problem_menu_with_visibility(db: Session, chat_id: str, *, menu_name: str, menu_key: str | None = None, text: str | None = None) -> dict:
    return _send_problem_menu_db_only(db, chat_id, menu_name=menu_name, menu_key=menu_key, text=text)


def _send_problem_menu_db_only(db: Session, chat_id: str, *, menu_name: str, menu_key: str | None = None, text: str | None = None) -> dict:
    settings_rows = [
        row
        for row in db.execute(select(BotButtonSetting).where(BotButtonSetting.menu_type == menu_name)).scalars().all()
        if bool(row.currently_present) and not bool(row.is_archived)
    ]
    by_parent: dict[str, list[BotButtonSetting]] = {}
    by_key: dict[str, BotButtonSetting] = {}
    for row in settings_rows:
        by_key[row.button_key] = row
        by_parent.setdefault((row.parent_path or "").strip(), []).append(row)
    for bucket in by_parent.values():
        bucket.sort(key=lambda item: ((item.updated_at or item.created_at), item.button_label))

    level_layout_map: dict[str, list[dict]] = {}
    for row in db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == menu_name)).scalars().all():
        level_layout_map[row.level_key] = _normalize_layout_rows(row.layout_json)

    def _effective_label(row: BotButtonSetting) -> str:
        if bool(row.label_overridden):
            override = (row.label_override or "").strip()
            if override:
                return override
        return row.button_label

    def _is_enabled(row: BotButtonSetting) -> bool:
        return True if not bool(row.visibility_overridden) else bool(row.is_enabled)

    def _has_visible_descendants(parent_path: str) -> bool:
        for child in by_parent.get(parent_path, []):
            if not _is_enabled(child):
                continue
            child_kind = (child.button_kind or "terminal").strip().lower()
            if child_kind in {"section", "group_parent"}:
                if _has_visible_descendants(child.button_path):
                    return True
                continue
            return True
        return False

    def build_markup_rows(parent_path: str) -> list[list[dict]]:
        current_level_key = build_level_layout_key(menu_name, parent_path)
        result_rows: list[list[dict]] = []
        for row in by_parent.get(parent_path, []):
            if not _is_enabled(row):
                continue
            kind = (row.button_kind or "terminal").strip().lower()
            workflow = (row.workflow_type or "").strip().lower()
            if workflow == "group_task" and not bool(row.is_db_created):
                # Imported txt group_task parents should run as terminal group_task actions.
                result_rows.append(
                    [
                        {
                            "text": _effective_label(row),
                            "callback_data": f"btnid:{row.id}",
                            "__layout_key": row.button_key,
                            **({"style": _button_style_value(row)} if _button_style_value(row) else {}),
                        }
                    ]
                )
                continue
            if kind in {"section", "group_parent"}:
                if not _has_visible_descendants(row.button_path):
                    continue
                result_rows.append(
                    [
                        {
                            "text": _effective_label(row),
                            "callback_data": f"menuid:{row.id}",
                            "__layout_key": row.button_key,
                            **({"style": _button_style_value(row)} if _button_style_value(row) else {}),
                        }
                    ]
                )
                continue
            result_rows.append(
                [
                    {
                        "text": _effective_label(row),
                        "callback_data": f"btnid:{row.id}",
                        "__layout_key": row.button_key,
                        **({"style": _button_style_value(row)} if _button_style_value(row) else {}),
                    }
                ]
            )
        laid_out = _apply_layout_to_markup_rows(result_rows, level_layout_map.get(current_level_key))
        clean_rows: list[list[dict]] = []
        for row in laid_out:
            clean_row: list[dict] = []
            for item in row:
                clean_item = {"text": item.get("text"), "callback_data": item.get("callback_data")}
                if item.get("style"):
                    clean_item["style"] = item.get("style")
                clean_row.append(clean_item)
            clean_rows.append(clean_row)
        return clean_rows

    parent_path = ""
    if menu_key:
        row = by_key.get(menu_key)
        if row is None:
            return send_message(chat_id, "Эта кнопка больше недоступна. Откройте меню заново.")
        parent_path = row.button_path
    inline_keyboard = build_markup_rows(parent_path)
    back_callback: str | None = None
    if menu_key:
        if parent_path and "/" in parent_path:
            parent_parent_path = parent_path.rsplit("/", 1)[0]
            parent_row = next(
                (
                    x for x in settings_rows
                    if (x.button_path or "").strip() == parent_parent_path
                    and (x.button_kind or "").strip().lower() in {"section", "group_parent"}
                ),
                None,
            )
            back_callback = f"menuid:{parent_row.id}" if parent_row is not None else "menu:__root__"
        elif parent_path:
            back_callback = "menu:__root__"
    if back_callback:
        inline_keyboard.append([{"text": "⬅ Назад", "callback_data": back_callback}])
    reply_markup = {"inline_keyboard": inline_keyboard}
    submenu_text = _resolve_submenu_client_text(db, menu_name, menu_key=menu_key, parent_path=parent_path) if menu_key else None
    message_text = text if text is not None else submenu_text
    return send_message(chat_id, message_text or "Выберите тип проблемы", reply_markup=reply_markup)


def _field_value_for_rule(db: Session, ticket: Ticket, field_name: str) -> str | None:
    profile = _client_profile(db, ticket.client_id)
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if field_name.startswith("custom_fields."):
        if profile is None:
            return None
        custom_key = field_name.split(".", 1)[1].strip()
        value = (profile.custom_fields or {}).get(custom_key)
        return str(value).strip() if value is not None else None
    if field_name == "telegram_username":
        value = contact.external_username if contact is not None else None
        return str(value).strip() if value is not None else None
    if field_name == "telegram_display_name":
        value = contact.external_display_name if contact is not None else None
        return str(value).strip() if value is not None else None
    if field_name == "telegram_user_id":
        value = contact.external_user_id if contact is not None else None
        return str(value).strip() if value is not None else None
    if field_name == "telegram_chat_id":
        value = contact.external_chat_id if contact is not None else None
        return str(value).strip() if value is not None else None
    if profile is None:
        return None
    value = getattr(profile, field_name, None)
    return str(value).strip() if value is not None else None


def _to_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _match_condition(actual: str | None, operator: str, expected: str | None) -> bool:
    actual_text = (actual or "").strip()
    expected_text = (expected or "").strip()
    actual_lower = actual_text.lower()
    expected_lower = expected_text.lower()
    if operator == "empty":
        return actual_text == ""
    if operator == "not_empty":
        return actual_text != ""
    if operator == "eq":
        if expected_lower in {"true", "false"}:
            actual_bool = actual_lower in {"true", "1", "да", "yes"}
            expected_bool = expected_lower == "true"
            return actual_bool == expected_bool
        return actual_lower == expected_lower
    if operator == "ne":
        return actual_lower != expected_lower
    if operator == "contains":
        return expected_lower in actual_lower
    if operator == "not_contains":
        return expected_lower not in actual_lower
    if operator == "is_true":
        return actual_lower in {"true", "1", "да", "yes"}
    if operator == "is_false":
        return actual_lower in {"false", "0", "нет", "no"}
    if operator in {"gt", "lt", "gte", "lte"}:
        actual_num = _to_number(actual_text)
        expected_num = _to_number(expected_text)
        if actual_num is None or expected_num is None:
            return False
        if operator == "gt":
            return actual_num > expected_num
        if operator == "lt":
            return actual_num < expected_num
        if operator == "gte":
            return actual_num >= expected_num
        if operator == "lte":
            return actual_num <= expected_num
    return False


def _resolve_effective_conditional_rules(db: Session, ticket: Ticket, button: ButtonSpec) -> list[dict]:
    row = _resolve_button_setting_row(db, ticket, button)
    if row is None or not isinstance(row.conditional_files_json, list):
        return []
    return [rule for rule in row.conditional_files_json if isinstance(rule, dict)]


def _send_file_items(
    db: Session,
    ticket: Ticket,
    external_chat_id: str,
    file_items: list[str],
    *,
    context_label: str,
) -> None:
    sent: set[str] = set()
    for file_item in file_items:
        if not file_item or file_item in sent:
            continue
        resolved = _resolve_button_file_path(db, file_item)
        if resolved is None:
            append_system_message(db, ticket, f"Некорректный путь файла {context_label}: {file_item}", agent_visible=True)
            continue
        resolved_path, telegram_filename = resolved
        if not resolved_path.exists() or not resolved_path.is_file():
            append_system_message(db, ticket, f"Файл {context_label} не найден: {file_item}", agent_visible=True)
            continue
        try:
            send_document(external_chat_id, resolved_path, filename=telegram_filename)
            sent.add(file_item)
        except Exception as exc:
            append_system_message(db, ticket, f"Не удалось отправить файл {context_label} {file_item}: {type(exc).__name__}", agent_visible=True)


def _send_button_rule_files(db: Session, ticket: Ticket, button: ButtonSpec, external_chat_id: str) -> None:
    rules = _resolve_effective_conditional_rules(db, ticket, button)
    if not rules:
        return
    files_to_send: list[str] = []
    for rule in rules:
        raw_conditions = rule.get("conditions") or []
        raw_files = rule.get("files") or []
        if not isinstance(raw_conditions, list) or not isinstance(raw_files, list):
            continue
        rule_ok = True
        for condition in raw_conditions:
            if not isinstance(condition, dict):
                rule_ok = False
                break
            field_name = str(condition.get("field") or "").strip()
            operator = str(condition.get("operator") or "").strip()
            value = condition.get("value")
            if not field_name or not operator:
                rule_ok = False
                break
            actual = _field_value_for_rule(db, ticket, field_name)
            if not _match_condition(actual, operator, None if value is None else str(value)):
                rule_ok = False
                break
        if not rule_ok:
            continue
        for raw_file in raw_files:
            item = str(raw_file or "").strip()
            if item:
                files_to_send.append(item)
    _send_file_items(db, ticket, external_chat_id, files_to_send, context_label="по правилу")


def _resolve_button_file_path(db: Session, file_name: str) -> tuple[Path, str] | None:
    name = (file_name or "").strip()
    if not name:
        return None
    if name.startswith("saved:"):
        saved_file_id = name.removeprefix("saved:").strip()
        try:
            saved_file_id = str(uuid.UUID(saved_file_id))
        except ValueError:
            return None
        saved_file = db.get(SavedFile, saved_file_id)
        if saved_file is None:
            return None
        try:
            return resolve_saved_file_path(saved_file), saved_file.display_filename
        except SavedFileStorageError:
            return None
    candidate = Path(name)
    if candidate.is_absolute():
        return None
    files_dir = (BUTTON_CONFIG_DIR / "files").resolve()
    full_path = (files_dir / candidate).resolve()
    try:
        full_path.relative_to(files_dir)
    except ValueError:
        return None
    return full_path, full_path.name


def _send_button_direct_file(db: Session, ticket: Ticket, button: ButtonSpec, external_chat_id: str) -> tuple[bool, str | None]:
    files_to_send = _resolve_effective_direct_files(db, ticket, button)
    if not files_to_send:
        return True, None
    for file_item in files_to_send:
        resolved = _resolve_button_file_path(db, file_item)
        if resolved is None:
            reason = f"Некорректный путь файла в кнопке: {file_item}"
            append_system_message(db, ticket, reason, agent_visible=True)
            return False, reason
        resolved_path, telegram_filename = resolved
        if not resolved_path.exists() or not resolved_path.is_file():
            reason = f"Файл кнопки не найден: {file_item}"
            append_system_message(db, ticket, reason, agent_visible=True)
            return False, reason
        try:
            send_document(external_chat_id, resolved_path, filename=telegram_filename)
        except Exception as exc:
            reason = f"Не удалось отправить файл кнопки {file_item}: {type(exc).__name__}"
            append_system_message(db, ticket, reason, agent_visible=True)
            return False, reason
    return True, None


def _fallback_auto_to_support(
    db: Session,
    ticket: Ticket,
    reason: str,
    external_chat_id: str,
    notify_client: bool,
    *,
    client_message_key: str = "telegram.automation.material_unavailable.client",
    client_message_fallback: str = MATERIAL_UNAVAILABLE_TEXT,
    client_message_extra: dict | None = None,
) -> User | None:
    append_system_message(db, ticket, f"Автоматическая отправка материала не выполнена: {reason}", agent_visible=True)
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.workflow_type = TicketWorkflowType.TICKET.value
    ticket.task_kind = TicketTaskKind.NORMAL.value
    ticket.agent_visible = True
    ticket.load_counted = True
    assigned_agent = _ready_ticket_for_distribution(db, ticket, None)
    if notify_client:
        material_text, material_parse_mode = _automatic_telegram_text(
            db,
            client_message_key,
            client_message_fallback,
            ticket=ticket,
            extra={"reason": reason, **(client_message_extra or {})},
        )
        send_message(external_chat_id, material_text, parse_mode=material_parse_mode)
        if assigned_agent is not None:
            notify_client_about_assignee(db, ticket, assigned_agent)
    return assigned_agent


def _dispatch_builtin_button_automation(
    db: Session,
    ticket: Ticket,
    button: ButtonSpec | None,
    external_chat_id: str,
    notify_client: bool,
    *,
    default_client_text: str | None = None,
) -> dict | None:
    effective_automation_key = resolve_effective_automation_key(
        db,
        ticket,
        button,
        button_setting_for_ticket_fn=_button_setting_for_ticket,
    )
    if not effective_automation_key:
        return None

    workflow_type = (ticket.workflow_type or TicketWorkflowType.TICKET.value).strip().lower()

    if effective_automation_key == "telegram_group":
        if workflow_type == TicketWorkflowType.AUTO.value:
            _fallback_auto_to_support(
                db, ticket, "Не удалось начать ожидание сообщения для Telegram-группы", external_chat_id, notify_client,
                client_message_key="telegram.automation.fallback_created",
                client_message_fallback="Заявка создана и передана в работу.",
            )
        else:
            append_system_message(db, ticket, "Не удалось начать ожидание сообщения для Telegram-группы", agent_visible=True)
        db.commit()
        return {"ok": True, "ticket_id": ticket.id, "automation_pending": True, "status": ticket.status}

    if effective_automation_key == AUTOMATION_TYPE_SEND_FILES:
        if workflow_type == TicketWorkflowType.AUTO.value:
            if notify_client:
                try:
                    auto_client_text, auto_client_parse_mode = _render_client_text_for_telegram(
                        db,
                        ticket,
                        button,
                        default_client_text or AUTO_DONE_TEXT,
                    )
                    send_message(external_chat_id, auto_client_text, parse_mode=auto_client_parse_mode)
                except Exception as exc:
                    _fallback_auto_to_support(
                        db,
                        ticket,
                        f"\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0438\u0442\u043e\u0433\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435: {type(exc).__name__}",
                        external_chat_id,
                        notify_client,
                        client_message_key="telegram.automation.fallback_created",
                        client_message_fallback=AUTOMATION_FALLBACK_CLIENT_TEXT,
                    )
                    db.commit()
                    return {
                        "ok": True,
                        "ticket_id": ticket.id,
                        "workflow_type": TicketWorkflowType.TICKET.value,
                        "status": ticket.status,
                        "automation_handoff": True,
                    }
            direct_file_ok, direct_file_error = _send_button_direct_file(db, ticket, button, external_chat_id)
            _send_button_rule_files(db, ticket, button, external_chat_id)
            if not direct_file_ok:
                _fallback_auto_to_support(
                    db,
                    ticket,
                    direct_file_error or "\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b",
                    external_chat_id,
                    notify_client,
                )
                db.commit()
                return {
                    "ok": True,
                    "ticket_id": ticket.id,
                    "workflow_type": TicketWorkflowType.TICKET.value,
                    "status": ticket.status,
                    "auto_file_error": True,
                }
            _complete_auto_ticket(db, ticket, external_chat_id, False)
            db.commit()
            return {
                "ok": True,
                "ticket_id": ticket.id,
                "workflow_type": TicketWorkflowType.AUTO.value,
                "builtin_automation": AUTOMATION_TYPE_SEND_FILES,
            }

        _send_button_direct_file(db, ticket, button, external_chat_id)
        _send_button_rule_files(db, ticket, button, external_chat_id)
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "workflow_type": workflow_type,
            "builtin_automation": AUTOMATION_TYPE_SEND_FILES,
        }

    if effective_automation_key == AUTOMATION_TYPE_MESSAGE_ONLY:
        if workflow_type != TicketWorkflowType.AUTO.value:
            append_system_message(
                db,
                ticket,
                unsupported_automation_message(effective_automation_key),
                agent_visible=True,
            )
            return {
                "ok": True,
                "ticket_id": ticket.id,
                "workflow_type": workflow_type,
                "unsupported_automation": True,
            }
        if notify_client:
            try:
                auto_client_text, auto_client_parse_mode = _render_client_text_for_telegram(
                    db,
                    ticket,
                    button,
                    default_client_text or AUTO_DONE_TEXT,
                )
                send_message(external_chat_id, auto_client_text, parse_mode=auto_client_parse_mode)
            except Exception as exc:
                _fallback_auto_to_support(
                    db,
                    ticket,
                    f"\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0438\u0442\u043e\u0433\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435: {type(exc).__name__}",
                    external_chat_id,
                    notify_client,
                    client_message_key="telegram.automation.fallback_created",
                    client_message_fallback=AUTOMATION_FALLBACK_CLIENT_TEXT,
                )
                db.commit()
                return {
                    "ok": True,
                    "ticket_id": ticket.id,
                    "workflow_type": TicketWorkflowType.TICKET.value,
                    "status": ticket.status,
                    "automation_handoff": True,
                }
        _complete_auto_ticket(db, ticket, external_chat_id, False)
        db.commit()
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "workflow_type": TicketWorkflowType.AUTO.value,
            "builtin_automation": AUTOMATION_TYPE_MESSAGE_ONLY,
        }

    if workflow_type == TicketWorkflowType.AUTO.value:
        _fallback_auto_to_support(
            db,
            ticket,
            unsupported_automation_message(effective_automation_key),
            external_chat_id,
            notify_client,
            client_message_key="telegram.automation.fallback_created",
            client_message_fallback=AUTOMATION_FALLBACK_CLIENT_TEXT,
        )
        db.commit()
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "workflow_type": TicketWorkflowType.TICKET.value,
            "status": ticket.status,
            "automation_handoff": True,
        }

    if workflow_type == TicketWorkflowType.TICKET.value:
        append_system_message(
            db,
            ticket,
            unsupported_automation_message(effective_automation_key),
            agent_visible=True,
        )
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "workflow_type": workflow_type,
            "unsupported_automation": True,
        }

    return None

def _ensure_ticket_for_incoming(db: Session, company_id: str, client_id: str, message_text: str) -> Ticket:
    ticket = _active_ticket_for_client(db, company_id, client_id)
    if ticket is not None:
        return ticket

    subject = (message_text or "Новое обращение").strip()
    if not subject:
        subject = "Новое обращение"
    subject = subject[:120]

    assignee = choose_online_agent(db, company_id)
    assignee_id = assignee.id if assignee is not None else None
    new_ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client_id,
        assignee_id=assignee_id,
        subject=subject,
        workflow_type=TicketWorkflowType.TICKET.value,
        intake_state=TicketIntakeState.READY.value,
        status=TicketStatus.NEW.value if assignee_id else TicketStatus.UNASSIGNED.value,
        is_frozen=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(new_ticket)
    db.flush()
    apply_call_center_employee_snapshot_on_ticket_create(db, new_ticket)
    if assignee_id:
        sync_work_interval_after_assignment_change(db, new_ticket, old_assignee_id=None, new_assignee_id=assignee_id, now=new_ticket.created_at)

    append_system_message(db, new_ticket, f"Заявка создана: {subject}")
    schedule_avatar_refresh(db, new_ticket)
    if assignee_id:
        assignee.last_assigned_at = datetime.utcnow()
        append_system_message(db, new_ticket, assignee_change_message(None, assignee))
        schedule_employee_assignment_notification(db, new_ticket, old_assignee=None, new_assignee=assignee)
    else:
        append_system_message(db, new_ticket, "На распределении")

    return new_ticket


def _create_task_from_intent(db: Session, intent: PendingTaskIntent, body: str) -> Ticket:
    assignee = choose_online_agent(db, intent.company_id)
    assignee_id = assignee.id if assignee is not None else None
    task = Ticket(
        id=str(uuid.uuid4()),
        company_id=intent.company_id,
        client_id=intent.client_id,
        assignee_id=assignee_id,
        subject=intent.subject,
        workflow_type=TicketWorkflowType.TASK.value,
        intake_state=TicketIntakeState.READY.value,
        status=TicketStatus.NEW.value if assignee_id else TicketStatus.UNASSIGNED.value,
        is_frozen=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.flush()
    if assignee_id:
        sync_work_interval_after_assignment_change(db, task, old_assignee_id=None, new_assignee_id=assignee_id, now=task.created_at)
    append_system_message(db, task, f"Задача создана: {intent.subject}")
    if assignee_id:
        assignee.last_assigned_at = datetime.utcnow()
        append_system_message(db, task, assignee_change_message(None, assignee))
        schedule_employee_assignment_notification(db, task, old_assignee=None, new_assignee=assignee)
    else:
        append_system_message(db, task, "На распределении")
    _append_client_message(db, task, body or "(пустое сообщение)")
    db.delete(intent)
    return task


def _client_full_name_for_note(db: Session, client_id: str) -> str:
    profile = _client_profile(db, client_id)
    full_name = " ".join(
        part for part in [
            getattr(profile, "last_name", None),
            getattr(profile, "contact_name", None),
        ]
        if part
    ).strip()
    if full_name:
        return full_name
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == client_id)).scalar_one_or_none()
    if contact is not None:
        return contact.external_display_name or contact.external_username or "Клиент"
    return "Клиент"


def _append_client_message(
    db: Session,
    ticket: Ticket,
    body: str,
    telegram_message_id: str | None = None,
    *,
    replaced_message_id: str | None = None,
    schedule_notification: bool = True,
):
    has_messages = db.execute(select(TicketMessage.id).where(TicketMessage.ticket_id == ticket.id).limit(1)).first()
    if has_messages is None and ticket.subject == "Новое обращение" and body.strip():
        ticket.subject = (body or "Новое обращение").strip()[:120] or "Новое обращение"

    msg = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body=body,
        telegram_message_id=telegram_message_id,
        replaced_message_id=replaced_message_id,
        created_at=datetime.utcnow(),
    )
    db.add(msg)
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value and ticket.is_frozen and ticket.status != TicketStatus.CLOSED.value:
        unfrozen_at = datetime.utcnow()
        ticket.is_frozen = False
        ticket.freeze_until = None
        sync_work_interval_after_unfreeze(db, ticket, now=unfrozen_at)
    ticket.updated_at = datetime.utcnow()
    db.flush()
    if schedule_notification:
        schedule_new_message_notification(db, ticket=ticket, message=msg)
    return msg


def _store_telegram_attachment_and_schedule_employee_notification(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    attachment_data: dict,
    external_chat_id: str,
) -> TicketAttachment | None:
    attachment = _store_telegram_attachment(db, ticket, message, attachment_data, external_chat_id)
    if attachment is not None:
        schedule_new_message_notification(db, ticket=ticket, message=message, attachment=attachment)
    return attachment


def _handle_edited_client_message(
    db: Session,
    contact: TelegramContact,
    body: str,
    telegram_message_id: str | None,
) -> Ticket | None:
    if not telegram_message_id:
        return None
    old_message = db.execute(
        select(TicketMessage)
        .join(Ticket, Ticket.id == TicketMessage.ticket_id)
        .where(
            and_(
                Ticket.client_id == contact.client_id,
                TicketMessage.telegram_message_id == telegram_message_id,
                TicketMessage.message_type == MessageType.CLIENT.value,
                TicketMessage.message_status != "edited",
            )
        )
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()
    if old_message is None:
        return None
    ticket = db.get(Ticket, old_message.ticket_id)
    if ticket is None:
        return None
    client_name = _client_full_name_for_note(db, contact.client_id)
    old_message.message_status = "edited"
    old_message.status_note = f"{client_name} исправил сообщение"
    _append_client_message(
        db,
        ticket,
        body or "(пустое сообщение)",
        telegram_message_id=telegram_message_id,
        replaced_message_id=old_message.id,
    )
    refresh_ticket_auto_close_timer(db, ticket, restart_from_now=False)
    return ticket

def _button_from_db_row(db: Session, row: BotButtonSetting, menu_name: str | None = None) -> ButtonSpec:
    row_kind = (row.button_kind or "").strip().lower()
    workflow = (row.workflow_type or "ticket").strip().lower()
    if row_kind == "group_parent" or workflow == "group_task":
        kind = "group_task"
    elif row_kind == "section":
        kind = "submenu"
    elif workflow == "group_task_child":
        kind = "task"
    else:
        kind = workflow or "ticket"
    label = (row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label
    final_label = label or row.button_label
    forced_type = "user_id" if bool(row.fixed_assignee_enabled) and (row.fixed_assignee_user_id or "").strip() else None
    forced_value = (row.fixed_assignee_user_id or "").strip() if forced_type else None
    file_path = None
    if isinstance(row.files_json, list) and row.files_json:
        file_path = str(row.files_json[0] or "").strip() or None
    file_rules = tuple(rule for rule in (row.conditional_files_json or []) if isinstance(rule, dict)) if isinstance(row.conditional_files_json, list) else ()

    button = ButtonSpec(
        key=row.button_key,
        label=final_label or row.button_label,
        kind=kind,
        confirmation_text=row.confirmation_text if bool(row.confirmation_enabled) else None,
        system_text=row.system_text,
        instruction_text=row.instruction_text,
        automation_key=row.automation_key,
        forced_assignee_type=forced_type,
        forced_assignee_value=forced_value,
        suppress_client_answer=bool(row.suppress_client_answer),
        file_path=file_path,
        file_rules=file_rules,
    )
    if button.kind == "group_task":
        return _with_db_group_task_children(db, button, row.menu_type, (row.button_path or "").strip())
    return button


def _button_from_callback(db: Session, data: str, menu_name: str | None = None) -> ButtonSpec | None:
    if data.startswith("btnid:"):
        row = db.get(BotButtonSetting, data.removeprefix("btnid:").strip())
        if row is None or not row.currently_present or row.is_archived:
            return None
        if menu_name and row.menu_type != menu_name:
            return None
    elif data.startswith("btn:") and menu_name:
        row = resolve_button_row(db, data.removeprefix("btn:"), menu_name)
    else:
        return None
    return _button_from_db_row(db, row, menu_name) if row is not None else None


def _is_callback_button_enabled(db: Session, *, menu_name: str, button: ButtonSpec) -> bool:
    row = db.execute(
        select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu_name,
            BotButtonSetting.button_key == button.key,
        )
    ).scalar_one_or_none()
    if row is None or not bool(row.currently_present) or bool(row.is_archived):
        return False
    if row.visibility_overridden and not bool(row.is_enabled):
        return False
    parent_path = (row.parent_path or "").strip()
    while parent_path:
        parent = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_path == parent_path,
                BotButtonSetting.button_kind.in_(["section", "group_parent"]),
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
        if parent is None:
            break
        if parent.visibility_overridden and not bool(parent.is_enabled):
            return False
        parent_path = (parent.parent_path or "").strip()
    return True


def _menu_key_from_callback(db: Session, data: str, menu_name: str | None = None) -> str | None:
    id_prefix = "menuid:"
    if data.startswith(id_prefix):
        id_raw = data.removeprefix(id_prefix).strip()
        if not id_raw:
            return None
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.id == id_raw)).scalar_one_or_none()
        if row is None or not bool(row.currently_present) or bool(row.is_archived):
            return None
        if menu_name and row.menu_type != menu_name:
            return None
        return row.button_key

    prefix = "menu:"
    if not data.startswith(prefix):
        return None
    menu_key = data.removeprefix(prefix).strip()
    if menu_key == "__root__":
        return menu_key
    row = resolve_button_row(db, menu_key, menu_name, submenu=True) if menu_key else None
    return row.button_key if row is not None else None








def _with_db_group_task_children(db: Session, button: ButtonSpec, menu_name: str | None, parent_path: str | None) -> ButtonSpec:
    menu = (menu_name or "").strip()
    parent = (parent_path or "").strip()
    if button.kind != "group_task" or not menu or not parent:
        return button
    rows = db.execute(
        select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu,
            BotButtonSetting.parent_path == parent,
            BotButtonSetting.workflow_type == "group_task_child",
            BotButtonSetting.button_kind == "terminal",
            BotButtonSetting.currently_present.is_(True),
            BotButtonSetting.is_archived.is_(False),
        )
    ).scalars().all()
    if not rows:
        return button
    layout = db.execute(select(BotMenuLevelLayout).where(
        BotMenuLevelLayout.menu_type == menu,
        BotMenuLevelLayout.level_key == build_level_layout_key(menu, parent),
    )).scalar_one_or_none()
    ordered_keys = [key for item in _normalize_layout_rows(layout.layout_json if layout else None)
                    for key in item["button_keys"]]
    rank = {key: index for index, key in enumerate(ordered_keys)}
    db_children: list[ButtonSpec] = []
    for row in sorted(rows, key=lambda item: (rank.get(item.button_key, len(rank)), (item.updated_at or item.created_at or datetime.utcnow()), item.button_label)):
        if not _button_visibility_enabled(row):
            continue
        child = _button_from_db_row(db, row, menu)
        if not (child.label or "").strip():
            continue
        db_children.append(child)
    return ButtonSpec(
        key=button.key,
        label=button.label,
        kind=button.kind,
        text=button.text,
        confirmation_text=button.confirmation_text,
        system_text=button.system_text,
        instruction_text=button.instruction_text,
        automation_key=button.automation_key,
        forced_assignee_type=button.forced_assignee_type,
        forced_assignee_value=button.forced_assignee_value,
        suppress_client_answer=button.suppress_client_answer,
        file_path=button.file_path,
        file_rules=button.file_rules,
        submenu_key=button.submenu_key,
        subtasks=tuple(db_children),
    )




def _submenu_path_by_key_or_db(db: Session, menu_name: str, submenu_key: str) -> str | None:
    row = resolve_button_row(db, submenu_key, menu_name, submenu=True)
    if row is not None and row.button_kind in {"section", "group_parent"}:
        return row.button_path
    return None


def _resolve_submenu_client_text(
    db: Session,
    menu_name: str,
    *,
    menu_key: str | None,
    parent_path: str | None = None,
) -> str | None:
    if not menu_key:
        return None

    row: BotButtonSetting | None = None
    normalized_path = (parent_path or "").strip()
    if normalized_path:
        stable_key = build_visibility_button_key(menu_name, "submenu", normalized_path)
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_key == stable_key,
                BotButtonSetting.button_kind == "section",
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
    if row is None:
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_key == menu_key,
                BotButtonSetting.button_kind == "section",
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    raw = (row.client_text or "").replace("\r\n", "\n").replace("\r", "\n")
    return raw if raw.strip() else None


def _resolve_instruction_button_key(db: Session, menu_name: str, button_key: str) -> str | None:
    row = resolve_button_row(db, button_key, menu_name)
    if row is not None and row.button_kind not in {"section", "group_parent"}:
        return row.button_key
    return None


def _button_setting_for_ticket(db: Session, ticket: Ticket) -> BotButtonSetting | None:
    key = (ticket.instruction_button_key or "").strip()
    if not key:
        return None
    return resolve_button_row(db, key, include_inactive=True)


def _resolve_button_setting_row(db: Session, ticket: Ticket, button: ButtonSpec | None = None) -> BotButtonSetting | None:
    keys: list[str] = []
    for candidate in (
        (ticket.instruction_button_key or "").strip(),
        (ticket.button_config_key or "").strip(),
        ((button.key or "").strip() if button is not None else ""),
    ):
        if candidate and candidate not in keys:
            keys.append(candidate)
    for key in keys:
        row = resolve_button_row(db, key, include_inactive=True)
        if row is not None:
            return row
    return None


def _apply_button_creation_flags(db: Session, ticket: Ticket):
    setting = _button_setting_for_ticket(db, ticket)
    if setting is None:
        return
    if bool(setting.freeze_on_create):
        was_frozen = bool(ticket.is_frozen)
        ticket.is_frozen = True
        if not was_frozen:
            sync_work_interval_after_freeze(db, ticket, now=datetime.utcnow())


def _apply_fixed_assignee_from_setting(db: Session, ticket: Ticket) -> User | None:
    setting = _button_setting_for_ticket(db, ticket)
    if setting is None or not bool(setting.fixed_assignee_enabled):
        return None
    user_id = (setting.fixed_assignee_user_id or "").strip()
    if not user_id:
        append_system_message(db, ticket, "Фиксированный ответственный не задан, заявка в общем распределении", agent_visible=True)
        return None
    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if target is None or not target.is_active:
        append_system_message(db, ticket, "Фиксированный ответственный недоступен, заявка в общем распределении", agent_visible=True)
        return None
    if target.role == "chiki_piki":
        append_system_message(db, ticket, "Фиксированный ответственный не может быть chiki_piki, заявка в общем распределении", agent_visible=True)
        return None

    old_assignee_id = ticket.assignee_id
    assigned_at = datetime.utcnow()
    ticket.assignee_id = target.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status != TicketStatus.CLOSED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = assigned_at
    sync_work_interval_after_assignment_change(db, ticket, old_assignee_id=old_assignee_id, new_assignee_id=target.id, now=assigned_at)

    if target.role == "admin":
        ticket.is_special = True
        ticket.special_assignee_id = target.id
        ticket.special_at = ticket.special_at or datetime.utcnow()
        append_system_message(db, ticket, f"Заявка назначена на администратора из настройки кнопки: {assignee_label(target)}", agent_visible=True)
        return target

    if target.role in {"agent", "trainee"}:
        ticket.is_special = False
        ticket.special_assignee_id = None
        target.last_assigned_at = datetime.utcnow()
        append_system_message(db, ticket, f"Заявка назначена на сотрудника из настройки кнопки: {assignee_label(target)}", agent_visible=True)
        return target

    append_system_message(db, ticket, "Фиксированный ответственный имеет неподдерживаемую роль, заявка в общем распределении", agent_visible=True)
    return None


def _resolve_effective_system_text(db: Session, ticket: Ticket, button: ButtonSpec) -> str | None:
    row = _resolve_button_setting_row(db, ticket, button)
    if row is None:
        return None
    return (row.system_text or "").strip() or None


def _resolve_effective_direct_files(db: Session, ticket: Ticket, button: ButtonSpec) -> list[str]:
    row = _resolve_button_setting_row(db, ticket, button)
    if row is None or not isinstance(row.files_json, list):
        return []
    cleaned: list[str] = []
    for value in row.files_json:
        item = str(value or "").strip().replace("\\", "/")
        if not item:
            continue
        cleaned.append(item)
    return cleaned


def _resolve_effective_confirmation(ticket: Ticket, button: ButtonSpec, db: Session) -> tuple[bool, str | None]:
    row = _resolve_button_setting_row(db, ticket, button)
    if row is None:
        return False, None
    enabled = bool(row.confirmation_enabled)
    text_value = (row.confirmation_text or "").strip() or None
    return enabled, text_value

def _resolve_effective_client_text(db: Session, ticket: Ticket, button: ButtonSpec) -> str | None:
    row = _resolve_button_setting_row(db, ticket, button)
    if row is None:
        return None
    raw = (row.client_text or "").replace("\r\n", "\n").replace("\r", "\n")
    return raw if raw.strip() else None


def _render_client_text(db: Session, ticket: Ticket, button: ButtonSpec, fallback: str) -> str:
    effective = _resolve_effective_client_text(db, ticket, button)
    if effective:
        return render_template_text(effective, db, ticket=ticket) or effective
    return fallback


def _render_client_text_for_telegram(
    db: Session,
    ticket: Ticket,
    button: ButtonSpec,
    fallback: str,
) -> tuple[str, str | None]:
    rendered = _render_client_text(db, ticket, button, fallback)
    try:
        return prepare_telegram_html_message(rendered)
    except Exception:
        return rendered, None


def _render_confirmation_text_for_telegram(
    db: Session,
    ticket: Ticket,
    confirmation_text: str | None,
    default_confirmation_text: str,
) -> tuple[str, str | None]:
    rendered = render_template_text(confirmation_text, db, ticket=ticket) if confirmation_text else None
    candidate = rendered or confirmation_text or default_confirmation_text
    try:
        return prepare_telegram_html_message(candidate)
    except Exception:
        return candidate, None


def _store_button_system_text(db: Session, ticket: Ticket, button: ButtonSpec, client_message: str | None = None):
    system_text = _resolve_effective_system_text(db, ticket, button)
    if not system_text:
        return
    rendered = render_template_text(system_text, db, ticket=ticket, client_message=client_message) or system_text
    append_system_message(db, ticket, rendered)


def _reset_collecting_ticket(db: Session, ticket: Ticket):
    old_assignee_id = ticket.assignee_id
    reset_at = datetime.utcnow()
    ticket.subject = "Выбор типа проблемы"
    ticket.button_config_key = None
    ticket.instruction_button_key = None
    ticket.instruction_title = None
    ticket.instruction_text = None
    ticket.workflow_type = TicketWorkflowType.TICKET.value
    ticket.task_kind = TicketTaskKind.NORMAL.value
    ticket.intake_state = TicketIntakeState.COLLECTING.value
    ticket.status = TicketStatus.UNASSIGNED.value
    ticket.assignee_id = None
    ticket.client_close_message_id = None
    ticket.agent_visible = True
    ticket.load_counted = True
    ticket.updated_at = reset_at
    sync_work_interval_after_assignment_change(
        db,
        ticket,
        old_assignee_id=old_assignee_id,
        new_assignee_id=None,
        now=reset_at,
    )


def _prepare_collecting_ticket_for_button(db: Session, ticket: Ticket, button: ButtonSpec, menu_name: str):
    ticket.subject = button.label
    ticket.button_config_key = button.key
    ticket.instruction_button_key = _resolve_instruction_button_key(db, menu_name, button.key)
    ticket.instruction_title = None
    ticket.instruction_text = None
    ticket.updated_at = datetime.utcnow()
    if button.kind == "task":
        ticket.workflow_type = TicketWorkflowType.TASK.value
        ticket.task_kind = TicketTaskKind.NORMAL.value
        ticket.agent_visible = True
        ticket.load_counted = True
    elif button.kind == "group_task":
        ticket.workflow_type = TicketWorkflowType.TASK.value
        ticket.task_kind = TicketTaskKind.GROUP_PARENT.value
        ticket.agent_visible = False
        ticket.load_counted = False
    elif button.kind == "auto":
        ticket.workflow_type = TicketWorkflowType.AUTO.value
        ticket.task_kind = TicketTaskKind.NORMAL.value
        ticket.agent_visible = True
        ticket.load_counted = False
    else:
        ticket.workflow_type = TicketWorkflowType.TICKET.value
        ticket.task_kind = TicketTaskKind.NORMAL.value
        ticket.agent_visible = True
        ticket.load_counted = True
    append_system_message(db, ticket, f"\u041a\u043b\u0438\u0435\u043d\u0442 \u0432\u044b\u0431\u0440\u0430\u043b \u043a\u043d\u043e\u043f\u043a\u0443: {button.label}")
    _store_button_system_text(db, ticket, button)
    if (menu_name or "").strip().casefold() == "колл центр":
        start_call_center_employee_refresh_if_needed(db, ticket.client_id)


def _create_group_children_from_collecting(
    db: Session,
    parent: Ticket,
    button: ButtonSpec,
    client_body: str,
    assignee: User | None,
) -> None:
    existing_child_id = db.execute(
        select(Ticket.id)
        .where(
            and_(
                Ticket.parent_ticket_id == parent.id,
                Ticket.task_kind == TicketTaskKind.GROUP_CHILD.value,
            )
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing_child_id is not None:
        return

    append_system_message(db, parent, "Созданы подзадачи групповой задачи")
    menu_name = _menu_name_for_ticket_client(db, parent)

    for child in button.subtasks:
        instruction_button_key = _resolve_instruction_button_key(db, menu_name, child.key)
        child_setting = db.execute(
            select(BotButtonSetting).where(BotButtonSetting.button_key == instruction_button_key)
        ).scalar_one_or_none() if instruction_button_key else None
        suppress_client_answer = (
            bool(child_setting.suppress_client_answer)
            if child_setting is not None
            else bool(child.suppress_client_answer)
        )
        child_forced_assignee: User | None = None
        child_forced_error: str | None = None
        child_forced_candidate: User | None = None
        if child.forced_assignee_type and child.forced_assignee_value:
            child_forced_assignee, child_forced_error, child_forced_candidate = resolve_forced_assignee(
                db,
                child.forced_assignee_type,
                child.forced_assignee_value,
            )

        child_ticket = Ticket(
            id=str(uuid.uuid4()),
            company_id=parent.company_id,
            client_id=parent.client_id,
            assignee_id=child_forced_assignee.id if child_forced_assignee is not None else (assignee.id if assignee is not None else None),
            subject=child.label,
            workflow_type=TicketWorkflowType.TASK.value,
            intake_state=TicketIntakeState.READY.value,
            task_kind=TicketTaskKind.GROUP_CHILD.value,
            parent_ticket_id=parent.id,
            agent_visible=True,
            load_counted=True,
            button_config_key=child.key,
            instruction_button_key=instruction_button_key,
            suppress_client_answer=suppress_client_answer,
            status=TicketStatus.NEW.value if (child_forced_assignee is not None or assignee is not None) else TicketStatus.UNASSIGNED.value,
            is_frozen=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(child_ticket)
        db.flush()
        if child_ticket.assignee_id:
            sync_work_interval_after_assignment_change(db, child_ticket, old_assignee_id=None, new_assignee_id=child_ticket.assignee_id, now=child_ticket.created_at)
        _append_client_message(db, child_ticket, client_body or "(пустое сообщение)")
        _store_button_system_text(db, child_ticket, child, client_message=client_body)

        if child_forced_assignee is not None:
            append_system_message(
                db,
                child_ticket,
                f"Заявка назначена на сотрудника из настройки кнопки: {child_forced_assignee.full_name}",
                agent_visible=True,
            )
        elif child.forced_assignee_type and child.forced_assignee_value:
            forced_label = f"{child.forced_assignee_type}:{child.forced_assignee_value}"
            if child_forced_error == "not_found":
                append_system_message(db, child_ticket, f"{forced_label} не найден, заявка в общем распределении", agent_visible=True)
            elif child_forced_error == "inactive" and child_forced_candidate is not None:
                append_system_message(
                    db,
                    child_ticket,
                    f"{assignee_label(child_forced_candidate)} недоступен для назначения, заявка в общем распределении",
                    agent_visible=True,
                )
            elif child_forced_error == "invalid_role" and child_forced_candidate is not None:
                append_system_message(
                    db,
                    child_ticket,
                    f"{assignee_label(child_forced_candidate)} не может быть ответственным по заявке, заявка в общем распределении",
                    agent_visible=True,
                )
            else:
                append_system_message(
                    db,
                    child_ticket,
                    f"{forced_label} не найден или недоступен, заявка в общем распределении",
                    agent_visible=True,
                )
            if assignee is not None:
                append_system_message(db, child_ticket, assignee_change_message(None, assignee))
            else:
                append_system_message(db, child_ticket, "На распределении")
        elif assignee is not None:
            append_system_message(db, child_ticket, assignee_change_message(None, assignee))
        else:
            append_system_message(db, child_ticket, "На распределении")

def _group_parent_client_body(db: Session, parent: Ticket) -> str:
    first_client = db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == parent.id,
                TicketMessage.message_type == MessageType.CLIENT.value,
            )
        )
        .order_by(asc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()
    text = (first_client.body or "").strip() if first_client is not None else ""
    return text or "(пустое сообщение)"


def _on_group_parent_assigned(db: Session, ticket: Ticket, agent: User) -> None:
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value:
        return
    if (ticket.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return
    key = (ticket.instruction_button_key or ticket.button_config_key or "").strip()
    if not key:
        return
    row = resolve_button_row(db, key)
    if row is not None:
        button = _button_from_db_row(db, row, row.menu_type)
        if button.kind == "group_task":
            _create_group_children_from_collecting(db, ticket, button, _group_parent_client_body(db, ticket), agent)


set_group_parent_assigned_hook(_on_group_parent_assigned)

def _process_menu_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
    menu_key: str,
) -> dict:
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    external_chat_id = str(chat.get("id", ""))
    message_id = message.get("message_id")
    if menu_key == "__root__":
        menu_key = None

    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=str(payload.get("update_id")),
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
    )
    db.commit()

    collecting_ticket: Ticket | None = None
    menu_name = DEFAULT_MENU_NAME
    if external_chat_id:
        company = _first_company(db)
        if company is not None:
            contact = telegram_contact_by_chat_id(db, external_chat_id)
            if contact is None:
                from_user = callback.get("from") or {}
                contact = get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
            if contact is not None:
                menu_name = _menu_name_for_contact(db, contact)
                submenu_path = _submenu_path_by_key_or_db(db, menu_name, menu_key)
                if submenu_path:
                    row = _button_settings_map(db, menu_name).get(build_visibility_button_key(menu_name, "submenu", submenu_path))
                    if not _button_visibility_enabled(row):
                        db.commit()
                        if notify_client:
                            if callback_id:
                                answer_callback_query(callback_id, "Эта кнопка больше недоступна. Откройте меню заново.")
                            _safe_delete_message(external_chat_id, message_id if isinstance(message_id, int) else None)
                            _send_problem_menu_with_visibility(db, external_chat_id, menu_name=menu_name)
                        return {"ok": True, "ignored": "menu_disabled"}
                elif menu_key:
                    row = db.execute(
                        select(BotButtonSetting).where(
                            BotButtonSetting.menu_type == menu_name,
                            BotButtonSetting.button_key == menu_key,
                            BotButtonSetting.is_db_created.is_(True),
                            BotButtonSetting.button_kind.in_(["section", "group_parent"]),
                            BotButtonSetting.currently_present.is_(True),
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        db.commit()
                        if notify_client and callback_id:
                            answer_callback_query(callback_id, "Эта кнопка больше недоступна. Откройте меню заново.")
                        return {"ok": True, "ignored": "menu_not_found"}
            if contact is not None and _active_ticket_for_client(db, contact.company_id, contact.client_id) is None:
                collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
                if collecting_ticket is None:
                    collecting_ticket = _create_collecting_ticket(db, contact.company_id, contact.client_id)
                collecting_ticket.button_config_key = f"{SUBMENU_MARKER_PREFIX}{menu_key}"
                collecting_ticket.updated_at = datetime.utcnow()
                db.commit()

    if notify_client and external_chat_id:
        if callback_id:
            answer_callback_query(callback_id)
        if isinstance(message_id, int):
            _safe_delete_message(external_chat_id, message_id)
        response = _send_problem_menu_with_visibility(db, external_chat_id, menu_key=menu_key, menu_name=menu_name)
        if collecting_ticket is not None:
            _store_menu_message_id(collecting_ticket, response)
            db.commit()
    return {"ok": True, "menu_key": menu_key}


def _process_problem_type_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
) -> dict:
    data = callback.get("data") or ""
    button: ButtonSpec | None = None
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    from_user = callback.get("from") or {}
    external_chat_id = str(chat.get("id", ""))
    message_id = message.get("message_id")

    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=str(payload.get("update_id")),
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
    )

    if not external_chat_id:
        db.commit()
        if notify_client and callback_id:
            answer_callback_query(callback_id, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043a\u043d\u043e\u043f\u043a\u0443")
        return {"ok": True, "ignored": "invalid_callback"}

    company = _first_company(db)
    if company is None:
        db.commit()
        return {"ok": True, "ignored": "no_company"}

    contact = get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
    menu_name_for_contact = _menu_name_for_contact(db, contact)
    button = _button_from_callback(db, data, menu_name_for_contact)
    if button is None:
        db.commit()
        if notify_client and callback_id:
            answer_callback_query(callback_id, "Эта кнопка больше недоступна. Откройте меню заново.")
        return {"ok": True, "ignored": "invalid_callback"}
    if not _is_callback_button_enabled(db, menu_name=menu_name_for_contact, button=button):
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "Эта кнопка больше недоступна. Откройте меню заново.")
            _safe_delete_message(external_chat_id, message_id if isinstance(message_id, int) else None)
            _send_problem_menu_with_visibility(db, external_chat_id, menu_name=menu_name_for_contact)
        return {"ok": True, "ignored": "button_disabled"}
    active_ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
    if active_ticket is not None:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u0423 \u0412\u0430\u0441 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435")
            _safe_delete_message(external_chat_id, message_id if isinstance(message_id, int) else None)
            markup = client_close_markup() if client_can_close_ticket(db, active_ticket) else None
            active_ticket_text, active_ticket_parse_mode = _automatic_telegram_text(
                db,
                "telegram.start.active_ticket.client",
                "У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
                ticket=active_ticket,
                contact=contact,
            )
            response = send_message(
                external_chat_id,
                active_ticket_text,
                parse_mode=active_ticket_parse_mode,
                reply_markup=markup,
            )
            if markup is not None:
                store_client_close_button(active_ticket, response, response_message_id_fn=_response_message_id)
                db.commit()
        return {"ok": True, "ticket_id": active_ticket.id, "already_active": True}

    collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if collecting_ticket is None:
        collecting_ticket = _create_collecting_ticket(db, contact.company_id, contact.client_id)
    _remove_menu_keyboard(db, collecting_ticket, fallback_chat_id=external_chat_id, fallback_message_id=message_id if isinstance(message_id, int) else None)

    menu_name = menu_name_for_contact
    collecting_ticket.instruction_button_key = _resolve_instruction_button_key(db, menu_name, button.key)
    confirmation_enabled, confirmation_text = _resolve_effective_confirmation(collecting_ticket, button, db)
    if confirmation_enabled:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id)
            default_confirmation_text = "Подтвердите, пожалуйста, создание обращения"
            confirmation_payload_text, confirmation_parse_mode = _render_confirmation_text_for_telegram(
                db,
                collecting_ticket,
                confirmation_text,
                default_confirmation_text,
            )
            send_message(
                external_chat_id,
                confirmation_payload_text,
                parse_mode=confirmation_parse_mode,
                reply_markup=_confirmation_markup(data),
            )
        return {"ok": True, "ticket_id": collecting_ticket.id, "awaiting_confirmation": True, "button": button.label}

    _prepare_collecting_ticket_for_button(db, collecting_ticket, button, menu_name_for_contact)
    origin_wait = _begin_client_origin_wait(db, collecting_ticket, external_chat_id, notify_client, callback_id)
    if origin_wait is not None:
        return origin_wait
    phone_wait = _begin_bitrix_phone_wait(db, collecting_ticket, external_chat_id, notify_client)
    if phone_wait is not None:
        return phone_wait
    group_wait = _begin_group_message_wait(db, collecting_ticket, button, external_chat_id, notify_client, callback_id)
    if group_wait is not None:
        return group_wait

    if button.kind == "auto":
        collecting_ticket.intake_state = TicketIntakeState.READY.value
        db.commit()
        builtin_result = _dispatch_builtin_button_automation(
            db,
            collecting_ticket,
            button,
            external_chat_id,
            notify_client,
            default_client_text=AUTO_DONE_TEXT,
        )
        if builtin_result is not None:
            if notify_client and callback_id:
                answer_callback_query(callback_id, "Заявка передана сотруднику" if builtin_result.get("automation_pending") else "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u0430")
            return builtin_result
        direct_file_ok = True
        direct_file_error = None
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u0430")
            auto_client_text, auto_client_parse_mode = _render_client_text_for_telegram(
                db, collecting_ticket, button, AUTO_DONE_TEXT
            )
            send_message(external_chat_id, auto_client_text, parse_mode=auto_client_parse_mode)
            direct_file_ok, direct_file_error = _send_button_direct_file(db, collecting_ticket, button, external_chat_id)
            _send_button_rule_files(db, collecting_ticket, button, external_chat_id)
        if not direct_file_ok:
            _fallback_auto_to_support(
                db,
                collecting_ticket,
                direct_file_error or "\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b",
                external_chat_id,
                notify_client,
            )
            db.commit()
            return {
                "ok": True,
                "ticket_id": collecting_ticket.id,
                "workflow_type": TicketWorkflowType.TICKET.value,
                "status": collecting_ticket.status,
                "auto_file_error": True,
            }
        _complete_auto_ticket(db, collecting_ticket, external_chat_id, False)
        db.commit()
        return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.AUTO.value}

    if button.kind in {"task", "group_task"}:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u041e\u043f\u0438\u0448\u0438\u0442\u0435 \u0437\u0430\u0434\u0430\u0447\u0443 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c")
            task_client_text, task_client_parse_mode = _render_client_text_for_telegram(
                db,
                collecting_ticket,
                button,
                _automatic_text(
                    db,
                    "telegram.task.request_text",
                    TASK_DESCRIPTION_REQUEST_TEXT,
                    ticket=collecting_ticket,
                    contact=contact,
                    extra={"timeout_minutes": TASK_DESCRIPTION_TIMEOUT_MINUTES, "button_label": button.label},
                ),
            )
            response = send_message(
                external_chat_id,
                task_client_text,
                parse_mode=task_client_parse_mode,
                reply_markup=client_close_markup(),
            )
            store_client_close_button(collecting_ticket, response, response_message_id_fn=_response_message_id)
            db.commit()
        return {"ok": True, "ticket_id": collecting_ticket.id, "task_pending": True, "button": button.label}

    ticket = collecting_ticket
    assigned_agent = _ready_ticket_for_distribution(db, ticket, button)
    db.commit()

    if notify_client:
        if callback_id:
            answer_callback_query(callback_id, "\u0417\u0430\u044f\u0432\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430")
        ticket_client_text, ticket_client_parse_mode = _render_client_text_for_telegram(
            db, ticket, button, "Заявка создана. Опишите проблему сообщением."
        )
        response = send_message(
            external_chat_id,
            ticket_client_text,
            parse_mode=ticket_client_parse_mode,
            reply_markup=client_close_markup(),
        )
        store_client_close_button(ticket, response, response_message_id_fn=_response_message_id)
        db.commit()
        if assigned_agent is not None and ticket.workflow_type != TicketWorkflowType.TASK.value:
            notify_client_about_assignee(db, ticket, assigned_agent)

    return {"ok": True, "ticket_id": ticket.id, "button": button.label, "status": ticket.status}

def _group_automation_callbacks():
    return {
        "append_system": append_system_message,
        "append_client": _append_client_message,
        "store_attachment": _store_telegram_attachment_and_schedule_employee_notification,
        "archive": start_background_archive_inbound_attachment,
        "remove_close": lambda db, ticket: remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard),
        "complete": _complete_auto_ticket,
        "send": send_message,
        "handoff": lambda db, ticket, button, chat, notify: _handoff_automation_task_to_agent(
            db, ticket, button, chat, notify, append_button_system_text_fn=_store_button_system_text,
            ready_ticket_for_distribution_fn=_ready_ticket_for_distribution, send_message_fn=send_message,
        ),
    }


def _origin_callbacks():
    return {
        **_group_automation_callbacks(),
        "append_client": lambda db, ticket, body, message_id: _append_client_message(
            db, ticket, body, message_id, schedule_notification=False),
        "store_attachment": _store_telegram_attachment,
        "store_close": lambda ticket, response: store_client_close_button(ticket, response, response_message_id_fn=_response_message_id),
    }


def _phone_callbacks():
    def ready(db, ticket, _button):
        button = _button_from_callback(db, f"btn:{ticket.button_config_key or ''}", _menu_name_for_ticket_client(db, ticket))
        if button is not None:
            _store_button_system_text(db, ticket, button)
        return _ready_ticket_for_distribution(db, ticket, button)
    return {**_origin_callbacks(), "send_rich": send_rich_message, "ready": ready}


def _begin_bitrix_phone_wait(db, ticket, chat, notify):
    row = _button_setting_for_ticket(db, ticket)
    if row is None or row.automation_key not in telegram_bitrix_phone.KEYS:
        return None
    return telegram_bitrix_phone.begin(db, ticket, chat, notify, _phone_callbacks(), key=row.automation_key)


def _automation_callback_text(db, key, ticket=None):
    text, _mode = render_automatic_message_for_telegram(db, key, ticket=ticket)
    return strip_telegram_html(text)[:200]


def _begin_client_origin_wait(db, ticket, chat, notify, callback_id):
    setting = _button_setting_for_ticket(db, ticket)
    if setting is None or setting.automation_key != telegram_client_origin.KEY:
        return None
    if notify and callback_id:
        answer_callback_query(callback_id, _automation_callback_text(db, "telegram.client_origin.selection_hint", ticket))
    return telegram_client_origin.begin(db, ticket, chat, notify, _origin_callbacks())


def recover_incomplete_group_automations(db: Session) -> int:
    return telegram_group_automation.recover(db, callbacks=_group_automation_callbacks())


def _is_group_automation(db, ticket):
    setting = _button_setting_for_ticket(db, ticket)
    return setting is not None and setting.automation_key == "telegram_group"


def _begin_group_message_wait(db, ticket, button, chat_id, notify_client, callback_id):
    if not _is_group_automation(db, ticket):
        return None
    ticket.intake_state = TicketIntakeState.COLLECTING.value
    ticket.load_counted = False
    db.commit()
    if notify_client:
        if callback_id:
            answer_callback_query(callback_id, "Отправьте одно сообщение")
        text, mode = _render_client_text_for_telegram(
            db, ticket, button, "Отправьте одним сообщением текст и/или вложение с подписью",
        )
        send_message(chat_id, text, parse_mode=mode)
    return {"ok": True, "ticket_id": ticket.id, "group_waiting": True}


def process_telegram_update(db: Session, payload: dict, notify_client: bool = False) -> dict:
    message = payload.get("message") or payload.get("edited_message") or (payload.get("callback_query") or {}).get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if payload.get("update_id") is not None and _is_private_telegram_chat(chat):
        callback_data = (payload.get("callback_query") or {}).get("data") or ""
        confirmation = _parse_confirm_callback_data(callback_data)
        if confirmation is not None:
            callback_data = confirmation[0]
        selected_setting = None
        if callback_data.startswith("btnid:"):
            selected_setting = db.get(BotButtonSetting, callback_data.removeprefix("btnid:"))
        elif callback_data.startswith("btn:") or confirmation is not None:
            selected_setting = resolve_button_row(db, callback_data.removeprefix("btn:"))
        selects_group = selected_setting is not None and selected_setting.automation_key in {"telegram_group", telegram_client_origin.KEY, *telegram_bitrix_phone.KEYS}
        candidates = db.scalars(select(Ticket).join(
            TelegramContact, and_(TelegramContact.client_id == Ticket.client_id, TelegramContact.company_id == Ticket.company_id),
        ).where(TelegramContact.external_chat_id == chat_id, Ticket.status != TicketStatus.CLOSED.value,
                Ticket.intake_state.in_([TicketIntakeState.COLLECTING.value, TicketIntakeState.TELEGRAM_GROUP_COPYING.value]))).all()
        if selects_group or any(ticket.intake_state == TicketIntakeState.TELEGRAM_GROUP_COPYING.value or _is_group_automation(db, ticket) or telegram_client_origin.waiting(ticket) or telegram_bitrix_phone.waiting(ticket)
            or (getattr(_button_setting_for_ticket(db, ticket), "automation_key", None) in telegram_bitrix_phone.KEYS) for ticket in candidates):
            with telegram_group_automation.chat_lock(db, chat_id):
                # End the read-only discovery snapshot before rechecking a concurrent update.
                if not db.new and not db.dirty and not db.deleted:
                    db.rollback()
                db.expire_all()
                telegram_group_automation.recover(db, callbacks=_group_automation_callbacks(), chat_id=chat_id, already_locked=True)
                return _process_telegram_update(db, payload, notify_client)
    return _process_telegram_update(db, payload, notify_client)


def _process_telegram_update(db: Session, payload: dict, notify_client: bool = False) -> dict:
    update_id = payload.get("update_id")
    if update_id is None:
        return {"ok": True, "ignored": "missing_update_id"}

    update_id_str = str(update_id)

    duplicate = db.execute(select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id_str)).first()
    if duplicate is not None:
        return {"ok": True, "duplicate": True}

    callback = payload.get("callback_query")
    if callback is not None:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        from_user = callback.get("from") or {}
        external_chat_id = str(chat.get("id", ""))
        external_user_id = str(from_user.get("id", ""))
        callback_update = TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=update_id_str,
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        if not _is_private_telegram_chat(chat):
            db.add(callback_update)
            db.commit()
            return {"ok": True, "ignored": "non_private_chat"}
        if external_chat_id:
            company = _first_company(db)
            if company is None:
                db.add(callback_update)
                db.commit()
                return {"ok": True, "ignored": "no_company"}
            access = evaluate_incoming_telegram_access(
                db,
                company_id=company.id,
                external_chat_id=external_chat_id,
                external_user_id=external_user_id,
            )
            if access.blocked:
                db.add(callback_update)
                db.commit()
                return {"ok": True, "blocked": True, "ignored": "blocked_contact"}
        data = callback.get("data") or ""
        if data.startswith(telegram_client_origin.REPEAT_PREFIX):
            contact = telegram_contact_by_chat_id(db, external_chat_id)
            ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id) if contact else None
            db.add(callback_update)
            if ticket is not None and not client_can_close_ticket(db, ticket):
                db.commit()
                return {"ok": True, "ignored": "close_not_allowed"}
            result = telegram_client_origin.repeat(db, ticket, external_chat_id, data, message.get("message_id"), notify_client, _origin_callbacks())
            if notify_client and callback.get("id"):
                answer_callback_query(callback["id"])
            return result
        if data.startswith((telegram_bitrix_phone.PREFIX, telegram_bitrix_phone.PHONE_DEALS_PREFIX)):
            contact = telegram_contact_by_chat_id(db, external_chat_id)
            ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id) if contact else None
            db.add(callback_update)
            if ticket is not None and not client_can_close_ticket(db, ticket):
                db.commit()
                return {"ok": True, "closed": False, "ignored": "close_not_allowed"}
            result = telegram_bitrix_phone.callback(db, ticket, external_chat_id, data, message.get("message_id"), notify_client, _phone_callbacks())
            if notify_client and callback.get("id"):
                answer_callback_query(callback["id"], _automation_callback_text(db,
                    f"{telegram_bitrix_phone.template_prefix(ticket, data=data)}.stale_action" if result.get("ignored") else f"{telegram_bitrix_phone.template_prefix(ticket, data=data)}.action_done", ticket))
            return result
        if data == "client_close":
            contact = telegram_contact_by_chat_id(db, external_chat_id)
            pending_origin = _collecting_ticket_for_client(db, contact.company_id, contact.client_id) if contact else None
            if telegram_bitrix_phone.waiting(pending_origin):
                db.add(callback_update)
                if (str(message.get("message_id")) != pending_origin.client_close_message_id
                        or not client_can_close_ticket(db, pending_origin)):
                    db.commit()
                    return {"ok": True, "closed": False, "ignored": "stale_client_close"}
                return telegram_bitrix_phone.finish(db, pending_origin, external_chat_id, "closed", notify_client, _phone_callbacks())
            if telegram_client_origin.waiting(pending_origin) or telegram_client_origin.owns_close_button(db, contact, message.get("message_id")):
                db.add(callback_update)
                if (not telegram_client_origin.waiting(pending_origin)
                        or str(message.get("message_id")) != pending_origin.client_close_message_id
                        or not client_can_close_ticket(db, pending_origin)):
                    db.commit()
                    if notify_client:
                        if callback.get("id"):
                            answer_callback_query(callback["id"], _automation_callback_text(db, "telegram.client_origin.stale_close", pending_origin))
                        if isinstance(message.get("message_id"), int):
                            _safe_remove_inline_keyboard(external_chat_id, message["message_id"])
                    return {"ok": True, "closed": False, "ignored": "stale_client_close"}
                result = telegram_client_origin.finish(db, pending_origin, external_chat_id, "closed", notify_client, _origin_callbacks())
                if notify_client and callback.get("id"):
                    answer_callback_query(callback["id"], _automation_callback_text(db,
                        "telegram.client_origin.closed_callback" if result["closed"] else "telegram.client_origin.close_pending_callback", pending_origin))
                return result
            return process_client_close_callback(
                db,
                payload,
                callback,
                notify_client,
                first_company_fn=_first_company,
                get_or_create_contact_from_user_fn=get_or_create_contact_from_user,
                collecting_ticket_for_client_fn=_collecting_ticket_for_client,
                active_ticket_for_client_fn=_active_ticket_for_client,
                client_can_close_ticket_fn=client_can_close_ticket,
                remove_menu_keyboard_fn=_remove_menu_keyboard,
                append_system_message_fn=append_system_message,
                answer_callback_query_fn=answer_callback_query,
                safe_remove_inline_keyboard_fn=_safe_remove_inline_keyboard,
                send_message_fn=send_message,
                ticket_closed_client_text=_automatic_text(db, "telegram.ticket.close.client", TICKET_CLOSED_CLIENT_TEXT),
                ticket_closed_client_message_key=CLIENT_CLOSE_MESSAGE_KEY,
            )
        if is_review_callback_data(data):
            return process_review_callback(
                db,
                payload,
                callback,
                notify_client,
                first_company_fn=_first_company,
                get_or_create_contact_from_user_fn=get_or_create_contact_from_user,
                answer_callback_query_fn=answer_callback_query,
                safe_remove_inline_keyboard_fn=_safe_remove_inline_keyboard,
                delete_message_fn=delete_message,
                send_message_fn=send_message,
                edit_message_reply_markup_fn=edit_message_reply_markup,
                ticket_closed_client_text=_automatic_text(db, "telegram.ticket.close.client", TICKET_CLOSED_CLIENT_TEXT),
            )
        if is_feedback_callback_data(data):
            return process_feedback_callback(
                db,
                payload,
                callback,
                notify_client,
                first_company_fn=_first_company,
                get_or_create_contact_from_user_fn=get_or_create_contact_from_user,
                answer_callback_query_fn=answer_callback_query,
                safe_remove_inline_keyboard_fn=_safe_remove_inline_keyboard,
                delete_message_fn=delete_message,
                send_message_fn=send_message,
                ticket_closed_client_text=_automatic_text(db, "telegram.ticket.close.client", TICKET_CLOSED_CLIENT_TEXT),
            )
        confirm = _parse_confirm_callback_data(data)
        if confirm is not None:
            button_ref, decision = confirm
            callback_id = str(callback.get("id", ""))
            message_id = message.get("message_id")

            db.add(
                TelegramUpdate(
                    id=str(uuid.uuid4()),
                    update_id=update_id_str,
                    external_chat_id=external_chat_id or None,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=datetime.utcnow(),
                )
            )

            if not external_chat_id:
                db.commit()
                if notify_client and callback_id:
                    answer_callback_query(callback_id, "Не удалось обработать подтверждение")
                return {"ok": True, "ignored": "missing_chat_id"}

            company = _first_company(db)
            if company is None:
                db.commit()
                return {"ok": True, "ignored": "no_company"}

            contact = get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
            collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
            menu_name_for_contact = _menu_name_for_contact(db, contact)
            button_callback_data = button_ref
            if not (button_callback_data.startswith("btn:") or button_callback_data.startswith("btnid:")):
                button_callback_data = f"btn:{button_callback_data}"
            button = _button_from_callback(db, button_callback_data, menu_name_for_contact)
            if collecting_ticket is None or button is None:
                db.commit()
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "Подтверждение устарело")
                    if isinstance(message_id, int):
                        _safe_remove_inline_keyboard(external_chat_id, message_id)
                    if collecting_ticket is not None:
                        _send_problem_menu_for_contact(db, contact, collecting_ticket)
                return {"ok": True, "ignored": "stale_confirmation"}

            if decision == "no":
                _reset_collecting_ticket(db, collecting_ticket)
                _remove_menu_keyboard(
                    db,
                    collecting_ticket,
                    fallback_chat_id=external_chat_id,
                    fallback_message_id=message_id if isinstance(message_id, int) else None,
                )
                db.commit()
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "Отменено")
                    if isinstance(message_id, int):
                        _safe_remove_inline_keyboard(external_chat_id, message_id)
                    _send_problem_menu_for_contact(db, contact, collecting_ticket)
                return {"ok": True, "ticket_id": collecting_ticket.id, "confirmation": "no"}

            _prepare_collecting_ticket_for_button(db, collecting_ticket, button, menu_name_for_contact)
            origin_wait = _begin_client_origin_wait(db, collecting_ticket, external_chat_id, notify_client, callback_id)
            if origin_wait is not None:
                return origin_wait
            phone_wait = _begin_bitrix_phone_wait(db, collecting_ticket, external_chat_id, notify_client)
            if phone_wait is not None:
                return phone_wait
            group_wait = _begin_group_message_wait(db, collecting_ticket, button, external_chat_id, notify_client, callback_id)
            if group_wait is not None:
                return group_wait
            _remove_menu_keyboard(
                db,
                collecting_ticket,
                fallback_chat_id=external_chat_id,
                fallback_message_id=message_id if isinstance(message_id, int) else None,
            )
            if button.kind == "auto":
                collecting_ticket.intake_state = TicketIntakeState.READY.value
                db.commit()
                builtin_result = _dispatch_builtin_button_automation(
                    db,
                    collecting_ticket,
                    button,
                    external_chat_id,
                    notify_client,
                    default_client_text=AUTO_DONE_TEXT,
                )
                if builtin_result is not None:
                    if notify_client and callback_id:
                        answer_callback_query(callback_id, "Заявка передана сотруднику" if builtin_result.get("automation_pending") else "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u0430")
                    return builtin_result
                direct_file_ok = True
                direct_file_error = None
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u0430")
                    auto_client_text, auto_client_parse_mode = _render_client_text_for_telegram(
                        db, collecting_ticket, button, AUTO_DONE_TEXT
                    )
                    send_message(external_chat_id, auto_client_text, parse_mode=auto_client_parse_mode)
                    direct_file_ok, direct_file_error = _send_button_direct_file(db, collecting_ticket, button, external_chat_id)
                    _send_button_rule_files(db, collecting_ticket, button, external_chat_id)
                if not direct_file_ok:
                    _fallback_auto_to_support(
                        db,
                        collecting_ticket,
                        direct_file_error or "\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b",
                        external_chat_id,
                        notify_client,
                    )
                    db.commit()
                    return {
                        "ok": True,
                        "ticket_id": collecting_ticket.id,
                        "workflow_type": TicketWorkflowType.TICKET.value,
                        "status": collecting_ticket.status,
                        "auto_file_error": True,
                    }
                _complete_auto_ticket(db, collecting_ticket, external_chat_id, False)
                db.commit()
                return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.AUTO.value}

            if button.kind in {"task", "group_task"}:
                db.commit()
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "Опишите задачу одним сообщением")
                    task_client_text, task_client_parse_mode = _render_client_text_for_telegram(
                        db,
                        collecting_ticket,
                        button,
                        _automatic_text(
                            db,
                            "telegram.task.request_text",
                            TASK_DESCRIPTION_REQUEST_TEXT,
                            ticket=collecting_ticket,
                            contact=contact,
                            extra={"timeout_minutes": TASK_DESCRIPTION_TIMEOUT_MINUTES, "button_label": button.label},
                        ),
                    )
                    response = send_message(
                        external_chat_id,
                        task_client_text,
                        parse_mode=task_client_parse_mode,
                        reply_markup=client_close_markup(),
                    )
                    store_client_close_button(collecting_ticket, response, response_message_id_fn=_response_message_id)
                    db.commit()
                return {"ok": True, "ticket_id": collecting_ticket.id, "task_pending": True, "button": button.label}

            ticket = collecting_ticket
            assigned_agent = _ready_ticket_for_distribution(db, ticket, button)
            db.commit()
            if notify_client:
                if callback_id:
                    answer_callback_query(callback_id, "Заявка создана")
                ticket_client_text, ticket_client_parse_mode = _render_client_text_for_telegram(
                    db,
                    ticket,
                    button,
                    _automatic_text(
                        db,
                        "telegram.ticket.create.default_client_text",
                        "Заявка создана. Опишите проблему сообщением.",
                        ticket=ticket,
                        contact=contact,
                        extra={"button_label": button.label},
                    ),
                )
                response = send_message(
                    external_chat_id,
                    ticket_client_text,
                    parse_mode=ticket_client_parse_mode,
                    reply_markup=client_close_markup(),
                )
                store_client_close_button(ticket, response, response_message_id_fn=_response_message_id)
                db.commit()
                if assigned_agent is not None and ticket.workflow_type != TicketWorkflowType.TASK.value:
                    notify_client_about_assignee(db, ticket, assigned_agent)
            return {"ok": True, "ticket_id": ticket.id, "button": button.label, "status": ticket.status}

        menu_key = _menu_key_from_callback(db, data, None)
        if menu_key is not None:
            return _process_menu_callback(db, payload, callback, notify_client, menu_key)
        return _process_problem_type_callback(db, payload, callback, notify_client)

    is_edited_message = "edited_message" in payload
    message = payload.get("message") or payload.get("edited_message")
    if not message:
        db.add(
            TelegramUpdate(
                id=str(uuid.uuid4()),
                update_id=update_id_str,
                external_chat_id=None,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=datetime.utcnow(),
            )
        )
        db.commit()
        return {"ok": True, "ignored": "unsupported_event"}

    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    raw_text = message.get("text") if message.get("text") is not None else (message.get("caption") or "")
    text = raw_text.strip()
    telegram_message_id = str(message.get("message_id")) if message.get("message_id") is not None else None
    attachment_data = None if is_edited_message else _extract_attachment(message)
    attachment_too_large = attachment_is_too_large(attachment_data)
    forbidden_attachment_type = None if is_edited_message else _forbidden_attachment_type(message)

    external_chat_id = str(chat.get("id", ""))
    group_registration = not is_edited_message and is_group_registration(message)
    db.add(
        TelegramUpdate(
            id=str(uuid.uuid4()),
            update_id=update_id_str,
            external_chat_id=external_chat_id or None,
            payload_json=json.dumps(
                {"update_id": update_id, "group_registration": True, "chat_id": external_chat_id}
                if is_group_registration(message) else payload, ensure_ascii=False,
            ),
            created_at=datetime.utcnow(),
        )
    )

    if group_registration:
        result = register_group(db, message)
        db.commit()
        return result

    if not _is_private_telegram_chat(chat):
        db.commit()
        return {"ok": True, "ignored": "non_private_chat"}

    if not external_chat_id:
        db.commit()
        return {"ok": True, "ignored": "missing_chat_id"}

    company = _first_company(db)
    if company is None:
        db.commit()
        return {"ok": True, "ignored": "no_company"}

    external_user_id = str(from_user.get("id", ""))
    access = evaluate_incoming_telegram_access(
        db,
        company_id=company.id,
        external_chat_id=external_chat_id,
        external_user_id=external_user_id,
    )
    if access.blocked:
        db.commit()
        return {"ok": True, "blocked": True, "ignored": "blocked_contact"}

    existing_contact = telegram_contact_by_chat_id(db, external_chat_id)
    contact = get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)

    if is_edited_message:
        edited_ticket = _handle_edited_client_message(db, contact, text or "(пустое сообщение)", telegram_message_id)
        db.commit()
        if edited_ticket is not None:
            return {"ok": True, "edited": True, "ticket_id": edited_ticket.id}
        return {"ok": True, "edited": True, "ignored": "original_message_not_found"}

    pending_origin = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if pending_origin is not None and not telegram_bitrix_phone.waiting(pending_origin):
        row = _button_setting_for_ticket(db, pending_origin)
        if row is not None and row.automation_key in telegram_bitrix_phone.KEYS:
            _begin_bitrix_phone_wait(db, pending_origin, external_chat_id, False)
    if telegram_bitrix_phone.waiting(pending_origin):
        if text.startswith("/start") or text.startswith("/close"):
            result = telegram_bitrix_phone.finish(db, pending_origin, external_chat_id, "closed", notify_client, _phone_callbacks())
            if text.startswith("/start") and result.get("closed"):
                draft = _create_collecting_ticket(db, contact.company_id, contact.client_id)
                if notify_client:
                    _send_problem_menu_for_contact(db, contact, draft)
                db.commit()
            return result
        return telegram_bitrix_phone.incoming(
            db, pending_origin, raw_text, telegram_message_id,
            attachment_data if not attachment_too_large and forbidden_attachment_type is None else None,
            external_chat_id, notify_client, _phone_callbacks(),
        )
    if telegram_client_origin.waiting(pending_origin):
        if text.startswith("/start") or text.startswith("/close"):
            result = telegram_client_origin.finish(db, pending_origin, external_chat_id, "closed", notify_client, _origin_callbacks())
            if text.startswith("/start") and result["closed"]:
                draft = _create_collecting_ticket(db, contact.company_id, contact.client_id)
                if notify_client:
                    _send_problem_menu_for_contact(db, contact, draft)
                db.commit()
            return result
        return telegram_client_origin.incoming(
            db, pending_origin, raw_text, telegram_message_id,
            attachment_data if not attachment_too_large and forbidden_attachment_type is None else None,
            external_chat_id, notify_client, _origin_callbacks(),
        )

    if text.startswith("/start"):
        cleanup_feedback_flow_for_contact(
            db,
            contact=contact,
            external_chat_id=external_chat_id,
            delete_message_fn=delete_message,
        )
        cleanup_review_flow_for_contact(
            db,
            contact=contact,
            external_chat_id=external_chat_id,
            delete_message_fn=delete_message,
            reason="start",
        )
    else:
        review_result = process_review_incoming_message(
            db,
            contact=contact,
            external_chat_id=external_chat_id,
            text=raw_text,
            has_attachment=attachment_data is not None or forbidden_attachment_type is not None,
            notify_client=notify_client,
            delete_message_fn=delete_message,
            send_message_fn=send_message,
        )
        if review_result is not None:
            return review_result
        feedback_result = process_feedback_incoming_message(
            db,
            contact=contact,
            external_chat_id=external_chat_id,
            text=raw_text,
            has_attachment=attachment_data is not None or forbidden_attachment_type is not None,
            notify_client=notify_client,
            delete_message_fn=delete_message,
            send_message_fn=send_message,
        )
        if feedback_result is not None:
            return feedback_result

    if not (text.startswith("/start") or text.startswith("/close")):
        group_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
        if group_ticket is not None and _is_group_automation(db, group_ticket):
            setting = _button_setting_for_ticket(db, group_ticket)
            button = _button_from_callback(db, f"btn:{setting.button_key}", setting.menu_type)
            return telegram_group_automation.process_message(
                db, group_ticket, button, setting, raw_text, telegram_message_id, attachment_data,
                external_chat_id, notify_client, callbacks=_group_automation_callbacks(),
                attachment_too_large=attachment_too_large, forbidden_attachment=forbidden_attachment_type is not None,
            )

    if forbidden_attachment_type is not None:
        ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id) or _collecting_ticket_for_client(
            db,
            contact.company_id,
            contact.client_id,
        )
        if ticket is not None:
            append_system_message(
                db,
                ticket,
                forbidden_attachment_system_text(forbidden_attachment_type),
                agent_visible=True,
            )
        db.commit()
        if notify_client:
            send_message(external_chat_id, *_automatic_telegram_text(db, "telegram.attachment.unsupported_type", FORBIDDEN_ATTACHMENT_TEXT, ticket=ticket, contact=contact))
        return {"ok": True, "forbidden_attachment": forbidden_attachment_type}

    if text.startswith("/close"):
        close_fallback_message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
        closed_ticket = close_active_ticket_for_contact(
            db,
            contact,
            active_ticket_for_client_fn=_active_ticket_for_client,
            collecting_ticket_for_client_fn=_collecting_ticket_for_client,
            remove_menu_keyboard_fn=_remove_menu_keyboard,
            remove_client_close_button_fn=lambda _db, _ticket: remove_client_close_button(
                _db, _ticket, remove_inline_keyboard_fn=remove_inline_keyboard
            ),
            append_system_message_fn=append_system_message,
            fallback_message_id=close_fallback_message_id,
        )
        db.commit()
        if notify_client:
            if closed_ticket is not None:
                _close_response, review_cycle = send_client_close_notification(
                    db,
                    ticket=closed_ticket,
                    contact=contact,
                    external_chat_id=external_chat_id,
                    send_message_fn=send_message,
                    ticket_closed_client_text=TICKET_CLOSED_CLIENT_TEXT,
                    ticket_closed_client_message_key=CLIENT_CLOSE_MESSAGE_KEY,
                )
                if review_cycle is not None:
                    db.commit()
            else:
                close_text, close_parse_mode = _automatic_telegram_text(
                    db,
                    "telegram.ticket.close.no_active.client",
                    NO_ACTIVE_TICKET_TEXT,
                    contact=contact,
                )
                send_message(external_chat_id, close_text, parse_mode=close_parse_mode, remove_keyboard=True)
        return {"ok": True, "command": "close", "closed": closed_ticket is not None}

    if text.startswith("/start"):
        ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
        if ticket is not None:
            append_system_message(
                db,
                ticket,
                "Клиент нажал /start и пытается создать новое обращение",
                agent_visible=True,
            )
            db.commit()
        profile = _client_profile(db, contact.client_id)
        has_destination = bool((profile.destination or "").strip()) if profile is not None else False
        needs_onboarding = bool(profile is None or not profile.is_verified or not has_destination)
        if ticket is None and needs_onboarding:
            first_connection_config = first_connection_values(db, _first_connection_defaults(db, contact))
            return process_first_connection_start(
                db,
                contact,
                external_chat_id,
                notify_client,
                first_connection_subject=first_connection_config["subject"],
                first_connection_agent_text=first_connection_config["system_text"],
                first_connection_client_text=first_connection_config["client_text"],
                ensure_ticket_for_incoming_fn=_ensure_ticket_for_incoming,
                append_system_message_fn=append_system_message,
                user_by_id_fn=lambda _db, _id: _db.execute(select(User).where(User.id == _id)).scalar_one_or_none(),
                send_message_fn=send_message,
                client_close_markup_fn=client_close_markup,
                store_client_close_button_fn=store_client_close_button,
                notify_client_about_assignee_fn=notify_client_about_assignee,
                response_message_id_fn=_response_message_id,
            )

        collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
        if ticket is None and collecting_ticket is None:
            draft = _create_collecting_ticket(db, contact.company_id, contact.client_id)
            append_system_message(db, draft, "Клиент нажал /start")
            collecting_ticket = draft

        if notify_client:
            if ticket is None:
                if collecting_ticket is not None:
                    old_menu_message_ids = _start_menu_cleanup_ids(contact, collecting_ticket)
                    response = _send_problem_menu_for_contact(db, contact, collecting_ticket)
                    new_message_id_raw = _response_message_id(response)
                    try:
                        new_message_id = int(new_message_id_raw) if new_message_id_raw is not None else None
                    except Exception:
                        new_message_id = None
                    _clear_client_menu_keyboards(
                        db,
                        contact,
                        old_menu_message_ids,
                        keep_message_id=new_message_id,
                    )
                    db.commit()
            else:
                old_menu_message_ids = _start_menu_cleanup_ids(contact, ticket)
                markup = client_close_markup() if client_can_close_ticket(db, ticket) else None
                active_ticket_text, active_ticket_parse_mode = _automatic_telegram_text(
                    db,
                    "telegram.start.active_ticket.client",
                    "У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
                    ticket=ticket,
                    contact=contact,
                )
                response = send_message(
                    external_chat_id,
                    active_ticket_text,
                    parse_mode=active_ticket_parse_mode,
                    reply_markup=markup,
                )
                _clear_client_menu_keyboards(
                    db,
                    contact,
                    old_menu_message_ids,
                )
                if markup is not None:
                    store_client_close_button(ticket, response, response_message_id_fn=_response_message_id)
                db.commit()
        return {"ok": True, "command": "start", "ticket_id": ticket.id if ticket else None}

    album_task = _task_for_client_album(db, contact, attachment_data)
    collecting_ticket = None if album_task is not None else _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if collecting_ticket is not None and collecting_ticket.workflow_type == TicketWorkflowType.TASK.value:
        if collecting_ticket.updated_at + timedelta(minutes=TASK_DESCRIPTION_TIMEOUT_MINUTES) <= _now_for(collecting_ticket.updated_at):
            collecting_ticket.intake_state = TicketIntakeState.CANCELLED.value
            closed_at = datetime.utcnow()
            collecting_ticket.status = TicketStatus.CLOSED.value
            collecting_ticket.closed_at = closed_at
            sync_work_interval_after_close(db, collecting_ticket, now=closed_at)
            append_system_message(db, collecting_ticket, "\u0421\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: \u043a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043b \u0442\u0435\u043a\u0441\u0442 \u0432\u043e\u0432\u0440\u0435\u043c\u044f")
            _remove_menu_keyboard(db, collecting_ticket, fallback_chat_id=external_chat_id)
            remove_client_close_button(db, collecting_ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
            db.commit()
            if notify_client:
                send_message(external_chat_id, *_automatic_telegram_text(db, "telegram.intake.task_timeout", TASK_EXPIRED_TEXT, ticket=collecting_ticket, contact=contact, extra={"timeout_minutes": TASK_DESCRIPTION_TIMEOUT_MINUTES}))
            return {"ok": True, "task_pending_expired": True, "ticket_id": collecting_ticket.id}

        client_body = text
        button = _button_from_callback(
            db,
            f"btn:{collecting_ticket.button_config_key or ''}",
            _menu_name_for_ticket_client(db, collecting_ticket),
        )
        automation_result = process_collecting_ticket_automation(
            db=db,
            ticket=collecting_ticket,
            button=button,
            client_body=client_body,
            telegram_message_id=telegram_message_id,
            attachment_data=attachment_data,
            attachment_too_large=attachment_too_large,
            external_chat_id=external_chat_id,
            notify_client=notify_client,
            append_system_message_fn=append_system_message,
            append_client_message_fn=lambda db_session, t, body, msg_id: _append_client_message(
                db_session,
                t,
                body,
                msg_id,
                schedule_notification=attachment_data is None or attachment_too_large,
            ),
            store_telegram_attachment_fn=_store_telegram_attachment_and_schedule_employee_notification,
            remove_client_close_button_fn=lambda _db, _ticket: remove_client_close_button(
                _db, _ticket, remove_inline_keyboard_fn=remove_inline_keyboard
            ),
            send_message_fn=send_message,
            too_large_attachment_system_text_fn=too_large_attachment_system_text,
            send_too_large_attachment_client_messages_fn=send_too_large_attachment_client_messages,
            button_setting_for_ticket_fn=_button_setting_for_ticket,
            append_button_system_text_fn=_store_button_system_text,
            ready_ticket_for_distribution_fn=_ready_ticket_for_distribution,
        )
        if automation_result is not None:
            return automation_result
        client_message = None
        archived_attachment_id = None
        if client_body or not attachment_too_large:
            client_message = _append_client_message(
                db,
                collecting_ticket,
                client_body,
                telegram_message_id,
                schedule_notification=attachment_data is None or attachment_too_large,
            )
        if attachment_too_large:
            append_system_message(
                db,
                collecting_ticket,
                too_large_attachment_system_text(attachment_data),
                agent_visible=True,
            )
            send_too_large_attachment_client_messages(
                db,
                collecting_ticket,
                external_chat_id,
                attachment_data,
                notify_client=notify_client,
            )
        elif attachment_data is not None and client_message is not None:
            attachment = _store_telegram_attachment_and_schedule_employee_notification(db, collecting_ticket, client_message, attachment_data, external_chat_id)
            archived_attachment_id = attachment.id if attachment is not None else None
        refresh_ticket_auto_close_timer(db, collecting_ticket, restart_from_now=False)

        if button is not None:
            _store_button_system_text(db, collecting_ticket, button, client_message=client_body)
        assigned_agent = _ready_ticket_for_distribution(db, collecting_ticket, button)
        if (
            collecting_ticket.task_kind == TicketTaskKind.GROUP_PARENT.value
            and button is not None
            and assigned_agent is not None
        ):
            _create_group_children_from_collecting(db, collecting_ticket, button, client_body, assigned_agent)
        db.commit()
        if archived_attachment_id:
            start_background_archive_inbound_attachment(archived_attachment_id)
        if notify_client:
            try:
                task_created_text, task_created_parse_mode = _automatic_telegram_text(
                    db,
                    "telegram.task.created",
                    TASK_CREATED_CLIENT_TEXT,
                    ticket=collecting_ticket,
                    contact=contact,
                )
                send_message(external_chat_id, task_created_text, parse_mode=task_created_parse_mode)
            except Exception:
                send_message(external_chat_id, TASK_CREATED_CLIENT_TEXT)
        _dispatch_builtin_button_automation(
            db,
            collecting_ticket,
            button,
            external_chat_id,
            notify_client,
        )
        db.commit()
        return {
            "ok": True,
            "ticket_id": collecting_ticket.id,
            "workflow_type": TicketWorkflowType.TASK.value,
            "status": collecting_ticket.status,
            "external_chat_id": external_chat_id,
        }

    active_ticket = album_task or _active_ticket_for_client(db, contact.company_id, contact.client_id)
    if active_ticket is None:
        collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
        db.commit()
        if notify_client:
            if collecting_ticket is not None and (collecting_ticket.button_config_key or "").startswith(SUBMENU_MARKER_PREFIX):
                send_message(external_chat_id, *_automatic_telegram_text(db, "telegram.menu.problem_not_selected", PROBLEM_SUBMENU_TEXT, ticket=collecting_ticket, contact=contact))
            else:
                send_message(external_chat_id, *_automatic_telegram_text(db, "telegram.menu.problem_not_selected", PROBLEM_NOT_SELECTED_TEXT, ticket=collecting_ticket, contact=contact))
        return {"ok": True, "ignored": "problem_type_required"}

    ticket = active_ticket
    had_client_messages = db.execute(
        select(TicketMessage.id)
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.CLIENT.value,
            )
        )
        .limit(1)
    ).scalar_one_or_none() is not None
    client_message = None
    archived_attachment_id = None
    if text or not attachment_too_large:
        client_message = _append_client_message(
            db,
            ticket,
            text,
            telegram_message_id,
            schedule_notification=attachment_data is None or attachment_too_large,
        )
    if attachment_too_large:
        append_system_message(
            db,
            ticket,
            too_large_attachment_system_text(attachment_data),
            agent_visible=True,
        )
        send_too_large_attachment_client_messages(
            db,
            ticket,
            external_chat_id,
            attachment_data,
            notify_client=notify_client,
        )
    elif attachment_data is not None and client_message is not None:
        attachment = _store_telegram_attachment_and_schedule_employee_notification(db, ticket, client_message, attachment_data, external_chat_id)
        archived_attachment_id = attachment.id if attachment is not None else None
    refresh_ticket_auto_close_timer(db, ticket, restart_from_now=False)
    db.commit()
    if archived_attachment_id:
        start_background_archive_inbound_attachment(archived_attachment_id)

    if not had_client_messages:
        button = _button_from_callback(
            db,
            f"btn:{ticket.button_config_key or ''}",
            _menu_name_for_ticket_client(db, ticket),
        )
        _dispatch_builtin_button_automation(
            db,
            ticket,
            button,
            external_chat_id,
            notify_client,
        )
        db.commit()

    return {"ok": True, "ticket_id": ticket.id}


def cleanup_expired_task_intents(db: Session) -> int:
    origin_closed = telegram_client_origin.cleanup(db, _origin_callbacks())
    phone_closed = telegram_bitrix_phone.cleanup(db, _phone_callbacks())
    tickets = db.execute(
        select(Ticket).where(
            and_(
                Ticket.status == TicketStatus.UNASSIGNED.value,
                Ticket.assignee_id.is_(None),
                Ticket.intake_state == TicketIntakeState.COLLECTING.value,
                Ticket.workflow_type.in_([TicketWorkflowType.TASK.value, TicketWorkflowType.TICKET.value]),
            )
        )
    ).scalars().all()
    expired = []
    for ticket in tickets:
        if _is_group_automation(db, ticket) or telegram_client_origin.waiting(ticket) or telegram_bitrix_phone.waiting(ticket):
            continue
        timeout_minutes = TASK_DESCRIPTION_TIMEOUT_MINUTES if ticket.workflow_type == TicketWorkflowType.TASK.value else MENU_SELECTION_TIMEOUT_MINUTES
        if ticket.updated_at + timedelta(minutes=timeout_minutes) <= _now_for(ticket.updated_at):
            expired.append(ticket)
    for ticket in expired:
        contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
        ticket.intake_state = TicketIntakeState.CANCELLED.value
        closed_at = datetime.utcnow()
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_at = closed_at
        sync_work_interval_after_close(db, ticket, now=closed_at)
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            append_system_message(db, ticket, "Создание задачи отменено: клиент не отправил текст вовремя")
        else:
            append_system_message(db, ticket, "Создание заявки отменено: клиент не выбрал конечный пункт меню вовремя")
        _remove_menu_keyboard(db, ticket, fallback_chat_id=contact.external_chat_id if contact is not None else None)
        remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        if contact is not None:
            timeout_key = "telegram.intake.task_timeout" if ticket.workflow_type == TicketWorkflowType.TASK.value else "telegram.intake.menu_timeout"
            timeout_fallback = TASK_EXPIRED_TEXT if ticket.workflow_type == TicketWorkflowType.TASK.value else MENU_SELECTION_TIMEOUT_TEXT
            timeout_minutes = TASK_DESCRIPTION_TIMEOUT_MINUTES if ticket.workflow_type == TicketWorkflowType.TASK.value else MENU_SELECTION_TIMEOUT_MINUTES
            timeout_text, timeout_parse_mode = _automatic_telegram_text(db, timeout_key, timeout_fallback, ticket=ticket, contact=contact, extra={"timeout_minutes": timeout_minutes})
            send_message(contact.external_chat_id, timeout_text, parse_mode=timeout_parse_mode)
    db.commit()
    return len(expired) + origin_closed + phone_closed
