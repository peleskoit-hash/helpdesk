import json
import uuid
from datetime import datetime, timedelta
from mimetypes import guess_extension
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, desc, or_, select
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
)
from ..config import settings
from ..services.telegram_bot import (
    answer_callback_query,
    client_close_markup,
    download_file,
    get_file,
    remove_inline_keyboard,
    send_message,
    send_problem_menu,
)
from ..services.button_config import BASIC_MENU_NAME, DEFAULT_MENU_NAME, ButtonSpec, find_button, find_menu, load_button_config
from ..services.assignment import append_system_message, notify_client_about_assignee
from ..services.assignment import assignee_change_message
from ..services.assignment import assign_ticket_to_agent
from ..services.assignment import choose_online_agent
from ..services.template_fields import render_template_text


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
    "Ваше обращение закрыто. Если Вам снова будет необходима помощь технической поддержки "
    "выберите тип заявки из списка ниже, если список у Вас не отображается, просто нажмите /start."
)
TASK_DESCRIPTION_REQUEST_TEXT = (
    "Опишите задачу одним сообщением.\n"
    "Если текст не будет отправлен в течение 5 минут, создание задачи будет отменено."
)
TASK_CREATED_CLIENT_TEXT = "Заявка создана и передана в работу. Для создания другой нажмите /start"
TASK_EXPIRED_TEXT = "Создание задачи не было завершено вовремя, попробуйте еще раз"
AUTO_REQUEST_TEXT = (
    "Автоматическая заявка принята в обработку.\n"
    "Система обработает её автоматически."
)
AUTO_DONE_TEXT = "Для создания другой нажмите /start"
FORBIDDEN_ATTACHMENT_TEXT = (
    "Голосовые и видеосообщения не принимаются. "
    "Пожалуйста, опишите проблему текстом или прикрепите файл/скриншот."
)
ALLOWED_ATTACHMENT_TYPES = {"document", "photo", "audio", "video", "animation", "sticker"}
FORBIDDEN_ATTACHMENT_TYPES = {"voice", "video_note"}
IMAGE_PREVIEW_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _safe_filename(value: str | None, fallback: str) -> str:
    name = (value or fallback).strip() or fallback
    forbidden = '<>:"/\\|?*'
    for char in forbidden:
        name = name.replace(char, "_")
    return name[:180]


def _attachment_extension(mime_type: str | None, telegram_file_path: str | None) -> str:
    if telegram_file_path and "." in telegram_file_path:
        suffix = Path(telegram_file_path).suffix
        if suffix:
            return suffix
    if mime_type:
        guessed = guess_extension(mime_type)
        if guessed:
            return guessed
    return ".bin"


def _extract_attachment(message: dict) -> dict | None:
    if "document" in message:
        item = message["document"]
        return {
            "source": "document",
            "file_id": item.get("file_id"),
            "file_unique_id": item.get("file_unique_id"),
            "file_size": item.get("file_size"),
            "mime_type": item.get("mime_type"),
            "original_filename": item.get("file_name"),
        }
    if "photo" in message:
        photos = message.get("photo") or []
        if not photos:
            return None
        item = max(photos, key=lambda p: p.get("file_size") or (p.get("width") or 0) * (p.get("height") or 0))
        return {
            "source": "photo",
            "file_id": item.get("file_id"),
            "file_unique_id": item.get("file_unique_id"),
            "file_size": item.get("file_size"),
            "mime_type": "image/jpeg",
            "original_filename": "photo.jpg",
        }
    for source in ("audio", "video", "animation", "sticker"):
        if source in message:
            item = message[source]
            return {
                "source": source,
                "file_id": item.get("file_id"),
                "file_unique_id": item.get("file_unique_id"),
                "file_size": item.get("file_size"),
                "mime_type": item.get("mime_type") or ("image/webp" if source == "sticker" else None),
                "original_filename": item.get("file_name") or f"{source}",
            }
    return None


def _forbidden_attachment_type(message: dict) -> str | None:
    for source in FORBIDDEN_ATTACHMENT_TYPES:
        if source in message:
            return source
    return None


def _response_message_id(response: dict) -> str | None:
    message_id = (response.get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


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
        is_frozen=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(ticket)
    db.flush()
    append_system_message(db, ticket, "Клиент начал создание обращения")
    return ticket


def _ready_ticket_for_distribution(db: Session, ticket: Ticket) -> User | None:
    ticket.intake_state = TicketIntakeState.READY.value
    append_system_message(db, ticket, "На распределении")
    assignee = choose_online_agent(db, ticket.company_id)
    if assignee is None:
        return None
    if assign_ticket_to_agent(db, ticket, assignee, notify_client=False):
        if ticket.workflow_type == TicketWorkflowType.TASK.value:
            _remove_client_close_button(db, ticket)
        return assignee
    return None


def _store_client_close_button(ticket: Ticket, response: dict):
    message_id = _response_message_id(response)
    if message_id is not None:
        ticket.client_close_message_id = message_id


def _remove_client_close_button(db: Session, ticket: Ticket):
    if not ticket.client_close_message_id:
        return
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        ticket.client_close_message_id = None
        return
    try:
        remove_inline_keyboard(contact.external_chat_id, int(ticket.client_close_message_id))
    except Exception:
        pass
    ticket.client_close_message_id = None


def _has_agent_message(db: Session, ticket: Ticket) -> bool:
    return db.execute(
        select(TicketMessage.id)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.AGENT.value))
        .limit(1)
    ).first() is not None


def _client_can_close_ticket(db: Session, ticket: Ticket) -> bool:
    if ticket.status == TicketStatus.CLOSED.value:
        return False
    if ticket.workflow_type == TicketWorkflowType.TASK.value:
        return ticket.assignee_id is None
    if ticket.workflow_type == TicketWorkflowType.AUTO.value:
        return ticket.assignee_id is None
    return not _has_agent_message(db, ticket)


def _complete_auto_ticket(db: Session, ticket: Ticket, external_chat_id: str | None, notify_client: bool):
    append_system_message(db, ticket, "Автоматическая заявка обработана системой")
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.intake_state = TicketIntakeState.READY.value
    ticket.client_close_message_id = None
    ticket.updated_at = datetime.utcnow()
    if notify_client and external_chat_id:
        send_message(external_chat_id, AUTO_DONE_TEXT)
        contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
        if contact is not None:
            _send_problem_menu_for_contact(db, contact)


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
        expires_at=datetime.utcnow() + timedelta(minutes=5),
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
    if profile is not None and profile.is_verified and profile.destination == "Руководство":
        return DEFAULT_MENU_NAME
    return BASIC_MENU_NAME


def _send_problem_menu_for_contact(db: Session, contact: TelegramContact):
    return send_problem_menu(contact.external_chat_id, menu_name=_menu_name_for_contact(db, contact))


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
    ticket.updated_at = datetime.utcnow()
    db.flush()
    return msg


def _store_telegram_attachment(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    attachment_data: dict,
    external_chat_id: str,
) -> TicketAttachment | None:
    file_id = attachment_data.get("file_id")
    if not file_id:
        return None

    file_response = get_file(file_id, chat_id=external_chat_id)
    result = file_response.get("result") or {}
    telegram_file_path = result.get("file_path")
    extension = _attachment_extension(attachment_data.get("mime_type"), telegram_file_path)
    attachment_id = str(uuid.uuid4())
    stored_name = f"{attachment_id}{extension}"
    local_path = Path(settings.attachment_storage_dir) / ticket.id / stored_name
    if telegram_file_path:
        download_file(telegram_file_path, local_path, chat_id=external_chat_id)
    else:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"")

    original_filename = _safe_filename(
        attachment_data.get("original_filename"),
        f"{attachment_data.get('source') or 'file'}{extension}",
    )
    attachment = TicketAttachment(
        id=attachment_id,
        ticket_id=ticket.id,
        message_id=message.id,
        source=attachment_data.get("source") or "telegram",
        telegram_file_id=file_id,
        telegram_file_unique_id=attachment_data.get("file_unique_id") or result.get("file_unique_id"),
        telegram_file_path=telegram_file_path,
        original_filename=original_filename,
        mime_type=attachment_data.get("mime_type"),
        file_size=attachment_data.get("file_size"),
        local_path=str(local_path),
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    ticket.updated_at = datetime.utcnow()
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
    return ticket


def _close_active_ticket_for_contact(db: Session, contact: TelegramContact) -> bool:
    ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
    if ticket is None:
        return False

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()

    append_system_message(db, ticket, "Заявка закрыта клиентом через /close")
    return True


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



def _button_from_callback(data: str) -> ButtonSpec | None:
    prefix = "btn:"
    if not data.startswith(prefix):
        return None
    return find_button(data.removeprefix(prefix))


def _menu_key_from_callback(data: str) -> str | None:
    prefix = "menu:"
    if not data.startswith(prefix):
        return None
    menu_key = data.removeprefix(prefix)
    return menu_key if find_menu(menu_key) is not None else None


def _append_button_system_text(db: Session, ticket: Ticket, button: ButtonSpec):
    if button.system_text:
        append_system_message(
            db,
            ticket,
            render_template_text(button.system_text, db, ticket=ticket) or button.system_text,
            agent_visible=True,
        )


def _prepare_collecting_ticket_for_button(db: Session, ticket: Ticket, button: ButtonSpec):
    ticket.subject = button.label
    ticket.button_config_key = button.key
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


def _create_group_children_from_collecting(db: Session, parent: Ticket, button: ButtonSpec, client_body: str) -> User | None:
    assignee = choose_online_agent(db, parent.company_id)
    parent.intake_state = TicketIntakeState.READY.value
    parent.assignee_id = assignee.id if assignee is not None else None
    parent.status = TicketStatus.NEW.value if assignee is not None else TicketStatus.UNASSIGNED.value
    append_system_message(db, parent, "\u0421\u043e\u0437\u0434\u0430\u043d\u044b \u043f\u043e\u0434\u0437\u0430\u0434\u0430\u0447\u0438 \u0433\u0440\u0443\u043f\u043f\u043e\u0432\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438")

    for child in button.subtasks:
        child_ticket = Ticket(
            id=str(uuid.uuid4()),
            company_id=parent.company_id,
            client_id=parent.client_id,
            assignee_id=assignee.id if assignee is not None else None,
            subject=child.label,
            workflow_type=TicketWorkflowType.TASK.value,
            intake_state=TicketIntakeState.READY.value,
            task_kind=TicketTaskKind.GROUP_CHILD.value,
            parent_ticket_id=parent.id,
            agent_visible=True,
            load_counted=True,
            button_config_key=child.key,
            status=TicketStatus.NEW.value if assignee is not None else TicketStatus.UNASSIGNED.value,
            is_frozen=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(child_ticket)
        db.flush()
        _append_client_message(db, child_ticket, client_body or "(\u043f\u0443\u0441\u0442\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435)")
        if child.system_text:
            append_system_message(
                db,
                child_ticket,
                render_template_text(child.system_text, db, ticket=child_ticket, client_message=client_body) or child.system_text,
                agent_visible=True,
            )
        if assignee is not None:
            append_system_message(db, child_ticket, assignee_change_message(None, assignee))
        else:
            append_system_message(db, child_ticket, "\u041d\u0430 \u0440\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0438")

    if assignee is not None:
        assignee.last_assigned_at = datetime.utcnow()
        append_system_message(db, parent, assignee_change_message(None, assignee))
    else:
        append_system_message(db, parent, "\u041d\u0430 \u0440\u0430\u0441\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u0438\u0438")
    return assignee


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

    if notify_client and external_chat_id:
        if callback_id:
            answer_callback_query(callback_id)
        found = find_menu(menu_key)
        menu_name = found[0] if found is not None else DEFAULT_MENU_NAME
        send_problem_menu(external_chat_id, menu_key=menu_key, menu_name=menu_name)
    return {"ok": True, "menu_key": menu_key}


def _process_problem_type_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
) -> dict:
    data = callback.get("data") or ""
    button = _button_from_callback(data)
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

    if not external_chat_id or button is None:
        db.commit()
        if notify_client and callback_id:
            answer_callback_query(callback_id, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043a\u043d\u043e\u043f\u043a\u0443")
        return {"ok": True, "ignored": "invalid_callback"}

    company = _first_company(db)
    if company is None:
        db.commit()
        return {"ok": True, "ignored": "no_company"}

    contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
    active_ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
    if active_ticket is not None:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u0423 \u0412\u0430\u0441 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435")
            if isinstance(message_id, int):
                remove_inline_keyboard(external_chat_id, message_id)
            markup = client_close_markup() if _client_can_close_ticket(db, active_ticket) else None
            response = send_message(
                external_chat_id,
                "\u0423 \u0412\u0430\u0441 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435, \u0438 \u043e\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u0441\u044f \u0432 \u0442\u0435\u043a\u0443\u0449\u0443\u044e \u0437\u0430\u044f\u0432\u043a\u0443.",
                reply_markup=markup,
            )
            if markup is not None:
                _store_client_close_button(active_ticket, response)
                db.commit()
        return {"ok": True, "ticket_id": active_ticket.id, "already_active": True}

    collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if collecting_ticket is None:
        collecting_ticket = _create_collecting_ticket(db, contact.company_id, contact.client_id)

    _prepare_collecting_ticket_for_button(db, collecting_ticket, button)

    if button.kind == "auto":
        collecting_ticket.intake_state = TicketIntakeState.READY.value
        _append_button_system_text(db, collecting_ticket, button)
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u0430")
            if isinstance(message_id, int):
                remove_inline_keyboard(external_chat_id, message_id)
            send_message(
                external_chat_id,
                render_template_text(button.text, db, ticket=collecting_ticket) or AUTO_DONE_TEXT,
            )
        _complete_auto_ticket(db, collecting_ticket, external_chat_id, False)
        db.commit()
        return {"ok": True, "ticket_id": collecting_ticket.id, "workflow_type": TicketWorkflowType.AUTO.value}

    if button.kind in {"task", "group_task"}:
        db.commit()
        if notify_client:
            if callback_id:
                answer_callback_query(callback_id, "\u041e\u043f\u0438\u0448\u0438\u0442\u0435 \u0437\u0430\u0434\u0430\u0447\u0443 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c")
            if isinstance(message_id, int):
                remove_inline_keyboard(external_chat_id, message_id)
            response = send_message(
                external_chat_id,
                render_template_text(button.text, db, ticket=collecting_ticket) or TASK_DESCRIPTION_REQUEST_TEXT,
                reply_markup=client_close_markup(),
            )
            _store_client_close_button(collecting_ticket, response)
            db.commit()
        return {"ok": True, "ticket_id": collecting_ticket.id, "task_pending": True, "button": button.label}

    ticket = collecting_ticket
    _append_button_system_text(db, ticket, button)
    assigned_agent = _ready_ticket_for_distribution(db, ticket)
    db.commit()

    if notify_client:
        if callback_id:
            answer_callback_query(callback_id, "\u0417\u0430\u044f\u0432\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430")
        if isinstance(message_id, int):
            remove_inline_keyboard(external_chat_id, message_id)
        response = send_message(
            external_chat_id,
            render_template_text(button.text, db, ticket=ticket) or "Заявка создана. Опишите проблему сообщением.",
            reply_markup=client_close_markup(),
        )
        _store_client_close_button(ticket, response)
        db.commit()
        if assigned_agent is not None and ticket.workflow_type != TicketWorkflowType.TASK.value:
            notify_client_about_assignee(db, ticket, assigned_agent)

    return {"ok": True, "ticket_id": ticket.id, "button": button.label, "status": ticket.status}

def _process_client_close_callback(
    db: Session,
    payload: dict,
    callback: dict,
    notify_client: bool,
) -> dict:
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

    company = _first_company(db)
    if company is None or not external_chat_id:
        db.commit()
        return {"ok": True, "ignored": "invalid_client_close"}

    contact = _get_or_create_contact_from_user(db, company.id, external_chat_id, from_user)
    ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id) or _active_ticket_for_client(
        db,
        contact.company_id,
        contact.client_id,
    )
    if ticket is None:
        db.commit()
        if notify_client and callback_id:
            answer_callback_query(callback_id, "Активное обращение не найдено")
        return {"ok": True, "closed": False}

    if not _client_can_close_ticket(db, ticket):
        db.commit()
        if notify_client and callback_id:
            answer_callback_query(callback_id, "Это обращение уже нельзя закрыть этой кнопкой")
        return {"ok": True, "closed": False, "ticket_id": ticket.id}

    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.intake_state = TicketIntakeState.CANCELLED.value if ticket.intake_state == TicketIntakeState.COLLECTING.value else ticket.intake_state
    ticket.client_close_message_id = None
    append_system_message(db, ticket, "Обращение закрыто клиентом через кнопку")
    db.commit()

    if notify_client:
        if callback_id:
            answer_callback_query(callback_id, "Обращение закрыто")
        if isinstance(message_id, int):
            remove_inline_keyboard(external_chat_id, message_id)
        send_message(external_chat_id, "Обращение закрыто. Если потребуется помощь, выберите тип заявки из списка ниже.")
        _send_problem_menu_for_contact(db, contact)

    return {"ok": True, "closed": True, "ticket_id": ticket.id}


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
        _store_client_close_button(ticket, response)
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
        if data == "client_close":
            return _process_client_close_callback(db, payload, callback, notify_client)
        menu_key = _menu_key_from_callback(data)
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
                f"Клиент отправил запрещённый тип сообщения: {forbidden_attachment_type}",
                agent_visible=True,
            )
        db.commit()
        if notify_client:
            send_message(external_chat_id, FORBIDDEN_ATTACHMENT_TEXT)
        return {"ok": True, "forbidden_attachment": forbidden_attachment_type}

    if text.startswith("/close"):
        closed = _close_active_ticket_for_contact(db, contact)
        db.commit()
        if notify_client:
            send_message(
                external_chat_id,
                TICKET_CLOSED_CLIENT_TEXT,
                remove_keyboard=True,
            )
            _send_problem_menu_for_contact(db, contact)
        return {"ok": True, "command": "close", "closed": closed}

    if text.startswith("/start"):
        ticket = _active_ticket_for_client(db, contact.company_id, contact.client_id)
        if is_new_contact and ticket is None:
            return _process_first_connection_start(db, contact, external_chat_id, notify_client)

        if ticket is None and _collecting_ticket_for_client(db, contact.company_id, contact.client_id) is None:
            draft = _create_collecting_ticket(db, contact.company_id, contact.client_id)
            append_system_message(db, draft, "Клиент нажал /start")

        db.commit()
        if notify_client:
            if ticket is None:
                _send_problem_menu_for_contact(db, contact)
            else:
                markup = client_close_markup() if _client_can_close_ticket(db, ticket) else None
                response = send_message(
                    external_chat_id,
                    "У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
                    reply_markup=markup,
                )
                if markup is not None:
                    _store_client_close_button(ticket, response)
                    db.commit()
        return {"ok": True, "command": "start", "ticket_id": ticket.id if ticket else None}

    collecting_ticket = _collecting_ticket_for_client(db, contact.company_id, contact.client_id)
    if collecting_ticket is not None and collecting_ticket.workflow_type == TicketWorkflowType.TASK.value:
        if collecting_ticket.updated_at + timedelta(minutes=5) <= _now_for(collecting_ticket.updated_at):
            collecting_ticket.intake_state = TicketIntakeState.CANCELLED.value
            collecting_ticket.status = TicketStatus.CLOSED.value
            collecting_ticket.closed_at = datetime.utcnow()
            append_system_message(db, collecting_ticket, "\u0421\u043e\u0437\u0434\u0430\u043d\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: \u043a\u043b\u0438\u0435\u043d\u0442 \u043d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043b \u0442\u0435\u043a\u0441\u0442 \u0432\u043e\u0432\u0440\u0435\u043c\u044f")
            db.commit()
            if notify_client:
                send_message(external_chat_id, TASK_EXPIRED_TEXT)
                _send_problem_menu_for_contact(db, contact)
            return {"ok": True, "task_pending_expired": True, "ticket_id": collecting_ticket.id}

        client_body = text
        client_message = _append_client_message(db, collecting_ticket, client_body, telegram_message_id)
        if attachment_data is not None:
            _store_telegram_attachment(db, collecting_ticket, client_message, attachment_data, external_chat_id)
        button = find_button(collecting_ticket.button_config_key or "")

        if collecting_ticket.task_kind == TicketTaskKind.GROUP_PARENT.value and button is not None:
            assigned_agent = _create_group_children_from_collecting(db, collecting_ticket, button, client_body)
            _remove_client_close_button(db, collecting_ticket)
            db.commit()
            if notify_client:
                send_message(external_chat_id, TASK_CREATED_CLIENT_TEXT)
            return {
                "ok": True,
                "ticket_id": collecting_ticket.id,
                "workflow_type": TicketWorkflowType.TASK.value,
                "task_kind": TicketTaskKind.GROUP_PARENT.value,
                "status": collecting_ticket.status,
                "external_chat_id": external_chat_id,
            }

        if button is not None:
            _append_button_system_text(db, collecting_ticket, button)
        assigned_agent = _ready_ticket_for_distribution(db, collecting_ticket)
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
        db.commit()
        if notify_client:
            _send_problem_menu_for_contact(db, contact)
        return {"ok": True, "ignored": "problem_type_required"}

    ticket = active_ticket
    client_message = _append_client_message(db, ticket, text, telegram_message_id)
    if attachment_data is not None:
        _store_telegram_attachment(db, ticket, client_message, attachment_data, external_chat_id)
    db.commit()

    return {"ok": True, "ticket_id": ticket.id}


def cleanup_expired_task_intents(db: Session) -> int:
    tickets = db.execute(
        select(Ticket).where(
            and_(
                Ticket.status == TicketStatus.UNASSIGNED.value,
                Ticket.assignee_id.is_(None),
                Ticket.workflow_type == TicketWorkflowType.TASK.value,
                Ticket.intake_state == TicketIntakeState.COLLECTING.value,
            )
        )
    ).scalars().all()
    expired = [
        ticket for ticket in tickets
        if ticket.updated_at + timedelta(minutes=5) <= _now_for(ticket.updated_at)
    ]
    for ticket in expired:
        contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
        ticket.intake_state = TicketIntakeState.CANCELLED.value
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_at = datetime.utcnow()
        append_system_message(db, ticket, "Создание задачи отменено: клиент не отправил текст вовремя")
        if contact is not None:
            send_message(contact.external_chat_id, TASK_EXPIRED_TEXT)
            _send_problem_menu_for_contact(db, contact)
    db.commit()
    return len(expired)


@router.post("/webhook/telegram")
def telegram_webhook(payload: dict, db: Session = Depends(get_db)):
    return process_telegram_update(db, payload, notify_client=False)


