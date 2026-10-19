from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, asc, select

from ..config import settings
from ..db import SessionLocal
from ..models import MessageType, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketWorkflowType, User
from .telegram_bot import TelegramApiError, delete_message, edit_message_text, send_document, send_document_by_file_id, send_message
from .telegram_text_format import prepare_telegram_html_message, strip_telegram_html, telegram_html_code, telegram_html_link, telegram_html_text
from .ticket_links import public_ticket_url
from .ticket_messages import TELEGRAM_CAPTION_LIMIT, record_client_delivery_failure


logger = logging.getLogger(__name__)

PRELOAD_STALE_TTL_HOURS = 24
ARCHIVE_INFO_PENDING_PREFIX = "pending:"
ARCHIVE_INFO_WAIT_ATTEMPTS = 30
ARCHIVE_INFO_WAIT_DELAY_SECONDS = 0.1
STAGING_BINARY_UPLOAD_CONCURRENCY = 1
STAGING_BINARY_UPLOAD_TIMEOUT_SECONDS = 300
STAGING_BINARY_UPLOAD_SEMAPHORE = threading.Semaphore(STAGING_BINARY_UPLOAD_CONCURRENCY)
try:
    PROJECT_TIMEZONE = ZoneInfo("Europe/Moscow")
except ZoneInfoNotFoundError:
    PROJECT_TIMEZONE = timezone(timedelta(hours=3))


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    return str(message_id) if message_id is not None else None


def _caption_compatible_text(text: str | None) -> bool:
    if not text:
        return False
    return len(strip_telegram_html(text).strip()) <= TELEGRAM_CAPTION_LIMIT


def _lock_stmt(stmt: Select, db) -> Select:
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        return stmt.with_for_update()
    return stmt


def _staging_chat_id() -> str:
    staging_chat_id = (settings.telegram_staging_chat_id or "").strip()
    if not staging_chat_id:
        raise RuntimeError("TELEGRAM_STAGING_CHAT_ID is not configured")
    return staging_chat_id


def _safe_unlink(path_value: str | None) -> None:
    path = Path(path_value or "")
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        logger.warning("Failed to remove file %s", path, exc_info=True)


def _unlink_attachment_local_file(path_value: str | None) -> None:
    _safe_unlink(path_value)


def _project_dt(value: datetime | None) -> datetime:
    source = value or datetime.now(timezone.utc)
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(PROJECT_TIMEZONE)


def _format_project_dt(value: datetime | None) -> str:
    return _project_dt(value).strftime("%d.%m.%Y %H:%M")


def _archive_ticket_header(ticket: Ticket) -> str:
    ticket_number = ticket.ticket_number if ticket.ticket_number is not None else "?"
    subject = (ticket.subject or "").strip() or "Без названия"
    label = f"Заявка №{ticket_number}"
    href = public_ticket_url(ticket)
    prefix = telegram_html_link(label, href) if href else telegram_html_text(label)
    return f"{prefix} — {telegram_html_text(subject)}"


def _archive_actor_name(user: User | None) -> str:
    full_name = (getattr(user, "full_name", "") or "").strip()
    return full_name or "Сотрудник не найден"


def _archive_group_id(preload: TicketAttachmentPreload) -> str:
    return (preload.archive_group_id or "").strip() or preload.id


def _ensure_archive_group_id(db, preload: TicketAttachmentPreload) -> str:
    normalized = _archive_group_id(preload)
    if (preload.archive_group_id or "").strip() != normalized:
        preload.archive_group_id = normalized
        db.flush()
    return normalized


def _archive_pending_token(preload_id: str) -> str:
    return f"{ARCHIVE_INFO_PENDING_PREFIX}{preload_id}"


def _is_pending_archive_message_id(value: str | None) -> bool:
    return str(value or "").startswith(ARCHIVE_INFO_PENDING_PREFIX)


def _group_status_text(group_rows: list[TicketAttachmentPreload]) -> str:
    count = len(group_rows)
    if not count:
        return "подготовка файла"
    if any(row.final_attachment_id and row.status == "failed" for row in group_rows):
        return "ошибка доставки"
    if any(row.final_attachment_id and row.status not in {"sent", "failed"} for row in group_rows):
        return "отправляется клиенту"
    if all(row.final_attachment_id and row.status == "sent" for row in group_rows):
        return "отправлено"
    if any(not row.final_attachment_id and row.status == "uploading" for row in group_rows):
        return "подготовка файлов" if count > 1 else "подготовка файла"
    if all(not row.final_attachment_id and row.status == "ready" for row in group_rows):
        return "готово к отправке" if count > 1 else "готов к отправке"
    return "подготовка файлов" if count > 1 else "подготовка файла"


def _archive_rows_for_display(group_rows: list[TicketAttachmentPreload]) -> list[TicketAttachmentPreload]:
    rows = []
    for row in group_rows:
        if row.final_attachment_id:
            if row.status != "cancelled":
                rows.append(row)
            continue
        if row.status in {"uploading", "ready"}:
            rows.append(row)
    return rows


def _group_leader_preload(group_rows: list[TicketAttachmentPreload]) -> TicketAttachmentPreload | None:
    if not group_rows:
        return None
    return sorted(group_rows, key=lambda row: (row.created_at or datetime.min, row.id))[0]


def _group_has_preparing_preloads(group_rows: list[TicketAttachmentPreload]) -> bool:
    return any(not row.final_attachment_id and row.status == "uploading" for row in group_rows)


def _group_active_final_rows(group_rows: list[TicketAttachmentPreload]) -> list[TicketAttachmentPreload]:
    return [
        row for row in group_rows
        if row.final_attachment_id and row.status not in {"sent", "failed", "cancelled"}
    ]


def _snapshot_group_rows(group_rows: list[TicketAttachmentPreload]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=row.id,
            ticket_id=row.ticket_id,
            user_id=row.user_id,
            archive_group_id=row.archive_group_id,
            original_filename=row.original_filename,
            mime_type=row.mime_type,
            file_size=row.file_size,
            local_path=row.local_path,
            telegram_file_id=row.telegram_file_id,
            telegram_file_unique_id=row.telegram_file_unique_id,
            status=row.status,
            final_attachment_id=row.final_attachment_id,
            reply_to_message_id=row.reply_to_message_id,
            prefer_caption=row.prefer_caption,
            telegram_staging_info_message_id=row.telegram_staging_info_message_id,
            telegram_staging_file_message_id=row.telegram_staging_file_message_id,
            created_at=row.created_at,
        )
        for row in group_rows
    ]


def _archive_info_text(
    *,
    ticket: Ticket,
    user: User | None,
    group_rows: list[TicketAttachmentPreload],
    status_text: str,
    event_label: str | None = None,
    event_at: datetime | None = None,
) -> str:
    if not group_rows:
        raise RuntimeError("Cannot render archive info text without group rows")
    sorted_rows = sorted(group_rows, key=lambda row: (row.created_at or datetime.min, row.id))
    filenames = [((row.original_filename or "").strip() or "Файл") for row in sorted_rows]
    file_label = "Файлы:" if len(filenames) > 1 else "Файл:"
    prepared_at = sorted_rows[0].created_at
    lines = [
        _archive_ticket_header(ticket),
        f"Сотрудник: {telegram_html_text(_archive_actor_name(user))}",
        f"Подготовлено: {_format_project_dt(prepared_at)}",
        file_label,
    ]
    lines.extend(f"• {telegram_html_code(filename)}" for filename in filenames)
    if event_label and event_at is not None:
        lines.append(f"{event_label}: {_format_project_dt(event_at)}")
    lines.append(f"Статус: {status_text}")
    return "\n".join(lines)


def _prepare_archive_html_text(text: str) -> tuple[str, str | None]:
    return prepare_telegram_html_message(text)


def _group_key_from_preload(preload: TicketAttachmentPreload) -> tuple[str, str, str]:
    return preload.ticket_id, preload.user_id, _archive_group_id(preload)


def _load_group_preloads(
    db,
    *,
    ticket_id: str,
    user_id: str,
    archive_group_id: str,
    for_update: bool = False,
) -> list[TicketAttachmentPreload]:
    stmt = (
        select(TicketAttachmentPreload)
        .where(
            TicketAttachmentPreload.ticket_id == ticket_id,
            TicketAttachmentPreload.user_id == user_id,
            TicketAttachmentPreload.archive_group_id == archive_group_id,
        )
        .order_by(asc(TicketAttachmentPreload.created_at), asc(TicketAttachmentPreload.id))
    )
    if for_update:
        stmt = _lock_stmt(stmt, db)
    return db.execute(stmt).scalars().all()


def _actual_info_message_id(group_rows: list[TicketAttachmentPreload]) -> str | None:
    for row in group_rows:
        message_id = str(row.telegram_staging_info_message_id or "").strip()
        if message_id and not _is_pending_archive_message_id(message_id):
            return message_id
    return None


def _pending_info_message_id(group_rows: list[TicketAttachmentPreload]) -> str | None:
    for row in group_rows:
        message_id = str(row.telegram_staging_info_message_id or "").strip()
        if _is_pending_archive_message_id(message_id):
            return message_id
    return None


def _delete_archive_message_by_id(message_id: str | None) -> None:
    message_id_value = str(message_id or "").strip()
    if not message_id_value or _is_pending_archive_message_id(message_id_value):
        return
    try:
        delete_message(_staging_chat_id(), message_id_value)
    except (RuntimeError, TelegramApiError):
        logger.warning("Failed to delete technical archive message %s", message_id_value, exc_info=True)


def _clear_pending_info_reservation(db, preload: TicketAttachmentPreload) -> bool:
    if not _is_pending_archive_message_id(preload.telegram_staging_info_message_id):
        return False
    preload.telegram_staging_info_message_id = None
    db.flush()
    return True


def _best_effort_delete_preload_file_message(preload: TicketAttachmentPreload) -> None:
    _delete_archive_message_by_id(preload.telegram_staging_file_message_id)


def _best_effort_delete_preload_info_message(preload: TicketAttachmentPreload) -> None:
    _delete_archive_message_by_id(preload.telegram_staging_info_message_id)


def _best_effort_delete_preload_archive(preload: TicketAttachmentPreload) -> None:
    _best_effort_delete_preload_info_message(preload)
    _best_effort_delete_preload_file_message(preload)


def _best_effort_edit_group_archive_message(
    *,
    group_rows: list[TicketAttachmentPreload],
    ticket: Ticket,
    user: User | None,
    status_text: str,
    event_label: str | None = None,
    event_at: datetime | None = None,
    force: bool = False,
) -> None:
    message_id = _actual_info_message_id(group_rows)
    if not message_id:
        return
    active_final_rows = _group_active_final_rows(group_rows)
    if not force and event_label is None and _group_has_preparing_preloads(group_rows):
        return
    if not force and event_label is not None and len(active_final_rows) > 1:
        return
    try:
        chat_id = _staging_chat_id()
    except RuntimeError:
        return
    archive_text = _archive_info_text(
        ticket=ticket,
        user=user,
        group_rows=group_rows,
        status_text=status_text,
        event_label=event_label,
        event_at=event_at,
    )
    try:
        payload_text, parse_mode = _prepare_archive_html_text(archive_text)
        edit_message_text(chat_id, message_id, payload_text, parse_mode=parse_mode)
    except TelegramApiError as exc:
        leader = group_rows[0].id if group_rows else "unknown"
        description = str(exc)
        lowered = description.lower()
        if "message is not modified" in lowered:
            logger.debug("Skipped unchanged technical archive edit for group leader=%s", leader)
            return
        if "too many requests" in lowered or "retry after" in lowered:
            logger.warning("Technical archive edit rate-limited for group leader=%s: %s", leader, description)
            return
        logger.warning("Failed to edit technical archive info message for group leader=%s: %s", leader, description)
    except RuntimeError as exc:
        leader = group_rows[0].id if group_rows else "unknown"
        logger.warning("Failed to edit technical archive info message for group leader=%s: %s", leader, exc)


def _send_preload_archive_info(
    *,
    ticket: Ticket,
    user: User | None,
    group_rows: list[TicketAttachmentPreload],
    status_text: str,
) -> str:
    payload_text, parse_mode = _prepare_archive_html_text(
        _archive_info_text(
            ticket=ticket,
            user=user,
            group_rows=group_rows,
            status_text=status_text,
        )
    )
    response = send_message(
        _staging_chat_id(),
        payload_text,
        parse_mode=parse_mode,
    )
    message_id = _telegram_message_id(response)
    if not message_id:
        raise RuntimeError("Telegram staging info message returned no message_id")
    return message_id


def _send_staging_document(
    *,
    preload: TicketAttachmentPreload,
) -> tuple[str, str | None, str]:
    return _send_staging_document_from_path(
        local_path=preload.local_path,
        original_filename=preload.original_filename,
        mime_type=preload.mime_type,
    )


def _send_staging_document_from_path(
    *,
    local_path: str,
    original_filename: str | None,
    mime_type: str | None,
) -> tuple[str, str | None, str]:
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))

    with STAGING_BINARY_UPLOAD_SEMAPHORE:
        response = send_document(
            _staging_chat_id(),
            path,
            filename=original_filename or path.name,
            mime_type=mime_type or "application/octet-stream",
            timeout=STAGING_BINARY_UPLOAD_TIMEOUT_SECONDS,
        )
    result = (response or {}).get("result") or {}
    document = result.get("document") or {}
    media = result.get("video") or document

    file_id = str(media.get("file_id") or "").strip()
    file_unique_id = str(media.get("file_unique_id") or "").strip() or None
    message_id = str(result.get("message_id") or "").strip()
    if not file_id:
        raise RuntimeError("Telegram staging upload returned no file_id")
    if not message_id:
        raise RuntimeError("Telegram staging upload returned no message_id")
    return file_id, file_unique_id, message_id


def _ensure_group_archive_info_message(*, preload_id: str) -> str:
    for _attempt in range(ARCHIVE_INFO_WAIT_ATTEMPTS):
        db = SessionLocal()
        try:
            preload = db.get(TicketAttachmentPreload, preload_id)
            if preload is None:
                return ""
            _ensure_archive_group_id(db, preload)
            ticket_id, user_id, archive_group_id = _group_key_from_preload(preload)
            group_rows = _load_group_preloads(
                db,
                ticket_id=ticket_id,
                user_id=user_id,
                archive_group_id=archive_group_id,
                for_update=False,
            )
            group_rows = _archive_rows_for_display(group_rows)
            leader = _group_leader_preload(group_rows)
            if leader is None:
                db.commit()
                return ""
            actual_message_id = _actual_info_message_id(group_rows)
            if actual_message_id:
                if preload.telegram_staging_info_message_id != actual_message_id:
                    preload.telegram_staging_info_message_id = actual_message_id
                db.commit()
                return actual_message_id

            token = _archive_pending_token(leader.id)
            pending_message_id = _pending_info_message_id(group_rows)
            if not pending_message_id:
                locked_leader = _load_locked_preload(db, preload_id=leader.id)
                if locked_leader is None:
                    db.rollback()
                    ticket = None
                    user = None
                    snapshot_rows = []
                else:
                    refreshed_group_rows = _archive_rows_for_display(
                        _load_group_preloads(
                            db,
                            ticket_id=ticket_id,
                            user_id=user_id,
                            archive_group_id=archive_group_id,
                            for_update=False,
                        )
                    )
                    refreshed_leader = _group_leader_preload(refreshed_group_rows)
                    actual_message_id = _actual_info_message_id(refreshed_group_rows)
                    pending_message_id = _pending_info_message_id(refreshed_group_rows)
                    if actual_message_id:
                        if preload.telegram_staging_info_message_id != actual_message_id:
                            preload.telegram_staging_info_message_id = actual_message_id
                        db.commit()
                        return actual_message_id
                    if pending_message_id or refreshed_leader is None or refreshed_leader.id != locked_leader.id:
                        db.rollback()
                        ticket = None
                        user = None
                        snapshot_rows = []
                    else:
                        locked_leader.telegram_staging_info_message_id = token
                        if preload.id == locked_leader.id:
                            preload.telegram_staging_info_message_id = token
                        db.commit()
                        ticket = db.get(Ticket, preload.ticket_id)
                        user = db.get(User, preload.user_id)
                        snapshot_rows = _snapshot_group_rows(refreshed_group_rows)
            else:
                db.rollback()
                ticket = None
                user = None
                snapshot_rows = []
        finally:
            db.close()

        if snapshot_rows:
            created_message_id = _send_preload_archive_info(
                ticket=ticket,
                user=user,
                group_rows=snapshot_rows,
                status_text=_group_status_text(snapshot_rows),
            )

            finalize_db = SessionLocal()
            try:
                preload = finalize_db.get(TicketAttachmentPreload, preload_id)
                if preload is None:
                    _delete_archive_message_by_id(created_message_id)
                    return ""
                _ensure_archive_group_id(finalize_db, preload)
                ticket_id, user_id, archive_group_id = _group_key_from_preload(preload)
                group_rows = _load_group_preloads(
                    finalize_db,
                    ticket_id=ticket_id,
                    user_id=user_id,
                    archive_group_id=archive_group_id,
                    for_update=False,
                )
                group_rows = _archive_rows_for_display(group_rows)
                leader = _group_leader_preload(group_rows)
                if leader is None:
                    finalize_db.rollback()
                    _delete_archive_message_by_id(created_message_id)
                    return ""
                actual_message_id = _actual_info_message_id(group_rows)
                if actual_message_id and actual_message_id != created_message_id:
                    finalize_db.rollback()
                    _delete_archive_message_by_id(created_message_id)
                    return actual_message_id
                if leader.id == preload.id:
                    preload.telegram_staging_info_message_id = created_message_id
                else:
                    leader_row = _load_locked_preload(finalize_db, preload_id=leader.id)
                    if leader_row is None:
                        finalize_db.rollback()
                        _delete_archive_message_by_id(created_message_id)
                        continue
                    leader_row.telegram_staging_info_message_id = created_message_id
                finalize_db.commit()
                return created_message_id
            finally:
                finalize_db.close()

        time.sleep(ARCHIVE_INFO_WAIT_DELAY_SECONDS)

    raise RuntimeError("Failed to coordinate common technical archive info message")


def _discard_preload(
    db,
    preload: TicketAttachmentPreload,
    *,
    delete_row: bool,
    unlink_local: bool,
) -> None:
    _ensure_archive_group_id(db, preload)
    ticket_id, user_id, archive_group_id = _group_key_from_preload(preload)
    info_message_id = _actual_info_message_id([preload]) or str(preload.telegram_staging_info_message_id or "").strip()
    cleanup_path = preload.local_path
    _best_effort_delete_preload_file_message(preload)

    if delete_row:
        db.delete(preload)
    else:
        preload.status = "cancelled"
    db.flush()

    remaining_rows = _archive_rows_for_display(
        _load_group_preloads(
            db,
            ticket_id=ticket_id,
            user_id=user_id,
            archive_group_id=archive_group_id,
            for_update=False,
        )
    )
    if remaining_rows:
        if info_message_id and not _actual_info_message_id(remaining_rows):
            next_leader = _group_leader_preload(remaining_rows)
            if next_leader is not None:
                next_leader.telegram_staging_info_message_id = info_message_id
        ticket = db.get(Ticket, ticket_id)
        user = db.get(User, user_id)
        if ticket is not None:
            _best_effort_edit_group_archive_message(
                group_rows=remaining_rows,
                ticket=ticket,
                user=user,
                status_text=_group_status_text(remaining_rows),
                force=True,
            )
    else:
        _delete_archive_message_by_id(info_message_id)

    db.commit()
    if unlink_local:
        _safe_unlink(cleanup_path)


def cleanup_stale_attachment_preloads(db) -> int:
    threshold = datetime.utcnow() - timedelta(hours=PRELOAD_STALE_TTL_HOURS)
    stale_rows = db.execute(
        select(TicketAttachmentPreload)
        .where(
            TicketAttachmentPreload.final_attachment_id.is_(None),
            TicketAttachmentPreload.created_at < threshold,
        )
        .order_by(asc(TicketAttachmentPreload.created_at), asc(TicketAttachmentPreload.id))
    ).scalars().all()
    removed = 0
    for stale in stale_rows:
        current = db.get(TicketAttachmentPreload, stale.id)
        if current is None or current.final_attachment_id:
            continue
        _discard_preload(db, current, delete_row=True, unlink_local=True)
        removed += 1
    return removed


def _mark_delivery_failed(
    db,
    *,
    ticket: Ticket,
    attachment: TicketAttachment,
    failure_note: str,
    create_note: bool = True,
) -> None:
    attachment.telegram_delivery_status = "failed"
    db.commit()
    if not create_note:
        return
    record_client_delivery_failure(db=db, ticket=ticket, body=failure_note)
    db.commit()


def _deliver_attachment_to_client(
    db,
    *,
    attachment: TicketAttachment,
    message: TicketMessage,
    ticket: Ticket,
    reply_to_message_id: str | None = None,
    prefer_caption: bool = False,
) -> None:
    filename = attachment.original_filename or "файл"
    file_id = str(attachment.telegram_file_id or "").strip()
    if not file_id:
        raise RuntimeError("Attachment has no telegram_file_id for final delivery")

    if message.message_status == "deleted":
        attachment.telegram_delivery_status = "failed"
        db.commit()
        return

    if ticket.channel == "web":
        # The file is already archived; WEB history uses the persisted file ID.
        attachment.telegram_delivery_status = "sent"
        db.commit()
        return

    contact = db.execute(
        select(TelegramContact).where(TelegramContact.client_id == ticket.client_id)
    ).scalar_one_or_none()
    if contact is None:
        _mark_delivery_failed(
            db,
            ticket=ticket,
            attachment=attachment,
            failure_note=f"Не удалось отправить клиенту вложение «{filename}»: Telegram-контакт не найден.",
        )
        return

    body = (message.body or "").strip()
    try:
        if body and prefer_caption and _caption_compatible_text(body):
            logger.info("attachment=%s final_send_by_file_id", attachment.id)
            payload_caption, parse_mode = prepare_telegram_html_message(body)
            response = send_document_by_file_id(
                contact.external_chat_id,
                file_id,
                caption=payload_caption,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
            )
            final_message_id = _telegram_message_id(response)
            message.telegram_message_id = final_message_id
            attachment.telegram_message_id = final_message_id
        elif body:
            payload_text, parse_mode = prepare_telegram_html_message(body)
            text_response = send_message(
                contact.external_chat_id,
                payload_text,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
            )
            logger.info("attachment=%s final_send_by_file_id", attachment.id)
            file_response = send_document_by_file_id(contact.external_chat_id, file_id)
            message.telegram_message_id = _telegram_message_id(text_response)
            attachment.telegram_message_id = _telegram_message_id(file_response)
        else:
            logger.info("attachment=%s final_send_by_file_id", attachment.id)
            file_response = send_document_by_file_id(
                contact.external_chat_id,
                file_id,
                reply_to_message_id=reply_to_message_id,
            )
            attachment.telegram_message_id = _telegram_message_id(file_response)
    except TelegramApiError as exc:
        _mark_delivery_failed(
            db,
            ticket=ticket,
            attachment=attachment,
            failure_note=f"Не удалось отправить клиенту вложение «{filename}» в Telegram: {exc}",
        )
        return

    attachment.telegram_delivery_status = "sent"
    db.commit()


def _load_entities(db, *, attachment_id: str, message_id: str, ticket_id: str):
    attachment = db.get(TicketAttachment, attachment_id)
    message = db.get(TicketMessage, message_id)
    ticket = db.get(Ticket, ticket_id)
    if attachment is None or message is None or ticket is None:
        return None, None, None
    if attachment.message_id != message_id or attachment.ticket_id != ticket_id or message.ticket_id != ticket_id:
        return None, None, None
    return attachment, message, ticket


def _load_locked_preload(db, *, preload_id: str) -> TicketAttachmentPreload | None:
    stmt = _lock_stmt(
        select(TicketAttachmentPreload).where(TicketAttachmentPreload.id == preload_id),
        db,
    )
    return db.execute(stmt).scalar_one_or_none()


def _finalize_claimed_preload_archive(*, preload_id: str, final_status: str) -> None:
    db = SessionLocal()
    try:
        preload = _load_locked_preload(db, preload_id=preload_id)
        if preload is None or not preload.final_attachment_id:
            return
        _ensure_archive_group_id(db, preload)
        preload.status = final_status
        ticket_id, user_id, archive_group_id = _group_key_from_preload(preload)
        group_rows = _load_group_preloads(
            db,
            ticket_id=ticket_id,
            user_id=user_id,
            archive_group_id=archive_group_id,
            for_update=False,
        )
        display_rows = _archive_rows_for_display(group_rows)
        ticket = db.get(Ticket, ticket_id)
        user = db.get(User, user_id)
        status_text = _group_status_text(display_rows)
        if final_status == "ready" and ticket is not None and ticket.workflow_type == TicketWorkflowType.TASK.value:
            if not any(row.status == "failed" for row in display_rows):
                status_text = "подготовка файлов" if any(row.status == "uploading" for row in display_rows) else "готово к отправке"
        terminal = final_status != "ready" and status_text in {"отправлено", "ошибка доставки"}
        db.commit()
        if ticket is not None and display_rows:
            event_label = None
            if status_text == "отправлено":
                event_label = "Отправлено клиенту"
            elif status_text == "ошибка доставки":
                event_label = "Отправка из ES DESK"
            _best_effort_edit_group_archive_message(
                group_rows=display_rows,
                ticket=ticket,
                user=user,
                status_text=status_text,
                event_label=event_label,
                event_at=datetime.now(timezone.utc) if event_label else None,
            )
        if terminal:
            cleanup_db = SessionLocal()
            try:
                cleanup_rows = _load_group_preloads(
                    cleanup_db,
                    ticket_id=ticket_id,
                    user_id=user_id,
                    archive_group_id=archive_group_id,
                    for_update=False,
                )
                for row in cleanup_rows:
                    cleanup_db.delete(row)
                cleanup_db.commit()
            finally:
                cleanup_db.close()
    finally:
        db.close()


def deliver_ticket_attachment(
    *,
    attachment_id: str,
    message_id: str,
    ticket_id: str,
    reply_to_message_id: str | None = None,
    prefer_caption: bool = False,
    preload_id: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        attachment, message, ticket = _load_entities(
            db,
            attachment_id=attachment_id,
            message_id=message_id,
            ticket_id=ticket_id,
        )
        if attachment is None or message is None or ticket is None:
            return
        if ticket.workflow_type == TicketWorkflowType.TASK.value and message.message_status == "deleted":
            return
        if attachment.telegram_delivery_status not in {"uploading", "sending"}:
            return

        if preload_id:
            preload = db.get(TicketAttachmentPreload, preload_id)
            if preload is not None and preload.final_attachment_id == attachment.id:
                _ensure_archive_group_id(db, preload)
                preload.status = "sending"
                db.commit()
                ticket_id_value, user_id, archive_group_id = _group_key_from_preload(preload)
                group_rows = _archive_rows_for_display(
                    _load_group_preloads(
                        db,
                        ticket_id=ticket_id_value,
                        user_id=user_id,
                        archive_group_id=archive_group_id,
                        for_update=False,
                    )
                )
                user = db.get(User, preload.user_id)
                _best_effort_edit_group_archive_message(
                    group_rows=group_rows,
                    ticket=ticket,
                    user=user,
                    status_text="отправляется клиенту",
                    event_label="Отправка из ES DESK",
                    event_at=datetime.now(timezone.utc),
                )

        if not (attachment.telegram_file_id or "").strip():
            filename = attachment.original_filename or "файл"
            cleanup_path = attachment.local_path
            try:
                file_id, file_unique_id, _staging_message_id = _send_staging_document_from_path(
                    local_path=attachment.local_path,
                    original_filename=attachment.original_filename,
                    mime_type=attachment.mime_type,
                )
            except FileNotFoundError:
                _mark_delivery_failed(
                    db,
                    ticket=ticket,
                    attachment=attachment,
                    failure_note=f"Не удалось отправить клиенту вложение «{filename}»: локальный файл отсутствует.",
                )
                if preload_id:
                    _finalize_claimed_preload_archive(preload_id=preload_id, final_status="failed")
                return
            except (RuntimeError, TelegramApiError) as exc:
                _mark_delivery_failed(
                    db,
                    ticket=ticket,
                    attachment=attachment,
                    failure_note=f"Не удалось отправить клиенту вложение «{filename}» в Telegram: {exc}",
                )
                if preload_id:
                    _finalize_claimed_preload_archive(preload_id=preload_id, final_status="failed")
                return
            attachment.telegram_file_id = file_id
            attachment.telegram_file_unique_id = file_unique_id
            db.commit()
            db.refresh(attachment)
            db.refresh(message)
            if ticket.workflow_type == TicketWorkflowType.TASK.value and message.message_status == "deleted":
                _delete_archive_message_by_id(_staging_message_id)
                attachment.telegram_delivery_status = "failed"
                db.commit()
                _unlink_attachment_local_file(cleanup_path)
                return
            _unlink_attachment_local_file(cleanup_path)

        _deliver_attachment_to_client(
            db,
            attachment=attachment,
            message=message,
            ticket=ticket,
            reply_to_message_id=reply_to_message_id,
            prefer_caption=prefer_caption,
        )
        if preload_id and attachment.telegram_delivery_status in {"sent", "failed"}:
            _finalize_claimed_preload_archive(preload_id=preload_id, final_status=attachment.telegram_delivery_status)
    finally:
        db.close()


def _mark_preload_preparation_failed(db, preload: TicketAttachmentPreload) -> bool:
    """Keep a claimed task attachment linked, including claims made during staging."""
    ticket = db.get(Ticket, preload.ticket_id)
    task = ticket is not None and ticket.workflow_type == TicketWorkflowType.TASK.value
    if task:
        db.refresh(preload, with_for_update=True)
        if preload.status == "cancelled":
            return False
    preload.status = "failed"
    if task and preload.final_attachment_id:
        attachment = db.get(TicketAttachment, preload.final_attachment_id)
        message = db.get(TicketMessage, attachment.message_id) if attachment is not None else None
        if message is not None and message.message_type == MessageType.AGENT.value:
            attachment.telegram_delivery_status = "failed"
            return True
    return False


def deliver_attachment_preload(*, preload_id: str) -> None:
    staged_task_file = False
    db = SessionLocal()
    try:
        preload = db.get(TicketAttachmentPreload, preload_id)
        if preload is None:
            return
        if not (preload.archive_group_id or "").strip():
            _ensure_archive_group_id(db, preload)
            db.commit()
            db.refresh(preload)
        if preload.status == "cancelled":
            _discard_preload(db, preload, delete_row=True, unlink_local=True)
            return
        if preload.status not in {"uploading", "ready"}:
            return

        ticket = db.get(Ticket, preload.ticket_id)
        user = db.get(User, preload.user_id)
        if ticket is None:
            preload.status = "failed"
            db.commit()
            return

        if ticket.workflow_type == TicketWorkflowType.TASK.value and preload.final_attachment_id:
            attachment = db.get(TicketAttachment, preload.final_attachment_id)
            message = db.get(TicketMessage, attachment.message_id) if attachment is not None else None
            if message is not None and message.message_status == "deleted":
                _discard_preload(db, preload, delete_row=True, unlink_local=True)
                return

        if not str(preload.telegram_staging_info_message_id or "").strip() or _is_pending_archive_message_id(preload.telegram_staging_info_message_id):
            try:
                preload.telegram_staging_info_message_id = _ensure_group_archive_info_message(preload_id=preload.id)
                db.commit()
                db.refresh(preload)
            except (RuntimeError, TelegramApiError) as exc:
                db.rollback()
                db.expire(preload)
                db.refresh(preload)
                _mark_preload_preparation_failed(db, preload)
                if preload.status == "cancelled":
                    _discard_preload(db, preload, delete_row=True, unlink_local=True)
                    return
                _clear_pending_info_reservation(db, preload)
                logger.warning("preload=%s filename=%s staging_failed=%s", preload.id, preload.original_filename, exc)
                db.commit()
                return

        db.refresh(preload)
        if preload.status == "cancelled":
            _discard_preload(db, preload, delete_row=True, unlink_local=True)
            return

        if not (preload.telegram_file_id or "").strip():
            try:
                file_id, file_unique_id, file_message_id = _send_staging_document(preload=preload)
                staged_task_file = ticket.workflow_type == TicketWorkflowType.TASK.value
            except FileNotFoundError:
                claimed_task = _mark_preload_preparation_failed(db, preload)
                db.commit()
                if not claimed_task:
                    _discard_preload(db, preload, delete_row=True, unlink_local=True)
                logger.warning("preload=%s filename=%s staging_failed=file_missing", preload.id, preload.original_filename)
                return
            except (RuntimeError, TelegramApiError) as exc:
                claimed_task = _mark_preload_preparation_failed(db, preload)
                db.commit()
                if not claimed_task:
                    _discard_preload(db, preload, delete_row=True, unlink_local=True)
                logger.warning("preload=%s filename=%s staging_failed=%s", preload.id, preload.original_filename, exc)
                return
        else:
            file_id = str(preload.telegram_file_id).strip()
            file_unique_id = str(preload.telegram_file_unique_id or "").strip() or None
            file_message_id = str(preload.telegram_staging_file_message_id or "").strip()
    finally:
        db.close()

    finalize_db = SessionLocal()
    try:
        preload = _load_locked_preload(finalize_db, preload_id=preload_id)
        if preload is None:
            if staged_task_file:
                _delete_archive_message_by_id(file_message_id)
            return
        _ensure_archive_group_id(finalize_db, preload)
        ticket = finalize_db.get(Ticket, preload.ticket_id)
        user = finalize_db.get(User, preload.user_id)

        preload.telegram_file_id = file_id
        preload.telegram_file_unique_id = file_unique_id
        preload.telegram_staging_file_message_id = file_message_id or preload.telegram_staging_file_message_id
        cleanup_path = preload.local_path

        if preload.status == "cancelled":
            finalize_db.commit()
            _discard_preload(finalize_db, preload, delete_row=True, unlink_local=True)
            return

        if not preload.final_attachment_id:
            preload.status = "ready"
            finalize_db.commit()
            _safe_unlink(cleanup_path)
            if ticket is not None:
                group_rows = _archive_rows_for_display(
                    _load_group_preloads(
                        finalize_db,
                        ticket_id=preload.ticket_id,
                        user_id=preload.user_id,
                        archive_group_id=_archive_group_id(preload),
                        for_update=False,
                    )
                )
                _best_effort_edit_group_archive_message(
                    group_rows=group_rows,
                    ticket=ticket,
                    user=user,
                    status_text=_group_status_text(group_rows),
                )
            return

        attachment = finalize_db.get(TicketAttachment, preload.final_attachment_id)
        if attachment is None:
            finalize_db.delete(preload)
            finalize_db.commit()
            _safe_unlink(cleanup_path)
            return

        message = finalize_db.get(TicketMessage, attachment.message_id)
        if message is None or ticket is None:
            finalize_db.delete(preload)
            finalize_db.commit()
            _safe_unlink(cleanup_path)
            return

        if ticket.workflow_type == TicketWorkflowType.TASK.value and message.message_status == "deleted":
            _discard_preload(finalize_db, preload, delete_row=True, unlink_local=True)
            return

        attachment.telegram_file_id = file_id
        attachment.telegram_file_unique_id = file_unique_id
        already_sent = attachment.telegram_delivery_status == "sent" or bool(str(attachment.telegram_message_id or "").strip())
        reply_to_message_id = preload.reply_to_message_id
        prefer_caption = bool(preload.prefer_caption)
        attachment_id = attachment.id
        message_id = message.id
        ticket_id = ticket.id
        if already_sent:
            preload.status = "sent"
            finalize_db.commit()
            _safe_unlink(cleanup_path)
            _finalize_claimed_preload_archive(preload_id=preload.id, final_status="sent")
            return

        if ticket.workflow_type == TicketWorkflowType.TASK.value and message.message_type == MessageType.AGENT.value:
            preload.status = "ready"
            attachment.telegram_delivery_status = "ready"
            attachment.telegram_archive_message_id = file_message_id or None
            finalize_db.commit()
            _safe_unlink(cleanup_path)
            _finalize_claimed_preload_archive(preload_id=preload.id, final_status="ready")
            return

        preload.status = "sending"
        attachment.telegram_delivery_status = "sending"
        finalize_db.commit()
        _safe_unlink(cleanup_path)
    finally:
        finalize_db.close()

    deliver_ticket_attachment(
        attachment_id=attachment_id,
        message_id=message_id,
        ticket_id=ticket_id,
        reply_to_message_id=reply_to_message_id,
        prefer_caption=prefer_caption,
        preload_id=preload_id,
    )


def claim_attachment_preload(
    db,
    *,
    preload_id: str,
    ticket_id: str,
    user_id: str,
) -> TicketAttachmentPreload | None:
    stmt = _lock_stmt(
        select(TicketAttachmentPreload).where(
            TicketAttachmentPreload.id == preload_id,
            TicketAttachmentPreload.ticket_id == ticket_id,
            TicketAttachmentPreload.user_id == user_id,
        ),
        db,
    )
    preload = db.execute(stmt).scalar_one_or_none()
    if preload is None or preload.final_attachment_id:
        return None
    return preload
