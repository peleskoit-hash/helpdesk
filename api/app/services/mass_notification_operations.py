import re
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import MassNotificationOperation, MassNotificationRecipient


class MassNotificationOperationConflict(RuntimeError):
    """The idempotency key belongs to a different request."""


@contextmanager
def _write_transaction(db: Session):
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _find_operation(db: Session, key: str) -> MassNotificationOperation | None:
    return db.scalar(select(MassNotificationOperation).where(MassNotificationOperation.idempotency_key == key))


def _existing_operation(operation: MassNotificationOperation, user_id: str, request_hash: str):
    if operation.requested_by_user_id != user_id or operation.request_hash != request_hash:
        raise MassNotificationOperationConflict("The idempotency key belongs to a different request")
    return operation, False


def _is_idempotency_violation(exc: IntegrityError) -> bool:
    original = exc.orig
    if getattr(getattr(original, "diag", None), "constraint_name", None) == "uq_mass_notification_idempotency_key":
        return True
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and str(original) == "UNIQUE constraint failed: mass_notification_operations.idempotency_key"
    )


def create_mass_notification_operation(
    db: Session,
    requested_by_user_id: str,
    idempotency_key: str,
    request_hash: str,
    text: str,
    filters: dict[str, str],
    attachments: list[dict],
    recipients: list[dict],
) -> tuple[MassNotificationOperation, bool]:
    user_id = _identifier(requested_by_user_id, "requested_by_user_id")
    key = _identifier(idempotency_key, "idempotency_key")
    if not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", request_hash):
        raise ValueError("request_hash must be a SHA-256 hex digest")
    request_hash = request_hash.lower()
    text = text if text.strip() else ""
    if not text and not attachments:
        raise ValueError("A notification needs text or an attachment")
    if len(attachments) > 10:
        raise ValueError("A notification supports at most 10 attachments")
    if not recipients:
        raise ValueError("At least one recipient is required")

    snapshot = [
        {
            "client_id": _identifier(recipient["client_id"], "client_id"),
            "chat_id": _identifier(recipient["chat_id"], "chat_id"),
            "client_name": recipient["client_name"],
        }
        for recipient in recipients
    ]
    if len({recipient["chat_id"] for recipient in snapshot}) != len(snapshot):
        raise ValueError("Recipient chat IDs must be unique within an operation")

    existing = _find_operation(db, key)
    if existing is not None:
        return _existing_operation(existing, user_id, request_hash)

    components = [{"key": "text", "kind": "text"}] if text else []
    components.extend(
        {"key": f"file:{index}", "kind": "attachment", "attachment_index": index}
        for index in range(len(attachments))
    )
    components = [
        dict(component, status="pending", telegram_message_id=None, error=None)
        for component in components
    ]
    operation = MassNotificationOperation(
        id=str(uuid4()), requested_by_user_id=user_id, idempotency_key=key, request_hash=request_hash,
        text=text, filters_json=deepcopy(filters), attachments_json=deepcopy(attachments), total_count=len(snapshot),
    )
    try:
        with _write_transaction(db):
            db.add(operation)
            db.flush()
            db.add_all([
                MassNotificationRecipient(
                    id=str(uuid4()), operation_id=operation.id, components_json=deepcopy(components), **recipient,
                )
                for recipient in snapshot
            ])
    except IntegrityError as exc:
        if not _is_idempotency_violation(exc):
            raise
        existing = _find_operation(db, key)
        if existing is None:
            raise
        return _existing_operation(existing, user_id, request_hash)
    return operation, True


def claim_mass_notification_operation(db: Session, operation_id: str, worker_token: str) -> bool:
    token = _identifier(worker_token, "worker_token")
    now = datetime.now(timezone.utc)
    with _write_transaction(db):
        result = db.execute(
            update(MassNotificationOperation)
            .where(MassNotificationOperation.id == operation_id, MassNotificationOperation.status == "queued")
            .values(status="running", started_at=now, heartbeat_at=now, worker_token=token)
        )
        return result.rowcount == 1


def _locked_recipient(db: Session, operation_id: str, recipient_id: str, worker_token: str):
    operation = MassNotificationOperation
    recipient = MassNotificationRecipient
    owns_operation = select(operation.id).where(
        operation.id == operation_id,
        operation.status == "running",
        operation.worker_token == worker_token,
    )
    recipient_match = (
        recipient.id == recipient_id,
        recipient.operation_id == operation_id,
        recipient.status.in_(("pending", "sending")),
    )
    if db.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Acquire its write lock before reading the component JSON.
        locked = db.execute(
            update(recipient).where(*recipient_match, owns_operation.exists())
            .values(updated_at=recipient.updated_at)
            .execution_options(synchronize_session=False)
        )
        if locked.rowcount != 1:
            return None
    return db.scalar(
        select(recipient).join(operation, operation.id == recipient.operation_id)
        .where(*recipient_match, operation.id.in_(owns_operation))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _change_component(
    db: Session, operation_id: str, recipient_id: str, component_key: str, worker_token: str,
    expected: str, status: str, telegram_message_id: int | None = None, error: str | None = None,
) -> bool:
    with _write_transaction(db):
        recipient = _locked_recipient(db, operation_id, recipient_id, _identifier(worker_token, "worker_token"))
        if recipient is None:
            return False
        for index, component in enumerate(recipient.components_json):
            if component["key"] == component_key and component["status"] == expected:
                components = list(recipient.components_json)
                components[index] = dict(component, status=status, telegram_message_id=telegram_message_id, error=error)
                recipient.components_json = components
                recipient.status = _recipient_status(components)
                if error is not None:
                    recipient.error_message = error
                recipient.updated_at = datetime.now(timezone.utc)
                return True
        return False


def _recipient_status(components: list[dict]) -> str:
    statuses = {component["status"] for component in components}
    if statuses & {"pending", "sending"}:
        return "sending"
    if statuses == {"sent"}:
        return "sent"
    if "sent" in statuses:
        return "partial"
    if "unknown" in statuses:
        return "unknown"
    return "failed"


def begin_mass_notification_component(
    db: Session, operation_id: str, recipient_id: str, component_key: str, worker_token: str,
) -> bool:
    return _change_component(db, operation_id, recipient_id, component_key, worker_token, "pending", "sending")


def finish_mass_notification_component(
    db: Session, operation_id: str, recipient_id: str, component_key: str, worker_token: str,
    status: str, telegram_message_id: int | None = None, error: str | None = None,
) -> bool:
    if status not in {"sent", "failed", "unknown"}:
        raise ValueError("A component must finish with sent, failed or unknown")
    if status == "sent":
        if type(telegram_message_id) is not int or telegram_message_id <= 0:
            raise ValueError("A sent component requires a positive Telegram message ID")
        error = None
    else:
        telegram_message_id = None
    return _change_component(
        db, operation_id, recipient_id, component_key, worker_token, "sending", status, telegram_message_id, error,
    )


def skip_mass_notification_recipient(
    db: Session, operation_id: str, recipient_id: str, worker_token: str, reason: str,
) -> bool:
    with _write_transaction(db):
        recipient = _locked_recipient(db, operation_id, recipient_id, _identifier(worker_token, "worker_token"))
        if recipient is None or any(component["status"] != "pending" for component in recipient.components_json):
            return False
        recipient.status = "skipped"
        recipient.error_message = reason
        recipient.updated_at = datetime.now(timezone.utc)
        return True
