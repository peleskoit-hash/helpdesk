from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import (EmployeeNotificationBacklog, EmployeeNotificationPeriod, TelegramNotificationAccount, User,
                      EmployeeScheduleBinding as Binding, EmployeeScheduleSnapshot as Snapshot)
from .employee_schedule import resolve_employee_schedule
from .telegram_bot import send_message, telegram_request

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def _moscow_now(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(MOSCOW)


def is_notification_work_time(now: datetime | None = None) -> bool:
    local = _moscow_now(now)
    return local.weekday() < 6 and 8 <= local.hour < 19


def _work_date(local: datetime):
    day = local.date()
    if local.hour >= 19:
        day += timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def _lock_user(db, user_id):
    if db.get_bind().dialect.name == "sqlite":
        # Earlier SELECTs may have opened only SQLAlchemy's virtual transaction.
        if not db.connection().connection.driver_connection.in_transaction:
            db.execute(text("BEGIN IMMEDIATE"))
    return db.scalar(select(User).where(User.id == user_id).with_for_update()
                     .execution_options(populate_existing=True))


def _schedule_binding(db, user_id):
    if not inspect(db.connection()).has_table(Binding.__tablename__):
        return None
    binding = db.scalar(select(Binding).where(Binding.user_id == user_id).with_for_update()
                        .execution_options(populate_existing=True))
    return binding if binding is not None and binding.enabled else None


def _inside_shift(shift, local):
    return (shift.start_at is not None and shift.end_at is not None
            and shift.start_at.utcoffset() is not None and shift.end_at.utcoffset() is not None
            and shift.start_at <= local < shift.end_at)


def _first_schedule_window(db, user_id, anchor):
    probe = anchor
    while True:
        shift = resolve_employee_schedule(db, user_id, probe)
        if shift.status == "unknown":
            return None
        if shift.status in {"before_shift", "working", "after_shift"}:
            if (shift.start_at is None or shift.end_at is None or shift.start_at.utcoffset() is None
                    or shift.end_at.utcoffset() is None or shift.start_at >= shift.end_at):
                return None
            if shift.end_at > anchor:
                return shift
        elif shift.status != "non_working":
            return None
        if probe.date() == datetime.max.date():
            return None
        probe = datetime.combine(probe.date() + timedelta(days=1), time.min, tzinfo=MOSCOW)


def _unresolved_period(db, user_id):
    return db.scalar(select(EmployeeNotificationPeriod).where(
        EmployeeNotificationPeriod.user_id == user_id, EmployeeNotificationPeriod.period_mode == "schedule",
        EmployeeNotificationPeriod.work_date.is_(None),
    ).with_for_update())


def _release_schedule_date(db, period):
    """Free a legacy target date without applying legacy expiry to Spark messages."""
    unresolved = _unresolved_period(db, period.user_id)
    if unresolved is None:
        unresolved = period
        unresolved.work_date = None
    else:
        # Reparent only: IDs retain the original global order, and all timestamps,
        # Telegram parameters and send-attempt fields stay unchanged.
        db.execute(update(EmployeeNotificationBacklog).where(EmployeeNotificationBacklog.period_id == period.id)
                   .values(period_id=unresolved.id).execution_options(synchronize_session=False))
        unresolved.anchor_at = min(
            (anchor for anchor in (unresolved.anchor_at, period.anchor_at) if anchor is not None),
            default=None,
        )
        db.delete(period)
    unresolved.window_state = "unresolved"
    unresolved.offer_at = unresolved.expires_at = unresolved.schedule_revision = None
    db.flush()  # Release (user_id, work_date) before the legacy INSERT.


def route_delivery(db, delivery, *, now: datetime | None = None, locked=False) -> str:
    """Decide send/accumulate/suppress and stage accumulation; caller commits.

    Lock order: User -> Spark binding -> snapshots -> period. locked=True is for
    the final time recheck inside the same already-locked send transaction only.
    """
    user = db.get(User, delivery.user_id) if locked else _lock_user(db, delivery.user_id)
    if user is None or not user.is_active:
        return "suppress"
    local = _moscow_now(now)
    binding = _schedule_binding(db, user.id)
    window = None
    if binding is not None:
        if inspect(db.connection()).has_table(Snapshot.__tablename__):
            list(db.scalars(select(Snapshot.month).order_by(Snapshot.month).with_for_update(read=True)))
        shift = resolve_employee_schedule(db, user.id, local)
        if shift.status == "working" and _inside_shift(shift, local):
            return "send"
        window = _first_schedule_window(db, user.id, local)
        day = window.date if window else None
    else:
        if is_notification_work_time(local):
            return "send"
        day = _work_date(local)
    period = None
    if day is not None:
        period = db.scalar(select(EmployeeNotificationPeriod).where(
            EmployeeNotificationPeriod.user_id == user.id, EmployeeNotificationPeriod.work_date == day,
        ).with_for_update())
        if binding is not None and period is not None and period.period_mode == "legacy":
            day, window, period = None, None, None
        elif binding is None and period is not None and period.period_mode == "schedule":
            _release_schedule_date(db, period)
            period = None
    if binding is not None and day is None:
        period = _unresolved_period(db, user.id)
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    if period is None:
        period = EmployeeNotificationPeriod(id=str(uuid.uuid4()), user_id=user.id, work_date=day,
                                            chat_id=delivery.telegram_chat_id)
        if binding is not None:
            period.period_mode, period.anchor_at = "schedule", utc
            period.window_state = "resolved" if window else "unresolved"
            if window:
                period.offer_at = window.start_at.astimezone(timezone.utc).replace(tzinfo=None)
                period.expires_at = window.end_at.astimezone(timezone.utc).replace(tzinfo=None)
                period.schedule_revision = db.scalar(select(Snapshot.revision).where(
                    Snapshot.month == window.date.strftime("%Y-%m")))
        db.add(period)
        db.flush()
    db.add(EmployeeNotificationBacklog(
        period_id=period.id, text=delivery.text, parse_mode=delivery.parse_mode,
        disable_link_preview=delivery.disable_link_preview, send_silently=delivery.send_silently,
        created_at=utc if binding is not None else None, send_state="pending",
    ))
    return "accumulate"


def accumulate_delivery(delivery, *, session_factory=SessionLocal, now: datetime | None = None) -> bool:
    # Compatibility: True means handled, including suppression, never a send fallback.
    with session_factory() as db:
        decision = route_delivery(db, delivery, now=now)
        db.commit()
    return decision != "send"


def _count(db: Session, period: EmployeeNotificationPeriod) -> int:
    return db.scalar(select(func.count()).select_from(EmployeeNotificationBacklog).where(
        EmployeeNotificationBacklog.period_id == period.id
    ))


def _delete_prompt(chat_id: str, message_id: str | None) -> None:
    if message_id is None:
        return
    try:
        telegram_request("deleteMessage", payload={"chat_id": chat_id, "message_id": int(message_id)},
                         bot_token=settings.employee_telegram_bot_token)
    except Exception:
        # Server-side message identity is invalidated even when Telegram refuses deletion.
        logger.warning("Could not delete employee backlog prompt", exc_info=True)


def _clear(db: Session, period: EmployeeNotificationPeriod) -> None:
    db.execute(delete(EmployeeNotificationBacklog).where(EmployeeNotificationBacklog.period_id == period.id))


def _eligible_account(db: Session, period: EmployeeNotificationPeriod):
    return db.execute(select(TelegramNotificationAccount).join(
        User, User.id == TelegramNotificationAccount.bound_user_id
    ).where(
        TelegramNotificationAccount.bound_user_id == period.user_id,
        TelegramNotificationAccount.telegram_chat_id == period.chat_id,
        TelegramNotificationAccount.request_status == "approved",
        TelegramNotificationAccount.is_blocked.is_(False),
        TelegramNotificationAccount.delivery_available.is_(True),
        User.is_active.is_(True),
    )).scalar_one_or_none()


def _offer(db: Session, period: EmployeeNotificationPeriod, *, sent: int | None = None) -> None:
    remaining = _count(db, period)
    if not remaining:
        period.offered = True
        return
    text = (f"В нерабочее время у Вас накопилось {remaining} уведомлений. Хотите их прочитать?"
            if sent is None else
            f"Выгрузил {sent} уведомлений. Осталось {remaining}. Показать следующие (максимум 5 за раз)?")
    response = send_message(
        period.chat_id, text, bot_token=settings.employee_telegram_bot_token,
        reply_markup={"inline_keyboard": [[
            {"text": "Да", "callback_data": f"nb:{period.id}:yes"},
            {"text": "Нет", "callback_data": f"nb:{period.id}:no"},
        ]]},
    )
    period.message_id = str(response["result"]["message_id"])
    period.offered = True


def _expired(period: EmployeeNotificationPeriod, local: datetime) -> bool:
    return local >= datetime.combine(period.work_date, time(19), tzinfo=MOSCOW)


def run_employee_notification_backlog_once(*, session_factory=SessionLocal, now_utc: datetime | None = None) -> None:
    if not settings.employee_telegram_bot_token:
        return
    local = _moscow_now(now_utc)
    with session_factory() as db:
        ids = list(db.execute(select(EmployeeNotificationPeriod.id, EmployeeNotificationPeriod.user_id).where(
            EmployeeNotificationPeriod.period_mode == "legacy",
            EmployeeNotificationPeriod.work_date <= local.date()
        )))
    for period_id, user_id in ids:
        with session_factory() as db:
            if _lock_user(db, user_id) is None or _schedule_binding(db, user_id) is not None:
                continue
            period = db.scalar(select(EmployeeNotificationPeriod).where(
                EmployeeNotificationPeriod.id == period_id
            ).with_for_update(skip_locked=True))
            if period is None or period.period_mode != "legacy":
                continue
            if _expired(period, local):
                chat_id, message_id = period.chat_id, period.message_id
                _clear(db, period)
                db.delete(period)
                db.commit()
                _delete_prompt(chat_id, message_id)
            elif is_notification_work_time(local) and not period.offered and _eligible_account(db, period):
                try:
                    _offer(db, period)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Could not offer employee notification backlog")


def handle_backlog_callback(db: Session, callback: dict, *, now: datetime | None = None) -> dict:
    ignored = {"ok": True, "ignored": True}
    parts = str(callback.get("data") or "").split(":")
    if len(parts) != 3 or parts[2] not in {"yes", "no"}:
        return ignored
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return ignored
    user_id = db.scalar(select(EmployeeNotificationPeriod.user_id).where(EmployeeNotificationPeriod.id == parts[1]))
    if user_id is None or _lock_user(db, user_id) is None or _schedule_binding(db, user_id) is not None:
        db.rollback()
        return ignored
    period = db.scalar(select(EmployeeNotificationPeriod).where(
        EmployeeNotificationPeriod.id == parts[1]
    ).with_for_update())
    local = _moscow_now(now)
    if (period is None or period.period_mode != "legacy" or period.message_id is None
            or period.message_id != str(message.get("message_id"))
            or period.chat_id != str(chat.get("id"))
            or period.work_date != local.date() or not is_notification_work_time(local)):
        db.rollback()
        return ignored
    account = _eligible_account(db, period)
    if account is None or account.telegram_user_id != str((callback.get("from") or {}).get("id")):
        db.rollback()
        return ignored
    old_message_id = period.message_id
    period.message_id = None
    period.offered = True
    _delete_prompt(period.chat_id, old_message_id)
    if parts[2] == "no":
        _clear(db, period)
    else:
        items = list(db.scalars(select(EmployeeNotificationBacklog).where(
            EmployeeNotificationBacklog.period_id == period.id
        ).order_by(EmployeeNotificationBacklog.id).limit(5)))
        sent = 0
        for item in items:
            try:
                response = send_message(
                    period.chat_id, item.text, parse_mode=item.parse_mode,
                    bot_token=settings.employee_telegram_bot_token,
                    disable_web_page_preview=item.disable_link_preview, disable_notification=item.send_silently,
                )
                if not response.get("ok"):
                    break
            except Exception:
                logger.exception("Could not deliver accumulated employee notification")
                break
            db.delete(item)
            sent += 1
        db.flush()
        # Keep successful sends even if sending the next question fails.
        period.offered = False
        try:
            _offer(db, period, sent=sent)
        except Exception:
            logger.exception("Could not send next employee backlog question")
    db.commit()
    return {"ok": True, "backlog": True}
