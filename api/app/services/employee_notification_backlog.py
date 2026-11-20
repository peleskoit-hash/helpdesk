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
    prefix = (f"nb:{period.id}:{period.offer_generation}" if period.period_mode == "schedule"
              else f"nb:{period.id}")
    response = send_message(
        period.chat_id, text, bot_token=settings.employee_telegram_bot_token,
        reply_markup={"inline_keyboard": [[
            {"text": "Да", "callback_data": f"{prefix}:yes"},
            {"text": "Нет", "callback_data": f"{prefix}:no"},
        ]]},
    )
    period.message_id = str(response["result"]["message_id"])
    period.offered = True


def _locked_schedule_period(db, period_id):
    user_id = db.scalar(select(EmployeeNotificationPeriod.user_id).where(EmployeeNotificationPeriod.id == period_id))
    if user_id is None or _lock_user(db, user_id) is None:
        return None
    _schedule_binding(db, user_id)
    if inspect(db.connection()).has_table(Snapshot.__tablename__):
        list(db.scalars(select(Snapshot.month).order_by(Snapshot.month).with_for_update(read=True)))
    return db.scalar(select(EmployeeNotificationPeriod).where(EmployeeNotificationPeriod.id == period_id)
                     .with_for_update().execution_options(populate_existing=True))


def _schedule_delivery_allowed(db, period, now=None):
    if (period is None or period.period_mode != "schedule" or not settings.employee_telegram_bot_token
            or period.window_state != "resolved" or period.offer_at is None or period.expires_at is None
            or _schedule_binding(db, period.user_id) is None):
        return None
    local = _moscow_now(now)
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    if not period.offer_at <= utc < period.expires_at:
        return None
    shift = resolve_employee_schedule(db, period.user_id, local)
    if (shift.status != "working" or not _inside_shift(shift, local) or shift.date != period.work_date
            or shift.start_at.astimezone(timezone.utc).replace(tzinfo=None) != period.offer_at
            or shift.end_at.astimezone(timezone.utc).replace(tzinfo=None) != period.expires_at):
        return None
    revision = db.scalar(select(Snapshot.revision).where(Snapshot.month == period.work_date.strftime("%Y-%m")))
    if revision != period.schedule_revision:
        return None
    account = _eligible_account(db, period)
    if account is not None:
        db.refresh(account, with_for_update=True)
        account = _eligible_account(db, period)
    return account


def _offer_schedule(db, period_id, *, now=None, sent=None):
    period = _locked_schedule_period(db, period_id)
    if (not _schedule_delivery_allowed(db, period, now) or period.offered or period.message_id is not None):
        return
    first = db.scalar(select(EmployeeNotificationBacklog).where(
        EmployeeNotificationBacklog.period_id == period.id).order_by(EmployeeNotificationBacklog.id).limit(1))
    if first is None or first.send_state != "pending":
        return
    period.offer_generation += 1
    period.offer_state, period.offered = "offered", True
    generation = period.offer_generation
    # Persist the offer attempt: an ambiguous Telegram result must not cause
    # another prompt on every worker pass or after restart.
    db.commit()
    period = _locked_schedule_period(db, period_id)
    if (not _schedule_delivery_allowed(db, period, now) or period.offer_generation != generation
            or period.offer_state != "offered" or not period.offered or period.message_id is not None):
        if (period is not None and period.offer_generation == generation and period.message_id is None):
            # The request has not started: this is a known unsent offer.
            period.offered, period.offer_state = False, "idle"
            db.commit()
        return
    try:
        _offer(db, period, sent=sent)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Could not confirm employee schedule backlog offer")


def _handle_schedule_callback(db, callback, parts, now):
    ignored = {"ok": True, "ignored": True}
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    period = _locked_schedule_period(db, parts[1])
    account = _schedule_delivery_allowed(db, period, now)
    if (account is None or period.offer_state != "offered" or not period.offered
            or str(period.offer_generation) != parts[2] or period.message_id is None
            or period.message_id != str(message.get("message_id"))
            or period.chat_id != str(chat.get("id"))
            or account.telegram_user_id != str((callback.get("from") or {}).get("id"))):
        db.rollback()
        return ignored
    period_id = period.id
    prompts = []
    _invalidate_offer(period, prompts)
    if parts[3] == "no":
        _clear(db, period)
    else:
        period.offered = True  # Reserve this batch across durable per-item commits.
    generation = period.offer_generation
    sender_id = account.telegram_user_id
    db.commit()  # Consume the button before any external action.
    for chat_id, message_id in prompts:
        _delete_prompt(chat_id, message_id)
    if parts[3] == "no":
        return {"ok": True, "backlog": True}
    sent = 0
    for _ in range(5):
        period = _locked_schedule_period(db, period_id)
        account = _schedule_delivery_allowed(db, period, now)
        if account is None or account.telegram_user_id != sender_id or period.offer_generation != generation:
            break
        item = db.scalar(select(EmployeeNotificationBacklog).where(
            EmployeeNotificationBacklog.period_id == period_id
        ).order_by(EmployeeNotificationBacklog.id).limit(1).with_for_update())
        if item is None or item.send_state != "pending":
            break
        item_id, attempt = item.id, str(uuid.uuid4())
        item.send_state, item.send_attempt_id = "sending", attempt
        db.commit()
        period = _locked_schedule_period(db, period_id)
        item = db.get(EmployeeNotificationBacklog, item_id, populate_existing=True)
        if item is None or item.period_id != period_id or item.send_attempt_id != attempt:
            break
        account = _schedule_delivery_allowed(db, period, now)
        if account is None or account.telegram_user_id != sender_id or period.offer_generation != generation:
            item.send_state, item.send_attempt_id = "pending", None
            db.commit()  # No network request was made.
            break
        try:
            response = send_message(
                period.chat_id, item.text, parse_mode=item.parse_mode,
                bot_token=settings.employee_telegram_bot_token,
                disable_web_page_preview=item.disable_link_preview, disable_notification=item.send_silently,
                retry_only_parse_errors=True,
            )
        except Exception:
            item.send_state = "unknown"
            db.commit()
            logger.warning("Employee schedule backlog delivery result is unknown")
            break
        if not response.get("ok"):
            item.send_state, item.send_attempt_id = "pending", None
            db.commit()
            break
        db.delete(item)
        db.commit()
        sent += 1
    period = _locked_schedule_period(db, period_id)
    if period is not None and period.offer_generation == generation:
        period.offered = False
        db.commit()
    _offer_schedule(db, period_id, now=now, sent=sent)
    db.rollback()  # Release locks if no next offer was needed.
    return {"ok": True, "backlog": True}


def _expired(period: EmployeeNotificationPeriod, local: datetime) -> bool:
    return local >= datetime.combine(period.work_date, time(19), tzinfo=MOSCOW)


def _invalidate_offer(period, prompts):
    if period.message_id is not None:
        prompts.append((period.chat_id, period.message_id))
    period.message_id = None
    period.offered = False
    period.offer_generation += 1
    period.offer_state = "invalidated"


def _process_schedule_periods(db, user, binding, periods, local, prompts):
    schedule = [p for p in periods if p.period_mode == "schedule"]
    if not schedule:
        return
    if not user.is_active:
        # Deactivation pauses delivery, not a previously confirmed storage window.
        # Do not call the resolver: user_inactive would erase that confirmation.
        now = local.astimezone(timezone.utc).replace(tzinfo=None)
        for period in schedule:
            if period.message_id is not None or period.offered or period.offer_state == "offered":
                _invalidate_offer(period, prompts)
            if (binding is None or period.window_state != "resolved" or period.work_date is None
                    or period.offer_at is None or period.expires_at is None
                    or period.offer_at >= period.expires_at or period.schedule_revision is None):
                continue
            revision = (db.scalar(select(Snapshot.revision).where(
                Snapshot.month == period.work_date.strftime("%Y-%m")))
                if inspect(db.connection()).has_table(Snapshot.__tablename__) else None)
            # A missing or changed revision cannot confirm the saved deadline.
            # Reactivation takes the normal recalculation path before any expiry.
            if revision == period.schedule_revision and now >= period.expires_at:
                _clear(db, period)
                db.delete(period)
        db.flush()
        return
    legacy_dates = {p.work_date for p in periods if p.period_mode == "legacy"}
    groups = {}
    cache = {}
    for period in schedule:
        items = list(db.scalars(select(EmployeeNotificationBacklog).where(
            EmployeeNotificationBacklog.period_id == period.id
        ).order_by(EmployeeNotificationBacklog.id).with_for_update()))
        for item in items:
            # Historical NULL timestamps cannot reliably place individual messages.
            anchor = item.created_at
            window = None
            if user.is_active and binding is not None and anchor is not None:
                if anchor not in cache:
                    cache[anchor] = _first_schedule_window(db, user.id, _moscow_now(anchor))
                window = cache[anchor]
            if window is not None and window.date in legacy_dates:
                window = None
            key = window.date if window else None
            group = groups.setdefault(key, {"window": window, "items": [], "origins": set(), "anchors": []})
            group["items"].append(item)
            group["origins"].add(period.id)
            if anchor is not None:
                group["anchors"].append(anchor)
            elif period.anchor_at is not None:
                group["anchors"].append(period.anchor_at)

    # If distribution changed, stage all rows in the sole unresolved period.
    # Release old dates before inserting targets, including date swaps and merges.
    unchanged = (len(groups) == len(schedule) and all(
        len(g["origins"]) == 1 and any(p.id in g["origins"] and p.work_date == day for p in schedule)
        for day, g in groups.items()))
    if not unchanged and schedule:
        staging = next((p for p in schedule if p.work_date is None), schedule[0])
        for p in schedule:
            _invalidate_offer(p, prompts)
            if p is not staging:
                db.execute(update(EmployeeNotificationBacklog).where(EmployeeNotificationBacklog.period_id == p.id)
                           .values(period_id=staging.id).execution_options(synchronize_session=False))
                db.delete(p)
        db.flush()
        staging.work_date = None
        db.flush()
    else:
        staging = None

    targets = []
    for day, group in groups.items():
        window = group["window"]
        offer = window.start_at.astimezone(timezone.utc).replace(tzinfo=None) if window else None
        expiry = window.end_at.astimezone(timezone.utc).replace(tzinfo=None) if window else None
        revision = db.scalar(select(Snapshot.revision).where(Snapshot.month == day.strftime("%Y-%m"))) if day else None
        if unchanged:
            target = next(p for p in schedule if p.id in group["origins"])
        elif staging is not None and (day is None or None not in groups):
            target, staging = staging, None
        else:
            target = EmployeeNotificationPeriod(id=str(uuid.uuid4()), user_id=user.id,
                chat_id=schedule[0].chat_id, period_mode="schedule", work_date=day)
            db.add(target)
            db.flush()
        state = "resolved" if window else "unresolved"
        if unchanged and (target.offer_at, target.expires_at, target.schedule_revision, target.window_state) != (
                offer, expiry, revision, state):
            _invalidate_offer(target, prompts)
        target.work_date, target.offer_at, target.expires_at = day, offer, expiry
        target.schedule_revision, target.window_state = revision, state
        target.anchor_at = min(group["anchors"], default=None)
        for item in group["items"]:
            item.period_id = target.id
        targets.append(target)
    db.flush()
    if staging is not None:
        db.delete(staging)
    now = local.astimezone(timezone.utc).replace(tzinfo=None)
    for target in targets:
        if target.expires_at is not None and now >= target.expires_at:
            _invalidate_offer(target, prompts)
            _clear(db, target)
            db.delete(target)
    db.flush()


def _maintain_backlog(session_factory, local):
    cutoff = local.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    with session_factory() as db:
        users = list(db.scalars(select(EmployeeNotificationPeriod.user_id).distinct()))
    for user_id in users:
        prompts = []
        with session_factory() as db:
            user = _lock_user(db, user_id)
            if user is None:
                continue
            binding = _schedule_binding(db, user_id)
            if binding is not None and inspect(db.connection()).has_table(Snapshot.__tablename__):
                list(db.scalars(select(Snapshot.month).order_by(Snapshot.month).with_for_update(read=True)))
            periods = list(db.scalars(select(EmployeeNotificationPeriod).where(
                EmployeeNotificationPeriod.user_id == user_id
            ).order_by(EmployeeNotificationPeriod.id).with_for_update()))
            remaining = []
            for period in periods:
                removed = db.execute(delete(EmployeeNotificationBacklog).where(
                    EmployeeNotificationBacklog.period_id == period.id,
                    EmployeeNotificationBacklog.created_at <= cutoff,
                )).rowcount
                if not _count(db, period):
                    _invalidate_offer(period, prompts)
                    db.delete(period)
                else:
                    if removed:
                        _invalidate_offer(period, prompts)
                    remaining.append(period)
            db.flush()
            _process_schedule_periods(db, user, binding, remaining, local, prompts)
            db.commit()
        if settings.employee_telegram_bot_token:
            for chat_id, message_id in prompts:
                _delete_prompt(chat_id, message_id)


def run_employee_notification_backlog_once(*, session_factory=SessionLocal, now_utc: datetime | None = None) -> None:
    local = _moscow_now(now_utc)
    _maintain_backlog(session_factory, local)
    if not settings.employee_telegram_bot_token:
        return
    with session_factory() as db:
        schedule_ids = list(db.scalars(select(EmployeeNotificationPeriod.id).where(
            EmployeeNotificationPeriod.period_mode == "schedule")))
    for period_id in schedule_ids:
        with session_factory() as db:
            _offer_schedule(db, period_id, now=now_utc)
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
    if len(parts) not in {3, 4} or parts[-1] not in {"yes", "no"}:
        return ignored
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return ignored
    if len(parts) == 4:
        return _handle_schedule_callback(db, callback, parts, now)
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
