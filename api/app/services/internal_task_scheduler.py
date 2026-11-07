from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import InternalTaskDefinition, InternalTaskDefinitionState, InternalTaskOccurrence, InternalTaskScheduleKind
from .internal_task_recurrence import (
    CYCLE_END_AT,
    CYCLE_END_COUNT,
    calculate_recurrence_occurrences,
    has_recurrence_capacity,
    validate_recurrence_end_rule,
)
from .internal_tasks import materialize_internal_task
from .telegram_client_avatar_nightly import run_avatar_cleanup_once
from .telegram_after_hours_cleanup import run_telegram_after_hours_cleanup_once
from .employee_auto_offline import run_employee_auto_offline_once


logger = logging.getLogger(__name__)

INTERNAL_TASK_SCHEDULER_INTERVAL_SECONDS = 30
INTERNAL_TASK_SCHEDULER_BATCH_LIMIT = 50

SessionFactory = Callable[[], Session]


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _due_filters(now_utc: datetime) -> list:
    now_utc = _utc_naive(now_utc) or now_utc
    return [
        InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value,
        InternalTaskDefinition.state == InternalTaskDefinitionState.ACTIVE.value,
        InternalTaskDefinition.scheduled_for_utc.is_not(None),
        InternalTaskDefinition.scheduled_for_utc <= now_utc,
    ]


def _recurring_filters() -> list:
    return [
        InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.RECURRING.value,
        InternalTaskDefinition.state == InternalTaskDefinitionState.ACTIVE.value,
    ]


def _due_definition_count(db: Session, *, now_utc: datetime) -> int:
    return db.execute(
        select(func.count())
        .select_from(InternalTaskDefinition)
        .where(and_(*_due_filters(now_utc)))
    ).scalar_one()


def _recurring_definition_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(InternalTaskDefinition)
        .where(and_(*_recurring_filters()))
    ).scalar_one()


def _due_definition_ids(
    db: Session,
    *,
    now_utc: datetime,
    batch_limit: int,
    attempted_definition_ids: set[str] | None = None,
) -> list[str]:
    filters = _due_filters(now_utc)
    if attempted_definition_ids:
        filters.append(InternalTaskDefinition.id.not_in(attempted_definition_ids))
    return list(
        db.execute(
            select(InternalTaskDefinition.id)
            .where(and_(*filters))
            .order_by(InternalTaskDefinition.scheduled_for_utc.asc(), InternalTaskDefinition.id.asc())
            .limit(batch_limit)
        ).scalars()
    )


def _recurring_definition_ids(
    db: Session,
    *,
    batch_limit: int,
    attempted_definition_ids: set[str] | None = None,
) -> list[str]:
    filters = _recurring_filters()
    if attempted_definition_ids:
        filters.append(InternalTaskDefinition.id.not_in(attempted_definition_ids))
    return list(
        db.execute(
            select(InternalTaskDefinition.id)
            .where(and_(*filters))
            .order_by(InternalTaskDefinition.generation_not_before_utc.asc().nullsfirst(), InternalTaskDefinition.id.asc())
            .limit(batch_limit)
        ).scalars()
    )


def _is_due_scheduled_definition(definition: InternalTaskDefinition | None, *, now_utc: datetime) -> bool:
    scheduled_for_utc = _utc_naive(definition.scheduled_for_utc) if definition is not None else None
    now_utc = _utc_naive(now_utc) or now_utc
    return (
        definition is not None
        and definition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value
        and definition.state == InternalTaskDefinitionState.ACTIVE.value
        and scheduled_for_utc is not None
        and scheduled_for_utc <= now_utc
    )


def _is_active_recurring_definition(definition: InternalTaskDefinition | None) -> bool:
    return (
        definition is not None
        and definition.schedule_kind == InternalTaskScheduleKind.RECURRING.value
        and definition.state == InternalTaskDefinitionState.ACTIVE.value
    )


def _effective_recurring_window_start(definition: InternalTaskDefinition) -> datetime:
    cycle_start_at_utc = _utc_naive(definition.cycle_start_at_utc)
    generation_not_before_utc = _utc_naive(definition.generation_not_before_utc)
    if cycle_start_at_utc is None:
        raise ValueError("recurring internal task definition requires cycle_start_at_utc")
    if generation_not_before_utc is None:
        return cycle_start_at_utc
    return max(cycle_start_at_utc, generation_not_before_utc)


def _existing_occurrence_times(db: Session, definition_id: str, *, window_start_utc: datetime, window_end_utc: datetime) -> set[datetime]:
    return {
        _utc_naive(value) or value
        for value in
        db.execute(
            select(InternalTaskOccurrence.occurrence_at_utc).where(
                and_(
                    InternalTaskOccurrence.definition_id == definition_id,
                    InternalTaskOccurrence.occurrence_at_utc >= window_start_utc,
                    InternalTaskOccurrence.occurrence_at_utc <= window_end_utc,
                )
            )
        ).scalars()
    }


def _successful_occurrence_count(db: Session, definition_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(InternalTaskOccurrence)
        .where(InternalTaskOccurrence.definition_id == definition_id)
    ).scalar_one()


def _finish_definition(db: Session, definition: InternalTaskDefinition) -> None:
    definition.state = InternalTaskDefinitionState.FINISHED.value
    definition.updated_at = datetime.utcnow()
    db.commit()


def _materialize_recurring_candidate(
    *,
    session_factory: SessionFactory,
    definition_id: str,
    candidate_at_utc: datetime,
    now_utc: datetime,
) -> bool:
    candidate_at_utc = _utc_naive(candidate_at_utc) or candidate_at_utc
    now_utc = _utc_naive(now_utc) or now_utc
    with session_factory() as db:
        try:
            definition = db.get(InternalTaskDefinition, definition_id)
            if not _is_active_recurring_definition(definition):
                db.rollback()
                return False
            window_start = _effective_recurring_window_start(definition)
            if candidate_at_utc < window_start or candidate_at_utc > now_utc:
                db.rollback()
                return False
            exists = db.execute(
                select(InternalTaskOccurrence.id)
                .where(
                    and_(
                        InternalTaskOccurrence.definition_id == definition_id,
                        InternalTaskOccurrence.occurrence_at_utc == candidate_at_utc,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()
            if exists is not None:
                db.rollback()
                return True

            successful_count = _successful_occurrence_count(db, definition_id)
            if not has_recurrence_capacity(
                cycle_end_mode=definition.cycle_end_mode,
                cycle_max_occurrences=definition.cycle_max_occurrences,
                successful_occurrence_count=successful_count,
            ):
                if definition.cycle_end_mode == CYCLE_END_COUNT:
                    _finish_definition(db, definition)
                else:
                    db.rollback()
                return False

            materialize_internal_task(db, definition, occurrence_at_utc=candidate_at_utc)
            successful_count += 1
            if (
                definition.cycle_end_mode == CYCLE_END_COUNT
                and not has_recurrence_capacity(
                    cycle_end_mode=definition.cycle_end_mode,
                    cycle_max_occurrences=definition.cycle_max_occurrences,
                    successful_occurrence_count=successful_count,
                )
            ):
                definition.state = InternalTaskDefinitionState.FINISHED.value
                definition.updated_at = datetime.utcnow()
            db.commit()
            return True
        except Exception:
            db.rollback()
            logger.exception(
                "Recurring internal task materialization failed for definition %s candidate %s",
                definition_id,
                candidate_at_utc,
            )
            return False


def _process_recurring_definition(*, session_factory: SessionFactory, definition_id: str, now_utc: datetime) -> int:
    now_utc = _utc_naive(now_utc) or now_utc
    with session_factory() as db:
        try:
            definition = db.get(InternalTaskDefinition, definition_id)
            if not _is_active_recurring_definition(definition):
                db.rollback()
                return 0
            validate_recurrence_end_rule(
                cycle_end_mode=definition.cycle_end_mode,
                cycle_end_at_utc=definition.cycle_end_at_utc,
                cycle_max_occurrences=definition.cycle_max_occurrences,
            )
            window_start = _effective_recurring_window_start(definition)
            cycle_end_at_utc = _utc_naive(definition.cycle_end_at_utc)
            if not has_recurrence_capacity(
                cycle_end_mode=definition.cycle_end_mode,
                cycle_max_occurrences=definition.cycle_max_occurrences,
                successful_occurrence_count=_successful_occurrence_count(db, definition_id),
            ):
                if definition.cycle_end_mode == CYCLE_END_COUNT:
                    _finish_definition(db, definition)
                else:
                    db.rollback()
                return 0
            if window_start > now_utc:
                if (
                    definition.cycle_end_mode == CYCLE_END_AT
                    and cycle_end_at_utc is not None
                    and now_utc >= cycle_end_at_utc
                    and window_start > cycle_end_at_utc
                ):
                    _finish_definition(db, definition)
                else:
                    db.rollback()
                return 0
            candidates = calculate_recurrence_occurrences(
                recurrence_mode=definition.recurrence_mode,
                recurrence_config=definition.recurrence_config,
                timezone_name=definition.timezone_name,
                cycle_start_at_utc=definition.cycle_start_at_utc,
                window_start_utc=window_start,
                window_end_utc=now_utc,
                cycle_end_mode=definition.cycle_end_mode,
                cycle_end_at_utc=cycle_end_at_utc,
            )
            candidates = [_utc_naive(candidate) or candidate for candidate in candidates]
            existing = _existing_occurrence_times(
                db,
                definition_id,
                window_start_utc=window_start,
                window_end_utc=now_utc,
            )
            pending = [candidate for candidate in candidates if candidate not in existing]
            should_finish_at = definition.cycle_end_mode == CYCLE_END_AT and cycle_end_at_utc is not None and now_utc >= cycle_end_at_utc
            if not pending and should_finish_at:
                _finish_definition(db, definition)
            else:
                db.rollback()
        except Exception:
            db.rollback()
            logger.exception("Recurring internal task scan failed for definition %s", definition_id)
            return 0

    processed = 0
    for candidate in pending:
        if not _materialize_recurring_candidate(
            session_factory=session_factory,
            definition_id=definition_id,
            candidate_at_utc=candidate,
            now_utc=now_utc,
        ):
            break
        processed += 1
        with session_factory() as db:
            definition = db.get(InternalTaskDefinition, definition_id)
            if not _is_active_recurring_definition(definition):
                break
            cycle_end_at_utc = _utc_naive(definition.cycle_end_at_utc)
            if definition.cycle_end_mode == CYCLE_END_AT and cycle_end_at_utc is not None and now_utc >= cycle_end_at_utc:
                remaining = calculate_recurrence_occurrences(
                    recurrence_mode=definition.recurrence_mode,
                    recurrence_config=definition.recurrence_config,
                    timezone_name=definition.timezone_name,
                    cycle_start_at_utc=definition.cycle_start_at_utc,
                    window_start_utc=_effective_recurring_window_start(definition),
                    window_end_utc=now_utc,
                    cycle_end_mode=definition.cycle_end_mode,
                    cycle_end_at_utc=cycle_end_at_utc,
                )
                remaining = [_utc_naive(candidate) or candidate for candidate in remaining]
                existing = _existing_occurrence_times(
                    db,
                    definition_id,
                    window_start_utc=_effective_recurring_window_start(definition),
                    window_end_utc=now_utc,
                )
                if all(candidate_at in existing for candidate_at in remaining):
                    _finish_definition(db, definition)
                    break
            db.rollback()
    return processed


def run_internal_task_scheduler_once(
    *,
    session_factory: SessionFactory = SessionLocal,
    now_utc: datetime | None = None,
    batch_limit: int = INTERNAL_TASK_SCHEDULER_BATCH_LIMIT,
) -> int:
    now_utc = _utc_naive(now_utc) or datetime.utcnow()
    processed = 0
    attempted_definition_ids: set[str] = set()
    with session_factory() as scan_db:
        initial_due_count = _due_definition_count(scan_db, now_utc=now_utc)

    while len(attempted_definition_ids) < initial_due_count:
        with session_factory() as scan_db:
            due_ids = _due_definition_ids(
                scan_db,
                now_utc=now_utc,
                batch_limit=batch_limit,
                attempted_definition_ids=attempted_definition_ids,
            )
        if not due_ids:
            break

        for definition_id in due_ids:
            attempted_definition_ids.add(definition_id)
            with session_factory() as db:
                try:
                    definition = db.get(InternalTaskDefinition, definition_id)
                    if not _is_due_scheduled_definition(definition, now_utc=now_utc):
                        db.rollback()
                        continue
                    materialize_internal_task(db, definition, occurrence_at_utc=_utc_naive(definition.scheduled_for_utc))
                    db.commit()
                    processed += 1
                except Exception:
                    db.rollback()
                    logger.exception("Scheduled internal task materialization failed for definition %s", definition_id)
                    continue
    return processed


def run_internal_task_recurring_once(
    *,
    session_factory: SessionFactory = SessionLocal,
    now_utc: datetime | None = None,
    batch_limit: int = INTERNAL_TASK_SCHEDULER_BATCH_LIMIT,
) -> int:
    now_utc = _utc_naive(now_utc) or datetime.utcnow()
    processed = 0
    attempted_definition_ids: set[str] = set()
    with session_factory() as scan_db:
        initial_recurring_count = _recurring_definition_count(scan_db)

    while len(attempted_definition_ids) < initial_recurring_count:
        with session_factory() as scan_db:
            definition_ids = _recurring_definition_ids(
                scan_db,
                batch_limit=batch_limit,
                attempted_definition_ids=attempted_definition_ids,
            )
        if not definition_ids:
            break

        for definition_id in definition_ids:
            attempted_definition_ids.add(definition_id)
            processed += _process_recurring_definition(
                session_factory=session_factory,
                definition_id=definition_id,
                now_utc=now_utc,
            )
    return processed


async def internal_task_scheduler_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_internal_task_scheduler_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Internal task scheduler pass failed")
        try:
            await asyncio.to_thread(run_internal_task_recurring_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Internal task recurring scheduler pass failed")
        try:
            await asyncio.to_thread(run_avatar_cleanup_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Telegram avatar nightly scheduler pass failed")
        try:
            await asyncio.to_thread(run_telegram_after_hours_cleanup_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram after-hours cleanup pass failed")
        try:
            await asyncio.to_thread(run_employee_auto_offline_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Employee auto-offline pass failed")
        await asyncio.sleep(INTERNAL_TASK_SCHEDULER_INTERVAL_SECONDS)
