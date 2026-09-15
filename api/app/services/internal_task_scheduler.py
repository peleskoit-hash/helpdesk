from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import and_, select
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import InternalTaskDefinition, InternalTaskDefinitionState, InternalTaskScheduleKind
from .internal_tasks import materialize_internal_task


logger = logging.getLogger(__name__)

INTERNAL_TASK_SCHEDULER_INTERVAL_SECONDS = 30
INTERNAL_TASK_SCHEDULER_BATCH_LIMIT = 50

SessionFactory = Callable[[], Session]


def _due_filters(now_utc: datetime) -> list:
    return [
        InternalTaskDefinition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value,
        InternalTaskDefinition.state == InternalTaskDefinitionState.ACTIVE.value,
        InternalTaskDefinition.scheduled_for_utc.is_not(None),
        InternalTaskDefinition.scheduled_for_utc <= now_utc,
    ]


def _due_definition_count(db: Session, *, now_utc: datetime) -> int:
    return db.execute(
        select(func.count())
        .select_from(InternalTaskDefinition)
        .where(and_(*_due_filters(now_utc)))
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


def _is_due_scheduled_definition(definition: InternalTaskDefinition | None, *, now_utc: datetime) -> bool:
    return (
        definition is not None
        and definition.schedule_kind == InternalTaskScheduleKind.SCHEDULED.value
        and definition.state == InternalTaskDefinitionState.ACTIVE.value
        and definition.scheduled_for_utc is not None
        and definition.scheduled_for_utc <= now_utc
    )


def run_internal_task_scheduler_once(
    *,
    session_factory: SessionFactory = SessionLocal,
    now_utc: datetime | None = None,
    batch_limit: int = INTERNAL_TASK_SCHEDULER_BATCH_LIMIT,
) -> int:
    now_utc = now_utc or datetime.utcnow()
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
                    materialize_internal_task(db, definition, occurrence_at_utc=definition.scheduled_for_utc)
                    db.commit()
                    processed += 1
                except Exception:
                    db.rollback()
                    logger.exception("Scheduled internal task materialization failed for definition %s", definition_id)
                    continue
    return processed


async def internal_task_scheduler_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_internal_task_scheduler_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Internal task scheduler pass failed")
        await asyncio.sleep(INTERNAL_TASK_SCHEDULER_INTERVAL_SECONDS)
