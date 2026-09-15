from __future__ import annotations

import html
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import asc, event, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import (
    AutomaticMessageTemplate,
    Client,
    ClientProfile,
    EmployeeNotificationPreference,
    InternalTaskOccurrence,
    MessageType,
    TelegramNotificationAccount,
    Ticket,
    TicketAttachment,
    TicketMessage,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from .automatic_messages import (
    NOTIFICATION_BOT_BASIC_TAGS,
    get_notification_bot_basic_definition,
    render_notification_bot_basic_semantic_tags,
)
from .telegram_bot import send_message
from .telegram_employee_binding import REQUEST_STATUS_APPROVED
from .telegram_text_format import prepare_telegram_html_message
from .ticket_links import public_ticket_url
from .ticket_numbering import ensure_ticket_number

logger = logging.getLogger(__name__)

_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY = "employee_assignment_notifications"
_PENDING_MESSAGE_NOTIFICATIONS_KEY = "employee_message_notifications"
_PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY = "employee_media_group_notifications"
_PENDING_INTERNAL_TASK_COMPLETION_NOTIFICATIONS_KEY = "internal_task_completion_notifications"
EMPLOYEE_MEDIA_GROUP_DEBOUNCE_SECONDS = 10.0
TELEGRAM_MEDIA_GROUP_MAX_ATTACHMENTS = 10
EMPLOYEE_NOTIFICATION_MESSAGE_TEXT_LIMIT = 200
ASSIGNMENT_EVENT_NEW = "new_assignment"
ASSIGNMENT_EVENT_TRANSFER = "transfer_between_agents"
ASSIGNMENT_EVENT_UNASSIGNED = "removed_from_agent"
DELIVERY_KIND_NEW = "new_assignment"
DELIVERY_KIND_TRANSFER_TO = "transfer_to_agent"
DELIVERY_KIND_TRANSFER_FROM = "transfer_from_agent"
ADMINISTRATIVE_CONTROL_LABEL = "Административный контроль"
BUSINESS_RECIPIENT_ROLES = {"agent", "trainee"}
_EMPTY = "не задано"
_TOKEN_RE = re.compile(r"\[[^\[\]\n]+\]")


@dataclass
class _MediaGroupDebounceState:
    generation: int
    timer: threading.Timer


# Process-local debounce only. In a multi-worker deployment or process restart,
# in-flight media-group notifications are not persisted by design for this slice.
_MEDIA_GROUP_DEBOUNCE_LOCK = threading.Lock()
_MEDIA_GROUP_DEBOUNCE: dict[tuple[str, str], _MediaGroupDebounceState] = {}
_MEDIA_GROUP_TIMER_FACTORY = threading.Timer


@dataclass(frozen=True)
class AssignmentNotificationDelivery:
    kind: str
    user_id: str


@dataclass(frozen=True)
class AssignmentNotificationEvent:
    kind: str
    old_assignee_id: str | None
    new_assignee_id: str | None
    deliveries: tuple[AssignmentNotificationDelivery, ...] = field(default_factory=tuple)
    old_is_special: bool = False
    new_is_special: bool = False
    actor_user_id: str | None = None


@dataclass(frozen=True)
class EmployeeTelegramDelivery:
    event_kind: str
    delivery_kind: str
    user_id: str
    telegram_chat_id: str
    text: str
    parse_mode: str | None
    disable_link_preview: bool
    send_silently: bool


AssignmentNotificationTelegramDelivery = EmployeeTelegramDelivery


@dataclass(frozen=True)
class InternalTaskCompletionTelegramDelivery:
    user_id: str
    telegram_chat_id: str
    text: str


@dataclass(frozen=True)
class EffectiveNotificationTemplate:
    template: str
    html_enabled: bool
    disable_link_preview: bool
    send_silently: bool


def _normalize_body(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.strip() else None


def _telegram_account_for_user(db: Session, user_id: str) -> TelegramNotificationAccount | None:
    return db.execute(
        select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id == user_id)
    ).scalar_one_or_none()


def _approved_account_for_user(db: Session, user_id: str) -> TelegramNotificationAccount | None:
    account = _telegram_account_for_user(db, user_id)
    if account is None:
        return None
    if account.bound_user_id != user_id:
        return None
    if account.is_blocked:
        return None
    if account.request_status != REQUEST_STATUS_APPROVED:
        return None
    if not account.delivery_available:
        return None
    if not str(account.telegram_chat_id or "").strip():
        return None
    return account


def _is_business_recipient(user: User | None) -> bool:
    return bool(user is not None and user.role in BUSINESS_RECIPIENT_ROLES and user.is_active)


def _base_template(db: Session, notification_type: str) -> tuple[AutomaticMessageTemplate | None, EffectiveNotificationTemplate]:
    definition = get_notification_bot_basic_definition(notification_type)
    if definition is None:
        raise KeyError(notification_type)
    row = db.execute(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == definition.key)).scalar_one_or_none()
    body = None if row is None else _normalize_body(row.body)
    template = body if row is not None and row.is_overridden and body is not None else definition.default_text
    return row, EffectiveNotificationTemplate(
        template=template or "",
        html_enabled=bool(row.html_enabled) if row is not None and row.html_enabled is not None else bool(definition.html_enabled),
        disable_link_preview=bool(row.disable_link_preview) if row is not None else False,
        send_silently=bool(row.send_silently) if row is not None else False,
    )


def _preference_for_user(db: Session, user_id: str) -> EmployeeNotificationPreference | None:
    return db.execute(select(EmployeeNotificationPreference).where(EmployeeNotificationPreference.user_id == user_id)).scalar_one_or_none()


def _global_enabled(db: Session, notification_type: str) -> bool:
    definition = get_notification_bot_basic_definition(notification_type)
    if definition is None:
        return False
    row = db.execute(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == definition.key)).scalar_one_or_none()
    return True if row is None or row.enabled is None else bool(row.enabled)


def _personal_switch_enabled(preference: EmployeeNotificationPreference | None, notification_type: str) -> bool:
    if notification_type == DELIVERY_KIND_NEW:
        return True
    if preference is None:
        return True
    if notification_type == DELIVERY_KIND_TRANSFER_TO:
        return bool(preference.receive_transfer_to_agent)
    if notification_type == DELIVERY_KIND_TRANSFER_FROM:
        return bool(preference.receive_transfer_from_agent)
    if notification_type == "new_message":
        return bool(preference.receive_new_message)
    return False


def _internal_task_completion_enabled(preference: EmployeeNotificationPreference | None) -> bool:
    if preference is None:
        return True
    return bool(preference.notify_internal_task_completion)


def _effective_template(
    db: Session,
    *,
    notification_type: str,
    preference: EmployeeNotificationPreference | None,
) -> EffectiveNotificationTemplate:
    _row, base = _base_template(db, notification_type)
    if notification_type == DELIVERY_KIND_NEW:
        if preference is None or preference.use_base_new_assignment_settings:
            return base
        return EffectiveNotificationTemplate(
            template=preference.personal_new_assignment_template or "",
            html_enabled=bool(preference.personal_new_assignment_html_enabled),
            disable_link_preview=bool(preference.personal_new_assignment_disable_link_preview),
            send_silently=bool(preference.personal_new_assignment_send_silently),
        )
    if notification_type == "new_message":
        if preference is None or preference.use_base_new_message_settings:
            return base
        return EffectiveNotificationTemplate(
            template=preference.personal_new_message_template or "",
            html_enabled=bool(preference.personal_new_message_html_enabled),
            disable_link_preview=bool(preference.personal_new_message_disable_link_preview),
            send_silently=bool(preference.personal_new_message_send_silently),
        )
    return base


def _value(value: object, *, html_enabled: bool) -> str:
    text = str(value or "").strip()
    if not text:
        return _EMPTY
    return html.escape(text, quote=False) if html_enabled else text


def _format_dt(value: datetime | None, *, html_enabled: bool) -> str:
    if value is None:
        return _EMPTY
    dt = value if value.tzinfo is not None else value.replace(tzinfo=ZoneInfo("UTC"))
    formatted = dt.astimezone(ZoneInfo(settings.app_timezone)).strftime("%d.%m.%Y, %H:%M")
    return html.escape(formatted, quote=False) if html_enabled else formatted


def _ticket_number(ticket: Ticket, *, html_enabled: bool) -> str:
    if ticket.ticket_number is None:
        return _EMPTY
    value = str(ticket.ticket_number)
    return html.escape(value, quote=False) if html_enabled else value


def _ticket_type(ticket: Ticket) -> str:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    return "задача" if workflow_type == TicketWorkflowType.TASK.value else "заявка"


def _is_group_child_ticket(ticket: Ticket | None) -> bool:
    if ticket is None:
        return False
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value:
        return False
    return (
        (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.GROUP_CHILD.value
        and bool(str(ticket.parent_ticket_id or "").strip())
    )


def _ticket_link(ticket: Ticket, *, html_enabled: bool) -> str:
    value = public_ticket_url(ticket)
    if not value:
        return _EMPTY
    return html.escape(value, quote=False) if html_enabled else value


def _author_name(db: Session, ticket: Ticket, *, html_enabled: bool) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    if profile is not None and profile.is_verified:
        full_name = " ".join(part for part in (profile.last_name, profile.contact_name) if part).strip()
        if full_name:
            return _value(full_name, html_enabled=html_enabled)
    client = db.get(Client, ticket.client_id)
    return _value(client.display_name if client is not None else None, html_enabled=html_enabled)


def _author_department(db: Session, ticket: Ticket, *, html_enabled: bool) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    return _value(profile.department if profile is not None and profile.is_verified else None, html_enabled=html_enabled)


def _user_name(user: User | None, *, html_enabled: bool) -> str:
    return _value(user.full_name if user is not None else None, html_enabled=html_enabled)


def _message_kind(message: TicketMessage | None) -> str:
    labels = {
        MessageType.CLIENT.value: "Клиент",
        MessageType.AGENT.value: "Сотрудник",
        MessageType.INTERNAL.value: "Комментарий",
        MessageType.SYSTEM.value: "Система",
    }
    return labels.get(message.message_type if message is not None else "", "Система")


def _attachment_label(attachment: TicketAttachment) -> str:
    source = str(attachment.source or "").strip().lower()
    if source == "photo":
        return "Фото"
    if source == "video":
        return "Видео"
    return str(attachment.original_filename or "").strip() or "Файл"


def _truncate_message_text(value: str) -> str:
    if len(value) <= EMPLOYEE_NOTIFICATION_MESSAGE_TEXT_LIMIT:
        return value
    return f"{value[:EMPLOYEE_NOTIFICATION_MESSAGE_TEXT_LIMIT]}…"


def _telegram_message_order(value: str | None) -> tuple[int, int | str]:
    normalized = str(value or "").strip()
    if normalized.isdigit():
        return (0, int(normalized))
    return (1, normalized)


def _load_message_attachments(db: Session, message_id: str) -> list[TicketAttachment]:
    return db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == message_id)
        .order_by(asc(TicketAttachment.created_at), asc(TicketAttachment.id))
    ).scalars().all()


def _composite_message_body(message: TicketMessage, attachments: list[TicketAttachment]) -> str:
    body = _truncate_message_text(str(message.body or "").strip())
    labels = [_attachment_label(attachment) for attachment in attachments]
    lines: list[str] = []
    if body:
        lines.append(body)
    if labels:
        if lines:
            lines.append("")
        lines.append("Файлы:")
        lines.extend(labels)
    return "\n".join(lines)


def _message_body(db: Session, message: TicketMessage | None, *, html_enabled: bool) -> str:
    if message is None:
        return _EMPTY
    body = _composite_message_body(message, _load_message_attachments(db, message.id))
    return _value(body if body else None, html_enabled=html_enabled)


def _load_media_group_rows(
    db: Session,
    *,
    ticket_id: str,
    media_group_id: str,
) -> list[tuple[TicketAttachment, TicketMessage]]:
    attachments = db.execute(
        select(TicketAttachment)
        .where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_media_group_id == media_group_id,
        )
        .order_by(asc(TicketAttachment.created_at), asc(TicketAttachment.id))
    ).scalars().all()
    if not attachments:
        return []
    message_ids = [attachment.message_id for attachment in attachments]
    messages = {
        message.id: message
        for message in db.execute(select(TicketMessage).where(TicketMessage.id.in_(message_ids))).scalars().all()
    }
    rows = [(attachment, messages[attachment.message_id]) for attachment in attachments if attachment.message_id in messages]
    rows.sort(
        key=lambda row: (
            _telegram_message_order(row[1].telegram_message_id or row[0].telegram_message_id),
            row[0].created_at or datetime.min,
            row[0].id,
        )
    )
    return rows


def _media_group_message_body(rows: list[tuple[TicketAttachment, TicketMessage]]) -> str:
    captions: list[str] = []
    seen_captions: set[str] = set()
    labels: list[str] = []
    for attachment, message in rows:
        body = _truncate_message_text(str(message.body or "").strip())
        if body and body not in seen_captions:
            seen_captions.add(body)
            captions.append(body)
        labels.append(_attachment_label(attachment))
    lines: list[str] = []
    lines.extend(captions)
    if labels:
        if lines:
            lines.append("")
        lines.append("Файлы:")
        lines.extend(labels)
    return "\n".join(lines)


def _message_author(db: Session, ticket: Ticket, message: TicketMessage | None, *, html_enabled: bool) -> str:
    if message is None:
        return _EMPTY
    if message.author_user_id:
        return _user_name(db.get(User, message.author_user_id), html_enabled=html_enabled)
    if message.message_type == MessageType.CLIENT.value:
        return _author_name(db, ticket, html_enabled=html_enabled)
    return "Система"


def _render_tags(
    db: Session,
    *,
    notification_type: str,
    template: str,
    account: TelegramNotificationAccount,
    ticket: Ticket,
    message: TicketMessage | None = None,
    old_assignee: User | None = None,
    new_assignee: User | None = None,
    old_is_special: bool = False,
    new_is_special: bool = False,
    actor_user: User | None = None,
    event_at: datetime | None = None,
    message_body: str | None = None,
    html_enabled: bool,
) -> str:
    previous_agent = ADMINISTRATIVE_CONTROL_LABEL if old_is_special else _user_name(old_assignee, html_enabled=html_enabled)
    destination = ADMINISTRATIVE_CONTROL_LABEL if new_is_special else _user_name(new_assignee, html_enabled=html_enabled)
    values = {
        "[Номер заявки]": _ticket_number(ticket, html_enabled=html_enabled),
        "[Тип заявки]": _ticket_type(ticket),
        "[Заголовок заявки]": _value(ticket.subject, html_enabled=html_enabled),
        "[Автор заявки/задачи]": _author_name(db, ticket, html_enabled=html_enabled),
        "[Подразделение автора]": _author_department(db, ticket, html_enabled=html_enabled),
        "[Дата создания]": _format_dt(ticket.created_at, html_enabled=html_enabled),
        "[Ссылка на заявку]": _ticket_link(ticket, html_enabled=html_enabled),
        "[Автор сообщения]": _message_author(db, ticket, message, html_enabled=html_enabled),
        "[Вид сообщения]": _message_kind(message),
        "[Тело сообщения]": _value(message_body, html_enabled=html_enabled) if message_body is not None else _message_body(db, message, html_enabled=html_enabled),
        "[Дата сообщения]": _format_dt(message.created_at if message is not None else None, html_enabled=html_enabled),
        "[Предыдущий агент]": previous_agent,
        "[Куда передана заявка]": destination,
        "[Администратор перевода]": _user_name(actor_user, html_enabled=html_enabled),
        "[Дата перевода]": _format_dt(event_at, html_enabled=html_enabled),
    }
    allowed_tags = set(NOTIFICATION_BOT_BASIC_TAGS.get(notification_type, ()))

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in allowed_tags:
            return values[token]
        semantic_value = render_notification_bot_basic_semantic_tags(
            token,
            recipient_telegram_username=account.telegram_username,
        )
        if semantic_value != token:
            return semantic_value
        return token

    return _TOKEN_RE.sub(replace_token, template)


def _render_telegram_text(template: EffectiveNotificationTemplate, rendered: str) -> tuple[str, str | None]:
    if template.html_enabled:
        return prepare_telegram_html_message(rendered)
    return rendered, None


def build_assignment_notification_event(
    *,
    old_assignee_id: str | None,
    new_assignee_id: str | None,
    old_is_special: bool = False,
    new_is_special: bool = False,
    actor_user_id: str | None = None,
) -> AssignmentNotificationEvent | None:
    if old_assignee_id == new_assignee_id and old_is_special == new_is_special:
        return None
    if old_is_special and new_is_special:
        return None
    if old_is_special and new_assignee_id:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_TRANSFER,
            old_assignee_id=old_assignee_id,
            new_assignee_id=new_assignee_id,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_TO, user_id=new_assignee_id),),
            old_is_special=True,
            actor_user_id=actor_user_id,
        )
    if old_assignee_id and new_is_special:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_TRANSFER,
            old_assignee_id=old_assignee_id,
            new_assignee_id=new_assignee_id,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_FROM, user_id=old_assignee_id),),
            new_is_special=True,
            actor_user_id=actor_user_id,
        )
    if old_assignee_id is None and new_assignee_id:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_NEW,
            old_assignee_id=None,
            new_assignee_id=new_assignee_id,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_NEW, user_id=new_assignee_id),),
            actor_user_id=actor_user_id,
        )
    if old_assignee_id and new_assignee_id:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_TRANSFER,
            old_assignee_id=old_assignee_id,
            new_assignee_id=new_assignee_id,
            deliveries=(
                AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_TO, user_id=new_assignee_id),
                AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_FROM, user_id=old_assignee_id),
            ),
            actor_user_id=actor_user_id,
        )
    if old_assignee_id and new_assignee_id is None:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_UNASSIGNED,
            old_assignee_id=old_assignee_id,
            new_assignee_id=None,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_FROM, user_id=old_assignee_id),),
            actor_user_id=actor_user_id,
        )
    return None


def _resolve_legacy_text_deliveries(
    db: Session,
    *,
    event: AssignmentNotificationEvent,
    text: str,
) -> tuple[EmployeeTelegramDelivery, ...]:
    resolved: list[EmployeeTelegramDelivery] = []
    for delivery in event.deliveries:
        account = _approved_account_for_user(db, delivery.user_id)
        if account is None:
            continue
        resolved.append(
            EmployeeTelegramDelivery(
                event_kind=event.kind,
                delivery_kind=delivery.kind,
                user_id=delivery.user_id,
                telegram_chat_id=str(account.telegram_chat_id),
                text=text,
                parse_mode=None,
                disable_link_preview=True,
                send_silently=False,
            )
        )
    return tuple(resolved)


def resolve_assignment_telegram_deliveries(
    db: Session,
    *,
    event: AssignmentNotificationEvent,
    ticket_id: str | None = None,
    ticket: Ticket | None = None,
    text: str | None = None,
    event_at: datetime | None = None,
) -> tuple[EmployeeTelegramDelivery, ...]:
    if text is not None and ticket is None and ticket_id is None:
        return _resolve_legacy_text_deliveries(db, event=event, text=text)
    ticket = ticket or (db.get(Ticket, ticket_id) if ticket_id else None)
    if ticket is None:
        return ()
    if _is_group_child_ticket(ticket) and event.kind == ASSIGNMENT_EVENT_NEW:
        return ()
    old_assignee = db.get(User, event.old_assignee_id) if event.old_assignee_id else None
    new_assignee = db.get(User, event.new_assignee_id) if event.new_assignee_id else None
    actor_user = db.get(User, event.actor_user_id) if event.actor_user_id else None
    resolved: list[EmployeeTelegramDelivery] = []
    for delivery in event.deliveries:
        notification_type = delivery.kind
        if not _global_enabled(db, notification_type):
            continue
        recipient = db.get(User, delivery.user_id)
        if not _is_business_recipient(recipient):
            continue
        preference = _preference_for_user(db, delivery.user_id)
        if not _personal_switch_enabled(preference, notification_type):
            continue
        account = _approved_account_for_user(db, delivery.user_id)
        if account is None:
            continue
        effective = _effective_template(db, notification_type=notification_type, preference=preference)
        rendered = _render_tags(
            db,
            notification_type=notification_type,
            template=effective.template,
            account=account,
            ticket=ticket,
            old_assignee=old_assignee,
            new_assignee=new_assignee,
            old_is_special=event.old_is_special,
            new_is_special=event.new_is_special,
            actor_user=actor_user,
            event_at=event_at,
            html_enabled=effective.html_enabled,
        )
        text_value, parse_mode = _render_telegram_text(effective, rendered)
        resolved.append(
            EmployeeTelegramDelivery(
                event_kind=event.kind,
                delivery_kind=notification_type,
                user_id=delivery.user_id,
                telegram_chat_id=str(account.telegram_chat_id),
                text=text_value,
                parse_mode=parse_mode,
                disable_link_preview=effective.disable_link_preview,
                send_silently=effective.send_silently,
            )
        )
    return tuple(resolved)


def schedule_assignment_notification(
    db: Session,
    *,
    old_assignee_id: str | None,
    new_assignee_id: str | None,
    ticket,
    old_is_special: bool = False,
    new_is_special: bool = False,
    actor_user_id: str | None = None,
) -> AssignmentNotificationEvent | None:
    event_payload = build_assignment_notification_event(
        old_assignee_id=old_assignee_id,
        new_assignee_id=new_assignee_id,
        old_is_special=old_is_special,
        new_is_special=new_is_special,
        actor_user_id=actor_user_id,
    )
    if event_payload is None or not event_payload.deliveries:
        return event_payload
    if _is_group_child_ticket(ticket) and event_payload.kind == ASSIGNMENT_EVENT_NEW:
        return event_payload
    ensure_ticket_number(db, ticket)
    db.info.setdefault(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, []).append(
        {
            "event_kind": event_payload.kind,
            "old_assignee_id": event_payload.old_assignee_id,
            "new_assignee_id": event_payload.new_assignee_id,
            "old_is_special": event_payload.old_is_special,
            "new_is_special": event_payload.new_is_special,
            "actor_user_id": event_payload.actor_user_id,
            "ticket_id": ticket.id,
            "event_at": datetime.utcnow(),
            "deliveries": [{"kind": delivery.kind, "user_id": delivery.user_id} for delivery in event_payload.deliveries],
        }
    )
    return event_payload


def schedule_new_message_notification(
    db: Session,
    *,
    ticket: Ticket,
    message: TicketMessage,
    attachment: TicketAttachment | None = None,
) -> None:
    if _is_group_child_ticket(ticket):
        return
    if not ticket.assignee_id:
        return
    if message.message_type == MessageType.SYSTEM.value:
        return
    if message.agent_visible is False:
        return
    if message.replaced_message_id:
        return
    ensure_ticket_number(db, ticket)
    media_group_id = str(attachment.telegram_media_group_id or "").strip() if attachment is not None else ""
    if media_group_id:
        db.info.setdefault(_PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY, []).append(
            {
                "ticket_id": ticket.id,
                "media_group_id": media_group_id,
            }
        )
        return
    db.info.setdefault(_PENDING_MESSAGE_NOTIFICATIONS_KEY, []).append(
        {
            "ticket_id": ticket.id,
            "message_id": message.id,
            "recipient_user_id": ticket.assignee_id,
        }
    )


def _format_internal_task_completion_text(
    *,
    title: str,
    result_text: str,
) -> str:
    return "\n".join([
        "Внутренняя задача выполнена",
        f"Задача: {title}",
        "Результат:",
        result_text,
    ])


def schedule_internal_task_completion_notification(
    db: Session,
    *,
    ticket: Ticket,
    final_message: TicketMessage,
) -> None:
    db.info.setdefault(_PENDING_INTERNAL_TASK_COMPLETION_NOTIFICATIONS_KEY, []).append(
        {
            "ticket_id": ticket.id,
            "final_message_id": final_message.id,
        }
    )


def resolve_internal_task_completion_telegram_delivery(
    db: Session,
    *,
    ticket_id: str,
    final_message_id: str,
) -> InternalTaskCompletionTelegramDelivery | None:
    occurrence = db.execute(
        select(InternalTaskOccurrence)
        .where(InternalTaskOccurrence.ticket_id == ticket_id)
        .limit(1)
    ).scalar_one_or_none()
    if occurrence is None or not occurrence.notify_on_completion or not occurrence.responsible_admin_id:
        return None

    responsible = db.get(User, occurrence.responsible_admin_id)
    if responsible is None or responsible.role != "admin" or not responsible.is_active:
        return None
    preference = _preference_for_user(db, responsible.id)
    if not _internal_task_completion_enabled(preference):
        return None
    account = _approved_account_for_user(db, responsible.id)
    if account is None:
        return None

    final_message = db.get(TicketMessage, final_message_id)
    if (
        final_message is None
        or final_message.ticket_id != ticket_id
        or final_message.message_type != MessageType.AGENT.value
        or final_message.message_status == "deleted"
    ):
        return None

    attachments = _load_message_attachments(db, final_message_id)
    result_text = _composite_message_body(final_message, attachments)
    if not result_text:
        return None

    return InternalTaskCompletionTelegramDelivery(
        user_id=responsible.id,
        telegram_chat_id=str(account.telegram_chat_id),
        text=_format_internal_task_completion_text(
            title=occurrence.title_snapshot or "Внутренняя задача",
            result_text=result_text,
        ),
    )


def _resolve_new_message_delivery(
    db: Session,
    *,
    ticket: Ticket,
    message: TicketMessage,
    recipient_user_id: str,
    message_body: str | None = None,
) -> EmployeeTelegramDelivery | None:
    notification_type = "new_message"
    if _is_group_child_ticket(ticket):
        return None
    if message.message_type == MessageType.SYSTEM.value:
        return None
    if message.agent_visible is False:
        return None
    if message.replaced_message_id:
        return None
    if message.author_user_id == recipient_user_id:
        return None
    recipient = db.get(User, recipient_user_id)
    if not _is_business_recipient(recipient):
        return None
    preference = _preference_for_user(db, recipient_user_id)
    if not _personal_switch_enabled(preference, notification_type):
        return None
    account = _approved_account_for_user(db, recipient_user_id)
    if account is None:
        return None
    effective = _effective_template(db, notification_type=notification_type, preference=preference)
    rendered = _render_tags(
        db,
        notification_type=notification_type,
        template=effective.template,
        account=account,
        ticket=ticket,
        message=message,
        message_body=message_body,
        html_enabled=effective.html_enabled,
    )
    text_value, parse_mode = _render_telegram_text(effective, rendered)
    return EmployeeTelegramDelivery(
        event_kind=notification_type,
        delivery_kind=notification_type,
        user_id=recipient_user_id,
        telegram_chat_id=str(account.telegram_chat_id),
        text=text_value,
        parse_mode=parse_mode,
        disable_link_preview=effective.disable_link_preview,
        send_silently=effective.send_silently,
    )


def resolve_new_message_telegram_delivery(
    db: Session,
    *,
    ticket_id: str,
    message_id: str,
    recipient_user_id: str,
) -> EmployeeTelegramDelivery | None:
    notification_type = "new_message"
    if not _global_enabled(db, notification_type):
        return None
    ticket = db.get(Ticket, ticket_id)
    message = db.get(TicketMessage, message_id)
    if ticket is None or message is None:
        return None
    return _resolve_new_message_delivery(
        db,
        ticket=ticket,
        message=message,
        recipient_user_id=recipient_user_id,
    )


def resolve_media_group_new_message_telegram_delivery(
    db: Session,
    *,
    ticket_id: str,
    media_group_id: str,
) -> EmployeeTelegramDelivery | None:
    notification_type = "new_message"
    if not _global_enabled(db, notification_type):
        return None
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or not ticket.assignee_id:
        return None
    rows = _load_media_group_rows(db, ticket_id=ticket_id, media_group_id=media_group_id)
    if not rows:
        return None
    message = rows[0][1]
    return _resolve_new_message_delivery(
        db,
        ticket=ticket,
        message=message,
        recipient_user_id=ticket.assignee_id,
        message_body=_media_group_message_body(rows),
    )


def _send_delivery(delivery: EmployeeTelegramDelivery) -> None:
    send_message(
        delivery.telegram_chat_id,
        delivery.text,
        parse_mode=delivery.parse_mode,
        bot_token=settings.employee_telegram_bot_token,
        disable_web_page_preview=delivery.disable_link_preview,
        disable_notification=delivery.send_silently,
    )


def _send_internal_task_completion_delivery(delivery: InternalTaskCompletionTelegramDelivery) -> None:
    send_message(
        delivery.telegram_chat_id,
        delivery.text,
        bot_token=settings.employee_telegram_bot_token,
    )


def _media_group_attachment_count(
    db: Session,
    *,
    ticket_id: str,
    media_group_id: str,
) -> int:
    return len(_load_media_group_rows(db, ticket_id=ticket_id, media_group_id=media_group_id))


def _cleanup_media_group_debounce(key: tuple[str, str], generation: int) -> None:
    with _MEDIA_GROUP_DEBOUNCE_LOCK:
        state = _MEDIA_GROUP_DEBOUNCE.get(key)
        if state is not None and state.generation == generation:
            _MEDIA_GROUP_DEBOUNCE.pop(key, None)


def _is_current_media_group_generation(key: tuple[str, str], generation: int) -> bool:
    with _MEDIA_GROUP_DEBOUNCE_LOCK:
        state = _MEDIA_GROUP_DEBOUNCE.get(key)
        return state is not None and state.generation == generation


def _send_debounced_media_group_notification(ticket_id: str, media_group_id: str, generation: int) -> None:
    key = (ticket_id, media_group_id)
    if not _is_current_media_group_generation(key, generation):
        return
    try:
        with SessionLocal() as db:
            delivery = resolve_media_group_new_message_telegram_delivery(
                db,
                ticket_id=ticket_id,
                media_group_id=media_group_id,
            )
            if delivery is not None and _is_current_media_group_generation(key, generation):
                _send_delivery(delivery)
    except Exception:
        logger.exception("Failed to send employee media group message notification")
    finally:
        _cleanup_media_group_debounce(key, generation)


def start_media_group_notification_debounce(*, ticket_id: str, media_group_id: str) -> None:
    normalized_group_id = str(media_group_id or "").strip()
    if not ticket_id or not normalized_group_id:
        return
    with SessionLocal() as db:
        attachment_count = _media_group_attachment_count(
            db,
            ticket_id=ticket_id,
            media_group_id=normalized_group_id,
        )
    delay_seconds = 0.0 if attachment_count >= TELEGRAM_MEDIA_GROUP_MAX_ATTACHMENTS else EMPLOYEE_MEDIA_GROUP_DEBOUNCE_SECONDS
    key = (ticket_id, normalized_group_id)
    with _MEDIA_GROUP_DEBOUNCE_LOCK:
        previous = _MEDIA_GROUP_DEBOUNCE.get(key)
        generation = (previous.generation + 1) if previous is not None else 1
        if previous is not None:
            previous.timer.cancel()
        timer = _MEDIA_GROUP_TIMER_FACTORY(
            delay_seconds,
            _send_debounced_media_group_notification,
            args=(ticket_id, normalized_group_id, generation),
        )
        timer.daemon = True
        _MEDIA_GROUP_DEBOUNCE[key] = _MediaGroupDebounceState(generation=generation, timer=timer)
    timer.start()


@event.listens_for(Session, "after_commit")
def _send_scheduled_employee_notifications(session: Session) -> None:
    assignment_pending = session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, [])
    message_pending = session.info.pop(_PENDING_MESSAGE_NOTIFICATIONS_KEY, [])
    media_group_pending = session.info.pop(_PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY, [])
    internal_task_completion_pending = session.info.pop(_PENDING_INTERNAL_TASK_COMPLETION_NOTIFICATIONS_KEY, [])
    if not assignment_pending and not message_pending and not media_group_pending and not internal_task_completion_pending:
        return
    if not settings.employee_telegram_bot_token:
        return

    for item in assignment_pending:
        try:
            with SessionLocal() as db:
                event_payload = AssignmentNotificationEvent(
                    kind=item["event_kind"],
                    old_assignee_id=item.get("old_assignee_id"),
                    new_assignee_id=item.get("new_assignee_id"),
                    old_is_special=bool(item.get("old_is_special")),
                    new_is_special=bool(item.get("new_is_special")),
                    actor_user_id=item.get("actor_user_id"),
                    deliveries=tuple(
                        AssignmentNotificationDelivery(kind=delivery["kind"], user_id=delivery["user_id"])
                        for delivery in (item.get("deliveries") or [])
                    ),
                )
                for delivery in resolve_assignment_telegram_deliveries(
                    db,
                    event=event_payload,
                    ticket_id=item.get("ticket_id"),
                    event_at=item.get("event_at"),
                ):
                    _send_delivery(delivery)
        except Exception:
            logger.exception("Failed to send employee assignment notification")

    for item in message_pending:
        try:
            with SessionLocal() as db:
                delivery = resolve_new_message_telegram_delivery(
                    db,
                    ticket_id=item["ticket_id"],
                    message_id=item["message_id"],
                    recipient_user_id=item["recipient_user_id"],
                )
                if delivery is not None:
                    _send_delivery(delivery)
        except Exception:
            logger.exception("Failed to send employee message notification")

    for item in media_group_pending:
        try:
            start_media_group_notification_debounce(
                ticket_id=item["ticket_id"],
                media_group_id=item["media_group_id"],
            )
        except Exception:
            logger.exception("Failed to debounce employee media group message notification")

    for item in internal_task_completion_pending:
        try:
            with SessionLocal() as db:
                delivery = resolve_internal_task_completion_telegram_delivery(
                    db,
                    ticket_id=item["ticket_id"],
                    final_message_id=item["final_message_id"],
                )
                if delivery is not None:
                    _send_internal_task_completion_delivery(delivery)
        except Exception:
            logger.exception("Failed to send internal task completion notification")


@event.listens_for(Session, "after_rollback")
def _clear_scheduled_employee_notifications(session: Session) -> None:
    session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_MESSAGE_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_INTERNAL_TASK_COMPLETION_NOTIFICATIONS_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _clear_scheduled_employee_notifications_after_soft_rollback(session: Session, previous_transaction) -> None:
    session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_MESSAGE_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_MEDIA_GROUP_NOTIFICATIONS_KEY, None)
    session.info.pop(_PENDING_INTERNAL_TASK_COMPLETION_NOTIFICATIONS_KEY, None)


def notify_user(db: Session, user_id: str, text: str) -> bool:
    if not settings.employee_telegram_bot_token:
        return False
    account = _approved_account_for_user(db, user_id)
    if account is None:
        return False
    send_message(account.telegram_chat_id, text, bot_token=settings.employee_telegram_bot_token)
    return True


def notify_users(db: Session, user_ids: list[str], text: str) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for user_id in user_ids:
        results[user_id] = notify_user(db, user_id, text)
    return results
