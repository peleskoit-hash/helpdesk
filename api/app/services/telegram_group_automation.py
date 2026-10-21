"""Copy one client message, preserving a durable, recoverable claim before Telegram I/O."""
from contextlib import contextmanager, nullcontext
import hashlib
from threading import Lock, RLock
from weakref import WeakValueDictionary

from sqlalchemy import select, text

from ..models import TelegramContact, Ticket, TicketAttachment, TicketIntakeState, TicketStatus, TicketWorkflowType
from .telegram_bot import copy_message
from .telegram_group_registry import group_for_ticket

_locks = WeakValueDictionary()
_locks_guard = Lock()


@contextmanager
def chat_lock(db, chat_id: str, *, blocking: bool = True):
    """Pin PostgreSQL's session lock and business commits to one pooled connection."""
    engine = db.get_bind()
    if engine.dialect.name == "postgresql":
        key = int.from_bytes(hashlib.blake2b(f"telegram_group:{chat_id}".encode(), digest_size=8).digest(), "big", signed=True)
        # Callers enter before modifying data; release their read-only discovery transaction.
        db.rollback()
        with engine.connect() as control:
            if blocking:
                control.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
                acquired = True
            else:
                acquired = bool(control.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
            control.commit()
            original_bind = db.bind
            if acquired:
                db.bind = control
            try:
                yield acquired
            finally:
                if acquired:
                    db.rollback()
                    db.bind = original_bind
                    try:
                        control.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        control.commit()
                    except BaseException:
                        control.invalidate()  # Never return a connection holding a session lock to the pool.
                        raise
    else:
        with _locks_guard:
            lock = _locks.setdefault((engine, chat_id), RLock())
        acquired = lock.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()


def pending_tickets(db, chat_id=None):
    stmt = select(Ticket, TelegramContact.external_chat_id).join(
        TelegramContact, (TelegramContact.client_id == Ticket.client_id)
        & (TelegramContact.company_id == Ticket.company_id),
    ).where(Ticket.intake_state == TicketIntakeState.TELEGRAM_GROUP_COPYING.value,
            Ticket.status != TicketStatus.CLOSED.value)
    if chat_id is not None:
        stmt = stmt.where(TelegramContact.external_chat_id == chat_id)
    return db.execute(stmt).all()


def handoff(db, ticket, button, chat_id, notify_client, *, callbacks, reason):
    callbacks["append_system"](db, ticket, reason, agent_visible=True)
    if ticket.workflow_type == TicketWorkflowType.AUTO.value:
        ticket.workflow_type = TicketWorkflowType.TICKET.value
    ticket.intake_state = TicketIntakeState.READY.value
    result = callbacks["handoff"](db, ticket, button, chat_id, notify_client)
    result["workflow_type"] = ticket.workflow_type
    return result


def finish_confirmed(db, ticket, chat_id, *, callbacks):
    """Closing failures retain the committed receipt for the next locked recovery."""
    if ticket.status == TicketStatus.CLOSED.value:
        return True
    receipt = ticket.quick_action_state_json["telegram_group_delivery"]
    try:
        callbacks["append_system"](db, ticket, f"Сообщение скопировано в Telegram-группу {receipt['chat_id']}; message_id={receipt['message_id']}", agent_visible=True)
        callbacks["remove_close"](db, ticket)
        callbacks["complete"](db, ticket, chat_id, False)
        db.commit()
        return True
    except Exception:
        db.rollback()
        db.refresh(ticket)
        return ticket.status == TicketStatus.CLOSED.value


def recover(db, *, callbacks, chat_id=None, already_locked=False):
    recovered = 0
    for candidate, source_chat in pending_tickets(db, chat_id):
        with (nullcontext(True) if already_locked else chat_lock(db, source_chat, blocking=False)) as acquired:
            if not acquired:
                continue
            db.refresh(candidate)
            if candidate.intake_state != TicketIntakeState.TELEGRAM_GROUP_COPYING.value or candidate.status == TicketStatus.CLOSED.value:
                continue
            for attachment in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == candidate.id)):
                try:
                    callbacks["archive"](attachment.id)
                except Exception:
                    callbacks["append_system"](db, candidate, "Не удалось запустить архивирование сохранённого вложения", agent_visible=True)
            if (candidate.quick_action_state_json or {}).get("telegram_group_delivery"):
                recovered += finish_confirmed(db, candidate, source_chat, callbacks=callbacks)
                continue
            handoff(db, candidate, None, source_chat, False, callbacks=callbacks,
                    reason="Результат копирования в Telegram-группу неизвестен. Повторная пересылка не выполнялась; заявка передана сотрудникам.")
            recovered += 1
    return recovered


def process_message(db, ticket, button, setting, body, message_id, attachment, chat_id, notify_client,
                    *, callbacks, attachment_too_large=False, forbidden_attachment=False):
    message = callbacks["append_client"](db, ticket, body, message_id)
    group_id = setting.telegram_group_id
    archived = None
    preparation_error = None
    if attachment_too_large or forbidden_attachment:
        preparation_error = "Вложение не поддерживается существующим архивом"
    elif attachment is not None:
        try:
            archived = callbacks["store_attachment"](db, ticket, message, attachment, chat_id)
        except Exception:
            preparation_error = "Не удалось сохранить вложение клиента"
    # Persist both the unique TelegramUpdate and the source history before copyMessage.
    ticket.intake_state = TicketIntakeState.TELEGRAM_GROUP_COPYING.value
    db.commit()
    if archived is not None:
        try:
            callbacks["archive"](archived.id)
        except Exception:
            preparation_error = "Не удалось запустить архивирование вложения клиента"
    if preparation_error:
        return handoff(db, ticket, button, chat_id, notify_client, callbacks=callbacks, reason=preparation_error)
    try:
        group = group_for_ticket(db, ticket, group_id)
        result = copy_message(group.chat_id, chat_id, message_id)
        copied_id = (result.get("result") or {}).get("message_id")
        if not result.get("ok") or type(copied_id) is not int or copied_id <= 0:
            raise ValueError("copyMessage did not confirm message_id")
        # This commit must precede every closing callback and its possible side effects.
        ticket.quick_action_state_json = {
            **(ticket.quick_action_state_json or {}),
            "telegram_group_delivery": {"confirmed": True, "message_id": copied_id,
                                        "chat_id": group.chat_id, "group_id": group_id},
        }
        db.commit()
    except Exception:
        db.rollback()
        db.refresh(ticket)
        # A commit may have succeeded even if its acknowledgement was lost.
        if not (ticket.quick_action_state_json or {}).get("telegram_group_delivery"):
            return handoff(db, ticket, button, chat_id, notify_client, callbacks=callbacks,
                           reason="Копирование в Telegram-группу не подтверждено. Повторная пересылка не выполнялась; заявка передана сотрудникам.")
    receipt = ticket.quick_action_state_json["telegram_group_delivery"]
    if not finish_confirmed(db, ticket, chat_id, callbacks=callbacks):
        return {"ok": True, "ticket_id": ticket.id, "automation_closing_pending": True}
    if notify_client:
        # Notification errors must never retry a successfully copied message or reopen the ticket.
        try:
            callbacks["send"](chat_id, "Обращение передано. Для создания новой заявки нажмите /start")
        except Exception:
            pass
    return {"ok": True, "ticket_id": ticket.id, "status": ticket.status,
            "automation_completed": True, "copied_message_id": receipt["message_id"]}
