import logging
from dataclasses import dataclass, field

from sqlalchemy import event, select
from ..config import settings
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import TelegramNotificationAccount
from .telegram_bot import send_message
from .telegram_employee_binding import REQUEST_STATUS_APPROVED

logger = logging.getLogger(__name__)
_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY = "employee_assignment_notifications"
ASSIGNMENT_EVENT_NEW = "new_assignment"
ASSIGNMENT_EVENT_TRANSFER = "transfer_between_agents"
ASSIGNMENT_EVENT_UNASSIGNED = "removed_from_agent"
DELIVERY_KIND_NEW = "new_assignment"
DELIVERY_KIND_TRANSFER_TO = "transfer_to_agent"
DELIVERY_KIND_TRANSFER_FROM = "transfer_from_agent"


@dataclass(frozen=True)
class AssignmentNotificationDelivery:
    kind: str
    user_id: str


@dataclass(frozen=True)
class AssignmentNotificationEvent:
    kind: str
    old_assignee_id: str | None
    new_assignee_id: str | None
    deliveries: tuple[AssignmentNotificationDelivery, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AssignmentNotificationTelegramDelivery:
    event_kind: str
    delivery_kind: str
    user_id: str
    telegram_chat_id: str
    text: str


def _telegram_account_for_user(db: Session, user_id: str) -> TelegramNotificationAccount | None:
    return db.execute(
        select(TelegramNotificationAccount).where(
            TelegramNotificationAccount.bound_user_id == user_id
        )
    ).scalar_one_or_none()


def _approved_account_for_user(db: Session, user_id: str) -> TelegramNotificationAccount | None:
    account = _telegram_account_for_user(db, user_id)
    if account is None:
        return None
    if account.is_blocked:
        return None
    if account.request_status != REQUEST_STATUS_APPROVED:
        return None
    if not account.delivery_available:
        return None
    if not account.telegram_chat_id:
        return None
    return account


def _ticket_reference(ticket) -> str:
    if getattr(ticket, "ticket_number", None):
        return f"№{ticket.ticket_number}"
    return f"ID {ticket.id}"


def build_assignment_notification_text(ticket) -> str:
    lines = [f"Вам назначена новая заявка {_ticket_reference(ticket)}"]
    subject = (getattr(ticket, "subject", None) or "").strip()
    if subject:
        lines.extend(["", "Тема:", subject])
    return "\n".join(lines)


def build_assignment_notification_event(
    *,
    old_assignee_id: str | None,
    new_assignee_id: str | None,
) -> AssignmentNotificationEvent | None:
    if old_assignee_id == new_assignee_id:
        return None
    if old_assignee_id is None and new_assignee_id:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_NEW,
            old_assignee_id=None,
            new_assignee_id=new_assignee_id,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_NEW, user_id=new_assignee_id),),
        )
    if old_assignee_id and new_assignee_id:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_TRANSFER,
            old_assignee_id=old_assignee_id,
            new_assignee_id=new_assignee_id,
            deliveries=(
                AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_TO, user_id=new_assignee_id),
                AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_FROM, user_id=old_assignee_id),
            ),
        )
    if old_assignee_id and new_assignee_id is None:
        return AssignmentNotificationEvent(
            kind=ASSIGNMENT_EVENT_UNASSIGNED,
            old_assignee_id=old_assignee_id,
            new_assignee_id=None,
            deliveries=(AssignmentNotificationDelivery(kind=DELIVERY_KIND_TRANSFER_FROM, user_id=old_assignee_id),),
        )
    return None


def resolve_assignment_telegram_deliveries(
    db: Session,
    *,
    event: AssignmentNotificationEvent,
    text: str,
) -> tuple[AssignmentNotificationTelegramDelivery, ...]:
    resolved: list[AssignmentNotificationTelegramDelivery] = []
    for delivery in event.deliveries:
        account = _approved_account_for_user(db, delivery.user_id)
        if account is None:
            continue
        resolved.append(
            AssignmentNotificationTelegramDelivery(
                event_kind=event.kind,
                delivery_kind=delivery.kind,
                user_id=delivery.user_id,
                telegram_chat_id=account.telegram_chat_id,
                text=text,
            )
        )
    return tuple(resolved)


def schedule_assignment_notification(
    db: Session,
    *,
    old_assignee_id: str | None,
    new_assignee_id: str | None,
    ticket,
) -> AssignmentNotificationEvent | None:
    event_payload = build_assignment_notification_event(
        old_assignee_id=old_assignee_id,
        new_assignee_id=new_assignee_id,
    )
    if event_payload is None:
        return None
    if not event_payload.deliveries:
        return
    pending = db.info.setdefault(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, [])
    pending.append(
        {
            "event_kind": event_payload.kind,
            "old_assignee_id": event_payload.old_assignee_id,
            "new_assignee_id": event_payload.new_assignee_id,
            "deliveries": [
                {"kind": delivery.kind, "user_id": delivery.user_id}
                for delivery in event_payload.deliveries
            ],
            "text": build_assignment_notification_text(ticket),
        }
    )
    return event_payload


@event.listens_for(Session, "after_commit")
def _send_scheduled_assignment_notifications(session: Session) -> None:
    pending = session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, [])
    if not pending:
        return

    for item in pending:
        try:
            with SessionLocal() as db:
                event_payload = AssignmentNotificationEvent(
                    kind=item["event_kind"],
                    old_assignee_id=item.get("old_assignee_id"),
                    new_assignee_id=item.get("new_assignee_id"),
                    deliveries=tuple(
                        AssignmentNotificationDelivery(
                            kind=delivery["kind"],
                            user_id=delivery["user_id"],
                        )
                        for delivery in (item.get("deliveries") or [])
                    ),
                )
                deliveries = resolve_assignment_telegram_deliveries(
                    db,
                    event=event_payload,
                    text=item["text"],
                )
                for delivery in deliveries:
                    send_message(
                        delivery.telegram_chat_id,
                        delivery.text,
                        bot_token=settings.employee_telegram_bot_token,
                    )
        except Exception:
            logger.exception("Failed to send employee assignment notification")


@event.listens_for(Session, "after_rollback")
def _clear_scheduled_assignment_notifications(session: Session) -> None:
    session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, None)


@event.listens_for(Session, "after_soft_rollback")
def _clear_scheduled_assignment_notifications_after_soft_rollback(session: Session, previous_transaction) -> None:
    session.info.pop(_PENDING_ASSIGNMENT_NOTIFICATIONS_KEY, None)


def notify_user(db: Session, user_id: str, text: str) -> bool:
    if not settings.employee_telegram_bot_token:
        return False
    account = _approved_account_for_user(db, user_id)
    if account is None:
        return False
    send_message(account.telegram_chat_id, text, bot_token=settings.employee_telegram_bot_token)
    return True


def notify_users(db: Session, user_ids: list[str], text: str) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for user_id in user_ids:
        results[user_id] = notify_user(db, user_id, text)
    return results
