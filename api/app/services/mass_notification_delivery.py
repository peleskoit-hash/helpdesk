import logging
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path, PureWindowsPath
from stat import S_ISREG
from unicodedata import category
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..db import SessionLocal
from ..models import MassNotificationOperation, MassNotificationRecipient, TelegramContact
from .mass_notification_operations import (
    begin_mass_notification_component,
    claim_mass_notification_operation,
    finish_mass_notification_component,
    skip_mass_notification_recipient,
)
from .mass_notification_staging import DOCUMENT_LIMIT, PHOTO_LIMIT, PHOTO_MIME_TYPES, stage_mass_notification_file
from .telegram_bot import TelegramApiError, copy_message, send_message


logger = logging.getLogger(__name__)
STALE_OPERATION_AGE = timedelta(minutes=15)
INTERRUPTED_OPERATION_ERROR = "Рассылка прервана. Автоматическое продолжение отключено."
INTERRUPTED_ATTACHMENT_ERROR = "Подготовка вложения прервана. Результат Telegram неизвестен."
INTERRUPTED_COMPONENT_ERROR = "Отправка прервана. Результат Telegram неизвестен."
DEFINITE_REFUSALS = {"bot was blocked by the user", "chat not found", "user is deactivated"}


class _DeliveryError(RuntimeError):
    pass


class _OwnershipLost(RuntimeError):
    pass


class _UnrecordedOutcome(RuntimeError):
    pass


def _update_operation(db, operation_id, worker_token, **values):
    operation = MassNotificationOperation
    result = db.execute(
        update(operation)
        .where(operation.id == operation_id, operation.status == "running", operation.worker_token == worker_token)
        .values(heartbeat_at=datetime.now(timezone.utc), **values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise _OwnershipLost
    db.commit()


def _operation_directory(operation_id):
    if str(UUID(operation_id)) != operation_id:
        raise _DeliveryError("The operation ID must be a canonical UUID")
    root = Path(tempfile.gettempdir()).resolve() / "es-desk-mass-notifications"
    directory = root / operation_id
    if root.is_symlink() or directory.is_symlink() or directory.resolve() != directory:
        raise _DeliveryError("Unsafe notification operation directory")
    return directory


def _attachment_path(directory, attachment):
    stored_name = attachment.get("stored_name")
    if not isinstance(stored_name, str) or not re.fullmatch(r"[0-9a-f]{32}", stored_name):
        raise _DeliveryError("Invalid stored attachment name")
    path = directory / stored_name
    if path.is_symlink() or path.resolve().parent != directory:
        raise _DeliveryError("Unsafe notification attachment path")
    info = path.stat()
    if not S_ISREG(info.st_mode) or info.st_size <= 0:
        raise _DeliveryError("The notification attachment must be a non-empty regular file")
    filename = attachment["original_filename"]
    mime_type = attachment.get("mime_type")
    if not isinstance(filename, str) or (mime_type is not None and not isinstance(mime_type, str)):
        raise _DeliveryError("Invalid notification attachment metadata")
    # Inspect the same displayed extension as the staging service.
    cleaned = "".join(char for char in filename if category(char) not in {"Cc", "Cf", "Cs"})
    extension = Path(PureWindowsPath(cleaned).name.strip().replace('"', "_")).suffix.lower()
    limit = PHOTO_LIMIT if extension in PHOTO_MIME_TYPES else DOCUMENT_LIMIT
    if info.st_size > limit:
        raise _DeliveryError(f"The notification attachment exceeds the {limit // 1024 ** 2} MiB limit")
    if attachment.get("file_size") != info.st_size:
        raise _DeliveryError("The local attachment size differs from its saved size")
    return path


def _error_message(exc):
    detail = getattr(exc, "detail", None)
    return (str(detail or exc) or type(exc).__name__)[:1000]


def _message_id(value):
    if type(value) is int and value > 0:
        return value
    raise _DeliveryError("Telegram did not confirm a valid message ID")


def _prepare_attachments(operation_id, attachments, save):
    def record(index, **changes):
        nonlocal attachments
        attachments = [
            dict(attachment, **changes) if position == index else attachment
            for position, attachment in enumerate(attachments)
        ]
        save(attachments_json=attachments)

    def check_local_file(index, attachment):
        try:
            if not (settings.telegram_staging_chat_id or "").strip():
                raise _DeliveryError("TELEGRAM_STAGING_CHAT_ID is not configured")
            return _attachment_path(_operation_directory(operation_id), attachment)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            record(index, status="failed", error=_error_message(exc))
            raise

    for index, attachment in enumerate(attachments):
        if attachment.get("status") != "pending":
            raise _DeliveryError(f"Attachment {index} cannot be uploaded again")
        check_local_file(index, attachment)

    for index, attachment in enumerate(attachments):
        path = check_local_file(index, attachment)
        record(index, status="uploading", error=None)
        try:
            staged = stage_mass_notification_file(path, attachment["original_filename"], attachment.get("mime_type"))
            message_id = _message_id(staged["message_id"])
            chat_id = staged["staging_chat_id"]
            if not isinstance(chat_id, str) or not chat_id.strip():
                raise _DeliveryError("Telegram staging chat ID is missing")
        except Exception as exc:
            record(index, status="unknown", error=_error_message(exc))
            raise _DeliveryError(_error_message(exc)) from exc
        record(index, status="ready", staging_chat_id=chat_id, staging_message_id=message_id, error=None)

    return attachments


def _is_eligible(db, client_id, chat_id, pairs):
    from ..routers.mass_notifications import _recipients_query

    query = _recipients_query(db, pairs).where(
        TelegramContact.client_id == client_id, TelegramContact.external_chat_id == chat_id,
    )
    eligible = db.execute(query.limit(1)).first() is not None
    db.commit()
    return eligible


def _send_component(chat_id, component, text, attachments):
    if component["kind"] == "text":
        response = send_message(chat_id, text, parse_mode="HTML", retry_only_parse_errors=True)
    else:
        staged = attachments[component["attachment_index"]]
        response = copy_message(
            chat_id=chat_id, from_chat_id=staged["staging_chat_id"], message_id=staged["staging_message_id"],
        )
    result = response.get("result") if isinstance(response, dict) and response.get("ok") is not False else None
    return _message_id(result.get("message_id") if isinstance(result, dict) else None)


def _send_to_recipients(db, operation_id, worker_token, save, text, pairs, attachments):
    recipient = MassNotificationRecipient
    snapshot = db.execute(
        select(recipient.id, recipient.client_id, recipient.chat_id, recipient.components_json)
        .where(recipient.operation_id == operation_id)
        .order_by(recipient.created_at, recipient.id)
    ).all()
    db.commit()

    last_request = None
    last_chat_id = None
    for recipient_id, client_id, chat_id, components in snapshot:
        if not _is_eligible(db, client_id, chat_id, pairs):
            if not skip_mass_notification_recipient(db, operation_id, recipient_id, worker_token, "No longer eligible"):
                save()
            continue
        for component in components:
            args = (db, operation_id, recipient_id, component["key"], worker_token)
            if not begin_mass_notification_component(*args):
                save()
                continue
            if last_request is not None:
                interval = 1.0 if last_chat_id == chat_id else 0.05
                delay = last_request + interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            save()
            last_request, last_chat_id = time.monotonic(), chat_id

            throttled = False
            error = None
            message_id = None
            try:
                message_id = _send_component(chat_id, component, text, attachments)
                status = "sent"
            except Exception as exc:
                error = _error_message(exc)
                status = "unknown"
                if isinstance(exc, TelegramApiError):
                    description = str(exc).strip().lower()
                    throttled = "too many requests" in description or "retry after" in description
                    refusal = description.removeprefix("forbidden: ").removeprefix("bad request: ")
                    if refusal in DEFINITE_REFUSALS:
                        status = "failed"
            try:
                recorded = finish_mass_notification_component(*args, status, message_id, error)
            except Exception as exc:
                raise _UnrecordedOutcome("The component result could not be saved") from exc
            if not recorded:
                raise _UnrecordedOutcome("The component result could not be saved")
            if throttled:
                raise _DeliveryError(error)


def _clean_operation_directory(operation_id):
    try:
        directory = _operation_directory(operation_id)
        if directory.is_dir():
            shutil.rmtree(directory)
    except (OSError, RuntimeError, ValueError):
        logger.warning("Cannot clean mass notification directory for %s", operation_id, exc_info=True)


def _stale_operation_candidates(db, operation_id, cutoff):
    operation = MassNotificationOperation
    activity = func.coalesce(operation.heartbeat_at, operation.started_at, operation.created_at)
    query = select(
        operation.id, operation.status, operation.worker_token, operation.created_at,
        operation.started_at, operation.heartbeat_at, activity,
    ).where(
        or_(
            and_(operation.status == "queued", operation.created_at <= cutoff),
            and_(operation.status == "running", activity <= cutoff),
        )
    )
    if operation_id is not None:
        query = query.where(operation.id == operation_id)
    return db.execute(query).all()


def _interrupted_recipient_status(components):
    if not isinstance(components, list) or not components:
        raise ValueError("Missing notification components")
    statuses = set()
    keys = set()
    for component in components:
        key, status = component["key"], component["status"]
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError("Invalid notification component key")
        if status not in {"pending", "sending", "sent", "failed", "unknown"}:
            raise ValueError("Invalid notification component status")
        message_id = component.get("telegram_message_id")
        if status == "sent":
            _message_id(message_id)
        elif message_id is not None:
            raise ValueError("Unconfirmed notification component has a message ID")
        keys.add(key)
        statuses.add(status)
    if statuses & {"pending", "sending"}:
        return "pending"
    if statuses == {"sent"}:
        return "sent"
    if "sent" in statuses:
        return "partial"
    if "unknown" in statuses:
        return "unknown"
    return "failed"


def _reconcile_operation(db, candidate, cutoff, now):
    operation_id, status, worker_token, created_at, started_at, heartbeat_at, last_activity = candidate
    operation = MassNotificationOperation
    if status == "queued":
        still_stale = and_(operation.created_at == created_at, operation.created_at <= cutoff)
    else:
        activity = func.coalesce(operation.heartbeat_at, operation.started_at, operation.created_at)
        still_stale = and_(
            operation.worker_token == worker_token,
            operation.created_at == created_at, operation.started_at == started_at, operation.heartbeat_at == heartbeat_at,
            activity == last_activity, activity <= cutoff,
        )
    result = db.execute(
        update(operation)
        .where(operation.id == operation_id, operation.status == status, still_stale)
        .values(status="interrupted", stage="interrupted", finished_at=now, error_message=INTERRUPTED_OPERATION_ERROR)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return False

    saved = db.get(operation, operation_id, populate_existing=True)
    saved.attachments_json = [
        dict(attachment, status="unknown", error=INTERRUPTED_ATTACHMENT_ERROR)
        if attachment["status"] == "uploading" else dict(attachment)
        for attachment in saved.attachments_json
    ]

    recipient = MassNotificationRecipient
    rows = db.scalars(
        select(recipient).where(recipient.operation_id == operation_id).execution_options(populate_existing=True)
    )
    for row in rows:
        if row.status == "skipped":
            continue
        components = [
            dict(component, status="unknown", error=INTERRUPTED_COMPONENT_ERROR)
            if component["status"] == "sending" else dict(component)
            for component in row.components_json
        ]
        new_status = _interrupted_recipient_status(components)
        if row.status != new_status or row.components_json != components:
            row.updated_at = now
        row.components_json = components
        row.status = new_status

    db.commit()
    return True


def reconcile_stale_mass_notification_operations(db, operation_id=None, now=None):
    """Interrupt stale work without resuming any Telegram calls."""
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    cutoff = now - STALE_OPERATION_AGE
    try:
        candidates = _stale_operation_candidates(db, operation_id, cutoff)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not read stale mass notification operations")
        return []

    # Release the read transaction before acquiring a write lock, particularly on SQLite.
    db.rollback()
    interrupted = []
    for candidate in candidates:
        candidate_id = candidate.id
        try:
            changed = _reconcile_operation(db, candidate, cutoff, now)
        except Exception:
            db.rollback()
            logger.exception("Could not interrupt mass notification operation %s", candidate_id)
            continue
        if changed:
            interrupted.append(candidate_id)
            _clean_operation_directory(candidate_id)
    return interrupted


def run_mass_notification_operation(operation_id: str) -> None:
    worker_token = str(uuid4())
    with SessionLocal() as db:
        if not claim_mass_notification_operation(db, operation_id, worker_token):
            return
        save = partial(_update_operation, db, operation_id, worker_token)
        try:
            operation = MassNotificationOperation
            text, filters, attachments = db.execute(
                select(operation.text, operation.filters_json, operation.attachments_json)
                .where(operation.id == operation_id)
            ).one()
            db.commit()
            if attachments:
                save(stage="preparing_attachments")
                attachments = _prepare_attachments(operation_id, attachments, save)
            save(stage="sending")
            _send_to_recipients(db, operation_id, worker_token, save, text, list(filters.items()), attachments)
        except _OwnershipLost:
            return
        except _UnrecordedOutcome:
            db.rollback()
            logger.exception("Stopped mass notification %s after a component result could not be saved", operation_id)
            return
        except Exception as exc:
            db.rollback()
            try:
                save(status="failed", error_message=_error_message(exc), finished_at=datetime.now(timezone.utc))
            except _OwnershipLost:
                return
            except SQLAlchemyError:
                logger.exception("Could not save failure for mass notification %s", operation_id)
                return
        else:
            try:
                save(status="completed", stage="completed", finished_at=datetime.now(timezone.utc))
            except _OwnershipLost:
                return
            except SQLAlchemyError:
                logger.exception("Could not complete mass notification %s", operation_id)
                return
    _clean_operation_directory(operation_id)
