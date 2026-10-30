"""Durable, secret-free upload receipts and attempts (no Telegram calls here)."""
from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

RECEIPT_TYPES = ("document", "video", "photo", "sticker", "audio", "voice", "animation", "video_note")
ATTEMPT_TIMEOUT = 360


class ReceiptRegistrationError(RuntimeError):
    def __init__(self, value):
        self.receipt = value
        super().__init__("Telegram receipt could not be registered")


def lock_row(db, model, row_id):
    # UPDATE also serializes SQLite writers; SELECT FOR UPDATE handles PostgreSQL.
    db.execute(update(model).where(model.id == row_id).values(id=model.id)
               .execution_options(synchronize_session=False))
    return db.scalar(select(model).where(model.id == row_id).with_for_update()
                     .execution_options(populate_existing=True))


def state(row):
    return dict(getattr(row, "telegram_upload_state_json", None) or {})


def patch(row, **values):
    row.telegram_upload_state_json = {**state(row), **values}


def begin_attempt(row, phase):
    previous = state(row).get(phase) or {}
    if previous.get("active"):
        return None
    token = uuid4().hex
    patch(row, **{phase: {"token": token, "active": True, "started_at": time.time()}})
    return token


def end_attempt(row, phase, token):
    attempt = state(row).get(phase) or {}
    if attempt.get("token") == token:
        patch(row, **{phase: {**attempt, "active": False}})


def receipt(response):
    result = response.get("result") if isinstance(response, dict) else None
    result = result if isinstance(result, dict) else {}
    value = {"accepted": bool(isinstance(response, dict) and response.get("ok", True)),
             "message_id": str(result.get("message_id") or "")[:100] or None}
    for kind in RECEIPT_TYPES:
        media = result.get(kind)
        if kind == "photo" and isinstance(media, list):
            media = max((p for p in media if isinstance(p, dict)),
                        key=lambda p: (p.get("file_size") or 0, (p.get("width") or 0) * (p.get("height") or 0)), default={})
        if isinstance(media, dict):
            value.update(media_type=kind, file_id=str(media.get("file_id") or "").strip()[:4096] or None,
                         file_unique_id=str(media.get("file_unique_id") or "")[:255] or None)
            break
    if not value.get("media_type"):
        value["unrecognized_file_ids"] = [str(media["file_id"])[:255] for media in result.values()
                                          if isinstance(media, dict) and media.get("file_id")][:16]
    # Whitelist fields only: never persist raw requests, URLs, tokens or exceptions.
    return value


def usable_receipt(value):
    return bool(value.get("accepted") and value.get("media_type") == "document" and value.get("file_id") and
                len(value["file_id"]) <= 255 and str(value.get("message_id") or "").isdecimal() and int(value["message_id"]) > 0)


def fail(row, code="staging_failed", uncertain=False):
    row.status = "failed"
    patch(row, error_code=code, error="Не удалось подготовить файл. Повторите попытку или удалите вложение.",
          uncertain=uncertain)


def preload_payload(row):
    info = state(row)
    return {"id": row.id, "status": row.status, "error": info.get("error"),
            "error_code": info.get("error_code"), "uncertain": bool(info.get("uncertain")),
            "claimed": bool(row.final_attachment_id)}


def recoverable_task_attachment(db, row):
    from ..models import Ticket, TicketAttachment, TicketMessage

    if not row.final_attachment_id or row.status != "failed":
        return None
    ticket = db.get(Ticket, row.ticket_id)
    attachment = db.get(TicketAttachment, row.final_attachment_id)
    message = db.get(TicketMessage, attachment.message_id) if attachment else None
    if (ticket and ticket.workflow_type == "task" and attachment and
            attachment.telegram_delivery_status == "failed" and not attachment.telegram_message_id and
            message and message.message_type == "agent" and message.message_status != "deleted" and
            message.author_user_id == row.user_id):
        return attachment
    return None


def expire_attempt(db, row):
    attempt = state(row).get("staging_attempt") or {}
    queued_at = state(row).get("queued_at")
    if queued_at is None and row.created_at is not None:
        from datetime import timezone
        queued_at = row.created_at.replace(tzinfo=timezone.utc).timestamp()
    if row.status == "uploading" and not attempt.get("active") and queued_at is not None and time.time() - queued_at > ATTEMPT_TIMEOUT:
        row = lock_row(db, type(row), row.id)
        if row is None:
            return None
        if row.status == "uploading" and not (state(row).get("staging_attempt") or {}).get("active"):
            fail(row, "staging_not_started")
            patch(row, archive_reconcile_pending=True)
            db.commit()
    if row.status == "uploading" and attempt.get("active") and time.time() - attempt.get("started_at", time.time()) > ATTEMPT_TIMEOUT:
        row = lock_row(db, type(row), row.id)
        if row is None:
            return None
        attempt = state(row).get("staging_attempt") or {}
        if row.status == "uploading" and attempt.get("active") and time.time() - attempt.get("started_at", time.time()) > ATTEMPT_TIMEOUT:
            fail(row, "staging_interrupted", uncertain=not usable_receipt(state(row).get("receipt") or {}))
            patch(row, staging_attempt={**attempt, "active": False}, archive_reconcile_pending=True)
            db.commit()
    if row.status == "failed" and row.final_attachment_id:
        from ..models import TicketAttachment
        attachment = db.get(TicketAttachment, row.final_attachment_id)
        if attachment is not None and attachment.telegram_delivery_status == "uploading":
            attachment.telegram_delivery_status = "failed"
            db.commit()
    if row.status == "failed" and str(row.telegram_staging_info_message_id or "").startswith("pending:") and (state(row).get("archive_reconcile_pending") or attempt_expired(row, "staging_attempt")):
        row.telegram_staging_info_message_id = None
        patch(row, uncertain=True, info_receipt_uncertain=True, archive_reconcile_pending=True)
        db.commit()
    return row


def attempt_expired(row, phase):
    from datetime import timezone

    info = state(row)
    attempt = info.get(phase) or {}
    fallback = row.created_at.replace(tzinfo=timezone.utc).timestamp() if row.created_at else time.time()
    started = attempt.get("started_at") if attempt.get("active") else info.get("queued_at", fallback)
    return time.time() - (started if started is not None else fallback) > ATTEMPT_TIMEOUT


def expire_delivery(db, row):
    from ..models import TicketAttachmentPreload, TicketMessage

    if row.telegram_delivery_status not in {"uploading", "sending"}:
        return row
    preload = db.scalar(select(TicketAttachmentPreload).where(TicketAttachmentPreload.final_attachment_id == row.id))
    if preload is not None and preload.status == "uploading" and not state(row).get("delivery_attempt"):
        expire_attempt(db, preload)
        return row
    if not attempt_expired(row, "delivery_attempt"):
        return row
    row = lock_row(db, type(row), row.id)
    if row is None or row.telegram_delivery_status not in {"uploading", "sending"} or not attempt_expired(row, "delivery_attempt"):
        return row
    info = state(row)
    confirmed = info.get("sent_file_message_id") and not info.get("delivery_pending")
    row.telegram_delivery_status = "sent" if confirmed else "failed"
    if confirmed:
        row.telegram_message_id = info["sent_file_message_id"]
        message = db.get(TicketMessage, row.message_id)
        if message is not None and not message.telegram_message_id and (message.body or "").strip():
            message.telegram_message_id = info.get("sent_text_message_id") or info["sent_file_message_id"]
    patch(row, delivery_attempt={**(info.get("delivery_attempt") or {}), "active": False},
          error_code=None if confirmed else "delivery_interrupted",
          error=None if confirmed else "Доставка Telegram не подтверждена. Автоматический повтор отключён.")
    if preload is not None and preload.status != "cancelled":
        preload.status = row.telegram_delivery_status
        patch(preload, archive_reconcile_pending=True)
    db.commit()
    return row


def heartbeat(db, row, phase):
    token = (state(row).get(phase) or {}).get("token")
    current = lock_row(db, type(row), row.id)
    attempt = state(current).get(phase) or {} if current is not None else {}
    if current is None or attempt.get("token") != token or not attempt.get("active") or getattr(current, "status", None) == "cancelled":
        raise RuntimeError("Upload attempt was cancelled or superseded")
    patch(current, **{phase: {**attempt, "started_at": time.time()}})
    db.commit()


def store_receipt(db, row, response):
    store_receipt_value(db, row, receipt(response))


def store_receipt_value(db, row, value):
    try:
        expected = (state(row).get("staging_attempt") or {}).get("token")
        current = lock_row(db, type(row), row.id)
        if current is None or (state(current).get("staging_attempt") or {}).get("token") != expected:
            raise RuntimeError("Staging attempt was superseded")
        patch(current, receipt=value)
        if value.get("message_id"):
            if hasattr(current, "telegram_staging_file_message_id"):
                current.telegram_staging_file_message_id = value["message_id"]
            else:
                current.telegram_archive_message_id = value["message_id"]
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise ReceiptRegistrationError(value) from exc


def retry_preload(db, row, confirm_uncertain=False):
    from fastapi import HTTPException

    def error(code, message):
        db.rollback()
        raise HTTPException(409, detail={"error": {"code": code, "message": message}})

    row = lock_row(db, type(row), row.id)
    if row is None:
        db.rollback()
        raise HTTPException(404, detail={"error": {"code": "attachment_preload_not_found", "message": "Предзагруженный файл не найден"}})
    refreshed = expire_attempt(db, row)
    row = lock_row(db, type(row), row.id) if refreshed is not None else None
    if row is None:
        db.rollback()
        raise HTTPException(404, detail={"error": {"code": "attachment_preload_not_found", "message": "Предзагруженный файл не найден"}})
    if row.status != "failed":
        db.commit()
        return row, False
    claimed = recoverable_task_attachment(db, row)
    if row.final_attachment_id and claimed is None:
        error("attachment_preload_already_claimed", "Вложение уже связано с отправленным сообщением")
    info = state(row)
    attempt = info.get("staging_attempt") or {}
    if attempt.get("active"):
        if time.time() - attempt.get("started_at", time.time()) <= ATTEMPT_TIMEOUT:
            db.commit()
            return row, False
        fail(row, "staging_interrupted", uncertain=not usable_receipt(info.get("receipt") or {}))
        patch(row, staging_attempt={**attempt, "active": False})
        db.commit()
        info = state(row)
    if info.get("uncertain") and not confirm_uncertain:
        error("attachment_retry_uncertain", "Telegram мог принять файл. Подтвердите повтор: в технической группе возможна лишняя копия.")
    if confirm_uncertain and not usable_receipt(info.get("receipt") or {}):
        patch(row, receipt=None, previous_receipts=[*info.get("previous_receipts", []), info.get("receipt") or {}])
        row.telegram_file_id = None
    row.status = "uploading"
    if claimed is not None:
        claimed.telegram_delivery_status = "uploading"
    patch(row, error=None, error_code=None, uncertain=False, queued_at=time.time())
    db.commit()
    return row, True


def source_references(db, path, excluded_ticket_ids=()):
    from ..models import Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage

    attachments = db.scalars(select(TicketAttachment).join(TicketMessage, TicketAttachment.message_id == TicketMessage.id).where(
        TicketAttachment.local_path == path, TicketAttachment.ticket_id.not_in(excluded_ticket_ids),
        (TicketMessage.message_status.is_(None)) | (TicketMessage.message_status != "deleted"))).all()
    preloads = db.scalars(select(TicketAttachmentPreload).join(Ticket, TicketAttachmentPreload.ticket_id == Ticket.id).where(
        TicketAttachmentPreload.local_path == path, TicketAttachmentPreload.ticket_id.not_in(excluded_ticket_ids),
        TicketAttachmentPreload.status != "cancelled")).all()
    return [*attachments, *preloads]


def archived_document(row):
    value = state(row).get("receipt")
    return usable_receipt(value) if isinstance(value, dict) else bool(row.telegram_file_id)
