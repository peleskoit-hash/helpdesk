from __future__ import annotations

from datetime import datetime
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, asc, desc, exists, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..config import settings
from ..models import MessageType, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from .auto_close import clear_ticket_auto_close_timer
from .statistics_lifecycle import (
    sync_completion_after_close,
    sync_work_interval_after_close,
    sync_work_interval_after_freeze,
    sync_work_interval_after_unfreeze,
)
from .automatic_messages import SafeTelegramHtml, render_automatic_message_text
from .telegram_post_close import (
    attributable_review_agent_id_for_actor,
    finalize_review_offer_close_message,
    post_close_actions_markup,
    prepare_review_offer_cycle,
)
from .employee_telegram_notifications import schedule_internal_task_completion_notification
from .ticket_messages import persist_client_delivery_failure, record_client_delivery_failure
from .ticket_subject import resolve_ticket_subject
from .telegram_text_format import telegram_html_code, telegram_html_text
from .internal_tasks import is_internal_task_ticket
from .ticket_admin_actions import delete_preliminary_telegram_ticket, is_preliminary_telegram_ticket, delete_uncreated_normal_task, is_uncreated_normal_task
from .ticket_admin_actions import is_uncreated_telegram_group_parent_task, delete_uncreated_telegram_group_parent_task


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _http_error_code(exc: HTTPException) -> str | None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code else None


def task_has_active_attachment_uploads(db: Session, ticket: Ticket) -> bool:
    if ticket.workflow_type != TicketWorkflowType.TASK.value:
        return False
    scope = Ticket.id == ticket.id
    if ticket.task_kind == TicketTaskKind.GROUP_PARENT.value:
        scope = or_(scope, Ticket.parent_ticket_id == ticket.id)
    ticket_ids = select(Ticket.id).where(scope)
    attachments = (select(TicketAttachment.id)
        .join(TicketMessage, TicketMessage.id == TicketAttachment.message_id)
        .where(TicketAttachment.ticket_id.in_(ticket_ids), _active_message_status_filter()))
    return bool(db.scalar(select(or_(
        attachments.where(TicketAttachment.telegram_delivery_status.in_(["uploading", "sending"])).exists(),
        exists().where(TicketAttachmentPreload.ticket_id.in_(ticket_ids),
                       or_(TicketAttachmentPreload.client_id.is_(None), TicketAttachmentPreload.final_attachment_id.is_not(None)),
                       TicketAttachmentPreload.status.in_(["uploading", "sending"]),
                       or_(TicketAttachmentPreload.final_attachment_id.is_(None),
                           TicketAttachmentPreload.final_attachment_id.in_(attachments))),
    ))))


def ensure_task_uploads_finished(db: Session, ticket: Ticket) -> None:
    if ticket.status != TicketStatus.CLOSED.value and task_has_active_attachment_uploads(db, ticket):
        _error(409, "task_attachments_uploading", "Дождитесь завершения загрузки файлов перед закрытием задачи.")


def _ticket_has_telegram_contact(db: Session, ticket: Ticket) -> bool:
    return db.execute(select(TelegramContact.id).where(TelegramContact.client_id == ticket.client_id).limit(1)).first() is not None


def _ticket_telegram_contact(db: Session, ticket: Ticket) -> TelegramContact | None:
    return db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id).limit(1)).scalar_one_or_none()


def _post_close_markup_for_ticket(db: Session, ticket: Ticket, review_cycle=None, *, assume_closed: bool = False) -> dict | None:
    if ticket.channel == "web":
        return None
    return post_close_actions_markup(
        db,
        ticket=ticket,
        contact=_ticket_telegram_contact(db, ticket),
        review_cycle=review_cycle,
        assume_closed=assume_closed,
    )


def _send_optional_client_message(
    db: Session,
    ticket: Ticket,
    text: str,
    *,
    failure_note: str,
    send_to_telegram_client_fn,
    remove_keyboard: bool = False,
    reply_markup: dict | None = None,
) -> dict | None:
    if ticket.channel == "web":
        return None
    response = send_to_telegram_client_fn(
        db,
        ticket,
        text,
        required=False,
        remove_keyboard=remove_keyboard,
        reply_markup=reply_markup,
    )
    if response is None and _ticket_has_telegram_contact(db, ticket):
        record_client_delivery_failure(
            db=db,
            ticket=ticket,
            body=failure_note,
        )
    return response


def _send_required_client_attachment(
    db: Session,
    ticket: Ticket,
    attachment: TicketAttachment,
    *,
    failure_note: str,
    send_attachment_to_telegram_client_fn,
) -> None:
    if ticket.workflow_type == TicketWorkflowType.TASK.value and attachment.telegram_delivery_status == "failed":
        return
    if ticket.channel == "web":
        return
    try:
        send_attachment_to_telegram_client_fn(db, ticket, attachment, required=True)
    except HTTPException as exc:
        if _http_error_code(exc) in {"telegram_send_failed", "attachment_file_missing"}:
            persist_client_delivery_failure(
                db=db,
                ticket_id=ticket.id,
                body=failure_note,
            )
        raise


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
                _active_message_status_filter(),
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
                _active_message_status_filter(),
                TicketMessage.author_user_id == user_id,
                User.role.in_(["agent", "trainee"]),
                or_(
                    TicketMessage.body != "",
                    TicketMessage.id.in_(
                        select(TicketAttachment.message_id).where(TicketAttachment.ticket_id == ticket.id)
                    ),
                ),
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
    if (
        assignee
        and assignee.role in {"agent", "trainee"}
        and _has_client_visible_support_message_by_user(db, ticket, assignee.id)
    ):
        return assignee.id
    return None


def _mark_ticket_completed(db: Session, ticket: Ticket, actor: CurrentUser, completed_at: datetime) -> None:
    completed_by_user_id = _resolve_completed_by_user_id(db, ticket, actor)
    synced = sync_completion_after_close(
        db,
        ticket,
        completed_by_user_id=completed_by_user_id,
        completed_at=completed_at,
    )
    if not synced:
        return
    ticket.completed_by_user_id = completed_by_user_id
    ticket.completed_counted_at = completed_at


def _active_message_status_filter():
    return or_(TicketMessage.message_status.is_(None), TicketMessage.message_status != "deleted")


def first_client_message_for_ticket(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(and_(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == MessageType.CLIENT.value))
        .order_by(asc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def last_active_agent_message_for_ticket(db: Session, ticket: Ticket) -> TicketMessage | None:
    return db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                _active_message_status_filter(),
            )
        )
        .order_by(desc(TicketMessage.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _attachment_completion_label(attachment: TicketAttachment) -> str:
    source = (attachment.source or "").strip().lower()
    if source == "photo":
        return "Фото"
    if source == "video":
        return "Видео"
    mime_type = (attachment.mime_type or "").strip().lower()
    if source in {"helpdesk", "task", "upload"}:
        if mime_type.startswith("image/"):
            return "Фото"
        if mime_type.startswith("video/"):
            return "Видео"
    return (attachment.original_filename or "").strip() or "Файл"


def _agent_answer_with_attachment_list(db: Session, agent_message: TicketMessage, agent_text: str) -> str:
    attachments = db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == agent_message.id)
        .order_by(asc(TicketAttachment.created_at))
    ).scalars().all()
    if not attachments:
        return agent_text
    file_lines = [telegram_html_code(_attachment_completion_label(attachment)) for attachment in attachments if attachment.source != "sticker"]
    if not file_lines:
        return agent_text
    return f"{telegram_html_text(agent_text)}\n\nФайлы:\n" + "\n".join(file_lines)


def _format_task_completion_text(
    db: Session,
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
    client_text = client_message.body or ""
    if agent_text:
        agent_answer = _agent_answer_with_attachment_list(db, agent_message, agent_text)
        fallback_text = (
            f"По Вашей задаче \"{telegram_html_text(subject)}\" от {telegram_html_text(created_text)}\n"
            f"\"{telegram_html_text(client_text)}\"\n"
            "\nПолучен ответ:\n"
            f"{agent_answer}"
        )
        if (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.NORMAL.value:
            try:
                return render_automatic_message_text(
                    db,
                    "telegram.task.completion.with_answer",
                    fallback_text,
                    ticket=ticket,
                    client_message=client_text,
                    extra={
                        "agent_answer": SafeTelegramHtml(agent_answer),
                        "ticket_created_at": created_text,
                        "ticket_subject": subject,
                        "subject": subject,
                    },
                    escape_values_for_html=True,
                )
            except Exception:
                return fallback_text
        return fallback_text
    fallback_text = (
        f"По Вашей задаче \"{subject}\" от {created_text}\n"
        f"\"{client_text}\"\n"
        "\nПолучены файлы от сотрудника:"
    )
    if (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.NORMAL.value:
        try:
            return render_automatic_message_text(
                db,
                "telegram.task.completion.files_only",
                fallback_text,
                ticket=ticket,
                client_message=client_text,
                extra={
                    "ticket_created_at": created_text,
                    "ticket_subject": subject,
                    "subject": subject,
                },
            )
        except Exception:
            return fallback_text
    return fallback_text


def _format_task_closed_without_answer_text(
    db: Session,
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
    fallback_text = (
        "Ваша задача:\n"
        f"\"{subject}\" от {created_text}\n"
        "с текстом:\n"
        f"\"{client_text}\"\n"
        "закрыта без ответа по причине некорректных или неполных данных.\n\n"
        "Для создания новой нажмите\n"
        "/start"
    )
    if (ticket.task_kind or TicketTaskKind.NORMAL.value) != TicketTaskKind.NORMAL.value:
        return fallback_text
    try:
        return render_automatic_message_text(
            db,
            "telegram.task.closed_without_answer.client",
            fallback_text,
            ticket=ticket,
            client_message=client_text,
            extra={
                "ticket_created_at": created_text,
                "ticket_subject": subject,
                "subject": subject,
            },
        )
    except Exception:
        return fallback_text


def format_task_closed_without_answer_text(db: Session, ticket: Ticket, client_message: TicketMessage | None) -> str:
    return _format_task_closed_without_answer_text(db, ticket, client_message, subject_text=resolve_ticket_subject(db, ticket))


def format_task_post_close_text(db: Session, ticket: Ticket) -> str:
    client_message = first_client_message_for_ticket(db, ticket)
    subject_text = resolve_ticket_subject(db, ticket)
    task_kind = ticket.task_kind or TicketTaskKind.NORMAL.value
    if task_kind == TicketTaskKind.GROUP_PARENT.value and client_message is not None:
        answers, _attachments = _group_parent_children_payload(db, ticket)
        return _format_group_task_completion_text(db, ticket, client_message, answers, subject_text=subject_text)

    last_agent_message = last_active_agent_message_for_ticket(db, ticket)
    if client_message is None or last_agent_message is None:
        return _format_task_closed_without_answer_text(db, ticket, client_message, subject_text=subject_text)
    return _format_task_completion_text(db, ticket, client_message, last_agent_message, subject_text=subject_text)


def _format_group_task_completion_text(
    db: Session,
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
    client_text = client_message.body or ""
    answers_text = "\n".join(answers)
    if not answers:
        fallback_text = (
            f"По Вашей задаче \"{subject}\" от {created_text}\n"
            f"\"{client_text}\"\n\n"
            "Получены файлы от сотрудников.\n\n"
            "Для создания новой нажмите\n"
            "/start"
        )
        try:
            return render_automatic_message_text(
                db,
                "telegram.group_task.completion.files_only",
                fallback_text,
                ticket=ticket,
                client_message=client_text,
                extra={
                    "ticket_created_at": created_text,
                    "ticket_subject": subject,
                    "subject": subject,
                },
            )
        except Exception:
            return fallback_text
    fallback_text = (
        f"По Вашей задаче \"{subject}\" от {created_text}\n"
        f"\"{client_text}\"\n\n"
        "Получены ответы:\n"
        f"{answers_text}\n\n"
        "Для создания новой нажмите\n"
        "/start"
    )
    try:
        return render_automatic_message_text(
            db,
            "telegram.group_task.completion.with_answers",
            fallback_text,
            ticket=ticket,
            client_message=client_text,
            extra={
                "group_task_answers": answers_text,
                "ticket_created_at": created_text,
                "ticket_subject": subject,
                "subject": subject,
            },
        )
    except Exception:
        return fallback_text

def _group_parent_children_payload(db: Session, parent: Ticket) -> tuple[list[str], list[TicketAttachment]]:
    # Prefer parent-level aggregated agent messages (they are appended when each child
    # is closed with an agent reply). This survives child force-close/delete flows.
    parent_agent_messages = db.execute(
        select(TicketMessage)
        .where(
            and_(
                TicketMessage.ticket_id == parent.id,
                TicketMessage.message_type == MessageType.AGENT.value,
                _active_message_status_filter(),
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
        if bool(child.suppress_client_answer):
            continue
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
    skip_client_notification: bool = False,
    include_post_close_actions: bool = True,
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

    ensure_task_uploads_finished(db, parent)
    parent_client_message = first_client_message_fn(db, parent)
    if parent_client_message is not None and not skip_client_notification:
        answers, attachments = _group_parent_children_payload(db, parent)
        subject_text = resolve_ticket_subject(db, parent)
        _send_optional_client_message(
            db,
            parent,
            _format_group_task_completion_text(
                db,
                parent,
                parent_client_message,
                answers,
                subject_text=subject_text,
            ),
            failure_note="Групповая задача завершена, но уведомление о завершении не удалось отправить клиенту в Telegram.",
            send_to_telegram_client_fn=send_to_telegram_client_fn,
            remove_keyboard=not include_post_close_actions,
            reply_markup=_post_close_markup_for_ticket(db, parent, assume_closed=True) if include_post_close_actions else None,
        )
        for attachment in attachments:
            filename = attachment.original_filename or "файл"
            _send_required_client_attachment(
                db,
                parent,
                attachment,
                failure_note=f"Групповая задача завершена, но не удалось отправить клиенту вложение «{filename}».",
                send_attachment_to_telegram_client_fn=send_attachment_to_telegram_client_fn,
            )

    closed_at = datetime.utcnow()
    parent.status = TicketStatus.CLOSED.value
    parent.closed_by_user_id = closed_by_user_id
    parent.closed_at = closed_at
    parent.is_frozen = False
    parent.freeze_until = None
    sync_work_interval_after_close(db, parent, now=closed_at)
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
    if parent.channel == "web":
        return
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
            db,
            parent,
            parent_client_message,
            answers,
            subject_text=subject_text,
        ),
        required=False,
        reply_markup=_post_close_markup_for_ticket(db, parent, assume_closed=True),
    )
    for attachment in attachments:
        send_attachment_to_telegram_client_fn(db, parent, attachment, required=True)


def _send_pending_wrong_problem_hint(db: Session, ticket: Ticket, *, send_to_telegram_client_fn, add_message_fn):
    if not ticket.wrong_problem_hint:
        return
    if _send_optional_client_message(
        db,
        ticket,
        ticket.wrong_problem_hint,
        failure_note="Подсказку по неверной проблеме не удалось отправить клиенту в Telegram.",
        send_to_telegram_client_fn=send_to_telegram_client_fn,
    ):
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

    if not bool(child.suppress_client_answer):
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
                    telegram_archive_message_id=attachment.telegram_archive_message_id,
                    telegram_delivery_status=attachment.telegram_delivery_status,
                    telegram_upload_state_json={"receipt": (attachment.telegram_upload_state_json or {}).get("receipt")},
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
    skip_client_notification: bool = False,
    add_message_fn,
    first_client_message_fn,
    send_to_telegram_client_fn,
    finalize_group_parent_if_ready_fn,
) -> None:
    closed_at = datetime.utcnow()
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = actor.id
    ticket.closed_at = closed_at
    ticket.is_frozen = False
    ticket.freeze_until = None
    sync_work_interval_after_close(db, ticket, now=closed_at)
    add_message_fn(db, ticket, MessageType.SYSTEM, "Задача принудительно закрыта администратором без ответа", None, agent_visible=True)
    if not skip_client_notification and ticket.task_kind != TicketTaskKind.GROUP_CHILD.value and not is_internal_task_ticket(db, ticket):
        client_message = first_client_message_fn(db, ticket)
        subject_text = resolve_ticket_subject(db, ticket)
        _send_optional_client_message(
            db,
            ticket,
            _format_task_closed_without_answer_text(db, ticket, client_message, subject_text=subject_text),
            failure_note="Задача закрыта, но уведомление о закрытии не удалось отправить клиенту в Telegram.",
            send_to_telegram_client_fn=send_to_telegram_client_fn,
            remove_keyboard=True,
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
    _mark_ticket_completed(db, ticket, actor, closed_at)


def close_ticket_by_actor(
    db: Session,
    ticket: Ticket,
    actor: CurrentUser,
    *,
    force_without_agent_reply_for_task: bool = False,
    skip_client_notification: bool = False,
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
    is_force_close_action = is_admin_action and force_without_agent_reply_for_task
    skip_client_notification = skip_client_notification and is_force_close_action

    def _finalize_wrapper(db_session: Session, parent_ticket: Ticket, **kwargs):
        return finalize_group_parent_if_ready(
            db_session,
            parent_ticket,
            skip_client_notification=skip_client_notification,
            include_post_close_actions=not is_force_close_action,
            first_client_message_fn=first_client_message_fn,
            send_to_telegram_client_fn=send_to_telegram_client_fn,
            send_attachment_to_telegram_client_fn=send_attachment_to_telegram_client_fn,
            add_message_fn=add_message_fn,
            resolve_group_parent_client_text_fn=resolve_group_parent_client_text_fn,
            **kwargs,
        )

    if ticket.status == TicketStatus.CLOSED.value:
        return "already_closed"

    uncreated_group_parent = is_force_close_action and is_uncreated_telegram_group_parent_task(db, ticket)
    uncreated_task = is_force_close_action and (is_uncreated_normal_task(db, ticket) or uncreated_group_parent)
    if is_preliminary_telegram_ticket(db, ticket) or uncreated_task:
        from .telegram_client_close import remove_client_close_button
        from .telegram_menu_flow import _remove_menu_keyboard
        from .telegram_bot import remove_inline_keyboard

        if ticket.channel == "telegram":
            _remove_menu_keyboard(db, ticket)
            remove_client_close_button(db, ticket, remove_inline_keyboard_fn=remove_inline_keyboard)
        if not skip_client_notification:
            _send_optional_client_message(
                db, ticket, (format_task_closed_without_answer_text(db, ticket, None)
                             if uncreated_task and ticket.channel == "telegram" else ticket_closed_client_text),
                failure_note="Уведомление об отмене создания заявки не удалось отправить клиенту в Telegram.",
                send_to_telegram_client_fn=send_to_telegram_client_fn,
                remove_keyboard=True,
            )
        if uncreated_task:
            if ticket.channel == "web":
                from .web_tasks import chat_event, message_text
                chat_event(db, ticket, "cancelled", message_text(db, "telegram.ticket.close.unassigned.client", ticket))
            if uncreated_group_parent:
                delete_uncreated_telegram_group_parent_task(db, ticket)
            else:
                delete_uncreated_normal_task(db, ticket)
        else:
            delete_preliminary_telegram_ticket(db, ticket)
        return "closed"

    ensure_task_uploads_finished(db, ticket)
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
                skip_client_notification=skip_client_notification,
            )
            return "closed"
        if workflow_type == TicketWorkflowType.TICKET.value:
            review_agent_id = attributable_review_agent_id_for_actor(db, ticket, actor)
            review_cycle = prepare_review_offer_cycle(db, ticket=ticket, agent_id=review_agent_id) if ticket.channel != "web" else None
            closed_at = datetime.utcnow()
            ticket.status = TicketStatus.CLOSED.value
            ticket.closed_by_user_id = actor.id
            ticket.closed_at = closed_at
            ticket.is_frozen = False
            ticket.freeze_until = None
            sync_work_interval_after_close(db, ticket, now=closed_at)
            clear_ticket_auto_close_timer(ticket)
            add_message_fn(db, ticket, MessageType.SYSTEM, f"Заявка принудительно завершена администратором: {actor_display_name_fn(db, actor)}", None, agent_visible=True)
            reply_markup = _post_close_markup_for_ticket(db, ticket, review_cycle)
            close_response = None if skip_client_notification else _send_optional_client_message(
                db,
                ticket,
                ticket_closed_client_text,
                failure_note="Заявка закрыта, но уведомление о закрытии не удалось отправить клиенту в Telegram.",
                send_to_telegram_client_fn=send_to_telegram_client_fn,
                remove_keyboard=reply_markup is None,
                reply_markup=reply_markup,
            )
            if review_cycle is not None:
                finalize_review_offer_close_message(db, cycle=review_cycle, close_response=close_response)
            _mark_ticket_completed(db, ticket, actor, closed_at)
            return "closed"
        closed_at = datetime.utcnow()
        ticket.status = TicketStatus.CLOSED.value
        ticket.closed_by_user_id = actor.id
        ticket.closed_at = closed_at
        ticket.is_frozen = False
        ticket.freeze_until = None
        sync_work_interval_after_close(db, ticket, now=closed_at)
        add_message_fn(db, ticket, MessageType.SYSTEM, f"Автообращение принудительно завершено администратором: {actor_display_name_fn(db, actor)}", None, agent_visible=True)
        _mark_ticket_completed(db, ticket, actor, closed_at)
        return "closed"

    if last_agent_message is None:
        action_name = "задачу" if workflow_type == TicketWorkflowType.TASK.value else "заявку"
        _error(409, "close_not_allowed", f"Нельзя завершить {action_name} без ответа сотрудника")

    closed_at = datetime.utcnow()
    ticket.status = TicketStatus.CLOSED.value
    ticket.closed_by_user_id = actor.id
    ticket.closed_at = closed_at
    ticket.is_frozen = False
    ticket.freeze_until = None
    sync_work_interval_after_close(db, ticket, now=closed_at)
    if workflow_type == TicketWorkflowType.TICKET.value:
        clear_ticket_auto_close_timer(ticket)

    if workflow_type == TicketWorkflowType.TASK.value:
        suppress_task_client_delivery = is_internal_task_ticket(db, ticket)
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
                    skip_client_notification=skip_client_notification,
                )
                return "closed"
            _error(409, "task_client_message_required", "У задачи нет текста клиента")
        if ticket.task_kind == TicketTaskKind.GROUP_PARENT.value:
            answers, attachments = _group_parent_children_payload(db, ticket)
            subject_text = resolve_ticket_subject(db, ticket)
            if not suppress_task_client_delivery and not skip_client_notification:
                _send_optional_client_message(
                    db,
                    ticket,
                    _format_group_task_completion_text(
                        db,
                        ticket,
                        client_message,
                        answers,
                        subject_text=subject_text,
                    ),
                    failure_note="Групповая задача завершена, но уведомление о завершении не удалось отправить клиенту в Telegram.",
                    send_to_telegram_client_fn=send_to_telegram_client_fn,
                    remove_keyboard=is_force_close_action,
                    reply_markup=None if is_force_close_action else _post_close_markup_for_ticket(db, ticket),
                )
                for attachment in attachments:
                    filename = attachment.original_filename or "файл"
                    _send_required_client_attachment(
                        db,
                        ticket,
                        attachment,
                        failure_note=f"Групповая задача завершена, но не удалось отправить клиенту вложение «{filename}».",
                        send_attachment_to_telegram_client_fn=send_attachment_to_telegram_client_fn,
                    )
                _send_pending_wrong_problem_hint(
                    db,
                    ticket,
                    send_to_telegram_client_fn=send_to_telegram_client_fn,
                    add_message_fn=add_message_fn,
                )
            close_text = (
                f"{actor_display_name_fn(db, actor)} закрыл групповую задачу"
                if is_admin_action and not is_force_close_action
                else "Групповая задача завершена сотрудником" if not is_force_close_action else f"Групповая задача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            )
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
            close_text = (
                f"{actor_display_name_fn(db, actor)} закрыл подзадачу"
                if is_admin_action and not is_force_close_action
                else "Подзадача завершена сотрудником" if not is_force_close_action else f"Подзадача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            )
            add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        else:
            subject_text = resolve_ticket_subject(db, ticket)
            final_attachments = message_attachments_fn(db, last_agent_message.id)
            if ticket.channel == "web" and ticket.task_kind == TicketTaskKind.NORMAL.value and not suppress_task_client_delivery and not skip_client_notification:
                from .web_task_results import publish_result

                publish_result(db, ticket, last_agent_message, final_attachments)
            if suppress_task_client_delivery and not is_force_close_action:
                schedule_internal_task_completion_notification(
                    db,
                    ticket=ticket,
                    final_message=last_agent_message,
                )
            if not suppress_task_client_delivery and not skip_client_notification:
                _send_optional_client_message(
                    db,
                    ticket,
                    _format_task_completion_text(db, ticket, client_message, last_agent_message, subject_text=subject_text),
                    failure_note="Задача завершена, но уведомление о завершении не удалось отправить клиенту в Telegram.",
                    send_to_telegram_client_fn=send_to_telegram_client_fn,
                    remove_keyboard=is_force_close_action,
                    reply_markup=None if is_force_close_action else _post_close_markup_for_ticket(db, ticket),
                )
                for attachment in final_attachments:
                    filename = attachment.original_filename or "файл"
                    _send_required_client_attachment(
                        db,
                        ticket,
                        attachment,
                        failure_note=f"Не удалось отправить клиенту вложение «{filename}».",
                        send_attachment_to_telegram_client_fn=send_attachment_to_telegram_client_fn,
                    )
                _send_pending_wrong_problem_hint(
                    db,
                    ticket,
                    send_to_telegram_client_fn=send_to_telegram_client_fn,
                    add_message_fn=add_message_fn,
                )
            close_text = (
                f"{actor_display_name_fn(db, actor)} закрыл задачу"
                if is_admin_action and not is_force_close_action
                else "Задача завершена сотрудником" if not is_force_close_action else f"Задача принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
            )
            add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    elif workflow_type == TicketWorkflowType.TICKET.value:
        review_agent_id = attributable_review_agent_id_for_actor(db, ticket, actor)
        review_cycle = prepare_review_offer_cycle(db, ticket=ticket, agent_id=review_agent_id) if ticket.channel != "web" else None
        close_text = (
            f"{actor_display_name_fn(db, actor)} закрыл заявку"
            if is_admin_action and not is_force_close_action
            else "Заявка закрыта сотрудником" if not is_force_close_action else f"Заявка принудительно завершена администратором: {actor_display_name_fn(db, actor)}"
        )
        add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
        if bool(ticket.onsite_triggered):
            add_message_fn(db, ticket, MessageType.SYSTEM, onsite_close_reminder_text, None, agent_visible=True)
        reply_markup = _post_close_markup_for_ticket(db, ticket, review_cycle)
        close_response = None if skip_client_notification else _send_optional_client_message(
            db,
            ticket,
            ticket_closed_client_text,
            failure_note="Заявка закрыта, но уведомление о закрытии не удалось отправить клиенту в Telegram.",
            send_to_telegram_client_fn=send_to_telegram_client_fn,
            remove_keyboard=reply_markup is None,
            reply_markup=reply_markup,
        )
        if review_cycle is not None:
            finalize_review_offer_close_message(db, cycle=review_cycle, close_response=close_response)
    else:
        close_text = "Автообращение завершено системой" if not is_force_close_action else f"Автообращение принудительно завершено администратором: {actor_display_name_fn(db, actor)}"
        add_message_fn(db, ticket, MessageType.SYSTEM, close_text, None, agent_visible=True)
    _mark_ticket_completed(db, ticket, actor, closed_at)
    return "closed"


def freeze_ticket(
    db: Session,
    ticket: Ticket,
    *,
    freeze_until,
    add_message_fn,
) -> None:
    was_frozen = bool(ticket.is_frozen)
    frozen_at = datetime.utcnow()
    ticket.is_frozen = True
    ticket.freeze_until = freeze_until
    if not was_frozen:
        sync_work_interval_after_freeze(db, ticket, now=frozen_at)
    clear_ticket_auto_close_timer(ticket)
    add_message_fn(db, ticket, MessageType.SYSTEM, "Заявка заморожена", None)


def unfreeze_ticket(
    db: Session,
    ticket: Ticket,
    *,
    add_message_fn,
    refresh_ticket_auto_close_timer_fn,
) -> None:
    was_frozen = bool(ticket.is_frozen)
    unfrozen_at = datetime.utcnow()
    ticket.is_frozen = False
    ticket.freeze_until = None
    if was_frozen:
        sync_work_interval_after_unfreeze(db, ticket, now=unfrozen_at)
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
