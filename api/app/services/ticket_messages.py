import logging
from datetime import datetime
from typing import Callable
import uuid

from fastapi import HTTPException
from fastapi import Request, UploadFile
from sqlalchemy import asc, func, select, update
from sqlalchemy.orm import Session

from ..auth import CurrentUser, can_comment, can_edit_messages, can_reply
from ..models import MessageType, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketStatus, TicketWorkflowType, User
from ..schemas import TextPayload
from ..services.employee_telegram_notifications import schedule_new_message_notification
from ..services.statistics_lifecycle import set_user_availability_status, sync_work_interval_after_unfreeze
from ..services.telegram_bot import TelegramApiError, delete_message, edit_message_caption, edit_message_text
from ..services.telegram_text_format import strip_telegram_html


TELEGRAM_CAPTION_LIMIT = 1024
QUICK_ACTION_CLARIFICATION_TEXT = "Могли бы Вы, пожалуйста, уточнить, что именно произошло?"
QUICK_ACTION_ONSITE_TEXT = "Иду к Вам"
QUICK_ACTION_TEXTS = {
    "clarification": QUICK_ACTION_CLARIFICATION_TEXT,
    "onsite": QUICK_ACTION_ONSITE_TEXT,
}
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _is_onsite_phrase(text: str | None) -> bool:
    normalized = str(text or "").strip().lower()
    if normalized.endswith("."):
        normalized = normalized[:-1].strip()
    return normalized == "иду к вам"


def _quick_action_used(ticket: Ticket, action: str) -> bool:
    return bool((ticket.quick_action_state_json or {}).get(action))


def _mark_quick_action_used(ticket: Ticket, action: str) -> None:
    state = dict(ticket.quick_action_state_json or {})
    state[action] = True
    ticket.quick_action_state_json = state


def _lock_ticket_for_quick_action(db: Session, ticket: Ticket) -> Ticket:
    return db.execute(
        select(Ticket)
        .where(Ticket.id == ticket.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def _prepare_quick_action(ticket: Ticket, action: str | None, text: str | None, *, is_internal_comment: bool) -> str | None:
    if not action:
        return text
    if action not in QUICK_ACTION_TEXTS:
        _error(400, "quick_action_unknown", "Неизвестное быстрое действие")
    if is_internal_comment:
        _error(400, "quick_action_reply_only", "Быстрое действие доступно только для ответа клиенту")
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TICKET.value:
        _error(409, "quick_action_ticket_only", "Быстрое действие доступно только для обычной заявки")
    if _quick_action_used(ticket, action):
        _error(409, "quick_action_already_used", "Быстрое действие уже использовано")
    if not _quick_action_used(ticket, "greeting"):
        _error(409, "quick_action_greeting_required", "Сначала отправьте приветствие")
    expected_text = QUICK_ACTION_TEXTS[action]
    if str(text or "").strip() != expected_text:
        _error(400, "quick_action_text_mismatch", "Текст быстрого действия не совпадает с серверным шаблоном")
    return expected_text


def _http_error_code(exc: HTTPException) -> str | None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code else None


def _is_caption_compatible_text(text: str | None) -> bool:
    if not text:
        return False
    return len(strip_telegram_html(text).strip()) <= TELEGRAM_CAPTION_LIMIT


def _message_uses_single_attachment_caption(message: TicketMessage, attachments: list[TicketAttachment]) -> bool:
    if len(attachments) != 1 or not message.telegram_message_id:
        return False
    attachment_message_id = str(attachments[0].telegram_message_id or "").strip()
    return bool(attachment_message_id) and attachment_message_id == str(message.telegram_message_id).strip()


def _attachment_from_preload(
    *,
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    preload: TicketAttachmentPreload,
    delivery_status: str | None,
) -> TicketAttachment:
    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        telegram_file_id=preload.telegram_file_id,
        telegram_file_unique_id=preload.telegram_file_unique_id,
        telegram_file_path=None,
        telegram_delivery_status=delivery_status,
        telegram_message_id=None,
        original_filename=preload.original_filename,
        mime_type=preload.mime_type,
        file_size=preload.file_size,
        local_path=preload.local_path,
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    db.flush()
    preload.final_attachment_id = attachment.id
    return attachment


def add_message(
    *,
    db: Session,
    ticket: Ticket,
    message_type: MessageType,
    body: str,
    author_user_id: str | None,
    agent_visible: bool | None = None,
    telegram_message_id: str | None = None,
    message_status: str = "normal",
    status_note: str | None = None,
    replaced_message_id: str | None = None,
) -> TicketMessage:
    if agent_visible is None:
        agent_visible = message_type != MessageType.SYSTEM
    msg = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=message_type.value,
        author_user_id=author_user_id,
        body=body,
        agent_visible=agent_visible,
        telegram_message_id=telegram_message_id,
        message_status=message_status,
        status_note=status_note,
        replaced_message_id=replaced_message_id,
    )
    db.add(msg)
    ticket.updated_at = datetime.utcnow()
    if message_type in {MessageType.CLIENT, MessageType.AGENT, MessageType.INTERNAL}:
        schedule_new_message_notification(db, ticket=ticket, message=msg)
    return msg


def clear_ticket_draft(db: Session, ticket: Ticket) -> None:
    db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id)
        .values(
            draft_text="",
            draft_mode="reply",
            draft_revision=func.coalesce(Ticket.draft_revision, 0) + 1,
            updated_at=datetime.utcnow(),
        )
    )
    db.flush()
    db.refresh(ticket)


def record_client_delivery_failure(
    *,
    db: Session,
    ticket: Ticket,
    body: str,
) -> TicketMessage:
    return add_message(
        db=db,
        ticket=ticket,
        message_type=MessageType.SYSTEM,
        body=body,
        author_user_id=None,
        agent_visible=True,
    )


def persist_client_delivery_failure(
    *,
    db: Session,
    ticket_id: str,
    body: str,
) -> None:
    db.rollback()
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        return
    record_client_delivery_failure(
        db=db,
        ticket=ticket,
        body=body,
    )
    db.commit()


async def reply_to_ticket(
    *,
    request: Request,
    db: Session,
    user: CurrentUser,
    ticket: Ticket,
    message_type: MessageType = MessageType.AGENT,
    send_to_telegram_client: Callable[..., dict | None],
    telegram_message_id_from_response: Callable[[dict | None], str | None],
    save_upload_attachment: Callable[[Session, Ticket, TicketMessage, UploadFile], TicketAttachment],
    send_attachment_to_telegram_client: Callable[..., dict | None],
    claim_attachment_preload: Callable[[Session, str, str, str], TicketAttachmentPreload | None],
    schedule_attachment_delivery: Callable[..., None],
    unfreeze_ticket_on_message: Callable[[Ticket], None],
    remove_client_close_button: Callable[[Session, Ticket], None],
    refresh_ticket_auto_close: Callable[[Session, Ticket, bool], None],
) -> dict:
    is_internal_comment = message_type == MessageType.INTERNAL
    if is_internal_comment:
        if not can_comment(user):
            _error(403, "readonly_role", "Нет прав на внутренний комментарий")
    elif not can_reply(user):
        _error(403, "readonly_role", "Нет прав на отправку ответа")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    content_type = (request.headers.get("content-type") or "").lower()
    text: str | None = None
    quote_message_id: str | None = None
    preloaded_attachment_ids: list[str] = []
    single_attachment_marker = False
    uploaded_files: list[UploadFile] = []
    quick_action: str | None = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        text = (form.get("text") or "").strip() or None
        quote_message_id = (form.get("quote_message_id") or "").strip() or None
        quick_action = (form.get("quick_action") or "").strip() or None
        for value in form.getlist("preloaded_attachment_id"):
            normalized_preload_id = str(value or "").strip()
            if normalized_preload_id:
                preloaded_attachment_ids.append(normalized_preload_id)
        single_attachment_marker = str(form.get("single_attachment") or "").strip().lower() == "true"
        for value in form.getlist("files"):
            if hasattr(value, "filename") and hasattr(value, "file"):
                uploaded_files.append(value)
        if preloaded_attachment_ids and uploaded_files and (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value:
            _error(400, "reply_payload_conflict", "Нельзя одновременно передавать preload и raw-файл")
        if not text and not uploaded_files and not preloaded_attachment_ids:
            _error(400, "reply_payload_empty", "Нужно передать текст или хотя бы один файл")
    else:
        payload_raw = await request.json()
        payload = TextPayload.model_validate(payload_raw)
        text = payload.text.strip()
        quote_message_id = payload.quote_message_id
        quick_action = (payload.quick_action or "").strip() or None
        if not text:
            _error(400, "reply_payload_empty", "Текст сообщения не может быть пустым")

    if quick_action:
        ticket = _lock_ticket_for_quick_action(db, ticket)
    text = _prepare_quick_action(ticket, quick_action, text, is_internal_comment=is_internal_comment)
    if quick_action and (uploaded_files or preloaded_attachment_ids or quote_message_id):
        _error(400, "quick_action_plain_text_only", "Быстрое действие отправляется только обычным текстом")
    if quick_action:
        _mark_quick_action_used(ticket, quick_action)

    quote_telegram_message_id = None
    if quote_message_id:
        quoted = db.get(TicketMessage, quote_message_id)
        if quoted is not None and quoted.ticket_id == ticket.id:
            quote_telegram_message_id = quoted.telegram_message_id

    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    is_plain_ticket = workflow_type == TicketWorkflowType.TICKET.value
    has_attachments = bool(uploaded_files) or bool(preloaded_attachment_ids)
    prefer_caption = single_attachment_marker and _is_caption_compatible_text(text)
    if is_plain_ticket and not is_internal_comment and len(preloaded_attachment_ids) > 1:
        _error(400, "reply_payload_too_many_preloads", "Для обычной заявки preload отправляется по одному файлу")

    if is_internal_comment and preloaded_attachment_ids:
        if any(not str(preload_id or "").strip() for preload_id in preloaded_attachment_ids):
            _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
    if is_internal_comment and uploaded_files:
        _error(400, "comment_raw_files_not_allowed", "Файлы комментария должны быть сначала подготовлены через preload")

    if is_plain_ticket and has_attachments and not is_internal_comment:
        agent_message = add_message(
            db=db,
            ticket=ticket,
            message_type=MessageType.AGENT,
            body=text or "",
            author_user_id=user.id,
            telegram_message_id=None,
        )

        attachments: list[TicketAttachment] = []
        attachments_to_schedule: list[dict[str, object]] = []
        if preloaded_attachment_ids:
            if not user.id:
                _error(409, "user_id_required", "Для отправки preload нужен корректный пользователь")
            preload = claim_attachment_preload(db, preloaded_attachment_ids[0], ticket.id, user.id)
            if preload is None:
                _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
            if preload.status not in {"uploading", "ready"}:
                _error(409, "attachment_preload_unavailable", "Предзагруженный файл недоступен для отправки")
            attachment = _attachment_from_preload(
                db=db,
                ticket=ticket,
                message=agent_message,
                preload=preload,
                delivery_status="sending" if preload.status == "ready" and preload.telegram_file_id else "uploading",
            )
            preload.reply_to_message_id = quote_telegram_message_id
            preload.prefer_caption = prefer_caption
            logger.info(
                "attachment transport preload=%s mode=%s attachment=%s",
                preload.id,
                "ready_preload" if preload.status == "ready" and preload.telegram_file_id else "uploading_preload",
                attachment.id,
            )
            logger.info("preload=%s claimed attachment=%s", preload.id, attachment.id)
            attachments.append(attachment)
            if preload.status == "ready" and preload.telegram_file_id:
                attachments_to_schedule.append(
                    {
                        "attachment_id": attachment.id,
                        "message_id": agent_message.id,
                        "ticket_id": ticket.id,
                        "reply_to_message_id": quote_telegram_message_id,
                        "prefer_caption": prefer_caption,
                        "preload_id": preload.id,
                    }
                )
        else:
            logger.info("attachment transport mode=raw_fallback reason=no_preloaded_attachment_id")
            for upload in uploaded_files:
                attachment = save_upload_attachment(db, ticket, agent_message, upload)
                attachment.telegram_delivery_status = "uploading"
                attachments.append(attachment)
                attachments_to_schedule.append(
                    {
                        "attachment_id": attachment.id,
                        "message_id": agent_message.id,
                        "ticket_id": ticket.id,
                        "reply_to_message_id": quote_telegram_message_id,
                        "prefer_caption": prefer_caption,
                    }
                )

        was_frozen = bool(ticket.is_frozen)
        unfreeze_ticket_on_message(ticket)
        if was_frozen and not ticket.is_frozen:
            sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
        if user.role == "agent" and _is_onsite_phrase(text):
            ticket.onsite_triggered = True
            actor_user = db.get(User, user.id)
            if actor_user is not None and actor_user.availability_status != "on_ticket":
                set_user_availability_status(db, actor_user, "on_ticket", now=datetime.utcnow())

        if ticket.channel != "web":
            remove_client_close_button(db, ticket)
        if ticket.status == TicketStatus.NEW.value:
            ticket.status = TicketStatus.PENDING.value
        refresh_ticket_auto_close(db, ticket, True)
        if not quick_action:
            clear_ticket_draft(db, ticket)
        db.commit()

        for delivery_kwargs in attachments_to_schedule:
            schedule_attachment_delivery(**delivery_kwargs)
        return {"ok": True}

    sent_message_id = None
    if workflow_type != TicketWorkflowType.TASK.value and text and not is_internal_comment and ticket.channel != "web":
        try:
            response = send_to_telegram_client(
                db,
                ticket,
                text,
                reply_to_message_id=quote_telegram_message_id,
            )
        except HTTPException as exc:
            if _http_error_code(exc) == "telegram_send_failed":
                persist_client_delivery_failure(
                    db=db,
                    ticket_id=ticket.id,
                    body="Не удалось отправить сообщение клиенту в Telegram.",
                )
            raise
        sent_message_id = telegram_message_id_from_response(response)

    agent_message = add_message(
        db=db,
        ticket=ticket,
        message_type=message_type,
        body=text or "",
        author_user_id=user.id,
        telegram_message_id=sent_message_id,
    )

    attachments: list[TicketAttachment] = []
    for preloaded_attachment_id in preloaded_attachment_ids:
        if workflow_type != TicketWorkflowType.TASK.value and not is_internal_comment:
            _error(409, "attachment_preload_unsupported_workflow", "Preload is not available for this reply flow")
        if not user.id:
            _error(409, "user_id_required", "Для отправки preload нужен корректный пользователь")
        preload = claim_attachment_preload(db, preloaded_attachment_id, ticket.id, user.id)
        if preload is None:
            _error(404, "attachment_preload_not_found", "Предзагруженный файл не найден")
        if preload.status != "ready" or not (preload.telegram_file_id or "").strip():
            _error(409, "attachment_preload_not_ready", "Файл ещё не подготовлен. Дождитесь статуса готовности и отправьте ответ снова.")
        attachment = _attachment_from_preload(
            db=db,
            ticket=ticket,
            message=agent_message,
            preload=preload,
            delivery_status=None,
        )
        attachments.append(attachment)
    for upload in uploaded_files:
        attachment = save_upload_attachment(db, ticket, agent_message, upload)
        if ticket.channel == "web":
            if workflow_type == TicketWorkflowType.TASK.value:
                # Complete archive metadata before a child task can copy it to its parent.
                # Import locally because outbound delivery also imports this module.
                from .telegram_outbound_delivery import _send_staging_document_from_path

                try:
                    file_id, unique_id, archive_id = _send_staging_document_from_path(
                        local_path=attachment.local_path,
                        original_filename=attachment.original_filename,
                        mime_type=attachment.mime_type,
                    )
                except (FileNotFoundError, RuntimeError, TelegramApiError) as exc:
                    _error(502, "telegram_send_failed", f"Не удалось сохранить файл в Telegram-архиве: {exc}")
                attachment.telegram_file_id = file_id
                attachment.telegram_file_unique_id = unique_id
                attachment.telegram_archive_message_id = archive_id
                attachment.telegram_delivery_status = "sent"
            else:
                attachment.telegram_delivery_status = "uploading"
        attachments.append(attachment)

    if workflow_type != TicketWorkflowType.TASK.value and not is_internal_comment and ticket.channel != "web":
        for attachment in attachments:
            try:
                send_attachment_to_telegram_client(
                    db,
                    ticket,
                    attachment,
                    True,
                    show_upload_action=single_attachment_marker and len(uploaded_files) == 1,
                )
            except HTTPException as exc:
                error_code = _http_error_code(exc)
                if error_code in {"telegram_send_failed", "attachment_file_missing"}:
                    filename = attachment.original_filename or "файл"
                    if sent_message_id:
                        failure_body = f"Текст отправлен клиенту, но не удалось отправить вложение «{filename}»."
                    else:
                        failure_body = f"Не удалось отправить клиенту вложение «{filename}»."
                    persist_client_delivery_failure(
                        db=db,
                        ticket_id=ticket.id,
                        body=failure_body,
                    )
                raise

    was_frozen = bool(ticket.is_frozen)
    unfreeze_ticket_on_message(ticket)
    if was_frozen and not ticket.is_frozen:
        sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
    if is_plain_ticket and not is_internal_comment and user.role == "agent" and _is_onsite_phrase(text):
        ticket.onsite_triggered = True
        actor_user = db.get(User, user.id)
        if actor_user is not None and actor_user.availability_status != "on_ticket":
            set_user_availability_status(db, actor_user, "on_ticket", now=datetime.utcnow())

    if not is_internal_comment:
        if ticket.channel != "web":
            remove_client_close_button(db, ticket)
        if ticket.status == TicketStatus.NEW.value:
            ticket.status = TicketStatus.PENDING.value
        refresh_ticket_auto_close(db, ticket, True)
    if not quick_action:
        clear_ticket_draft(db, ticket)
    db.commit()
    if ticket.channel == "web":
        for attachment in attachments:
            if attachment.telegram_delivery_status == "uploading":
                schedule_attachment_delivery(
                    attachment_id=attachment.id,
                    message_id=agent_message.id,
                    ticket_id=ticket.id,
                )
    return {"ok": True}


def comment_on_ticket(
    *,
    db: Session,
    user: CurrentUser,
    ticket: Ticket,
    payload: TextPayload,
    unfreeze_ticket_on_message: Callable[[Ticket], None],
) -> dict:
    if not can_comment(user):
        _error(403, "readonly_role", "Нет прав на внутренний комментарий")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    add_message(
        db=db,
        ticket=ticket,
        message_type=MessageType.INTERNAL,
        body=payload.text,
        author_user_id=user.id,
    )
    was_frozen = bool(ticket.is_frozen)
    unfreeze_ticket_on_message(ticket)
    if was_frozen and not ticket.is_frozen:
        sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
    db.commit()
    return {"ok": True}


def edit_agent_message(
    *,
    db: Session,
    user: CurrentUser,
    ticket: Ticket,
    message_id: str,
    payload: TextPayload,
    telegram_chat_id_by_client_id: Callable[[Session, str], str | None],
    actor_display_name: Callable[[Session, CurrentUser], str],
) -> dict:
    if not can_edit_messages(user):
        _error(403, "readonly_role", "Нет прав на редактирование сообщений")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    message = db.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket.id:
        _error(404, "message_not_found", "Сообщение не найдено")
    if message.message_type != MessageType.AGENT.value:
        _error(409, "only_agent_message_edit", "Редактировать можно только сообщения сотрудника")
    if message.message_status == "deleted":
        _error(409, "message_deleted", "Удалённое сообщение нельзя редактировать")
    if ticket.channel != "web" and not message.telegram_message_id:
        _error(
            409,
            "telegram_message_id_missing",
            "У этого сообщения нет Telegram ID. Его нельзя изменить в Telegram, потому что оно было отправлено до включения синхронизации сообщений.",
        )

    attachments = db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == message.id)
        .order_by(asc(TicketAttachment.created_at))
    ).scalars().all()

    external_chat_id = telegram_chat_id_by_client_id(db, ticket.client_id) if ticket.channel != "web" else None
    if external_chat_id:
        try:
            if _message_uses_single_attachment_caption(message, attachments):
                edit_message_caption(external_chat_id, message.telegram_message_id, payload.text)
            else:
                edit_message_text(external_chat_id, message.telegram_message_id, payload.text)
        except TelegramApiError as exc:
            _error(502, "telegram_edit_failed", f"Telegram не принял редактирование: {exc}")

    message.body = payload.text
    message.message_status = "edited"
    actor_name = actor_display_name(db, user)
    message.status_note = f"{actor_name} исправил сообщение"
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


def delete_agent_message(
    *,
    db: Session,
    user: CurrentUser,
    ticket: Ticket,
    message_id: str,
    telegram_chat_id_by_client_id: Callable[[Session, str], str | None],
    actor_display_name: Callable[[Session, CurrentUser], str],
) -> dict:
    if not can_edit_messages(user):
        _error(403, "readonly_role", "Нет прав на удаление сообщений")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    message = db.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket.id:
        _error(404, "message_not_found", "Сообщение не найдено")
    if message.message_type != MessageType.AGENT.value:
        _error(409, "only_agent_message_delete", "Удалять можно только сообщения сотрудника")
    if message.message_status == "deleted":
        return {"ok": True}

    attachments = db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == message.id)
        .order_by(asc(TicketAttachment.created_at))
    ).scalars().all()

    external_chat_id = telegram_chat_id_by_client_id(db, ticket.client_id) if ticket.channel != "web" else None
    if external_chat_id:
        telegram_message_ids: list[str] = []
        if message.telegram_message_id:
            telegram_message_ids.append(str(message.telegram_message_id).strip())
        for attachment in attachments:
            attachment_message_id = str(attachment.telegram_message_id or "").strip()
            if attachment_message_id:
                telegram_message_ids.append(attachment_message_id)

        seen_message_ids: set[str] = set()
        unique_message_ids = [
            message_id_value
            for message_id_value in telegram_message_ids
            if message_id_value and not (message_id_value in seen_message_ids or seen_message_ids.add(message_id_value))
        ]

        primary_message_id = str(message.telegram_message_id or "").strip()
        for message_id_value in unique_message_ids:
            try:
                delete_message(external_chat_id, message_id_value)
            except TelegramApiError as exc:
                if primary_message_id and message_id_value == primary_message_id:
                    _error(502, "telegram_delete_failed", f"Telegram не принял удаление: {exc}")
                pass

    message.message_status = "deleted"
    actor_name = actor_display_name(db, user)
    message.status_note = f"{actor_name} удалил сообщение"
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
