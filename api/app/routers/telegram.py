import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, asc, desc, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
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
    remove_inline_keyboard,
    send_document,
    send_message,
    send_problem_menu,
)
from ..services.button_config import BASIC_MENU_NAME, BUTTON_CONFIG_DIR, DEFAULT_MENU_NAME, ButtonSpec, build_level_layout_key, build_terminal_button_key, build_visibility_button_key, find_button, find_menu, list_terminal_buttons, load_button_config
from ..services.assignment import append_system_message, notify_client_about_assignee
from ..services.assignment import assignee_change_message, assignee_label
from ..services.assignment import resolve_forced_assignee
from ..services.assignment import assign_ticket_to_forced_user
from ..services.assignment import assign_ticket_to_agent
from ..services.assignment import choose_online_agent
from ..services.assignment import set_group_parent_assigned_hook
from ..services.template_fields import render_template_text
from ..services.telegram_text_format import prepare_telegram_html_message
from ..services.telegram_client_close import (
    client_can_close_ticket,
    close_active_ticket_for_contact,
    process_client_close_callback,
    remove_client_close_button,
    store_client_close_button,
)
from ..services.auto_close import refresh_ticket_auto_close_timer
from ..services.telegram_attachments import (
    _attachment_extension,
    _extract_attachment,
    _forbidden_attachment_type,
    _safe_filename,
    _store_telegram_attachment,
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
from ..services.telegram_automation import (
    process_collecting_ticket_automation,
)
from ..services.telegram_attachment_policy import (
    FORBIDDEN_ATTACHMENT_TEXT,
    attachment_is_too_large,
    forbidden_attachment_system_text,
    send_too_large_attachment_client_messages,
    too_large_attachment_system_text,
)


router = APIRouter(prefix="/api/v1/messenger", tags=["messenger"])


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


def _use_db_buttons() -> bool:
    return bool(settings.use_db_buttons)


def _first_company(db: Session) -> Company | None:
    return db.execute(select(Company).order_by(Company.created_at.asc()).limit(1)).scalar_one_or_none()


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
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.client_close_message_id = None
    ticket.menu_message_id = None
    ticket.updated_at = datetime.utcnow()
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


def _create_client_and_contact(
    db: Session,
    company_id: str,
    external_user_id: str,
    external_chat_id: str,
    display_name: str,
    username: str | None,
) -> TelegramContact:
    client = Client(
        id=str(uuid.uuid4()),
        company_id=company_id,
        display_name=display_name or "Клиент Telegram",
    )
    db.add(client)
    db.flush()

    contact = TelegramContact(
        id=str(uuid.uuid4()),
        company_id=company_id,
        client_id=client.id,
        external_user_id=external_user_id,
        external_chat_id=external_chat_id,
        external_username=username,
        external_display_name=display_name or "Клиент Telegram",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(contact)
    db.flush()
    return contact


def _telegram_contact_by_chat_id(db: Session, external_chat_id: str) -> TelegramContact | None:
    return db.execute(
        select(TelegramContact).where(TelegramContact.external_chat_id == external_chat_id)
    ).scalar_one_or_none()


def _client_profile(db: Session, client_id: str) -> ClientProfile | None:
    return db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id)).scalar_one_or_none()


def _menu_name_for_contact(db: Session, contact: TelegramContact) -> str:
    profile = _client_profile(db, contact.client_id)
    if profile is not None and profile.is_verified and profile.destination:
        destination = profile.destination.strip()
        if destination:
            binding = db.execute(
                select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
            ).scalar_one_or_none()
            if binding is not None:
                if settings.use_db_buttons:
                    return binding.menu_type
                try:
                    load_button_config(binding.menu_type)
                    return binding.menu_type
                except Exception:
                    pass
            try:
                load_button_config(destination)
                return destination
            except FileNotFoundError:
                pass
            except Exception:
                pass
    return BASIC_MENU_NAME


def _menu_name_for_ticket_client(db: Session, ticket: Ticket) -> str:
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is not None:
        return _menu_name_for_contact(db, contact)
    profile = _client_profile(db, ticket.client_id)
    if profile is not None and profile.is_verified and profile.destination:
        destination = profile.destination.strip()
        if destination:
            binding = db.execute(
                select(BotMenuDestinationBinding).where(BotMenuDestinationBinding.destination_value == destination)
            ).scalar_one_or_none()
            if binding is not None:
                if settings.use_db_buttons:
                    return binding.menu_type
                try:
                    load_button_config(binding.menu_type)
                    return binding.menu_type
                except Exception:
                    pass
            try:
                load_button_config(destination)
                return destination
            except FileNotFoundError:
                pass
            except Exception:
                pass
    return BASIC_MENU_NAME


def _send_problem_menu_for_contact(db: Session, contact: TelegramContact, ticket: Ticket | None = None):
    menu_name = _menu_name_for_contact(db, contact)
    response = _send_problem_menu_with_visibility(db, contact.external_chat_id, menu_name=menu_name)
    if ticket is not None:
        _store_menu_message_id(ticket, response)
    return response


def _button_visibility_enabled(row: BotButtonSetting | None) -> bool:
    if row is None or not bool(row.visibility_overridden):
        return True
    return bool(row.is_enabled)


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
        cols = 2 if cols == 2 else 1
        keys: list[str] = []
        keys_raw = row.get("button_keys")
        if isinstance(keys_raw, list):
            for item in keys_raw:
                key = str(item or "").strip()
                if key and key not in keys:
                    keys.append(key)
        normalized.append({"columns": cols, "button_keys": keys[:2]})
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
            if len(row_items) >= 2:
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
    if _use_db_buttons():
        return _send_problem_menu_db_only(db, chat_id, menu_name=menu_name, menu_key=menu_key, text=text)

    config = load_button_config(menu_name)
    menu = config.menus.get(menu_key or config.root_menu_key)
    is_txt_submenu = bool(menu_key and find_menu(menu_key) is not None)
    if menu_key and not is_txt_submenu and menu is None:
        parent_path = _submenu_path_by_key_or_db(db, menu_name, menu_key)
        if not parent_path:
            return send_problem_menu(chat_id, menu_name=menu_name, menu_key=None, text=text)
        menu = config.menus.get(config.root_menu_key)
    if menu is None:
        return send_problem_menu(chat_id, menu_name=menu_name, menu_key=None, text=text)
    refs = list_terminal_buttons(menu_name)
    terminal_by_source = {ref.source_button_key: ref for ref in refs}
    settings_map = _button_settings_map(db, menu_name)
    level_layout_map: dict[str, list[dict]] = {}
    for row in db.execute(select(BotMenuLevelLayout).where(BotMenuLevelLayout.menu_type == menu_name)).scalars().all():
        level_layout_map[row.level_key] = _normalize_layout_rows(row.layout_json)
    db_rows_by_parent: dict[str, list[BotButtonSetting]] = {}
    for row in settings_map.values():
        if not bool(row.is_db_created) or not bool(row.currently_present):
            continue
        parent = (row.parent_path or "").strip()
        db_rows_by_parent.setdefault(parent, []).append(row)
    for bucket in db_rows_by_parent.values():
        bucket.sort(key=lambda item: ((item.updated_at or item.created_at), item.button_label))

    def _effective_label(setting_key: str, fallback: str) -> str:
        row = settings_map.get(setting_key)
        if row is not None and bool(row.label_overridden):
            name = (row.label_override or "").strip()
            if name:
                return name
        return fallback

    def is_terminal_enabled(source_key: str) -> bool:
        ref = terminal_by_source.get(source_key)
        if ref is None:
            return True
        if not _button_visibility_enabled(settings_map.get(ref.technical_key)):
            return False
        segments = ref.full_path.split("/")
        parent = ""
        for segment in segments[:-1]:
            parent = f"{parent}/{segment}" if parent else segment
            if not _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "submenu", parent))):
                return False
            if not _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "group_task", parent))):
                return False
        return True

    def is_submenu_enabled(full_path: str) -> bool:
        return _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "submenu", full_path)))

    def is_group_enabled(full_path: str) -> bool:
        return _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "group_task", full_path)))

    def build_markup_rows(current_menu_key: str | None, parent_path: str = "") -> list[list[dict]]:
        current_menu = config.menus.get(current_menu_key) if current_menu_key else None
        result_rows: list[list[dict]] = []
        current_level_key = build_level_layout_key(menu_name, parent_path)
        if current_menu is not None:
            for row in current_menu.rows:
                rendered_row: list[dict] = []
                for button_key in row:
                    button = config.buttons.get(button_key)
                    if button is None:
                        continue
                    if button.kind == "submenu" and button.submenu_key:
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        if not is_submenu_enabled(full_path):
                            continue
                        child_rows = build_markup_rows(button.submenu_key, full_path)
                        has_visible_children = any(child_rows)
                        if not has_visible_children:
                            continue
                        submenu_key_for_visibility = build_visibility_button_key(menu_name, "submenu", full_path)
                        rendered_row.append(
                            {
                                "text": _effective_label(submenu_key_for_visibility, button.label),
                                "callback_data": f"menu:{button.submenu_key}",
                                "__layout_key": submenu_key_for_visibility,
                            }
                        )
                        continue
                    if button.kind == "group_task":
                        full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                        if not is_group_enabled(full_path):
                            continue
                        any_child_visible = any(is_terminal_enabled(child.key) for child in button.subtasks)
                        if not any_child_visible:
                            continue
                        group_key_for_visibility = build_visibility_button_key(menu_name, "group_task", full_path)
                        rendered_row.append(
                            {
                                "text": _effective_label(group_key_for_visibility, button.label),
                                "callback_data": f"btn:{button.key}",
                                "__layout_key": group_key_for_visibility,
                            }
                        )
                        continue
                    if not is_terminal_enabled(button.key):
                        continue
                    terminal_ref = terminal_by_source.get(button.key)
                    terminal_key = terminal_ref.technical_key if terminal_ref is not None else ""
                    rendered_row.append(
                        {
                            "text": _effective_label(terminal_key, button.label),
                            "callback_data": f"btn:{button.key}",
                            "__layout_key": terminal_key,
                        }
                    )
                if rendered_row:
                    result_rows.append(rendered_row)
        for row in db_rows_by_parent.get(parent_path, []):
            if not _button_visibility_enabled(row):
                continue
            row_kind = (row.button_kind or "").strip().lower()
            row_workflow = (row.workflow_type or "").strip().lower()
            effective_label = _effective_label(row.button_key, row.button_label)
            if row_workflow == "group_task" and not bool(row.is_db_created):
                # Imported txt group_task parents must behave as terminal group_task buttons.
                # They should trigger task flow (btnid), not open DB submenu level (menuid).
                result_rows.append(
                    [
                        {
                            "text": effective_label,
                            "callback_data": f"btnid:{row.id}",
                            "__layout_key": row.button_key,
                        }
                    ]
                )
                continue
            if row_kind in {"section", "group_parent"}:
                child_rows = build_markup_rows(None, row.button_path)
                if not any(child_rows):
                    continue
                result_rows.append(
                    [
                        {
                            "text": effective_label,
                            "callback_data": f"menuid:{row.id}",
                            "__layout_key": row.button_key,
                        }
                    ]
                )
                continue
            result_rows.append(
                [
                    {
                        "text": effective_label,
                        "callback_data": f"btnid:{row.id}",
                        "__layout_key": row.button_key,
                    }
                ]
            )
        laid_out = _apply_layout_to_markup_rows(result_rows, level_layout_map.get(current_level_key))
        clean_rows: list[list[dict]] = []
        for row in laid_out:
            clean_rows.append([{"text": item.get("text"), "callback_data": item.get("callback_data")} for item in row])
        return clean_rows

    parent_path = _submenu_path_by_key_or_db(db, menu_name, menu_key) if menu_key else ""
    inline_keyboard = build_markup_rows(menu.key if (menu_key is None or is_txt_submenu) else None, parent_path or "")
    back_callback: str | None = None
    if menu_key:
        if is_txt_submenu:
            parent_menu_key = _find_parent_submenu_key(menu_name, menu_key)
            back_callback = f"menu:{parent_menu_key}" if parent_menu_key else "menu:__root__"
        else:
            if parent_path and "/" in parent_path:
                parent_parent_path = parent_path.rsplit("/", 1)[0]
                parent_row = db.execute(
                    select(BotButtonSetting).where(
                        BotButtonSetting.menu_type == menu_name,
                        BotButtonSetting.button_path == parent_parent_path,
                        BotButtonSetting.button_kind.in_(["section", "group_parent"]),
                        BotButtonSetting.currently_present.is_(True),
                    )
                ).scalar_one_or_none()
                back_callback = f"menuid:{parent_row.id}" if parent_row is not None else "menu:__root__"
            elif parent_path:
                back_callback = "menu:__root__"
    if back_callback:
        inline_keyboard.append([{"text": "⬅ Назад", "callback_data": back_callback}])
    reply_markup = {"inline_keyboard": inline_keyboard}
    return send_message(chat_id, text or menu.text or "Выберите тип проблемы", reply_markup=reply_markup)


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
                    }
                ]
            )
        laid_out = _apply_layout_to_markup_rows(result_rows, level_layout_map.get(current_level_key))
        clean_rows: list[list[dict]] = []
        for row in laid_out:
            clean_rows.append([{"text": item.get("text"), "callback_data": item.get("callback_data")} for item in row])
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
    return send_message(chat_id, text or "Выберите тип проблемы", reply_markup=reply_markup)


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
    key = (ticket.instruction_button_key or "").strip()
    if key:
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
        if row is not None and bool(row.conditional_files_overridden):
            if not isinstance(row.conditional_files_json, list):
                return []
            return [rule for rule in row.conditional_files_json if isinstance(rule, dict)]
    result: list[dict] = []
    for rule in button.file_rules or ():
        field_name = (rule.get("field") or "").strip()
        file_name = (rule.get("file") or "").strip()
        equals = (rule.get("equals") or "").strip()
        if not field_name or not file_name:
            continue
        result.append(
            {
                "conditions": [{"field": field_name, "operator": "eq", "value": equals}],
                "files": [file_name],
            }
        )
    return result


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
        resolved = _resolve_button_file_path(file_item)
        if resolved is None:
            append_system_message(db, ticket, f"Некорректный путь файла {context_label}: {file_item}", agent_visible=True)
            continue
        if not resolved.exists() or not resolved.is_file():
            append_system_message(db, ticket, f"Файл {context_label} не найден: {file_item}", agent_visible=True)
            continue
        try:
            send_document(external_chat_id, resolved, filename=resolved.name)
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


def _resolve_button_file_path(file_name: str) -> Path | None:
    name = (file_name or "").strip()
    if not name:
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
    return full_path


def _send_button_direct_file(db: Session, ticket: Ticket, button: ButtonSpec, external_chat_id: str) -> tuple[bool, str | None]:
    files_to_send = _resolve_effective_direct_files(db, ticket, button)
    if not files_to_send:
        return True, None
    for file_item in files_to_send:
        resolved = _resolve_button_file_path(file_item)
        if resolved is None:
            reason = f"Некорректный путь файла в кнопке: {file_item}"
            append_system_message(db, ticket, reason, agent_visible=True)
            return False, reason
        if not resolved.exists() or not resolved.is_file():
            reason = f"Файл кнопки не найден: {file_item}"
            append_system_message(db, ticket, reason, agent_visible=True)
            return False, reason
        try:
            send_document(external_chat_id, resolved, filename=resolved.name)
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
) -> User | None:
    append_system_message(db, ticket, f"Автоматическая отправка материала не выполнена: {reason}", agent_visible=True)
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.workflow_type = TicketWorkflowType.TICKET.value
    ticket.task_kind = TicketTaskKind.NORMAL.value
    ticket.agent_visible = True
    ticket.load_counted = True
    assigned_agent = _ready_ticket_for_distribution(db, ticket, None)
    if notify_client:
        send_message(external_chat_id, MATERIAL_UNAVAILABLE_TEXT)
        if assigned_agent is not None:
            notify_client_about_assignee(db, ticket, assigned_agent)
    return assigned_agent


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

    append_system_message(db, new_ticket, f"Заявка создана: {subject}")
    if assignee_id:
        assignee.last_assigned_at = datetime.utcnow()
        append_system_message(db, new_ticket, assignee_change_message(None, assignee))
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
    append_system_message(db, task, f"Задача создана: {intent.subject}")
    if assignee_id:
        assignee.last_assigned_at = datetime.utcnow()
        append_system_message(db, task, assignee_change_message(None, assignee))
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
        ticket.is_frozen = False
        ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()
    db.flush()
    return msg


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


def _get_or_create_contact_from_user(
    db: Session,
    company_id: str,
    external_chat_id: str,
    from_user: dict,
) -> TelegramContact:
    external_user_id = str(from_user.get("id", ""))
    display_name = " ".join(x for x in [from_user.get("first_name"), from_user.get("last_name")] if x).strip()
    username = (from_user.get("username") or "").strip() or None

    contact = _telegram_contact_by_chat_id(db, external_chat_id)

    if contact is None:
        return _create_client_and_contact(
            db=db,
            company_id=company_id,
            external_user_id=external_user_id or external_chat_id,
            external_chat_id=external_chat_id,
            display_name=display_name or "Клиент Telegram",
            username=username,
        )

    contact.updated_at = datetime.utcnow()
    contact.external_username = username
    if display_name:
        contact.external_display_name = display_name
    return contact



def _button_from_callback(db: Session, data: str, menu_name: str | None = None) -> ButtonSpec | None:
    def _button_from_db_row(row: BotButtonSetting) -> ButtonSpec:
        txt_button = None
        try:
            if menu_name:
                for ref in list_terminal_buttons(menu_name):
                    if ref.technical_key == row.button_key:
                        txt_button = find_button(ref.source_button_key)
                        break
            if txt_button is None:
                txt_button = find_button(row.button_key)
            if txt_button is None and menu_name and (row.workflow_type or "").strip().lower() == "group_task":
                full_path = (row.button_path or "").strip()
                if full_path:
                    txt_button = _find_txt_group_button_by_full_path(menu_name, full_path)
        except Exception:
            txt_button = None

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

        if txt_button is not None:
            return ButtonSpec(
                key=row.button_key,
                label=final_label or txt_button.label,
                kind=kind or txt_button.kind,
                text=txt_button.text,
                confirmation_text=txt_button.confirmation_text,
                system_text=txt_button.system_text,
                instruction_text=txt_button.instruction_text,
                automation_key=txt_button.automation_key,
                forced_assignee_type=txt_button.forced_assignee_type,
                forced_assignee_value=txt_button.forced_assignee_value,
                file_path=txt_button.file_path,
                file_rules=txt_button.file_rules,
                submenu_key=txt_button.submenu_key,
                subtasks=txt_button.subtasks,
            )

        return ButtonSpec(
            key=row.button_key,
            label=final_label or row.button_label,
            kind=kind,
        )

    id_prefix = "btnid:"
    if data.startswith(id_prefix):
        id_raw = data.removeprefix(id_prefix).strip()
        if not id_raw:
            return None
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.id == id_raw)).scalar_one_or_none()
        if row is None or not bool(row.currently_present) or bool(row.is_archived):
            return None
        if menu_name and row.menu_type != menu_name:
            return None
        return _button_from_db_row(row)

    prefix = "btn:"
    if not data.startswith(prefix):
        return None
    key = data.removeprefix(prefix)
    if _use_db_buttons():
        if not menu_name:
            return None
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_key == key,
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _button_from_db_row(row)
    txt_button = find_button(key)
    if txt_button is not None:
        return txt_button
    if not menu_name:
        return None
    row = db.execute(
        select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu_name,
            BotButtonSetting.button_key == key,
        )
    ).scalar_one_or_none()
    if row is None or not bool(row.currently_present):
        return None
    row_kind = (row.button_kind or "").strip().lower()
    if row_kind in {"section", "group_parent"}:
        kind = "submenu"
    elif (row.workflow_type or "").strip().lower() == "group_task_child":
        kind = "task"
    else:
        kind = row.workflow_type or "ticket"
    label = (row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else row.button_label
    return ButtonSpec(
        key=row.button_key,
        label=label or row.button_label,
        kind=kind,
    )


def _is_callback_button_enabled(db: Session, *, menu_name: str, button: ButtonSpec) -> bool:
    if _use_db_buttons():
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

    settings_map = _button_settings_map(db, menu_name)
    direct_setting = settings_map.get(button.key)
    if direct_setting is not None:
        if not bool(direct_setting.currently_present):
            return False
        if not _button_visibility_enabled(direct_setting):
            return False
    refs = list_terminal_buttons(menu_name)
    terminal_ref = next((ref for ref in refs if ref.source_button_key == button.key), None)
    if terminal_ref is not None:
        if not _button_visibility_enabled(settings_map.get(terminal_ref.technical_key)):
            return False
        parent = ""
        segments = terminal_ref.full_path.split("/")
        for segment in segments[:-1]:
            parent = f"{parent}/{segment}" if parent else segment
            if not _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "submenu", parent))):
                return False
            if not _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "group_task", parent))):
                return False
        return True
    if button.kind == "submenu":
        submenu_key = button.submenu_key or ""
        menu = load_button_config(menu_name).menus.get(submenu_key)
        submenu_title = menu.title if menu is not None else button.label
        return _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "submenu", submenu_title)))
    if button.kind == "group_task":
        return _button_visibility_enabled(settings_map.get(build_visibility_button_key(menu_name, "group_task", button.label)))
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
    return menu_key or None


def _submenu_path_by_key(menu_name: str, submenu_key: str) -> str | None:
    config = load_button_config(menu_name)
    def walk(menu_key: str, parent_path: str = "") -> str | None:
        menu = config.menus.get(menu_key)
        if menu is None:
            return None
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None or button.kind != "submenu" or not button.submenu_key:
                    continue
                full_path = f"{parent_path}/{button.label}" if parent_path else button.label
                if button.submenu_key == submenu_key:
                    return full_path
                found = walk(button.submenu_key, full_path)
                if found:
                    return found
        return None
    return walk(config.root_menu_key, "")


def _find_txt_group_button_by_full_path(menu_name: str, full_path: str) -> ButtonSpec | None:
    config = load_button_config(menu_name)

    def walk(menu_key: str, parent_path: str = "") -> ButtonSpec | None:
        menu = config.menus.get(menu_key)
        if menu is None:
            return None
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None:
                    continue
                current_path = f"{parent_path}/{button.label}" if parent_path else button.label
                if button.kind == "group_task" and current_path == full_path:
                    return button
                if button.kind == "submenu" and button.submenu_key:
                    found = walk(button.submenu_key, current_path)
                    if found is not None:
                        return found
        return None

    return walk(config.root_menu_key, "")


def _find_parent_submenu_key(menu_name: str, submenu_key: str) -> str | None:
    config = load_button_config(menu_name)

    def walk(menu_key: str) -> str | None:
        menu = config.menus.get(menu_key)
        if menu is None:
            return None
        for row in menu.rows:
            for button_key in row:
                button = config.buttons.get(button_key)
                if button is None or button.kind != "submenu" or not button.submenu_key:
                    continue
                if button.submenu_key == submenu_key:
                    return menu_key
                found = walk(button.submenu_key)
                if found is not None:
                    return found
        return None

    return walk(config.root_menu_key)


def _submenu_path_by_key_or_db(db: Session, menu_name: str, submenu_key: str) -> str | None:
    if _use_db_buttons():
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_key == submenu_key,
                BotButtonSetting.button_kind.in_(["section", "group_parent"]),
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
        return row.button_path if row is not None else None

    from_txt = _submenu_path_by_key(menu_name, submenu_key)
    if from_txt:
        return from_txt
    row = db.execute(
        select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu_name,
            BotButtonSetting.button_key == submenu_key,
            BotButtonSetting.is_db_created.is_(True),
            BotButtonSetting.button_kind.in_(["section", "group_parent"]),
            BotButtonSetting.currently_present.is_(True),
        )
    ).scalar_one_or_none()
    return (row.button_path if row is not None else None)


def _resolve_instruction_button_key(db: Session, menu_name: str, button_key: str) -> str | None:
    if _use_db_buttons():
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.menu_type == menu_name,
                BotButtonSetting.button_key == button_key,
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
        if row is not None and (row.button_kind or "") not in {"section", "group_parent"}:
            return row.button_key
        return None

    for ref in list_terminal_buttons(menu_name):
        if ref.source_button_key == button_key:
            return ref.technical_key
    row = db.execute(
        select(BotButtonSetting).where(
            BotButtonSetting.menu_type == menu_name,
            BotButtonSetting.button_key == button_key,
            BotButtonSetting.is_db_created.is_(True),
            BotButtonSetting.currently_present.is_(True),
        )
    ).scalar_one_or_none()
    if row is None:
        row = db.execute(
            select(BotButtonSetting).where(
                BotButtonSetting.button_key == button_key,
                BotButtonSetting.is_db_created.is_(True),
                BotButtonSetting.currently_present.is_(True),
            )
        ).scalar_one_or_none()
    if row is not None and (row.button_kind or "") not in {"section", "group_parent"}:
        return row.button_key
    return None


def _button_setting_for_ticket(db: Session, ticket: Ticket) -> BotButtonSetting | None:
    key = (ticket.instruction_button_key or "").strip()
    if not key:
        return None
    return db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()


def _apply_button_creation_flags(db: Session, ticket: Ticket):
    setting = _button_setting_for_ticket(db, ticket)
    if setting is None:
        return
    if bool(setting.freeze_on_create):
        ticket.is_frozen = True


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

    ticket.assignee_id = target.id
    ticket.intake_state = TicketIntakeState.READY.value
    if ticket.status != TicketStatus.CLOSED.value:
        ticket.status = TicketStatus.NEW.value
    ticket.updated_at = datetime.utcnow()

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
    key = (ticket.instruction_button_key or "").strip()
    if key:
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
        if row is not None and row.system_text_overridden:
            return (row.system_text or "").strip() or None
    return (button.system_text or "").strip() or None


def _resolve_effective_direct_files(db: Session, ticket: Ticket, button: ButtonSpec) -> list[str]:
    key = (ticket.instruction_button_key or "").strip()
    if key:
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
        if row is not None and bool(row.files_overridden):
            if not isinstance(row.files_json, list):
                return []
            cleaned: list[str] = []
            for value in row.files_json:
                item = str(value or "").strip().replace("\\", "/")
                if not item:
                    continue
                cleaned.append(item)
            return cleaned
    txt_file = (button.file_path or "").strip()
    return [txt_file] if txt_file else []


def _resolve_effective_confirmation(ticket: Ticket, button: ButtonSpec, db: Session) -> tuple[bool, str | None]:
    key = (ticket.instruction_button_key or "").strip()
    if key:
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
        if row is not None and bool(row.confirmation_overridden):
            enabled = bool(row.confirmation_enabled)
            text_value = (row.confirmation_text or "").strip() or None
            return enabled, text_value
    txt = (button.confirmation_text or "").strip() or None
    return bool(txt), txt

def _resolve_effective_client_text(db: Session, ticket: Ticket, button: ButtonSpec) -> str | None:
    keys: list[str] = []
    instruction_key = (ticket.instruction_button_key or "").strip()
    button_config_key = (ticket.button_config_key or "").strip()
    button_key = (button.key or "").strip()
    for candidate in (instruction_key, button_config_key, button_key):
        if candidate and candidate not in keys:
            keys.append(candidate)
    for key in keys:
        row = db.execute(select(BotButtonSetting).where(BotButtonSetting.button_key == key)).scalar_one_or_none()
        if row is None or not bool(row.client_text_overridden):
            continue
        raw = (row.client_text or "").replace("\r\n", "\n").replace("\r", "\n")
        return raw if raw.strip() else None
    raw_txt = (button.text or "").replace("\r\n", "\n").replace("\r", "\n")
    return raw_txt if raw_txt.strip() else None


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


def _reset_collecting_ticket(ticket: Ticket):
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
    ticket.updated_at = datetime.utcnow()


def _prepare_collecting_ticket_for_button(db: Session, ticket: Ticket, button: ButtonSpec, menu_name: str):
    ticket.subject = button.label
    ticket.button_config_key = button.key
    ticket.instruction_button_key = _resolve_instruction_button_key(db, menu_name, button.key)
    ticket.instruction_title = None
    ticket.instruction_text = None
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

    for child in button.subtasks:
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
            instruction_button_key=_resolve_instruction_button_key(db, _menu_name_for_ticket_client(db, parent), child.key),
            status=TicketStatus.NEW.value if (child_forced_assignee is not None or assignee is not None) else TicketStatus.UNASSIGNED.value,
            is_frozen=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(child_ticket)
        db.flush()
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
    key = (ticket.button_config_key or "").strip()
    if not key:
        return
    button = find_button(key)
    if button is None or button.kind != "group_task":
        parent_row = db.execute(
            select(BotButtonSetting).where(
                and_(
                    BotButtonSetting.button_key == key,
                    BotButtonSetting.currently_present.is_(True),
                    BotButtonSetting.is_archived.is_(False),
                )
            )
        ).scalar_one_or_none()
        if parent_row is None:
            return
        child_rows = db.execute(
            select(BotButtonSetting).where(
                and_(
                    BotButtonSetting.menu_type == parent_row.menu_type,
                    BotButtonSetting.parent_path == (parent_row.button_path or ""),
                    BotButtonSetting.currently_present.is_(True),
                    BotButtonSetting.is_archived.is_(False),
                )
            )
            .order_by(asc(BotButtonSetting.created_at), asc(BotButtonSetting.button_label))
        ).scalars().all()
        subtasks: list[ButtonSpec] = []
        for row in child_rows:
            row_kind = (row.button_kind or "").strip().lower()
            if row_kind in {"section", "group_parent"}:
                continue
            workflow = (row.workflow_type or "").strip().lower()
            if workflow not in {"task", "group_task_child"}:
                continue
            label = (row.label_override or "").strip() if bool(row.label_overridden) and (row.label_override or "").strip() else (row.button_label or "")
            if not label:
                continue
            subtasks.append(ButtonSpec(key=row.button_key, label=label, kind="task"))
        if not subtasks:
            return
        button = ButtonSpec(key=parent_row.button_key, label=parent_row.button_label or ticket.subject, kind="group_task", subtasks=tuple(subtasks))
    _create_group_children_from_collecting(
        db,
        ticket,
        button,
        _group_parent_client_body(db, ticket),
        agent,
    )


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
            contact = _telegram_contact_by_chat_id(db, external_chat_id)
            if contact is None:
                from_user = callback.get("from") or {}
                contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
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

    contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
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
            response = send_message(
                external_chat_id,
                "\u0423 \u0412\u0430\u0441 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435, \u0438 \u043e\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u0441\u044f \u0432 \u0442\u0435\u043a\u0443\u0449\u0443\u044e \u0437\u0430\u044f\u0432\u043a\u0443.",
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

    if button.kind == "auto":
        collecting_ticket.intake_state = TicketIntakeState.READY.value
        db.commit()
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
                direct_file_error or "не удалось отправить материал",
                external_chat_id,
                notify_client,
            )
            db.commit()
            return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.TICKET.value, "auto_file_error": True}
        _complete_auto_ticket(db, collecting_ticket, external_chat_id, False)
        db.commit()
        return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.AUTO.value}

    if button.kind in {"task", "group_task"}:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u041e\u043f\u0438\u0448\u0438\u0442\u0435 \u0437\u0430\u0434\u0430\u0447\u0443 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c")
            task_client_text, task_client_parse_mode = _render_client_text_for_telegram(
                db, collecting_ticket, button, TASK_DESCRIPTION_REQUEST_TEXT
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

def _process_first_connection_start(
    db: Session,
    contact: TelegramContact,
    external_chat_id: str,
    notify_client: bool,
) -> dict:
    ticket = _ensure_ticket_for_incoming(
        db,
        contact.company_id,
        contact.client_id,
        FIRST_CONNECTION_SUBJECT,
    )
    append_system_message(
        db,
        ticket,
        FIRST_CONNECTION_AGENT_TEXT.format(username_text="@username"),
        agent_visible=True,
    )

    assigned_agent = None
    if ticket.assignee_id:
        assigned_agent = db.execute(select(User).where(User.id == ticket.assignee_id)).scalar_one_or_none()

    db.commit()

    if notify_client:
        response = send_message(external_chat_id, FIRST_CONNECTION_CLIENT_TEXT, reply_markup=client_close_markup())
        store_client_close_button(ticket, response, response_message_id_fn=_response_message_id)
        db.commit()
        if assigned_agent is not None:
            notify_client_about_assignee(db, ticket, assigned_agent)

    return {
        "ok": True,
        "command": "start",
        "first_connection": True,
        "ticket_id": ticket.id,
        "status": ticket.status,
        "external_chat_id": external_chat_id,
    }


def process_telegram_update(db: Session, payload: dict, notify_client: bool = False) -> dict:
    update_id = payload.get("update_id")
    if update_id is None:
        return {"ok": True, "ignored": "missing_update_id"}

    update_id_str = str(update_id)

    duplicate = db.execute(select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id_str)).first()
    if duplicate is not None:
        return {"ok": True, "duplicate": True}

    callback = payload.get("callback_query")
    if callback is not None:
        data = callback.get("data") or ""
        confirm = _parse_confirm_callback_data(data)
        if confirm is not None:
            button_ref, decision = confirm
            callback_id = str(callback.get("id", ""))
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            from_user = callback.get("from") or {}
            external_chat_id = str(chat.get("id", ""))
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

            contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
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
                _reset_collecting_ticket(collecting_ticket)
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
            _remove_menu_keyboard(
                db,
                collecting_ticket,
                fallback_chat_id=external_chat_id,
                fallback_message_id=message_id if isinstance(message_id, int) else None,
            )
            if button.kind == "auto":
                collecting_ticket.intake_state = TicketIntakeState.READY.value
                db.commit()
                direct_file_ok = True
                direct_file_error = None
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "Автоматическая заявка обработана")
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
                        direct_file_error or "не удалось отправить материал",
                        external_chat_id,
                        notify_client,
                    )
                    db.commit()
                    return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.TICKET.value, "auto_file_error": True}
                _complete_auto_ticket(db, collecting_ticket, external_chat_id, False)
                db.commit()
                return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.AUTO.value}

            if button.kind in {"task", "group_task"}:
                db.commit()
                if notify_client:
                    if callback_id:
                        answer_callback_query(callback_id, "Опишите задачу одним сообщением")
                    task_client_text, task_client_parse_mode = _render_client_text_for_telegram(
                        db, collecting_ticket, button, TASK_DESCRIPTION_REQUEST_TEXT
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

        if data == "client_close":
            return process_client_close_callback(
                db,
                payload,
                callback,
                notify_client,
                first_company_fn=_first_company,
                get_or_create_contact_from_user_fn=_get_or_create_contact_from_user,
                collecting_ticket_for_client_fn=_collecting_ticket_for_client,
                active_ticket_for_client_fn=_active_ticket_for_client,
                client_can_close_ticket_fn=client_can_close_ticket,
                remove_menu_keyboard_fn=_remove_menu_keyboard,
                append_system_message_fn=append_system_message,
                answer_callback_query_fn=answer_callback_query,
                safe_remove_inline_keyboard_fn=_safe_remove_inline_keyboard,
                send_message_fn=send_message,
                ticket_closed_client_text=TICKET_CLOSED_CLIENT_TEXT,
            )
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
    text = (message.get("text") or message.get("caption") or "").strip()
    telegram_message_id = str(message.get("message_id")) if message.get("message_id") is not None else None
    attachment_data = None if is_edited_message else _extract_attachment(message)
    attachment_too_large = attachment_is_too_large(attachment_data)
    forbidden_attachment_type = None if is_edited_message else _forbidden_attachment_type(message)

    external_chat_id = str(chat.get("id", ""))
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
        return {"ok": True, "ignored": "missing_chat_id"}

    company = _first_company(db)
    if company is None:
        db.commit()
        return {"ok": True, "ignored": "no_company"}

    existing_contact = _telegram_contact_by_chat_id(db, external_chat_id)
    is_new_contact = existing_contact is None
    contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)

    if is_edited_message:
        edited_ticket = _handle_edited_client_message(db, contact, text or "(пустое сообщение)", telegram_message_id)
        db.commit()
        if edited_ticket is not None:
            return {"ok": True, "edited": True, "ticket_id": edited_ticket.id}
        return {"ok": True, "edited": True, "ignored": "original_message_not_found"}

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
            send_message(external_chat_id, FORBIDDEN_ATTACHMENT_TEXT)
        return {"ok": True, "forbidden_attachment": forbidden_attachment_type}

    if text.startswith("/close"):
        close_fallback_message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
        closed = close_active_ticket_for_contact(
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
            close_text = TICKET_CLOSED_CLIENT_TEXT if closed else NO_ACTIVE_TICKET_TEXT
            send_message(external_chat_id, close_text, remove_keyboard=True)
        return {"ok": True, "command": "close", "closed": closed}

    if text.startswith("/start"):
        ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
        if is_new_contact and ticket is None:
            return _process_first_connection_start(db, contact, external_chat_id, notify_client)

        collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
        if ticket is None and collecting_ticket is None:
            draft = _create_collecting_ticket(db, contact.company_id, contact.client_id)
            append_system_message(db, draft, "Клиент нажал /start")
            collecting_ticket = draft

        db.commit()
        if notify_client:
            if ticket is None:
                if collecting_ticket is not None:
                    _send_problem_menu_for_contact(db, contact, collecting_ticket)
                    db.commit()
            else:
                markup = client_close_markup() if client_can_close_ticket(db, ticket) else None
                response = send_message(
                    external_chat_id,
                    "У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
                    reply_markup=markup,
                )
                if markup is not None:
                    store_client_close_button(ticket, response, response_message_id_fn=_response_message_id)
                    db.commit()
        return {"ok": True, "command": "start", "ticket_id": ticket.id if ticket else None}

    collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if collecting_ticket is not None and collecting_ticket.workflow_type == TicketWorkflowType.TASK.value:
        if collecting_ticket.updated_at + timedelta(minutes=TASK_DESCRIPTION_TIMEOUT_MINUTES) <= _now_for(collecting_ticket.updated_at):
            collecting_ticket.intake_state = TicketIntakeState.CANCELLED.value
            collecting_ticket.status = TicketStatus.CLOSED.value
            collecting_ticket.closed_at = datetime.utcnow()
            append_system_message(db, collecting_ticket, "\u0421\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: \u043a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043b \u0442\u0435\u043a\u0441\u0442 \u0432\u043e\u0432\u0440\u0435\u043c\u044f")
            _remove_menu_keyboard(db, collecting_ticket, fallback_chat_id=external_chat_id)
            remove_client_close_button(db, collecting_ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
            db.commit()
            if notify_client:
                send_message(external_chat_id, TASK_EXPIRED_TEXT)
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
            append_client_message_fn=_append_client_message,
            store_telegram_attachment_fn=_store_telegram_attachment,
            remove_client_close_button_fn=_remove_client_close_button,
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
        if client_body or not attachment_too_large:
            client_message = _append_client_message(db, collecting_ticket, client_body, telegram_message_id)
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
            _store_telegram_attachment(db, collecting_ticket, client_message, attachment_data, external_chat_id)
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
        if notify_client:
            send_message(external_chat_id, TASK_CREATED_CLIENT_TEXT)
        return {
            "ok": True,
            "ticket_id": collecting_ticket.id,
            "workflow_type": TicketWorkflowType.TASK.value,
            "status": collecting_ticket.status,
            "external_chat_id": external_chat_id,
        }

    active_ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
    if active_ticket is None:
        collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
        db.commit()
        if notify_client:
            if collecting_ticket is not None and (collecting_ticket.button_config_key or "").startswith(SUBMENU_MARKER_PREFIX):
                send_message(external_chat_id, PROBLEM_SUBMENU_TEXT)
            else:
                send_message(external_chat_id, PROBLEM_NOT_SELECTED_TEXT)
        return {"ok": True, "ignored": "problem_type_required"}

    ticket = active_ticket
    client_message = None
    if text or not attachment_too_large:
        client_message = _append_client_message(db, ticket, text, telegram_message_id)
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
        _store_telegram_attachment(db, ticket, client_message, attachment_data, external_chat_id)
    refresh_ticket_auto_close_timer(db, ticket, restart_from_now=False)
    db.commit()

    return {"ok": True, "ticket_id": ticket.id}


def cleanup_expired_task_intents(db: Session) -> int:
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
        timeout_minutes = TASK_DESCRIPTION_TIMEOUT_MINUTES if ticket.workflow_type == TicketWorkflowType.TASK.value else MENU_SELECTION_TIMEOUT_MINUTES
        if ticket.updated_at + timedelta(minutes=timeout_minutes) <= _now_for(ticket.updated_at):
            expired.append(ticket)
    for ticket in expired:
        contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
        ticket.intake_state = TicketIntakeState.CANCELLED.value
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_at = datetime.utcnow()
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            append_system_message(db, ticket, "Создание задачи отменено: клиент не отправил текст вовремя")
        else:
            append_system_message(db, ticket, "Создание заявки отменено: клиент не выбрал конечный пункт меню вовремя")
        _remove_menu_keyboard(db, ticket, fallback_chat_id=contact.external_chat_id if contact is not None else None)
        remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        if contact is not None:
            timeout_text = TASK_EXPIRED_TEXT if ticket.workflow_type == TicketWorkflowType.TASK.value else MENU_SELECTION_TIMEOUT_TEXT
            send_message(contact.external_chat_id, timeout_text)
    db.commit()
    return len(expired)


@router.post("/webhook/telegram")
def telegram_webhook(payload: dict, db: Session = Depends(get_db)):
    return process_telegram_update(db, payload, notify_client=False)
