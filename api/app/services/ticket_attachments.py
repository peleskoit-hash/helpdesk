from __future__ import annotations

from datetime import datetime
from mimetypes import guess_extension
from pathlib import Path
import uuid

from fastapi import HTTPException, UploadFile
from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage
from .telegram_bot import TelegramApiError, download_file, get_file, send_chat_action, send_document, send_document_by_file_id
from .telegram_text_format import prepare_telegram_html_message


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _attachment_is_image(attachment: TicketAttachment) -> bool:
    mime_type = (attachment.mime_type or "").lower()
    filename = (attachment.original_filename or "").lower()
    return mime_type in {"image/jpeg", "image/png", "image/webp", "image/gif"} or filename.endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".gif")
    )


def _attachment_out(ticket_id: str, attachment: TicketAttachment) -> dict:
    return {
        "id": attachment.id,
        "original_filename": attachment.original_filename,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "telegram_delivery_status": attachment.telegram_delivery_status,
        "download_url": f"/api/v1/tickets/{ticket_id}/attachments/{attachment.id}/download",
        "is_image": _attachment_is_image(attachment),
        "created_at": attachment.created_at,
    }


def _safe_filename(value: str | None, fallback: str = "file.bin") -> str:
    name = (value or "").strip() or fallback
    forbidden = '<>:"/\\|?*'
    for char in forbidden:
        name = name.replace(char, "_")
    return name[:180]


def _save_upload_attachment(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    upload: UploadFile,
) -> TicketAttachment:
    file_path, content_size, safe_filename, mime_type = _store_upload_file(upload)

    attachment = TicketAttachment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_id=message.id,
        source="helpdesk",
        telegram_file_id=None,
        telegram_file_unique_id=None,
        telegram_file_path=None,
        telegram_delivery_status=None,
        telegram_message_id=None,
        original_filename=safe_filename,
        mime_type=mime_type,
        file_size=content_size,
        local_path=str(file_path),
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    return attachment


def _store_upload_file(upload: UploadFile) -> tuple[Path, int, str, str]:
    storage_dir = Path(settings.attachment_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(upload.filename or "").suffix
    if not ext and upload.content_type:
        ext = guess_extension(upload.content_type) or ""
    if not ext:
        ext = ".bin"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = storage_dir / stored_name

    content = upload.file.read()
    if isinstance(content, str):
        content = content.encode("utf-8")
    file_path.write_bytes(content)

    safe_filename = _safe_filename(upload.filename, fallback=f"file{ext}")
    mime_type = upload.content_type or "application/octet-stream"
    return file_path, len(content) if content is not None else 0, safe_filename, mime_type


def _save_attachment_preload(
    db: Session,
    ticket: Ticket,
    user_id: str,
    upload: UploadFile,
    archive_group_id: str | None = None,
) -> TicketAttachmentPreload:
    file_path, content_size, safe_filename, mime_type = _store_upload_file(upload)
    normalized_archive_group_id = archive_group_id.strip() if isinstance(archive_group_id, str) else None
    preload = TicketAttachmentPreload(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user_id,
        original_filename=safe_filename,
        mime_type=mime_type,
        file_size=content_size,
        local_path=str(file_path),
        telegram_file_id=None,
        telegram_file_unique_id=None,
        archive_group_id=normalized_archive_group_id or None,
        status="uploading",
        final_attachment_id=None,
        reply_to_message_id=None,
        prefer_caption=False,
        created_at=datetime.utcnow(),
    )
    db.add(preload)
    return preload


def _send_attachment_to_telegram_client(
    db: Session,
    ticket: Ticket,
    attachment: TicketAttachment,
    *,
    required: bool = True,
    caption: str | None = None,
    reply_to_message_id: str | int | None = None,
    show_upload_action: bool = False,
) -> dict | None:
    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is None:
        if required:
            _error(409, "telegram_contact_not_found", "У клиента нет Telegram-контакта")
        return None
    path = Path(attachment.local_path)
    file_id = str(attachment.telegram_file_id or "").strip()
    if not path.exists() or not path.is_file():
        if file_id:
            try:
                payload_caption = None
                parse_mode = None
                if caption:
                    payload_caption, parse_mode = prepare_telegram_html_message(caption)
                response = send_document_by_file_id(
                    contact.external_chat_id,
                    file_id,
                    caption=payload_caption,
                    parse_mode=parse_mode,
                    reply_to_message_id=reply_to_message_id,
                )
                message_id = ((response or {}).get("result") or {}).get("message_id")
                attachment.telegram_message_id = str(message_id) if message_id is not None else None
                return response
            except TelegramApiError as exc:
                if required:
                    _error(502, "telegram_send_failed", f"Telegram не принял файл {attachment.original_filename or attachment.id}: {exc}")
                return None
        if required:
            _error(404, "attachment_file_missing", f"Файл отсутствует: {attachment.original_filename or attachment.id}")
        return None
    try:
        payload_caption = None
        parse_mode = None
        if caption:
            payload_caption, parse_mode = prepare_telegram_html_message(caption)
        if show_upload_action:
            try:
                send_chat_action(contact.external_chat_id, "upload_document")
            except TelegramApiError:
                # Cosmetic-only UX hint: file delivery should proceed even if chat action fails.
                pass
        response = send_document(
            contact.external_chat_id,
            path,
            filename=attachment.original_filename or path.name,
            caption=payload_caption,
            mime_type=attachment.mime_type or "application/octet-stream",
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
        )
        message_id = ((response or {}).get("result") or {}).get("message_id")
        attachment.telegram_message_id = str(message_id) if message_id is not None else None
        return response
    except TelegramApiError as exc:
        if required:
            _error(502, "telegram_send_failed", f"Telegram не принял файл {attachment.original_filename or path.name}: {exc}")
        return None


def _message_attachments(db: Session, message_id: str) -> list[TicketAttachment]:
    return db.execute(
        select(TicketAttachment)
        .where(TicketAttachment.message_id == message_id)
        .order_by(asc(TicketAttachment.created_at))
    ).scalars().all()


def _ensure_local_attachment_file(attachment: TicketAttachment, external_chat_id: str | int | None = None) -> Path:
    path = Path(attachment.local_path)
    if path.exists() and path.is_file():
        return path

    file_path = (attachment.telegram_file_path or "").strip()
    if not file_path:
        file_id = (attachment.telegram_file_id or "").strip()
        if not file_id:
            raise TelegramApiError("Отсутствуют telegram_file_path и telegram_file_id")
        file_response = get_file(file_id, chat_id=external_chat_id)
        file_path = ((file_response.get("result") or {}).get("file_path") or "").strip()
        if not file_path:
            raise TelegramApiError("Telegram не вернул путь к файлу")
        attachment.telegram_file_path = file_path

    download_file(file_path, path, chat_id=external_chat_id)
    return path
