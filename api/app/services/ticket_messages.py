from datetime import datetime
from typing import Callable
import uuid

from fastapi import HTTPException
from fastapi import Request, UploadFile
from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, can_comment, can_edit_messages, can_reply
from ..models import MessageType, Ticket, TicketAttachment, TicketMessage, TicketStatus, TicketWorkflowType, User
from ..schemas import TextPayload
from ..services.telegram_bot import TelegramApiError, delete_message, edit_message_text


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _is_onsite_phrase(text: str | None) -> bool:
    normalized = str(text or "").strip().lower()
    if normalized.endswith("."):
        normalized = normalized[:-1].strip()
    return normalized == "иду к вам"


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
    return msg


async def reply_to_ticket(
    *,
    request: Request,
    db: Session,
    user: CurrentUser,
    ticket: Ticket,
    send_to_telegram_client: Callable[..., dict | None],
    telegram_message_id_from_response: Callable[[dict | None], str | None],
    save_upload_attachment: Callable[[Session, Ticket, TicketMessage, UploadFile], TicketAttachment],
    send_attachment_to_telegram_client: Callable[[Session, Ticket, TicketAttachment, bool], None],
    unfreeze_ticket_on_message: Callable[[Ticket], None],
    remove_client_close_button: Callable[[Session, Ticket], None],
    refresh_ticket_auto_close: Callable[[Session, Ticket, bool], None],
) -> dict:
    if not can_reply(user):
        _error(403, "readonly_role", "Нет прав на отправку ответа")
    if ticket.status == TicketStatus.CLOSED.value:
        _error(409, "ticket_closed_readonly", "Закрытая заявка только для чтения")

    content_type = (request.headers.get("content-type") or "").lower()
    text: str | None = None
    quote_message_id: str | None = None
    uploaded_files: list[UploadFile] = []
    if "multipart/form-data" in content_type:
        form = await request.form()
        text = (form.get("text") or "").strip() or None
        quote_message_id = (form.get("quote_message_id") or "").strip() or None
        for value in form.getlist("files"):
            if hasattr(value, "filename") and hasattr(value, "file"):
                uploaded_files.append(value)
        if not text and not uploaded_files:
            _error(400, "reply_payload_empty", "Нужно передать текст или хотя бы один файл")
    else:
        payload_raw = await request.json()
        payload = TextPayload.model_validate(payload_raw)
        text = payload.text.strip()
        quote_message_id = payload.quote_message_id
        if not text:
            _error(400, "reply_payload_empty", "Текст ответа не может быть пустым")

    quote_telegram_message_id = None
    if quote_message_id:
        quoted = db.get(TicketMessage, quote_message_id)
        if quoted is not None and quoted.ticket_id == ticket.id:
            quote_telegram_message_id = quoted.telegram_message_id

    sent_message_id = None
    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value and text:
        response = send_to_telegram_client(
            db,
            ticket,
            text,
            reply_to_message_id=quote_telegram_message_id,
        )
        sent_message_id = telegram_message_id_from_response(response)

    agent_message = add_message(
        db=db,
        ticket=ticket,
        message_type=MessageType.AGENT,
        body=text or "",
        author_user_id=user.id,
        telegram_message_id=sent_message_id,
    )

    attachments: list[TicketAttachment] = []
    for upload in uploaded_files:
        attachment = save_upload_attachment(db, ticket, agent_message, upload)
        attachments.append(attachment)

    if (ticket.workflow_type or TicketWorkflowType.TICKET.value) != TicketWorkflowType.TASK.value:
        for attachment in attachments:
            send_attachment_to_telegram_client(db, ticket, attachment, True)

    unfreeze_ticket_on_message(ticket)
    if (
        (ticket.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value
        and user.role == "agent"
        and _is_onsite_phrase(text)
    ):
        ticket.onsite_triggered = True
        actor_user = db.get(User, user.id)
        if actor_user is not None and actor_user.availability_status != "on_ticket":
            actor_user.availability_status = "on_ticket"
            actor_user.updated_at = datetime.utcnow()

    remove_client_close_button(db, ticket)
    if ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.PENDING.value
    refresh_ticket_auto_close(db, ticket, True)
    db.commit()
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
    unfreeze_ticket_on_message(ticket)
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
    if not message.telegram_message_id:
        _error(
            409,
            "telegram_message_id_missing",
            "У этого сообщения нет Telegram ID. Его нельзя изменить в Telegram, потому что оно было отправлено до включения синхронизации сообщений.",
        )

    external_chat_id = telegram_chat_id_by_client_id(db, ticket.client_id)
    if external_chat_id:
        try:
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

    external_chat_id = telegram_chat_id_by_client_id(db, ticket.client_id)
    if external_chat_id and message.telegram_message_id:
        try:
            delete_message(external_chat_id, message.telegram_message_id)
        except TelegramApiError as exc:
            _error(502, "telegram_delete_failed", f"Telegram не принял удаление: {exc}")

    if external_chat_id:
        for attachment in attachments:
            telegram_attachment_message_id = (attachment.telegram_message_id or "").strip()
            if not telegram_attachment_message_id:
                continue
            try:
                delete_message(external_chat_id, telegram_attachment_message_id)
            except TelegramApiError:
                # Attachment message may already be gone; local deletion should still complete.
                pass

    message.message_status = "deleted"
    actor_name = actor_display_name(db, user)
    message.status_note = f"{actor_name} удалил сообщение"
    ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
