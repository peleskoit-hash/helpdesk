from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Client,
    Company,
    InternalTaskAssignmentMode,
    InternalTaskDefinition,
    InternalTaskDefinitionState,
    InternalTaskOccurrence,
    InternalTaskScheduleKind,
    MessageType,
    Ticket,
    TicketIntakeState,
    TicketMessage,
    TicketStatus,
    TicketTaskKind,
    TicketWorkflowType,
    User,
)
from .assignment import append_system_message, assign_ticket_by_admin, assign_ticket_to_agent, choose_online_agent

INTERNAL_TASK_SYSTEM_CLIENT_NAME = "Система"
INTERNAL_TASK_CLIENT_UUID_NAMESPACE = uuid.UUID("5e88beac-c5c3-4d67-a60f-a96ec6bcf1d0")


@dataclass(frozen=True)
class InternalTaskMaterializationResult:
    ticket: Ticket
    occurrence: InternalTaskOccurrence
    created: bool


def _acquire_transaction_lock(db: Session, lock_key: str) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})


def _technical_internal_client(db: Session, company_id: str) -> Client:
    stable_client_id = str(uuid.uuid5(INTERNAL_TASK_CLIENT_UUID_NAMESPACE, company_id))
    client = db.execute(
        select(Client)
        .where(and_(Client.company_id == company_id, Client.id == stable_client_id))
        .limit(1)
    ).scalar_one_or_none()
    if client is not None:
        if client.display_name != INTERNAL_TASK_SYSTEM_CLIENT_NAME:
            client.display_name = INTERNAL_TASK_SYSTEM_CLIENT_NAME
        return client

    _acquire_transaction_lock(db, f"internal_task_client:{company_id}")
    client = db.execute(
        select(Client)
        .where(and_(Client.company_id == company_id, Client.id == stable_client_id))
        .limit(1)
    ).scalar_one_or_none()
    if client is not None:
        if client.display_name != INTERNAL_TASK_SYSTEM_CLIENT_NAME:
            client.display_name = INTERNAL_TASK_SYSTEM_CLIENT_NAME
        return client

    client = Client(
        id=stable_client_id,
        company_id=company_id,
        display_name=INTERNAL_TASK_SYSTEM_CLIENT_NAME,
    )
    db.add(client)
    db.flush()
    return client


def _deadline_text(deadline_at_utc: datetime | None) -> str | None:
    if deadline_at_utc is None:
        return None
    deadline = deadline_at_utc
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=ZoneInfo("UTC"))
    return deadline.astimezone(ZoneInfo(settings.app_timezone)).strftime("%d.%m.%Y %H:%M")


def internal_task_description_with_deadline(definition: InternalTaskDefinition) -> str:
    description = definition.description or ""
    deadline = _deadline_text(definition.deadline_at_utc)
    if deadline is None:
        return description
    return f"{description}\n\nДедлайн: {deadline}"


def _append_initial_message(db: Session, ticket: Ticket, body: str, *, now: datetime) -> TicketMessage:
    message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.CLIENT.value,
        author_user_id=None,
        body=body,
        agent_visible=True,
        created_at=now,
    )
    db.add(message)
    ticket.updated_at = now
    db.flush()
    return message


def _existing_result(db: Session, definition_id: str, occurrence_at_utc: datetime) -> InternalTaskMaterializationResult | None:
    occurrence = db.execute(
        select(InternalTaskOccurrence)
        .where(
            and_(
                InternalTaskOccurrence.definition_id == definition_id,
                InternalTaskOccurrence.occurrence_at_utc == occurrence_at_utc,
            )
        )
        .limit(1)
    ).scalar_one_or_none()
    if occurrence is None:
        return None
    ticket = db.get(Ticket, occurrence.ticket_id)
    if ticket is None:
        raise RuntimeError("Internal task occurrence points to a missing ticket")
    return InternalTaskMaterializationResult(ticket=ticket, occurrence=occurrence, created=False)


def _resolve_direct_assignee(db: Session, definition: InternalTaskDefinition) -> User | None:
    if definition.assignment_mode != InternalTaskAssignmentMode.DIRECT.value:
        return None
    if not definition.assignee_user_id:
        raise ValueError("direct internal task definition requires assignee_user_id")
    assignee = db.get(User, definition.assignee_user_id)
    if assignee is None or not assignee.is_active or assignee.role not in {"agent", "trainee"}:
        return None
    if assignee.company_id != definition.company_id:
        return None
    return assignee


def _validate_definition(db: Session, definition: InternalTaskDefinition, occurrence_at_utc: datetime | None) -> None:
    if occurrence_at_utc is None:
        raise ValueError("occurrence_at_utc is required")
    if definition.company_id is None or db.get(Company, definition.company_id) is None:
        raise ValueError("internal task definition must belong to an existing company")
    if definition.assignment_mode not in {InternalTaskAssignmentMode.DIRECT.value, InternalTaskAssignmentMode.DISTRIBUTION.value}:
        raise ValueError("unsupported internal task assignment_mode")
    if definition.assignment_mode == InternalTaskAssignmentMode.DIRECT.value and not definition.assignee_user_id:
        raise ValueError("direct internal task definition requires assignee_user_id")
    if definition.schedule_kind not in {
        InternalTaskScheduleKind.IMMEDIATE.value,
        InternalTaskScheduleKind.SCHEDULED.value,
        InternalTaskScheduleKind.RECURRING.value,
    }:
        raise ValueError("unsupported internal task schedule_kind")


def _finish_one_off_definition(definition: InternalTaskDefinition, *, now: datetime) -> None:
    if definition.schedule_kind in {InternalTaskScheduleKind.IMMEDIATE.value, InternalTaskScheduleKind.SCHEDULED.value}:
        definition.state = InternalTaskDefinitionState.FINISHED.value
        definition.updated_at = now


def materialize_internal_task(
    db: Session,
    definition: InternalTaskDefinition,
    occurrence_at_utc: datetime,
) -> InternalTaskMaterializationResult:
    _validate_definition(db, definition, occurrence_at_utc)

    existing = _existing_result(db, definition.id, occurrence_at_utc)
    if existing is not None:
        return existing

    try:
        with db.begin_nested():
            existing = _existing_result(db, definition.id, occurrence_at_utc)
            if existing is not None:
                return existing

            now = datetime.utcnow()
            client = _technical_internal_client(db, definition.company_id)
            assignee = _resolve_direct_assignee(db, definition)
            effective_assignment_mode = (
                InternalTaskAssignmentMode.DIRECT.value
                if assignee is not None
                else InternalTaskAssignmentMode.DISTRIBUTION.value
            )
            effective_assignee_user_id = assignee.id if assignee is not None else None
            ticket = Ticket(
                id=str(uuid.uuid4()),
                company_id=definition.company_id,
                client_id=client.id,
                assignee_id=None,
                subject=definition.title,
                workflow_type=TicketWorkflowType.TASK.value,
                intake_state=TicketIntakeState.READY.value,
                task_kind=TicketTaskKind.NORMAL.value,
                agent_visible=True,
                load_counted=True,
                status=TicketStatus.UNASSIGNED.value,
                is_frozen=False,
                created_at=now,
                updated_at=now,
            )
            description = internal_task_description_with_deadline(definition)
            occurrence = InternalTaskOccurrence(
                id=str(uuid.uuid4()),
                definition_id=definition.id,
                occurrence_at_utc=occurrence_at_utc,
                ticket_id=ticket.id,
                title_snapshot=definition.title,
                description_snapshot=description,
                creator_admin_id=definition.creator_admin_id,
                responsible_admin_id=definition.responsible_admin_id,
                display_author=bool(definition.display_author),
                effective_assignment_mode=effective_assignment_mode,
                effective_assignee_user_id=effective_assignee_user_id,
                notify_on_completion=bool(definition.notify_on_completion),
                deadline_at_utc=definition.deadline_at_utc,
                materialized_at_utc=now,
                created_at=now,
            )
            db.add(occurrence)
            db.flush()

            db.add(ticket)
            db.flush()
            append_system_message(db, ticket, f"Внутренняя задача создана: {definition.title}", agent_visible=True)
            _append_initial_message(db, ticket, description, now=now)

            if assignee is not None:
                assign_ticket_by_admin(
                    db,
                    ticket,
                    assignee,
                    actor_user_id=definition.creator_admin_id,
                    notify_client=False,
                )
            else:
                online_agent = choose_online_agent(db, definition.company_id)
                if online_agent is not None:
                    assign_ticket_to_agent(db, ticket, online_agent, notify_client=False)
                if ticket.assignee_id is None:
                    append_system_message(db, ticket, "На распределении", agent_visible=True)

            _finish_one_off_definition(definition, now=now)
            db.flush()
            return InternalTaskMaterializationResult(ticket=ticket, occurrence=occurrence, created=True)
    except IntegrityError:
        existing = _existing_result(db, definition.id, occurrence_at_utc)
        if existing is not None:
            return existing
        raise


def is_internal_task_ticket(db: Session, ticket: Ticket) -> bool:
    return db.execute(
        select(InternalTaskOccurrence.id)
        .where(InternalTaskOccurrence.ticket_id == ticket.id)
        .limit(1)
    ).scalar_one_or_none() is not None
