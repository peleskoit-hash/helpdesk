from __future__ import annotations

from datetime import datetime
from mimetypes import guess_extension
from pathlib import Path
from threading import Thread
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Ticket, TicketAttachment, TicketMessage
from .telegram_bot import TelegramApiError, download_file, get_file

FORBIDDEN_ATTACHMENT_TYPES = {"voice", "video_note"}
TELEGRAM_DOWNLOAD_PREFETCH_LIMIT_BYTES = 20 * 1024 * 1024


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
            mime_type = item.get("mime_type") or ("image/webp" if source == "sticker" and not item.get("is_animated") and not item.get("is_video") else None)
            if source == "sticker" and item.get("is_animated"):
                mime_type = mime_type or "application/x-tgsticker"
            if source == "sticker" and item.get("is_video"):
                mime_type = mime_type or "video/webm"
            original_filename = item.get("file_name")
            if source == "sticker" and not original_filename:
                if item.get("is_animated"):
                    original_filename = "sticker.tgs"
                elif item.get("is_video"):
                    original_filename = "sticker.webm"
                else:
                    original_filename = "sticker.webp"
            return {
                "source": source,
                "file_id": item.get("file_id"),
                "file_unique_id": item.get("file_unique_id"),
                "file_size": item.get("file_size"),
                "mime_type": mime_type,
                "original_filename": original_filename or f"{source}",
            }
    return None


def _forbidden_attachment_type(message: dict) -> str | None:
    for source in FORBIDDEN_ATTACHMENT_TYPES:
        if source in message:
            return source
    return None


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
    # Do not download Telegram files in the update processing path.
    # This keeps inbound message handling responsive for large or multiple files.
    local_path.parent.mkdir(parents=True, exist_ok=True)

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
    _try_prefetch_attachment_file(attachment, external_chat_id=external_chat_id)
    return attachment


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


def _try_prefetch_attachment_file(attachment: TicketAttachment, external_chat_id: str | int | None = None) -> None:
    file_path = (attachment.telegram_file_path or "").strip()
    if not file_path:
        return
    file_size = attachment.file_size or 0
    if file_size and int(file_size) > TELEGRAM_DOWNLOAD_PREFETCH_LIMIT_BYTES:
        return
    path = Path(attachment.local_path)
    if path.exists() and path.is_file():
        return

    def _worker():
        try:
            download_file(file_path, path, chat_id=external_chat_id)
        except Exception:
            # Best-effort warm cache: ignore failures, fallback stays in download endpoint.
            pass

    Thread(target=_worker, daemon=True).start()
