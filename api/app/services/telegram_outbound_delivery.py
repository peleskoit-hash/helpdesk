from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, asc, event, inspect, or_, select
from sqlalchemy.orm import Session, object_session

from ..config import settings
from ..db import SessionLocal
from ..models import Client, MessageType, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketWorkflowType, User, WebClientDraft
from .telegram_bot import TelegramApiError, delete_message, edit_message_text, send_document, send_document_by_file_id, send_message
from .telegram_text_format import prepare_telegram_html_message, strip_telegram_html, telegram_html_code, telegram_html_link, telegram_html_text
from .ticket_links import public_ticket_url
from .ticket_messages import TELEGRAM_CAPTION_LIMIT, record_client_delivery_failure


from . import telegram_upload_state as upload_state
from .attachment_preload_owners import archive_owner, client_bound_attachment, client_preload_allowed, owner_scope

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


def _safe_unlink(path_value: str | None, *, db=None, archived=False, ignored_attachment_ids=(), ignored_preload_ids=()) -> bool:
    path = Path(path_value or "")
    try:
        if not path.is_file():
            return True
        if db is not None:
            references = [row for row in upload_state.source_references(db, path_value)
                if not (isinstance(row, TicketAttachment) and row.id in ignored_attachment_ids)
                and not (isinstance(row, TicketAttachmentPreload) and row.id in ignored_preload_ids)]
            if any(not archived or (isinstance(row, TicketAttachmentPreload) and row.status == "failed")
                   or not upload_state.archived_document(row) for row in references):
                return True
        path.unlink()
        return True
    except OSError:
        logger.warning("Failed to remove file %s", path, exc_info=True)
        return False


def _release_prepared_source(row, path):
    if getattr(row, "composer_draft_id", None) is not None:
        return  # Draft previews must remain available independently of a Ticket.
    if upload_state.archived_document(row):
        _safe_unlink(path, db=object_session(row), archived=True)


def _unlink_attachment_local_file(path_value: str | None, db=None) -> None:
    _safe_unlink(path_value, db=db)


def _project_dt(value: datetime | None) -> datetime:
    source = value or datetime.now(timezone.utc)
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc)
    return source.astimezone(PROJECT_TIMEZONE)


def _format_project_dt(value: datetime | None) -> str:
    return _project_dt(value).strftime("%d.%m.%Y %H:%M")


def _archive_ticket_header(ticket: Ticket) -> str:
    if ticket.channel == "web" and ticket.ticket_number is None:
        return "WEB-черновик"
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
    if any(row.status == "failed" and not row.final_attachment_id for row in group_rows):
        return "ошибка подготовки"
    if any(row.status == "failed" for row in group_rows):
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
        if row.status in {"uploading", "ready", "failed"}:
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
            client_id=row.client_id,
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
    ticket: Ticket | None,
    user: User | Client | None,
    group_rows: list[TicketAttachmentPreload],
    status_text: str,
    event_label: str | None = None,
    event_at: datetime | None = None,
) -> str:
    if not group_rows:
        raise RuntimeError("Cannot render archive info text without group rows")
    sorted_rows = sorted(group_rows, key=lambda row: (row.created_at or datetime.min, row.id))
    filenames = [((row.original_filename or "").strip() or "Файл") for row in sorted_rows]
    is_client = isinstance(user, Client) or getattr(sorted_rows[0], 'client_id', None) is not None
    file_label = "Файлы:" if len(filenames) > 1 else "Файл:"
    prepared_at = sorted_rows[0].created_at
    lines = [
        _archive_ticket_header(ticket) if ticket is not None else "WEB-черновик",
        (f"Клиент: {telegram_html_text(getattr(user, 'display_name', None) or 'Клиент не найден')}" if is_client
         else f"Сотрудник: {telegram_html_text(_archive_actor_name(user))}"),
        f"Подготовлено: {_format_project_dt(prepared_at)}",
        file_label,
    ]
    lines.extend(f"• {telegram_html_code(filename)}" for filename in filenames)
    if event_label and event_at is not None:
        lines.append(f"{event_label}: {_format_project_dt(event_at)}")
    created_web_task = (is_client and ticket is not None and ticket.channel == "web"
        and ticket.workflow_type == TicketWorkflowType.TASK.value and ticket.task_kind == "normal"
        and ticket.intake_state == "ready" and ticket.ticket_number is not None and public_ticket_url(ticket))
    if not created_web_task:
        lines.append(f"Статус: {status_text}")
    return "\n".join(lines)


def _prepare_archive_html_text(text: str) -> tuple[str, str | None]:
    return prepare_telegram_html_message(text)


def _group_key_from_preload(preload: TicketAttachmentPreload) -> tuple[str | None, str | None, str | None, str]:
    return preload.ticket_id, preload.user_id, preload.client_id, _archive_group_id(preload)


def _load_group_preloads(
    db,
    *,
    ticket_id: str | None,
    user_id: str | None,
    archive_group_id: str,
    client_id: str | None = None,
    for_update: bool = False,
) -> list[TicketAttachmentPreload]:
    stmt = (
        select(TicketAttachmentPreload)
        .where(
            TicketAttachmentPreload.ticket_id == ticket_id,
            *owner_scope(user_id=user_id, client_id=client_id),
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


def _delete_archive_message_by_id(message_id: str | None) -> bool:
    message_id_value = str(message_id or "").strip()
    if not message_id_value or _is_pending_archive_message_id(message_id_value):
        return True
    try:
        delete_message(_staging_chat_id(), message_id_value)
        return True
    except (RuntimeError, TelegramApiError) as exc:
        if 'message to delete not found' in str(exc).lower():
            return True
        logger.warning("Failed to delete technical archive message %s (%s)", message_id_value, type(exc).__name__)
        return False


def _preload_archive_ids(row):
    info = upload_state.state(row)
    return {str(mid) for mid in [row.telegram_staging_file_message_id, row.telegram_staging_info_message_id,
        (info.get('receipt') or {}).get('message_id'),
        *(value.get('message_id') for value in info.get('previous_receipts', []))] if mid and not _is_pending_archive_message_id(str(mid))}


def _archive_message_referenced(db, message_id, ignored_preload_ids=()):
    return bool(db.scalar(select(TicketAttachment.id).where(or_(
        TicketAttachment.telegram_archive_message_id == message_id,
        TicketAttachment.telegram_archive_info_message_id == message_id)).limit(1)) or db.scalar(
        select(TicketAttachmentPreload.id).where(TicketAttachmentPreload.id.not_in(ignored_preload_ids),
            TicketAttachmentPreload.status != 'cancelled', or_(
            TicketAttachmentPreload.telegram_staging_file_message_id == message_id,
            TicketAttachmentPreload.telegram_staging_info_message_id == message_id)).limit(1)))


def _discard_client_preload(db, preload):
    if client_bound_attachment(db, preload) is not None:
        return  # An accepted message owns the file; retiring a preload must not erase it.
    preload.status = 'cancelled'
    db.flush()
    remaining = _archive_rows_for_display(_load_group_preloads(db, ticket_id=preload.ticket_id,
        user_id=None, client_id=preload.client_id, archive_group_id=_archive_group_id(preload)))
    info_id = preload.telegram_staging_info_message_id
    failed = []
    for message_id in _preload_archive_ids(preload):
        if (message_id == info_id and remaining) or _archive_message_referenced(db, message_id, [preload.id]):
            continue
        if not _delete_archive_message_by_id(message_id):
            failed.append(message_id)
    if remaining:
        if info_id and not _actual_info_message_id(remaining):
            remaining[0].telegram_staging_info_message_id = info_id
        ticket = db.get(Ticket, preload.ticket_id) if preload.ticket_id is not None else None
        if (ticket is not None or preload.composer_draft_id is not None) and not _best_effort_edit_group_archive_message(group_rows=remaining, ticket=ticket,
                user=archive_owner(db, preload), status_text=_group_status_text(remaining), force=True):
            for row in remaining:
                upload_state.patch(row, archive_reconcile_pending=True)
    active = bool((upload_state.state(preload).get('staging_attempt') or {}).get('active')) and not upload_state.attempt_expired(preload, 'staging_attempt')
    path, row_id = preload.local_path, preload.id
    upload_state.patch(preload, cleanup_pending=True, cleanup_failed_message_ids=failed,
        error_code='archive_cleanup_failed' if failed else None)
    db.commit()
    if not active:
        if not _safe_unlink(path, db=db, ignored_preload_ids=[row_id]):
            upload_state.patch(preload, cleanup_local_failed=True, error_code='local_cleanup_failed')
            db.commit()
        elif not failed:
            if preload.composer_draft_id is not None:
                # Keep a tombstone so a delayed repeated upload cannot resurrect a deleted file.
                preload.telegram_staging_info_message_id = None
                preload.telegram_staging_file_message_id = None
                upload_state.patch(preload, receipt=None, previous_receipts=[], cleanup_pending=False)
            else:
                db.delete(preload)
            db.commit()


def cleanup_client_preloads(db, ticket_id):
    rows = db.scalars(select(TicketAttachmentPreload).where(TicketAttachmentPreload.ticket_id == ticket_id,
        TicketAttachmentPreload.client_id.is_not(None))).all()
    for candidate in rows:
        row = upload_state.lock_row(db, TicketAttachmentPreload, candidate.id)
        if row is not None and (row.status == 'cancelled' or not client_preload_allowed(db, row)):
            _discard_client_preload(db, row)


_DELETED_UPLOAD_CLEANUP = 'deleted_ticket_upload_cleanup'


def schedule_ticket_upload_delete(db, ticket_ids, warnings):
    """Detach preload FKs now; delete external resources only after the caller commits."""
    rows = db.scalars(_lock_stmt(select(TicketAttachmentPreload).where(
        TicketAttachmentPreload.ticket_id.in_(ticket_ids)).order_by(TicketAttachmentPreload.id), db)).all()
    paths = {row.local_path for row in rows if row.local_path}
    ids = set().union(*(_preload_archive_ids(row) for row in rows)) if rows else set()
    for row in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id.in_(ticket_ids))):
        if row.local_path: paths.add(row.local_path)
        ids.update(str(mid) for mid in [row.telegram_archive_message_id, row.telegram_archive_info_message_id] if mid)
    db.execute(TicketAttachmentPreload.__table__.delete().where(TicketAttachmentPreload.ticket_id.in_(ticket_ids)))
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(_DELETED_UPLOAD_CLEANUP, {}).setdefault(transaction, []).append((paths, ids, tuple(ticket_ids), warnings))


@event.listens_for(Session, 'after_commit')
def _delete_committed_ticket_uploads(db):
    pending = db.info.get(_DELETED_UPLOAD_CLEANUP, {})
    nested = db.get_nested_transaction()
    if nested is not None:
        pending.setdefault(nested.parent, []).extend(pending.pop(nested, []))
        return
    db.info.pop(_DELETED_UPLOAD_CLEANUP, None)
    for items in pending.values():
        for paths, ids, ticket_ids, warnings in items:
            try:
                with Session(bind=db.get_bind()) as cleanup_db:
                    for message_id in ids:
                        if not _archive_message_referenced(cleanup_db, message_id) and not _delete_archive_message_by_id(message_id):
                            warnings.append(f'Не удалось удалить сообщение технического архива {message_id}: {ticket_ids}')
                    for path in paths:
                        if not _safe_unlink(path, db=cleanup_db):
                            warnings.append(f'Не удалось удалить локальный файл {path}: {ticket_ids}')
            except Exception:
                warnings.append(f'Очистка вложений после удаления требует повтора: {ticket_ids}')
                logger.exception('Deleted ticket upload cleanup failed tickets=%s archive_ids=%s paths=%s', ticket_ids, sorted(ids), sorted(paths))


@event.listens_for(Session, 'after_rollback')
def _rollback_ticket_upload_delete(db):
    nested = db.get_nested_transaction()
    if nested is not None:
        db.info.get(_DELETED_UPLOAD_CLEANUP, {}).pop(nested, None)
    else:
        db.info.pop(_DELETED_UPLOAD_CLEANUP, None)


def _clear_pending_info_reservation(db, preload: TicketAttachmentPreload) -> bool:
    if not _is_pending_archive_message_id(preload.telegram_staging_info_message_id):
        return False
    preload.telegram_staging_info_message_id = None
    db.flush()
    return True


def _best_effort_delete_preload_file_message(preload: TicketAttachmentPreload) -> None:
    ids = [preload.telegram_staging_file_message_id]
    ids.extend(value.get("message_id") for value in upload_state.state(preload).get("previous_receipts", []))
    for message_id in dict.fromkeys(ids):
        _delete_archive_message_by_id(message_id)


def _best_effort_delete_preload_info_message(preload: TicketAttachmentPreload) -> None:
    _delete_archive_message_by_id(preload.telegram_staging_info_message_id)


def _best_effort_delete_preload_archive(preload: TicketAttachmentPreload) -> None:
    _best_effort_delete_preload_info_message(preload)
    _best_effort_delete_preload_file_message(preload)


def _best_effort_edit_group_archive_message(
    *,
    group_rows: list[TicketAttachmentPreload],
    ticket: Ticket | None,
    user: User | Client | None,
    status_text: str,
    event_label: str | None = None,
    event_at: datetime | None = None,
    force: bool = False,
) -> bool:
    message_id = _actual_info_message_id(group_rows)
    if not message_id:
        return False
    active_final_rows = _group_active_final_rows(group_rows)
    if not force and event_label is None and _group_has_preparing_preloads(group_rows):
        return False
    if not force and event_label is not None and len(active_final_rows) > 1:
        return False
    try:
        chat_id = _staging_chat_id()
    except RuntimeError:
        return False
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
        return True
    except TelegramApiError as exc:
        leader = group_rows[0].id if group_rows else "unknown"
        description = str(exc)
        lowered = description.lower()
        if "message is not modified" in lowered:
            logger.debug("Skipped unchanged technical archive edit for group leader=%s", leader)
            return True
        if "too many requests" in lowered or "retry after" in lowered:
            logger.warning("Technical archive edit rate-limited for group leader=%s", leader)
            return False
        logger.warning("Failed to edit technical archive info message for group leader=%s", leader)
    except RuntimeError as exc:
        leader = group_rows[0].id if group_rows else "unknown"
        logger.warning("Failed to edit technical archive info message for group leader=%s", leader)

    return False


def _send_preload_archive_info(
    *,
    ticket: Ticket | None,
    user: User | Client | None,
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
        retry_only_parse_errors=True,
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
        on_receipt=lambda response: upload_state.store_receipt(object_session(preload), preload, response),
        on_wait=lambda: upload_state.heartbeat(object_session(preload), preload, "staging_attempt"),
        allow_video=upload_state.preload_accepts_video(preload),
    )


def _send_staging_document_from_path(
    *,
    local_path: str,
    original_filename: str | None,
    mime_type: str | None,
    on_receipt=None,
    on_wait=None,
    allow_video: bool = False,
) -> tuple[str, str | None, str]:
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))

    while not STAGING_BINARY_UPLOAD_SEMAPHORE.acquire(timeout=30):
        if on_wait:
            on_wait()
    try:
        if on_wait:
            on_wait()
        response = send_document(
            _staging_chat_id(),
            path,
            filename=original_filename or path.name,
            mime_type=mime_type or "application/octet-stream",
            timeout=STAGING_BINARY_UPLOAD_TIMEOUT_SECONDS,
        )
    finally:
        STAGING_BINARY_UPLOAD_SEMAPHORE.release()
    if on_receipt:
        on_receipt(response)
    value = upload_state.receipt(response)
    if not upload_state.usable_receipt(value, allow_video=allow_video):
        raise RuntimeError("Telegram staging upload returned incomplete media receipt")
    return value["file_id"], value.get("file_unique_id"), value["message_id"]



def _lock_web_preload_draft(db, preload):
    if preload.client_id is not None:
        draft = db.scalar(select(WebClientDraft).where(WebClientDraft.client_id == preload.client_id))
        if draft is not None:
            upload_state.lock_row(db, WebClientDraft, draft.id)
            db.refresh(preload)


def synchronize_web_preload_archives(db, preload_ids):
    """Post-commit, retryable metadata reconciliation; never uploads binary data."""
    results = {}
    for preload_id in preload_ids:
        try:
            db.commit()
            row = db.get(TicketAttachmentPreload, preload_id)
            if row is None or row.status == 'cancelled' or row.client_id is None:
                continue
            _ensure_group_archive_info_message(preload_id=row.id)
            db.rollback()  # Discard snapshots taken before the archive coordination transaction.
            row = db.get(TicketAttachmentPreload, preload_id)
            if row is None:
                continue
            _lock_web_preload_draft(db, row)
            if row.status == 'cancelled':
                db.commit()
                continue
            key = _group_key_from_preload(row)
            rows = _archive_rows_for_display(_load_group_preloads(db, ticket_id=row.ticket_id,
                user_id=row.user_id, client_id=row.client_id, archive_group_id=_archive_group_id(row)))
            info_id = _actual_info_message_id(rows)
            if not info_id:
                db.commit()
                continue  # A concurrent split requires a fresh coordination attempt.
            ticket = db.get(Ticket, row.ticket_id) if row.ticket_id is not None else None
            if key not in results:
                results[key] = _best_effort_edit_group_archive_message(group_rows=rows, ticket=ticket,
                    user=archive_owner(db, row), status_text=_group_status_text(rows), force=True)
            # Other files may hold their own attempt/receipt locks. Reconcile only
            # this file; the group leader coordinates the shared Telegram message.
            row.telegram_staging_info_message_id = info_id
            upload_state.patch(row, archive_reconcile_pending=not results[key])
            if row.final_attachment_id:
                attachment = db.get(TicketAttachment, row.final_attachment_id)
                if attachment is not None:
                    attachment.telegram_archive_info_message_id = info_id
            db.commit()
        except Exception as exc:
            db.rollback()
            # The original transfer committed with this marker; leave it retryable.
            logger.warning('preload=%s archive reconciliation pending=%s', preload_id, type(exc).__name__)


def _ensure_group_archive_info_message(*, preload_id: str) -> str:
    for _attempt in range(ARCHIVE_INFO_WAIT_ATTEMPTS):
        db = SessionLocal()
        try:
            preload = db.get(TicketAttachmentPreload, preload_id)
            if preload is None:
                return ""
            _lock_web_preload_draft(db, preload)
            if preload.status == 'cancelled':
                return ''
            if not client_preload_allowed(db, preload):
                raise RuntimeError('Client preload owner or collecting task is unavailable')
            _ensure_archive_group_id(db, preload)
            ticket_id, user_id, client_id, archive_group_id = _group_key_from_preload(preload)
            group_rows = _load_group_preloads(
                db,
                ticket_id=ticket_id,
                user_id=user_id, client_id=client_id,
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
                            user_id=user_id, client_id=client_id,
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
                        ticket = db.get(Ticket, preload.ticket_id) if preload.ticket_id is not None else None
                        user = archive_owner(db, preload)
                        snapshot_rows = _snapshot_group_rows(refreshed_group_rows)
            else:
                db.rollback()
                ticket = None
                user = None
                snapshot_rows = []
        finally:
            db.close()

        if snapshot_rows:
            sent_group_key = (ticket_id, user_id, client_id, archive_group_id)
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
                _lock_web_preload_draft(finalize_db, preload)
                if _group_key_from_preload(preload) != sent_group_key or preload.status == 'cancelled':
                    finalize_db.rollback()
                    _delete_archive_message_by_id(created_message_id)
                    continue
                _ensure_archive_group_id(finalize_db, preload)
                ticket_id, user_id, client_id, archive_group_id = _group_key_from_preload(preload)
                group_rows = _load_group_preloads(
                    finalize_db,
                    ticket_id=ticket_id,
                    user_id=user_id, client_id=client_id,
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
    if preload.client_id is not None:
        return _discard_client_preload(db, preload)
    _ensure_archive_group_id(db, preload)
    ticket_id, user_id, client_id, archive_group_id = _group_key_from_preload(preload)
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
            user_id=user_id, client_id=client_id,
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
        user = archive_owner(db, preload)
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
        _safe_unlink(cleanup_path, db=db)


def recover_ticket_uploads(db, ticket_id):
    # Reconcile persisted attempts on the next card read, never resend a binary or client message.
    rows = db.scalars(select(TicketAttachmentPreload).where(TicketAttachmentPreload.ticket_id == ticket_id)).all()
    for row in rows:
        if row.status == "cancelled":
            row = upload_state.lock_row(db, TicketAttachmentPreload, row.id)
            if row is None:
                continue
            attachment = db.get(TicketAttachment, row.final_attachment_id) if row.final_attachment_id else None
            staging_active = (upload_state.state(row).get("staging_attempt") or {}).get("active")
            delivery_active = attachment is not None and (upload_state.state(attachment).get("delivery_attempt") or {}).get("active")
            if (staging_active and not upload_state.attempt_expired(row, "staging_attempt")) or (delivery_active and not upload_state.attempt_expired(attachment, "delivery_attempt")):
                continue
            _discard_preload(db, row, delete_row=True, unlink_local=True)
        elif row.status == "uploading":
            upload_state.expire_attempt(db, row)
        elif row.status == "failed" and upload_state.attempt_expired(row, "staging_attempt"):
            attempt = upload_state.state(row).get("staging_attempt") or {}
            if attempt.get("active"):
                upload_state.end_attempt(row, "staging_attempt", attempt.get("token"))
                db.commit()
        if row in db and row.status not in {"cancelled", "failed"} and upload_state.archived_document(row):
            _release_prepared_source(row, row.local_path)
    for row in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_delivery_status.in_(["uploading", "sending"]))):
        upload_state.expire_delivery(db, row)
    for row in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.source == "helpdesk", TicketAttachment.telegram_file_id.is_not(None))):
        _release_prepared_source(row, row.local_path)
    for row in db.scalars(select(TicketAttachmentPreload).where(TicketAttachmentPreload.ticket_id == ticket_id)).all():
        if not upload_state.state(row).get("archive_reconcile_pending"):
            continue
        group = _archive_rows_for_display(_load_group_preloads(db, ticket_id=row.ticket_id,
            user_id=row.user_id, client_id=row.client_id, archive_group_id=_archive_group_id(row), for_update=False))
        ticket = db.get(Ticket, ticket_id)
        if ticket is not None and group and _actual_info_message_id(group):
            if not _best_effort_edit_group_archive_message(group_rows=group, ticket=ticket,
                    user=archive_owner(db, row), status_text=_group_status_text(group), force=True):
                continue
        upload_state.patch(row, archive_reconcile_pending=False)
        db.commit()
    cleanup_failed_attachment_preloads(db, ticket_id)
    cleanup_stale_attachment_preloads(db, ticket_id=ticket_id)


def cleanup_failed_attachment_preloads(db, ticket_id):
    cleanup_client_preloads(db, ticket_id)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.status != "closed":
        return 0
    ticket = upload_state.lock_row(db, Ticket, ticket_id)
    if ticket is None or ticket.status != "closed":
        return 0
    rows = db.scalars(select(TicketAttachmentPreload).where(
        TicketAttachmentPreload.ticket_id == ticket_id, TicketAttachmentPreload.status == "failed",
        TicketAttachmentPreload.client_id.is_(None),
    ).order_by(TicketAttachmentPreload.id)).all()
    paths = set()
    ignored_attachment_ids = set()
    removed = 0
    for candidate in rows:
        row = upload_state.lock_row(db, TicketAttachmentPreload, candidate.id)
        if row is None or row.status != "failed":
            continue
        attachment = db.get(TicketAttachment, row.final_attachment_id) if row.final_attachment_id else None
        if ((upload_state.state(row).get("staging_attempt") or {}).get("active")
                and not upload_state.attempt_expired(row, "staging_attempt")):
            continue
        if attachment is not None and ((upload_state.state(attachment).get("delivery_attempt") or {}).get("active")
                and not upload_state.attempt_expired(attachment, "delivery_attempt")):
            continue
        if attachment is not None and attachment.ticket_id == ticket_id and attachment.telegram_delivery_status == "failed":
            info = upload_state.state(row)
            saved = upload_state.state(attachment)
            upload_state.patch(attachment, receipt=saved.get("receipt") or info.get("receipt"),
                previous_receipts=[*saved.get("previous_receipts", []), *info.get("previous_receipts", [])])
            if not attachment.telegram_archive_message_id:
                attachment.telegram_archive_message_id = row.telegram_staging_file_message_id
            ignored_attachment_ids.add(attachment.id)
        paths.add(row.local_path)
        db.delete(row)
        removed += 1
    # A parent's failed copy can outlive the child's preload and share its source.
    client_bound_ids = select(TicketAttachmentPreload.final_attachment_id).where(
        TicketAttachmentPreload.client_id.is_not(None), TicketAttachmentPreload.final_attachment_id.is_not(None))
    for attachment in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.telegram_delivery_status == "failed", TicketAttachment.id.not_in(client_bound_ids))):
        if (upload_state.state(attachment).get("delivery_attempt") or {}).get("active") and not upload_state.attempt_expired(attachment, "delivery_attempt"):
            continue
        paths.add(attachment.local_path)
        ignored_attachment_ids.add(attachment.id)
    if removed or paths:
        db.commit()
        for attachment in db.scalars(select(TicketAttachment).join(Ticket, TicketAttachment.ticket_id == Ticket.id).where(
                TicketAttachment.local_path.in_(paths), TicketAttachment.telegram_delivery_status == "failed", Ticket.status == "closed")):
            if not (upload_state.state(attachment).get("delivery_attempt") or {}).get("active") or upload_state.attempt_expired(attachment, "delivery_attempt"):
                ignored_attachment_ids.add(attachment.id)
        for path in paths:
            _safe_unlink(path, db=db, ignored_attachment_ids=ignored_attachment_ids)
    return removed


_FAILED_CLOSE_CLEANUP = "failed_attachment_close_cleanup"


@event.listens_for(Session, "before_flush")
def _schedule_failed_cleanup_on_close(db, *_args):
    ids = {row.id for row in db.dirty.union(db.new) if isinstance(row, Ticket)
           and ((row.status == "closed" and inspect(row).attrs.status.history.has_changes())
                or (row.channel == 'web' and inspect(row).attrs.intake_state.history.has_changes()))}
    if ids:
        transaction = db.get_nested_transaction() or db.get_transaction()
        db.info.setdefault(_FAILED_CLOSE_CLEANUP, {}).setdefault(transaction, set()).update(ids)


@event.listens_for(Session, "after_commit")
def _cleanup_failed_after_close_commit(db):
    pending = db.info.get(_FAILED_CLOSE_CLEANUP, {})
    nested = db.get_nested_transaction()
    if nested is not None:
        pending.setdefault(nested.parent, set()).update(pending.pop(nested, set()))
        return
    db.info.pop(_FAILED_CLOSE_CLEANUP, None)
    ids = set().union(*pending.values()) if pending else set()
    for ticket_id in ids:
        try:
            with SessionLocal(bind=db.get_bind()) as cleanup_db:
                cleanup_failed_attachment_preloads(cleanup_db, ticket_id)
        except Exception:
            logger.warning("Failed preload cleanup deferred ticket=%s", ticket_id)


@event.listens_for(Session, "after_rollback")
def _cancel_failed_cleanup_on_rollback(db):
    nested = db.get_nested_transaction()
    if nested is not None:
        db.info.get(_FAILED_CLOSE_CLEANUP, {}).pop(nested, None)
    else:
        db.info.pop(_FAILED_CLOSE_CLEANUP, None)


def cleanup_stale_attachment_preloads(db, ticket_id=None) -> int:
    threshold = datetime.utcnow() - timedelta(hours=PRELOAD_STALE_TTL_HOURS)
    stale_rows = db.execute(
        select(TicketAttachmentPreload)
        .where(
            TicketAttachmentPreload.final_attachment_id.is_(None),
            TicketAttachmentPreload.composer_draft_id.is_(None),
            TicketAttachmentPreload.status != "failed",
            TicketAttachmentPreload.ticket_id == ticket_id if ticket_id is not None else True,
            TicketAttachmentPreload.created_at < threshold,
        )
        .order_by(asc(TicketAttachmentPreload.created_at), asc(TicketAttachmentPreload.id))
    ).scalars().all()
    removed = 0
    for stale in stale_rows:
        current = upload_state.lock_row(db, TicketAttachmentPreload, stale.id)
        if current is None or current.final_attachment_id or current.status == "failed":
            continue
        if (upload_state.state(current).get("staging_attempt") or {}).get("active") and not upload_state.attempt_expired(current, "staging_attempt"):
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

    info = upload_state.state(attachment)
    stored_receipt = info.get("receipt")

    def checkpoint_send(kind, send):
        current = upload_state.state(attachment)
        saved_id = current.get(f"sent_{kind}_message_id")
        if saved_id:
            return saved_id
        if current.get("delivery_pending"):
            raise RuntimeError("Previous Telegram delivery has no confirmed acknowledgement")
        upload_state.heartbeat(db, attachment, "delivery_attempt")
        upload_state.patch(attachment, delivery_pending=kind)
        db.commit()
        response = send()
        sent_id = _telegram_message_id(response)
        if not sent_id:
            raise RuntimeError("Telegram delivery returned no message_id")
        upload_state.patch(attachment, **{f"sent_{kind}_message_id": sent_id, "delivery_pending": None})
        if kind == "file":
            attachment.telegram_message_id = sent_id
        else:
            message.telegram_message_id = sent_id
        db.commit()
        return sent_id

    def send_file(caption=None, parse_mode=None, reply=None):
        return send_document_by_file_id(contact.external_chat_id, file_id, caption=caption,
                                        parse_mode=parse_mode, reply_to_message_id=reply, retry_only_parse_errors=True)

    body = (message.body or "").strip()
    try:
        if isinstance(stored_receipt, dict) and not upload_state.usable_receipt(stored_receipt):
            raise RuntimeError("Unsupported prepared media type")
        if body and prefer_caption and _caption_compatible_text(body):
            payload_caption, parse_mode = prepare_telegram_html_message(body)
            sent_id = checkpoint_send("file", lambda: send_file(payload_caption, parse_mode, reply_to_message_id))
            message.telegram_message_id = sent_id
        elif body:
            payload_text, parse_mode = prepare_telegram_html_message(body)
            checkpoint_send("text", lambda: send_message(contact.external_chat_id, payload_text,
                            parse_mode=parse_mode, reply_to_message_id=reply_to_message_id, retry_only_parse_errors=True))
            checkpoint_send("file", send_file)
        else:
            checkpoint_send("file", lambda: send_file(reply=reply_to_message_id))
    except (TelegramApiError, RuntimeError):
        _mark_delivery_failed(db, ticket=ticket, attachment=attachment,
            failure_note=f"Telegram не подтвердил доставку вложения «{filename}». Автоматический повтор отключён во избежание дубликата.")
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
        ticket_id, user_id, client_id, archive_group_id = _group_key_from_preload(preload)
        group_rows = _load_group_preloads(
            db,
            ticket_id=ticket_id,
            user_id=user_id, client_id=client_id,
            archive_group_id=archive_group_id,
            for_update=False,
        )
        display_rows = _archive_rows_for_display(group_rows)
        ticket = db.get(Ticket, ticket_id)
        user = archive_owner(db, preload)
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
                    user_id=user_id, client_id=client_id,
                    archive_group_id=archive_group_id,
                    for_update=False,
                )
                for row in cleanup_rows:
                    if row.status == "sent":
                        cleanup_db.delete(row)
                cleanup_db.commit()
            finally:
                cleanup_db.close()
    finally:
        db.close()


def deliver_ticket_attachment(*, attachment_id: str, message_id: str, ticket_id: str,
                              reply_to_message_id=None, prefer_caption=False, preload_id=None) -> None:
    with SessionLocal() as db:
        row = upload_state.lock_row(db, TicketAttachment, attachment_id)
        if row is not None:
            upload_state.expire_delivery(db, row)
        if row is None or row.telegram_delivery_status not in {"uploading", "sending"}:
            return
        token = upload_state.begin_attempt(row, "delivery_attempt")
        db.commit()
        if not token:
            return
    try:
        _deliver_ticket_attachment(attachment_id=attachment_id, message_id=message_id, ticket_id=ticket_id,
            reply_to_message_id=reply_to_message_id, prefer_caption=prefer_caption, preload_id=preload_id)
    except Exception as exc:
        with SessionLocal() as db:
            row = upload_state.lock_row(db, TicketAttachment, attachment_id)
            if row is not None and (upload_state.state(row).get("delivery_attempt") or {}).get("token") == token:
                row.telegram_delivery_status = "failed"
                if isinstance(exc, upload_state.ReceiptRegistrationError):
                    upload_state.patch(row, receipt=exc.receipt)
                    row.telegram_archive_message_id = exc.receipt.get("message_id")
                upload_state.patch(row, error_code="delivery_registration_failed", error="Не удалось подтвердить доставку. Автоматический повтор отключён.")
                db.commit()
        logger.warning("attachment=%s delivery_failed=%s", attachment_id, type(exc).__name__)
    finally:
        with SessionLocal() as db:
            row = upload_state.lock_row(db, TicketAttachment, attachment_id)
            if row is not None:
                upload_state.end_attempt(row, "delivery_attempt", token)
                db.commit()
                if row.telegram_delivery_status == "failed":
                    cleanup_failed_attachment_preloads(db, row.ticket_id)
                elif row.telegram_delivery_status == "sent":
                    # Apply only the unfreeze effect; delivery/attempt state is already committed.
                    _, message, ticket = _load_entities(db, attachment_id=attachment_id,
                        message_id=message_id, ticket_id=ticket_id)
                    if (ticket is not None and message is not None and ticket.is_frozen
                            and ticket.workflow_type == TicketWorkflowType.TICKET.value
                            and message.message_type == MessageType.AGENT.value):
                        from .ticket_lifecycle import unfreeze_ticket_on_message
                        from .statistics_lifecycle import sync_work_interval_after_unfreeze
                        from .auto_close import refresh_ticket_auto_close_timer

                        ticket = upload_state.lock_row(db, Ticket, ticket.id)
                        if (ticket is not None and ticket.is_frozen and message.author_user_id
                                and ticket.assignee_id == message.author_user_id
                                and not db.scalar(select(TicketAttachment.id).where(
                                    TicketAttachment.message_id == message_id,
                                    or_(TicketAttachment.telegram_delivery_status.is_(None),
                                        TicketAttachment.telegram_delivery_status != "sent"),
                                ).limit(1))):
                            unfreeze_ticket_on_message(ticket)
                            if not ticket.is_frozen:
                                sync_work_interval_after_unfreeze(db, ticket, now=ticket.updated_at)
                                refresh_ticket_auto_close_timer(db, ticket, restart_from_now=True)
                                db.commit()


def _deliver_ticket_attachment(
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
                ticket_id_value, user_id, client_id, archive_group_id = _group_key_from_preload(preload)
                group_rows = _archive_rows_for_display(
                    _load_group_preloads(
                        db,
                        ticket_id=ticket_id_value,
                        user_id=user_id, client_id=client_id,
                        archive_group_id=archive_group_id,
                        for_update=False,
                    )
                )
                user = archive_owner(db, preload)
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
                    on_receipt=lambda response: upload_state.store_receipt(db, attachment, response),
                    on_wait=lambda: upload_state.heartbeat(db, attachment, "delivery_attempt"),
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
                if isinstance(exc, upload_state.ReceiptRegistrationError):
                    upload_state.store_receipt_value(db, attachment, exc.receipt)
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
                _unlink_attachment_local_file(cleanup_path, db=db)
                return
            _release_prepared_source(attachment, cleanup_path)

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
    token = (upload_state.state(preload).get("staging_attempt") or {}).get("token")
    upload_state.lock_row(db, TicketAttachmentPreload, preload.id)
    if preload.status == "cancelled" or (upload_state.state(preload).get("staging_attempt") or {}).get("token") != token:
        return False
    ticket = db.get(Ticket, preload.ticket_id) if preload.ticket_id is not None else None
    task = ticket is not None and ticket.workflow_type == TicketWorkflowType.TASK.value
    if task:
        db.refresh(preload, with_for_update=True)
        if preload.status == "cancelled":
            return False
    upload_state.fail(preload, uncertain=bool(upload_state.state(preload).get("binary_started")) and not upload_state.usable_preload_receipt(preload))
    value = upload_state.state(preload).get("receipt") or {}
    if value.get("accepted") and value.get("media_type") not in {None, "document"} and not upload_state.usable_preload_receipt(preload, value):
        upload_state.patch(preload, error_code="staging_media_mismatch",
            error="Telegram вернул другой тип файла вместо документа. Исходник сохранён; повтор требует подтверждения.", uncertain=True)
    if task and preload.final_attachment_id:
        attachment = db.get(TicketAttachment, preload.final_attachment_id)
        message = db.get(TicketMessage, attachment.message_id) if attachment is not None else None
        if message is not None and message.message_type == MessageType.AGENT.value:
            attachment.telegram_delivery_status = "failed"
            return True
    return False


def deliver_attachment_preload(*, preload_id: str) -> None:
    with SessionLocal() as db:
        row = upload_state.lock_row(db, TicketAttachmentPreload, preload_id)
        if row is None or row.status not in {"uploading", "ready", "cancelled"}:
            return
        cleanup_path = row.local_path
        token = upload_state.begin_attempt(row, "staging_attempt")
        db.commit()
        if not token:
            return
    try:
        _deliver_attachment_preload(preload_id=preload_id)
    except Exception as exc:
        with SessionLocal() as db:
            row = upload_state.lock_row(db, TicketAttachmentPreload, preload_id)
            if (row is None or (upload_state.state(row).get('staging_attempt') or {}).get('token') != token) and isinstance(exc, upload_state.ReceiptRegistrationError):
                message_id = exc.receipt.get('message_id')
                if message_id and not _archive_message_referenced(db, message_id):
                    _delete_archive_message_by_id(message_id)
            if row is not None and (upload_state.state(row).get("staging_attempt") or {}).get("token") == token:
                if row.status != "cancelled":
                    _mark_preload_preparation_failed(db, row)
                    if isinstance(exc, upload_state.ReceiptRegistrationError):
                        upload_state.patch(row, receipt=exc.receipt)
                        row.telegram_staging_file_message_id = exc.receipt.get("message_id")
                    upload_state.patch(row, error_code="staging_registration_failed",
                        uncertain=not upload_state.usable_preload_receipt(row))
                db.commit()
        logger.warning("preload=%s preparation_failed=%s", preload_id, type(exc).__name__)
    finally:
        with SessionLocal() as db:
            row = upload_state.lock_row(db, TicketAttachmentPreload, preload_id)
            if row is None:
                _safe_unlink(cleanup_path, db=db)
                return
            if row is not None:
                upload_state.end_attempt(row, "staging_attempt", token)
                db.commit()
                if row.status == "cancelled":
                    _discard_preload(db, row, delete_row=True, unlink_local=True)
                    return
                if row.status == "failed":
                    ticket = db.get(Ticket, row.ticket_id) if row.ticket_id is not None else None
                    if ticket is not None and ticket.status == "closed":
                        cleanup_failed_attachment_preloads(db, ticket.id)
                        return
                    if row.client_id is not None:
                        synchronize_web_preload_archives(db, [row.id])
                    elif ticket is not None:
                        group = _archive_rows_for_display(_load_group_preloads(db, ticket_id=row.ticket_id,
                            user_id=row.user_id, client_id=row.client_id, archive_group_id=_archive_group_id(row), for_update=False))
                        _best_effort_edit_group_archive_message(group_rows=group, ticket=ticket,
                            user=archive_owner(db, row), status_text="ошибка подготовки", force=True)


def _deliver_attachment_preload(*, preload_id: str) -> None:
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

        ticket = db.get(Ticket, preload.ticket_id) if preload.ticket_id is not None else None
        user = archive_owner(db, preload)
        if (ticket is None and preload.composer_draft_id is None) or not client_preload_allowed(db, preload):
            if preload.client_id is not None:
                _discard_client_preload(db, preload)
                return
            preload.status = "failed"
            db.commit()
            return

        if ticket is not None and ticket.workflow_type == TicketWorkflowType.TASK.value and preload.final_attachment_id:
            attachment = db.get(TicketAttachment, preload.final_attachment_id)
            message = db.get(TicketMessage, attachment.message_id) if attachment is not None else None
            if message is not None and message.message_status == "deleted":
                _discard_preload(db, preload, delete_row=True, unlink_local=True)
                return

        if not str(preload.telegram_staging_info_message_id or "").strip() or _is_pending_archive_message_id(preload.telegram_staging_info_message_id):
            try:
                info_id = _ensure_group_archive_info_message(preload_id=preload.id)
                if preload.client_id is None:
                    preload.telegram_staging_info_message_id = info_id
                    db.commit()
                else:
                    db.rollback()  # Coordination may have raced with a draft batch split.
                db.refresh(preload)
            except (RuntimeError, TelegramApiError) as exc:
                db.rollback()
                db.expire(preload)
                db.refresh(preload)
                _mark_preload_preparation_failed(db, preload)
                if preload.status == "cancelled":
                    _discard_preload(db, preload, delete_row=True, unlink_local=True)
                    return
                upload_state.patch(preload, uncertain=True)
                _clear_pending_info_reservation(db, preload)
                logger.warning("preload=%s staging_failed=%s", preload.id, type(exc).__name__)
                db.commit()
                return

        db.refresh(preload)
        if preload.status == "cancelled":
            _discard_preload(db, preload, delete_row=True, unlink_local=True)
            return

        attempt_token = (upload_state.state(preload).get("staging_attempt") or {}).get("token")
        saved_receipt = upload_state.state(preload).get("receipt") or {}
        if upload_state.usable_preload_receipt(preload, saved_receipt):
            preload.telegram_file_id = saved_receipt["file_id"]
            preload.telegram_file_unique_id = saved_receipt.get("file_unique_id")
            preload.telegram_staging_file_message_id = saved_receipt["message_id"]
            db.commit()
        elif saved_receipt.get("accepted"):
            _mark_preload_preparation_failed(db, preload)
            upload_state.patch(preload, error_code="staging_response_invalid", uncertain=True)
            db.commit()
            return
        if not (preload.telegram_file_id or "").strip():
            try:
                upload_state.patch(preload, binary_started=True)
                db.commit()
                file_id, file_unique_id, file_message_id = _send_staging_document(preload=preload)
                staged_task_file = ticket is None or ticket.workflow_type == TicketWorkflowType.TASK.value
            except FileNotFoundError:
                claimed_task = _mark_preload_preparation_failed(db, preload)
                upload_state.patch(preload, error_code="staging_file_missing", uncertain=False)
                db.commit()
                logger.warning("preload=%s filename=%s staging_failed=file_missing", preload.id, preload.original_filename)
                return
            except (RuntimeError, TelegramApiError) as exc:
                if isinstance(exc, upload_state.ReceiptRegistrationError):
                    upload_state.store_receipt_value(db, preload, exc.receipt)
                claimed_task = _mark_preload_preparation_failed(db, preload)
                db.commit()
                logger.warning("preload=%s staging_failed=%s", preload.id, type(exc).__name__)
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
            if staged_task_file and not _archive_message_referenced(finalize_db, file_message_id):
                _delete_archive_message_by_id(file_message_id)
            return
        if (upload_state.state(preload).get("staging_attempt") or {}).get("token") != attempt_token:
            return
        _ensure_archive_group_id(finalize_db, preload)
        ticket = finalize_db.get(Ticket, preload.ticket_id) if preload.ticket_id is not None else None
        user = archive_owner(finalize_db, preload)

        upload_state.patch(preload, error=None, error_code=None, uncertain=False)
        preload.telegram_file_id = file_id
        preload.telegram_file_unique_id = file_unique_id
        preload.telegram_staging_file_message_id = file_message_id or preload.telegram_staging_file_message_id
        cleanup_path = preload.local_path

        if preload.status == "cancelled":
            finalize_db.commit()
            _discard_preload(finalize_db, preload, delete_row=True, unlink_local=True)
            return

        if not client_preload_allowed(finalize_db, preload):
            _discard_client_preload(finalize_db, preload)
            return

        if not preload.final_attachment_id:
            preload.status = "ready"
            finalize_db.commit()
            _release_prepared_source(preload, cleanup_path)
            if preload.client_id is not None:
                synchronize_web_preload_archives(finalize_db, [preload.id])
            elif ticket is not None:
                group_rows = _archive_rows_for_display(
                    _load_group_preloads(
                        finalize_db,
                        ticket_id=preload.ticket_id,
                        user_id=preload.user_id, client_id=preload.client_id,
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
            _safe_unlink(cleanup_path, db=finalize_db)
            return

        message = finalize_db.get(TicketMessage, attachment.message_id)
        if message is None or ticket is None:
            finalize_db.delete(preload)
            finalize_db.commit()
            _safe_unlink(cleanup_path, db=finalize_db)
            return

        if ticket.workflow_type == TicketWorkflowType.TASK.value and message.message_status == "deleted":
            _discard_preload(finalize_db, preload, delete_row=True, unlink_local=True)
            return

        if upload_state.state(preload).get("receipt"):
            upload_state.patch(attachment, receipt=upload_state.state(preload)["receipt"])
        if preload.client_id is not None:
            attachment.telegram_archive_message_id = file_message_id
            attachment.telegram_archive_info_message_id = preload.telegram_staging_info_message_id
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
            _release_prepared_source(preload, cleanup_path)
            _finalize_claimed_preload_archive(preload_id=preload.id, final_status="sent")
            return

        if ticket.workflow_type == TicketWorkflowType.TASK.value and (preload.client_id is not None or message.message_type == MessageType.AGENT.value):
            preload.status = "ready"
            attachment.telegram_delivery_status = "ready"
            attachment.telegram_archive_message_id = file_message_id or None
            finalize_db.commit()
            _release_prepared_source(preload, cleanup_path)
            _finalize_claimed_preload_archive(preload_id=preload.id, final_status="ready")
            return

        preload.status = "sending"
        attachment.telegram_delivery_status = "sending"
        finalize_db.commit()
        _release_prepared_source(preload, cleanup_path)
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
    user_id: str | None = None,
    client_id: str | None = None,
) -> TicketAttachmentPreload | None:
    if client_id is not None:
        from .attachment_preload_owners import client_collecting_ticket
        if client_collecting_ticket(db, ticket_id, client_id) is None:
            return None
    stmt = _lock_stmt(
        select(TicketAttachmentPreload).where(
            TicketAttachmentPreload.id == preload_id,
            TicketAttachmentPreload.ticket_id == ticket_id,
            *owner_scope(user_id=user_id, client_id=client_id),
        ),
        db,
    )
    preload = db.execute(stmt).scalar_one_or_none()
    if preload is None or preload.final_attachment_id:
        return None
    return preload
