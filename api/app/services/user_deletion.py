from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    BotButtonSetting,
    EmployeeNotificationBacklog,
    EmployeeNotificationPeriod,
    EmployeeNotificationPreference,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskOccurrence,
    QuickReply,
    QuickReplyUsage,
    StatsCompletion,
    StatsUserStatusInterval,
    StatsWorkInterval,
    TelegramGroupRegistrationCode,
    TelegramNotificationAccount,
    Ticket,
    TicketAttachmentPreload,
    TicketMessage,
    TicketReview,
    TicketReviewCycle,
    TicketStatus,
    TicketView,
    User,
    UserDeletionOperation,
)
from .user_avatars import resolve_avatar_path

logger = logging.getLogger(__name__)


class UserDeletionError(Exception):
    pass


def format_deleted_employee_name(full_name: str) -> str:
    parts = full_name.split()
    short_name = " ".join(parts[:1] + [f"{name[0]}." for name in parts[1:2]])
    return f"{short_name} (wasted)"


def _check_active_work(db: Session, user_id: str) -> None:
    assigned_task_tickets = select(InternalTaskOccurrence.ticket_id).where(
        or_(
            InternalTaskOccurrence.responsible_admin_id == user_id,
            InternalTaskOccurrence.effective_assignee_user_id == user_id,
        ),
    )
    # Hold existing ticket rows so a closed assignment cannot be reopened mid-deletion.
    statuses = db.scalars(
        select(Ticket.status)
        .where(or_(
            Ticket.assignee_id == user_id, Ticket.special_assignee_id == user_id,
            Ticket.id.in_(assigned_task_tickets),
        ))
        .order_by(Ticket.id)
        .with_for_update()
    )
    if any(status != TicketStatus.CLOSED.value for status in statuses):
        raise UserDeletionError("У сотрудника есть незакрытая заявка или незавершённая внутренняя задача.")


def _locked_target(db: Session, operation: UserDeletionOperation) -> User:
    actor = db.get(User, operation.requested_by_user_id)
    if actor is None or actor.company_id != operation.company_id or actor.role != "admin" or not actor.is_active:
        raise UserDeletionError("Удалить сотрудника может только активный администратор его компании.")
    target = db.scalar(
        select(User)
        .where(User.id == operation.target_user_id, User.company_id == operation.company_id)
        .with_for_update()
    )
    if target is None:
        raise UserDeletionError("Сотрудник не найден.")
    if target.id == actor.id:
        raise UserDeletionError("Нельзя удалить собственную учётную запись.")
    _check_active_work(db, target.id)
    return target


def _remove_personal_data(db: Session, user_id: str) -> None:
    periods = select(EmployeeNotificationPeriod.id).where(EmployeeNotificationPeriod.user_id == user_id)
    db.execute(delete(EmployeeNotificationBacklog).where(EmployeeNotificationBacklog.period_id.in_(periods)))

    reviews = select(TicketReview.id).where(TicketReview.agent_id == user_id)
    db.execute(update(TicketReviewCycle).where(TicketReviewCycle.review_id.in_(reviews)).values(review_id=None))
    db.execute(update(TicketReview).where(TicketReview.previous_version_id.in_(reviews)).values(previous_version_id=None))
    for field in (
        TicketReview.agent_id, TicketReviewCycle.agent_id,
        StatsCompletion.completed_by_user_id, StatsWorkInterval.user_id, StatsUserStatusInterval.user_id,
        TicketView.user_id, EmployeeNotificationPreference.user_id, EmployeeNotificationPeriod.user_id,
        QuickReplyUsage.user_id, TelegramGroupRegistrationCode.created_by_user_id,
        TicketAttachmentPreload.user_id,
    ):
        # Only the preload rows are removed. Final attachments may share their physical paths.
        db.execute(delete(field.class_).where(field == user_id))

    db.execute(update(TelegramNotificationAccount).where(
        TelegramNotificationAccount.bound_user_id == user_id,
    ).values(bound_user_id=None))
    db.execute(update(BotButtonSetting).where(
        BotButtonSetting.fixed_assignee_user_id == user_id,
    ).values(fixed_assignee_user_id=None, fixed_assignee_enabled=False))
    db.execute(update(BotButtonSetting).where(
        BotButtonSetting.deleted_by_user_id == user_id,
    ).values(deleted_by_user_id=None))


def delete_user_data(db: Session, operation: UserDeletionOperation, set_stage) -> str | None:
    target = _locked_target(db, operation)
    user_id = target.id
    name = format_deleted_employee_name(target.full_name)

    def detach(field, **values):
        db.execute(update(field.class_).where(field == user_id).values({field.key: None, **values}))

    set_stage("ticket_history")
    detach(Ticket.assignee_id, assignee_name_snapshot=name)
    detach(Ticket.special_assignee_id, special_assignee_name_snapshot=name)
    detach(Ticket.closed_by_user_id, closed_by_name_snapshot=name)
    detach(Ticket.completed_by_user_id)
    detach(TicketMessage.author_user_id, author_name_snapshot=name)
    detach(QuickReply.author_user_id, author_name_snapshot=name)

    set_stage("internal_tasks")
    for model in (InternalTaskDefinition, InternalTaskOccurrence):
        detach(model.creator_admin_id)
        detach(model.responsible_admin_id, responsible_admin_name_snapshot=name)
    detach(InternalTaskOccurrence.effective_assignee_user_id, effective_assignee_name_snapshot=name)
    detach(InternalTaskDefinition.assignee_user_id, assignment_mode=InternalTaskAssignmentMode.DISTRIBUTION.value)

    set_stage("personal_data")
    _remove_personal_data(db, user_id)

    set_stage("user_record")
    avatar = target.avatar_path
    db.delete(target)
    db.flush()
    return avatar


def _update_operation(operation_id: str, **values) -> None:
    with SessionLocal.begin() as db:
        db.execute(update(UserDeletionOperation).where(UserDeletionOperation.id == operation_id).values(**values))


def run_user_deletion_operation(operation_id: str) -> None:
    with SessionLocal.begin() as db:
        claimed = db.execute(update(UserDeletionOperation).where(
            UserDeletionOperation.id == operation_id, UserDeletionOperation.status == "queued",
        ).values(status="running", started_at=datetime.now(timezone.utc))).rowcount
    if not claimed:
        return

    def set_stage(stage: str) -> None:
        _update_operation(operation_id, stage=stage)

    try:
        with SessionLocal.begin() as db:
            operation = db.get(UserDeletionOperation, operation_id)
            avatar = delete_user_data(db, operation, set_stage)
    except Exception as exc:
        if isinstance(exc, UserDeletionError):
            message = str(exc)
        else:
            logger.exception("User deletion failed for operation %s", operation_id)
            message = "Не удалось удалить сотрудника. Изменения отменены."
        _update_operation(operation_id, status="failed", error_message=message, finished_at=datetime.now(timezone.utc))
        return

    try:
        _update_operation(operation_id, status="succeeded", finished_at=datetime.now(timezone.utc))
    finally:
        try:
            # Use the same path validation as the profile avatar deletion.
            path = resolve_avatar_path(avatar)
            if path is not None:
                path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Avatar cleanup failed after user deletion %s", operation_id)
