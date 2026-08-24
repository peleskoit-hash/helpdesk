from __future__ import annotations

from datetime import datetime
import logging
from mimetypes import guess_extension
from pathlib import Path
from threading import Thread
import uuid

from sqlalchemy import Select, asc, or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Client, ClientProfile, TelegramContact, Ticket, TicketAttachment, TicketMessage
from .telegram_bot import TelegramApiError, copy_message, download_file, edit_message_text, get_file, send_message
from .telegram_outbound_delivery import _archive_ticket_header, _format_project_dt, _lock_stmt, _staging_chat_id, _telegram_message_id


logger = logging.getLogger(__name__)

FORBIDDEN_ATTACHMENT_TYPES = {"voice", "video_note"}
TELEGRAM_DOWNLOAD_PREFETCH_LIMIT_BYTES = 20 * 1024 * 1024
ARCHIVE_PENDING_PREFIX = "pending:"
ARCHIVE_INFO_PENDING_PREFIX = "pending:info:"


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
    media_group_id = message.get("media_group_id")
    if "document" in message:
        item = message["document"]
        return {
            "source": "document",
            "file_id": item.get("file_id"),
            "file_unique_id": item.get("file_unique_id"),
            "file_size": item.get("file_size"),
            "mime_type": item.get("mime_type"),
            "original_filename": item.get("file_name"),
            "media_group_id": media_group_id,
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
            "media_group_id": media_group_id,
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
                "media_group_id": media_group_id,
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
        telegram_media_group_id=str(attachment_data.get("media_group_id") or "").strip() or None,
        original_filename=original_filename,
        mime_type=attachment_data.get("mime_type"),
        file_size=attachment_data.get("file_size"),
        local_path=str(local_path),
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    ticket.updated_at = datetime.utcnow()
    return attachment


def _client_name_for_archive(db: Session, ticket: Ticket) -> str:
    profile = db.execute(select(ClientProfile).where(ClientProfile.client_id == ticket.client_id)).scalar_one_or_none()
    if profile is not None:
        full_name = " ".join(
            part for part in [profile.contact_name, profile.last_name] if part and str(part).strip()
        ).strip()
        if full_name:
            return full_name

    contact = db.execute(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)).scalar_one_or_none()
    if contact is not None:
        display_name = str(contact.external_display_name or "").strip()
        if display_name:
            return display_name
        username = str(contact.external_username or "").strip()
        if username:
            return username

    client = db.get(Client, ticket.client_id)
    client_name = str(client.display_name if client is not None else "").strip()
    return client_name or "Клиент"


def _inbound_attachment_archive_caption(db: Session, ticket: Ticket, attachment: TicketAttachment) -> str:
    filename = (attachment.original_filename or "").strip() or "Файл"
    event_at = attachment.created_at or datetime.utcnow()
    return "\n".join(
        [
            _archive_ticket_header(ticket),
            f"Клиент: {_client_name_for_archive(db, ticket)}",
            f"Получено: {_format_project_dt(event_at)}",
            "Файл:",
            f"• {filename}",
            "Статус: получено от клиента",
        ]
    )


def _inbound_group_archive_info_text(ticket: Ticket, attachments: list[TicketAttachment], client_name: str) -> str:
    if not attachments:
        raise RuntimeError("Cannot render inbound archive group info without attachments")
    sorted_rows = sorted(attachments, key=lambda row: (row.created_at or datetime.min, row.id))
    filenames = [((row.original_filename or "").strip() or "Файл") for row in sorted_rows]
    file_label = "Файлы:" if len(filenames) > 1 else "Файл:"
    event_at = sorted_rows[0].created_at
    lines = [
        _archive_ticket_header(ticket),
        f"Клиент: {client_name}",
        f"Получено: {_format_project_dt(event_at)}",
        file_label,
    ]
    lines.extend(f"• {filename}" for filename in filenames)
    lines.append("Статус: получено от клиента")
    return "\n".join(lines)


def _archive_pending_token(attachment_id: str) -> str:
    return f"{ARCHIVE_PENDING_PREFIX}{attachment_id}:{uuid.uuid4()}"


def _is_pending_archive_message_id(value: str | None) -> bool:
    return str(value or "").startswith(ARCHIVE_PENDING_PREFIX)


def _archive_info_pending_token(ticket_id: str, media_group_id: str) -> str:
    return f"{ARCHIVE_INFO_PENDING_PREFIX}{uuid.uuid4()}"


def _is_pending_archive_info_message_id(value: str | None) -> bool:
    return str(value or "").startswith(ARCHIVE_INFO_PENDING_PREFIX)


def _load_media_group_attachments(
    db: Session,
    *,
    ticket_id: str,
    media_group_id: str,
    for_update: bool = False,
) -> list[TicketAttachment]:
    stmt: Select = (
        select(TicketAttachment)
        .where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_media_group_id == media_group_id,
        )
        .order_by(asc(TicketAttachment.created_at), asc(TicketAttachment.id))
    )
    if for_update:
        stmt = _lock_stmt(stmt, db)
    return db.execute(stmt).scalars().all()


def _actual_inbound_group_info_message_id(attachments: list[TicketAttachment]) -> str | None:
    for attachment in attachments:
        message_id = str(attachment.telegram_archive_info_message_id or "").strip()
        if message_id and not _is_pending_archive_info_message_id(message_id):
            return message_id
    return None


def _pending_inbound_group_info_message_id(attachments: list[TicketAttachment]) -> str | None:
    for attachment in attachments:
        message_id = str(attachment.telegram_archive_info_message_id or "").strip()
        if _is_pending_archive_info_message_id(message_id):
            return message_id
    return None


def _claim_inbound_attachment_archive(db: Session, attachment_id: str) -> str | None:
    pending_token = _archive_pending_token(attachment_id)
    result = db.execute(
        update(TicketAttachment)
        .where(
            TicketAttachment.id == attachment_id,
            or_(
                TicketAttachment.telegram_archive_message_id.is_(None),
                TicketAttachment.telegram_archive_message_id == "",
            ),
        )
        .values(telegram_archive_message_id=pending_token)
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        return None
    db.commit()
    return pending_token


def _claim_inbound_archive_info_message(db: Session, ticket_id: str, media_group_id: str) -> str | None:
    existing_rows = _load_media_group_attachments(
        db,
        ticket_id=ticket_id,
        media_group_id=media_group_id,
        for_update=True,
    )
    if _actual_inbound_group_info_message_id(existing_rows) or _pending_inbound_group_info_message_id(existing_rows):
        db.rollback()
        return None

    pending_token = _archive_info_pending_token(ticket_id, media_group_id)
    result = db.execute(
        update(TicketAttachment)
        .where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_media_group_id == media_group_id,
            or_(
                TicketAttachment.telegram_archive_info_message_id.is_(None),
                TicketAttachment.telegram_archive_info_message_id == "",
            ),
        )
        .values(telegram_archive_info_message_id=pending_token)
    )
    if int(result.rowcount or 0) < 1:
        db.rollback()
        return None
    db.commit()
    return pending_token


def _release_inbound_attachment_archive_claim(db: Session, attachment_id: str, pending_token: str) -> None:
    db.execute(
        update(TicketAttachment)
        .where(
            TicketAttachment.id == attachment_id,
            TicketAttachment.telegram_archive_message_id == pending_token,
        )
        .values(telegram_archive_message_id=None)
    )
    db.commit()


def _release_inbound_archive_info_claim(db: Session, ticket_id: str, media_group_id: str, pending_token: str) -> None:
    db.execute(
        update(TicketAttachment)
        .where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_media_group_id == media_group_id,
            TicketAttachment.telegram_archive_info_message_id == pending_token,
        )
        .values(telegram_archive_info_message_id=None)
    )
    db.commit()


def _apply_inbound_group_info_message_id(
    db: Session,
    *,
    ticket_id: str,
    media_group_id: str,
    message_id: str,
    pending_token: str | None = None,
) -> None:
    conditions = [
        TicketAttachment.ticket_id == ticket_id,
        TicketAttachment.telegram_media_group_id == media_group_id,
    ]
    if pending_token is None:
        conditions.append(
            or_(
                TicketAttachment.telegram_archive_info_message_id.is_(None),
                TicketAttachment.telegram_archive_info_message_id == "",
            )
        )
    else:
        conditions.append(
            or_(
                TicketAttachment.telegram_archive_info_message_id == pending_token,
                TicketAttachment.telegram_archive_info_message_id.is_(None),
                TicketAttachment.telegram_archive_info_message_id == "",
            )
        )
    db.execute(update(TicketAttachment).where(*conditions).values(telegram_archive_info_message_id=message_id))
    db.flush()


def _best_effort_edit_inbound_group_archive_info_message(
    *,
    db: Session,
    ticket: Ticket,
    attachments: list[TicketAttachment],
) -> None:
    message_id = _actual_inbound_group_info_message_id(attachments)
    if not message_id:
        return
    try:
        edit_message_text(
            _staging_chat_id(),
            message_id,
            _inbound_group_archive_info_text(ticket, attachments, _client_name_for_archive(db, ticket)),
        )
    except TelegramApiError as exc:
        description = str(exc).lower()
        if "message is not modified" in description:
            return
        logger.warning("Failed to edit inbound archive group info ticket_id=%s media_group_id=%s: %s", ticket.id, attachments[0].telegram_media_group_id if attachments else "", exc)
    except RuntimeError as exc:
        logger.warning("Failed to edit inbound archive group info ticket_id=%s media_group_id=%s: %s", ticket.id, attachments[0].telegram_media_group_id if attachments else "", exc)


def _inbound_archive_source_chat_id(db: Session, ticket: Ticket) -> str:
    stmt = select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)
    if ticket.company_id:
        stmt = stmt.where(TelegramContact.company_id == ticket.company_id)
    contact = db.execute(stmt).scalar_one_or_none()
    chat_id = str(contact.external_chat_id if contact is not None else "").strip()
    if not chat_id:
        raise RuntimeError("Inbound archive source Telegram chat is missing")
    return chat_id


def _inbound_archive_source_message_id(db: Session, attachment: TicketAttachment) -> str:
    message = db.get(TicketMessage, attachment.message_id)
    message_id = str(message.telegram_message_id if message is not None else "").strip()
    if not message_id:
        raise RuntimeError("Inbound archive source Telegram message is missing")
    return message_id


def _ensure_inbound_group_info_message(db: Session, ticket: Ticket, attachment: TicketAttachment) -> str | None:
    media_group_id = str(attachment.telegram_media_group_id or "").strip()
    if not media_group_id:
        return None

    group_rows = _load_media_group_attachments(db, ticket_id=ticket.id, media_group_id=media_group_id, for_update=False)
    actual_message_id = _actual_inbound_group_info_message_id(group_rows)
    if actual_message_id:
        _apply_inbound_group_info_message_id(
            db,
            ticket_id=ticket.id,
            media_group_id=media_group_id,
            message_id=actual_message_id,
            pending_token=None,
        )
        return actual_message_id
    if _pending_inbound_group_info_message_id(group_rows):
        return None

    pending_token = _claim_inbound_archive_info_message(db, ticket.id, media_group_id)
    if not pending_token:
        refreshed_rows = _load_media_group_attachments(db, ticket_id=ticket.id, media_group_id=media_group_id, for_update=False)
        actual_message_id = _actual_inbound_group_info_message_id(refreshed_rows)
        if actual_message_id:
            _apply_inbound_group_info_message_id(
                db,
                ticket_id=ticket.id,
                media_group_id=media_group_id,
                message_id=actual_message_id,
                pending_token=None,
            )
        return actual_message_id

    try:
        refreshed_rows = _load_media_group_attachments(db, ticket_id=ticket.id, media_group_id=media_group_id, for_update=False)
        response = send_message(
            _staging_chat_id(),
            _inbound_group_archive_info_text(ticket, refreshed_rows, _client_name_for_archive(db, ticket)),
        )
        message_id = _telegram_message_id(response)
        if not message_id:
            raise RuntimeError("Telegram inbound archive group info returned no message_id")
        _apply_inbound_group_info_message_id(
            db,
            ticket_id=ticket.id,
            media_group_id=media_group_id,
            message_id=message_id,
            pending_token=pending_token,
        )
        db.commit()
        return message_id
    except (RuntimeError, TelegramApiError):
        db.rollback()
        _release_inbound_archive_info_claim(db, ticket.id, media_group_id, pending_token)
        logger.warning(
            "Failed to create inbound archive group info ticket_id=%s media_group_id=%s",
            ticket.id,
            media_group_id,
            exc_info=True,
        )
        return None
    except Exception:
        logger.exception(
            "Unexpected inbound archive group info failure ticket_id=%s media_group_id=%s",
            ticket.id,
            media_group_id,
        )
        db.rollback()
        _release_inbound_archive_info_claim(db, ticket.id, media_group_id, pending_token)
        return None


def _best_effort_archive_inbound_attachment(db: Session, ticket: Ticket, attachment: TicketAttachment) -> None:
    archive_message_id = str(attachment.telegram_archive_message_id or "").strip()
    if archive_message_id and not _is_pending_archive_message_id(archive_message_id):
        return

    try:
        source_chat_id = _inbound_archive_source_chat_id(db, ticket)
        source_message_id = _inbound_archive_source_message_id(db, attachment)
        media_group_id = str(attachment.telegram_media_group_id or "").strip()
        if media_group_id:
            _ensure_inbound_group_info_message(db, ticket, attachment)
            response = copy_message(
                _staging_chat_id(),
                source_chat_id,
                source_message_id,
                caption="",
            )
        else:
            response = copy_message(
                _staging_chat_id(),
                source_chat_id,
                source_message_id,
                caption=_inbound_attachment_archive_caption(db, ticket, attachment),
            )
        message_id = _telegram_message_id(response)
        if not message_id:
            raise RuntimeError("Telegram staging archive returned no message_id")
        attachment.telegram_archive_message_id = message_id
        if media_group_id:
            group_rows = _load_media_group_attachments(db, ticket_id=ticket.id, media_group_id=media_group_id, for_update=False)
            actual_info_message_id = _actual_inbound_group_info_message_id(group_rows)
            if actual_info_message_id:
                _apply_inbound_group_info_message_id(
                    db,
                    ticket_id=ticket.id,
                    media_group_id=media_group_id,
                    message_id=actual_info_message_id,
                    pending_token=None,
                )
                refreshed_rows = _load_media_group_attachments(db, ticket_id=ticket.id, media_group_id=media_group_id, for_update=False)
                _best_effort_edit_inbound_group_archive_info_message(
                    db=db,
                    ticket=ticket,
                    attachments=refreshed_rows,
                )
        db.flush()
    except (RuntimeError, TelegramApiError):
        logger.warning(
            "Failed to archive inbound Telegram attachment attachment_id=%s ticket_id=%s",
            attachment.id,
            ticket.id,
            exc_info=True,
        )


def _archive_inbound_attachment_worker(attachment_id: str) -> None:
    with SessionLocal() as db:
        pending_token = _claim_inbound_attachment_archive(db, attachment_id)
        if not pending_token:
            return
        try:
            attachment = db.get(TicketAttachment, attachment_id)
            if attachment is None:
                _release_inbound_attachment_archive_claim(db, attachment_id, pending_token)
                return
            ticket = db.get(Ticket, attachment.ticket_id)
            if ticket is None:
                _release_inbound_attachment_archive_claim(db, attachment_id, pending_token)
                return
            _best_effort_archive_inbound_attachment(db, ticket, attachment)
            if str(attachment.telegram_archive_message_id or "").strip() == pending_token:
                attachment.telegram_archive_message_id = None
            db.commit()
        except Exception:
            logger.exception("Unexpected inbound attachment archive failure attachment_id=%s", attachment_id)
            db.rollback()
            _release_inbound_attachment_archive_claim(db, attachment_id, pending_token)


def start_background_archive_inbound_attachment(attachment_id: str | None) -> None:
    normalized = str(attachment_id or "").strip()
    if not normalized:
        return
    Thread(
        target=_archive_inbound_attachment_worker,
        args=(normalized,),
        daemon=True,
    ).start()


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
