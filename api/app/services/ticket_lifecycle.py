from __future__ import annotations

from datetime import datetime
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, asc, desc, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..config import settings
from ..models import MessageType, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from .auto_close import clear_ticket_auto_close_timer
from .ticket_subject import resolve_ticket_subject


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def refresh_ticket_auto_close(
    db: Session,
    ticket: Ticket,
    *,
    restart_from_now: bool = False,
    refresh_ticket_auto_close_timer_fn,
):
    refresh_ticket_auto_close_timer_fn(db, ticket, restart_from_now=restart_from_now)


def unfreeze_ticket_on_message(ticket: Ticket) -> None:
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TICKET.value:
        return
    if ticket.status == TicketStatus.CLOSED.value:
        return
    if not ticket.is_frozen:
        return
    ticket.is_frozen = False
    ticket.freeze_until = None
    ticket.updated_at = datetime.utcnow()


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
    if actor.role != "admin":
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


def _format_task_completion_text(
    ticket: Ticket,
    client_message: TicketMessage,
    agent_message: TicketMessage,
    *,
    subject_text: str | None = None,
) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    subject = subject_text or ticket.subject
    agent_text = (agent_message.body or "").strip()
    if agent_text:
        return (
            f"По Вашей задаче \"{subject}\" от {created_text}\n"
            f"\"{client_message.body}\"\n"
            "\nПолучен ответ:\n"
            f"{agent_text}"
        )
    return (
        f"По Вашей задаче \"{subject}\" от {created_text}\n"
        f"\"{client_message.body}\"\n"
        "\nПолучены файлы от сотрудника:"
    )


def _format_task_closed_without_answer_text(
    ticket: Ticket,
    client_message: TicketMessage | None,
    *,
    subject_text: str | None = None,
) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    subject = subject_text or ticket.subject
    client_text = (client_message.body or "").strip() if client_message is not None else ""
    if not client_text:
        client_text = "(без текста)"
    return (
        "Ваша задача:\n"
        f"\"{subject}\" от {created_text}\n"
        "с текстом:\n"
        f"\"{client_text}\"\n"
        "закрыта без ответа по причине некорректных или неполных данных.\n\n"
        "Для создания новой нажмите\n"
        "/start"
    )


def format_task_closed_without_answer_text(db: Session, ticket: Ticket, client_message: TicketMessage | None) -> str:
    return _format_task_closed_without_answer_text(ticket, client_message, subject_text=resolve_ticket_subject(db, ticket))


def _format_group_task_completion_text(
    ticket: Ticket,
    client_message: TicketMessage,
    answers: list[str],
    *,
    subject_text: str | None = None,
) -> str:
    created_at = ticket.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local_created_at = created_at.astimezone(ZoneInfo(settings.app_timezone))
    created_text = local_created_at.strftime("%d.%m.%Y, %H:%M")
    subject = subject_text or ticket.subject
    if not answers:
        return (
            f"По Вашей задаче \"{subject}\" от {created_text}\n"
            f"\"{client_message.body}\"\n\n"
            "Получены файлы от сотрудников.\n\n"
            "Для создания новой нажмите\n"
            "/start"
        )
    return (
        f"По Вашей задаче \"{subject}\" от {created_text}\n"
        f"\"{client_message.body}\"\n\n"
        "Получены ответы:\n"
        f"{chr(10).join(answers)}\n\n"
        "Для создания новой нажмите\n"
        "/start"
    )


def _group_parent_children_payload(db: Session, parent: Ticket) -> tuple[list[str], list[TicketAttachment]]:
    # Prefer parent-level aggregated agent messages (they are appended when each child
    # is closed with an agent reply). This survives child force-close/delete flows.
    parent_agent_messages = db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == parent.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                TicketMessage.message_status != "deleted",
            )
        )
        .order_by(asc(TicketMessage.created_at))
    ).scalars().all()
    if parent_agent_messages:
        answers = [(m.body or "").strip() for m in parent_agent_messages if (m.body or "").strip()]
        parent_message_ids = [m.id for m in parent_agent_messages]
        attachments: list[TicketAttachment] = []
        if parent_message_ids:
            attachments = db.execute(
                select(TicketAttachment)
                .where(TicketAttachment.message_id.in_(parent_message_ids))
                .order_by(asc(TicketAttachment.created_at))
            ).scalars().all()
        return answers, attachments

    # Fallback for legacy data where parent-level aggregation was not present.
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


def finalize_group_parent_if_ready(
    db: Session,
    parent: Ticket,
    *,
    closed_by_user_id: str | None = None,
    system_note: str = "Групповая задача завершена системой",
    first_client_message_fn,
    send_to_telegram_client_fn,
    send_attachment_to_telegram_client_fn,
    add_message_fn,
    resolve_group_parent_client_text_fn=None,
) -> bool:
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return False
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return False
    if parent.status == TicketStatus.CLOSED.value:
        return False

    # SessionLocal uses autoflush=False, so ensure current child status updates
    # are pushed before checking whether any open children remain.
    db.flush()

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

    parent_client_message = first_client_message_fn(db, parent)
    if parent_client_message is not None:
        answers, attachments = _group_parent_children_payload(db, parent)
        subject_text = resolve_ticket_subject(db, parent)
        send_to_telegram_client_fn(
            db,
            parent,
            _format_group_task_completion_text(
                parent,
                parent_client_message,
                answers,
                subject_text=subject_text,
            ),
            required=False,
        )
        for attachment in attachments:
            send_attachment_to_telegram_client_fn(db, parent, attachment, required=True)

    parent.status = TicketStatus.CLOSED.value
    parent.closed_by_user_id = closed_by_user_id
    parent.closed_at = datetime.utcnow()
    parent.is_frozen = False
    parent.freeze_until = None
    add_message_fn(db, parent, MessageType.SYSTEM, system_note, None, agent_visible=True)
    return True


def notify_group_parent_completion_before_delete(
    db: Session,
    parent: Ticket,
    *,
    first_client_message_fn,
    send_to_telegram_client_fn,
    send_attachment_to_telegram_client_fn,
    resolve_group_parent_client_text_fn=None,
) -> None:
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return
    parent_client_message = first_client_message_fn(db, parent)
    if parent_client_message is None:
        return
    answers, attachments = _group_parent_children_payload(db, parent)
    subject_text = resolve_ticket_subject(db, parent)
    send_to_telegram_client_fn(
        db,
        parent,
        _format_group_task_completion_text(
            parent,
            parent_client_message,
            answers,
            subject_text=subject_text,
        ),
        required=False,
    )
    for attachment in attachments:
        send_attachment_to_telegram_client_fn(db, parent, attachment, required=True)


def _send_pending_wrong_problem_hint(db: Session, ticket: Ticket, *, send_to_telegram_client_fn, add_message_fn):
    if not ticket.wrong_problem_hint:
        return
    if send_to_telegram_client_fn(db, ticket, ticket.wrong_problem_hint, required=False):
        add_message_fn(db, ticket, MessageType.SYSTEM, "Подсказка по неверной проблеме отправлена клиенту", None)
        ticket.wrong_problem_hint = None


def _close_group_child_if_ready(
    db: Session,
    child: Ticket,
    last_agent_message: TicketMessage,
    user: CurrentUser,
    *,
    add_message_fn,
    finalize_group_parent_if_ready_fn,
) -> bool:
    if child.task_kind != TicketTaskKind.GROUP_CHILD.value or not child.parent_ticket_id:
        return False

    parent = db.get(Ticket, child.parent_ticket_id)
    if parent is None:
        return False
    if (parent.workflow_type or TicketWorkflowType.TASK.value) != TicketWorkflowType.TASK.value:
        return False
    if (parent.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.GROUP_PARENT.value:
        return False

    parent_agent_message = add_message_fn(db, parent, MessageType.AGENT, last_agent_message.body or "", user.id)
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

    finalize_group_parent_if_ready_fn(
        db,
        parent,
        closed_by_user_id=user.id,
        system_note="Групповая задача завершена системой",
    )
    return True


def _close_task_without_reply(
    db: Session,
    ticket: Ticket,
    actor: CurrentUser,
    *,
    add_message_fn,
    first_client_message_fn,
    send_to_telegram_client_fn,
    finalize_group_parent_if_ready_fn,
) -> None:
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = actor.id
    ticket.closed_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    add_message_fn(db, ticket, MessageType.SYSTEM, "Задача принудительно закрыта администратором без ответа", None, agent_visible=True)
    if ticket.task_kind != TicketTaskKind.GROUP_CHILD.value:
        client_message = first_client_message_fn(db, ticket)
        subject_text = resolve_ticket_subject(db, ticket)
        send_to_telegram_client_fn(
            db,
            ticket,
            _format_task_closed_without_answer_text(ticket, client_message, subject_text=subject_text),
            required=False,
        )
    if ticket.task_kind == TicketTaskKind.GROUP_CHILD.value and ticket.parent_ticket_id:
        parent = db.get(Ticket, ticket.parent_ticket_id)
        if parent is not None:
            finalize_group_parent_if_ready_fn(
                db,
                parent,
                closed_by_user_id=actor.id,
                system_note="Групповая задача завершена системой (возможен неполный ответ)",
            )
    _mark_ticket_completed_once(db, ticket, actor)


def close_ticket_by_actor(
    db: Session,
    ticket: Ticket,
    actor: CurrentUser,
    *,
    force_without_agent_reply_for_task: bool = False,
    is_admin_fn,
    get_or_create_client_profile_fn,
    last_agent_message_fn,
    first_client_message_fn,
    send_to_telegram_client_fn,
    actor_display_name_fn,
    add_message_fn,
    message_attachments_fn,
    send_attachment_to_telegram_client_fn,
    onsite_close_reminder_text: str,
    ticket_closed_client_text: str,
    resolve_group_parent_client_text_fn=None,
) -> str:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    is_admin_action = is_admin_fn(actor)

    def _finalize_wrapper(db_session: Session, parent_ticket: Ticket, **kwargs):
        return finalize_group_parent_if_ready(
            db_session,
            parent_ticket,
            first_client_message_fn=first_client_message_fn,
            send_to_telegram_client_fn=send_to_telegram_client_fn,
            send_attachment_to_telegram_client_fn=send_attachment_to_telegram_client_fn,
            add_message_fn=add_message_fn,
            resolve_group_parent_client_text_fn=resolve_group_parent_client_text_fn,
            **kwargs,
        )

    if ticket.status == TicketStatus.CLOSED.value:
        return "already_closed"

    if ticket.subject == "Первое подключение к боту":
        profile = get_or_create_client_profile_fn(db, ticket.client_id)
        if not profile.is_verified and not is_admin_action:
            _error(409, "first_connection_not_verified", "Нельзя закрыть первое подключение, пока карточка клиента не верифицирована")

    last_agent_message = last_agent_message_fn(db, ticket)
    if last_agent_message is None and is_admin_action and force_without_agent_reply_for_task:
        if workflow_type == TicketWorkflowType.TASK.value:
            _close_task_without_reply(
                db,
                ticket,
                actor,
                add_message_fn=add_message_fn,
                first_client_message_fn=first_client_message_fn,
                send_to_telegram_client_fn=send_to_telegram_client_fn,
                finalize_group_parent_if_ready_fn=_finalize_wrapper,
            )
            return "closed"
        if workflow_type == TicketWorkflowType.TICKET.value:
            ticket.status = TicketStatus.CLOSED.value
            ticket.closed_by_user_id = actor.id
            ticket.closed_at = datetime.utcnow()
            ticket.is_frozen = False
            ticket.freeze_until = None
            add_message_fn(db, ticket, MessageType.SYSTEM, f"Заявка принудительно завершена администратором: {actor_display_name_fn(db, actor)}", None, agent_visible=True)
            send_to_telegram_client_fn(
                db,
                ticket,
                ticket_closed_client_text,
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
        add_message_fn(db, ticket, MessageType.SYSTEM, f"Автообращение принудительно завершено администратором: {actor_display_name_fn(db, actor)}", None, agent_visible=True)
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
        client_message = first_client_message_fn(db, ticket)
        if client_message is None:
            if force_without_agent_reply_for_task:
                _close_task_without_reply(
                    db,
                    ticket,
                    actor,
                    add_message_fn=add_message_fn,
                    first_client_message_fn=first_client_message_fn,
                    send_to_telegram_client_fn=send_to_telegram_client_fn,
                    finalize_group_parent_if_ready_fn=_finalize_wrapper,
                )
                return "closed"
            _error(409, "task_client_message_required", "У задачи нет текста клиента")
        if ticket.task_kind == TicketTaskKind.GROUP_PARENT.value:
            answers, attachments = _group_parent_children_payload(db, ticket)
            subject_text = resolve_ticket_subject(db, ticket)
            send_to_telegram_client_fn(
                db,
                ticket,
                _format_group_task_completion_text(
                    ticket,
                    client_message,
                    answers,
                    subject_text=subject_text,
                ),
                required=False,
            )
            for attachment in attachments:
                send_attachment_to_telegram_client_fn(db, ticket, attachment, required=True)
            _send_pending_wrong_problem_hint(
                db,
                ticket,
                send_to_telegram_client_fn=send_to_telegram_client_fn,
                add_message_fn=add_message_fn,
            )
            close_text = "Групповая задача завершена сотрудником" if not is_admin_action else f"Групповая задача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        elif ticket.task_kind == TicketTaskKind.GROUP_CHILD.value:
            _close_group_child_if_ready(
                db,
                ticket,
                last_agent_message,
                actor,
                add_message_fn=add_message_fn,
                finalize_group_parent_if_ready_fn=_finalize_wrapper,
            )
            close_text = "Подзадача завершена сотрудником" if not is_admin_action else f"Подзадача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        else:
            subject_text = resolve_ticket_subject(db, ticket)
            send_to_telegram_client_fn(
                db,
                ticket,
                _format_task_completion_text(ticket, client_message, last_agent_message, subject_text=subject_text),
                required=False,
            )
            for attachment in message_attachments_fn(db, last_agent_message.id):
                send_attachment_to_telegram_client_fn(db, ticket, attachment, required=True)
            _send_pending_wrong_problem_hint(
                db,
                ticket,
                send_to_telegram_client_fn=send_to_telegram_client_fn,
                add_message_fn=add_message_fn,
            )
            close_text = "Задача завершена сотрудником" if not is_admin_action else f"Задача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    elif workflow_type == TicketWorkflowType.TICKET.value:
        close_text = "Заявка закрыта сотрудником" if not is_admin_action else f"Заявка принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
        add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        if bool(ticket.onsite_triggered):
            add_message_fn(db, ticket, MessageType.SYSTEM, onsite_close_reminder_text, None, agent_visible=True)
        send_to_telegram_client_fn(
            db,
            ticket,
            ticket_closed_client_text,
            required=False,
            remove_keyboard=True,
        )
    else:
        close_text = "Автообращение завершено системой" if not is_admin_action else f"Автообращение принудительно завершено администратором: {actor_display_name_fn(db, actor)}"
        add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    _mark_ticket_completed_once(db, ticket, actor)
    return "closed"


def freeze_ticket(
    db: Session,
    ticket: Ticket,
    *,
    freeze_until,
    add_message_fn,
) -> None:
    ticket.is_frozen = True
    ticket.freeze_until = freeze_until
    clear_ticket_auto_close_timer(ticket)
    add_message_fn(db, ticket, MessageType.SYSTEM, "Заявка заморожена", None)


def unfreeze_ticket(
    db: Session,
    ticket: Ticket,
    *,
    add_message_fn,
    refresh_ticket_auto_close_timer_fn,
) -> None:
    ticket.is_frozen = False
    ticket.freeze_until = None
    add_message_fn(db, ticket, MessageType.SYSTEM, "Заявка разморожена", None)
    refresh_ticket_auto_close(db, ticket, restart_from_now=True, refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer_fn)


def update_ticket_auto_close(
    db: Session,
    ticket: Ticket,
    *,
    auto_close_disabled: bool,
    refresh_ticket_auto_close_timer_fn,
) -> None:
    ticket.auto_close_disabled = bool(auto_close_disabled)
    if ticket.auto_close_disabled:
        clear_ticket_auto_close_timer(ticket)
    else:
        refresh_ticket_auto_close(db, ticket, restart_from_now=True, refresh_ticket_auto_close_timer_fn=refresh_ticket_auto_close_timer_fn)
    ticket.updated_at = datetime.utcnow()
