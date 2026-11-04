from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, select, text
from sqlalchemy.orm import Session

from ..models import Client, Company, IntegrationLog, MessageType, Ticket, TicketIntakeState, TicketMessage, TicketStatus, TicketWorkflowType
from .ticket_numbering import ensure_ticket_number
from .assignment import append_system_message, assign_ticket_to_agent, choose_online_agent

INTEGRATION_SOURCE_EXTERNAL_API = "external_api"
INTEGRATION_EVENT_TASK_CREATE = "task_create"
INTEGRATION_DEDUP_WINDOW = timedelta(days=30)
INTEGRATION_LOG_RETENTION = timedelta(days=183)
INTEGRATION_TASK_SUBJECT = "Не переданная сделка от КЦ"
LEGACY_INTEGRATION_SYSTEM_CLIENT_NAME = "ES DESK Integration API"
INTEGRATION_SYSTEM_CLIENT_NAME = "Аналитическая база Bitrix"
INTEGRATION_CLIENT_UUID_NAMESPACE = uuid.UUID("260a2ebc-b810-4c8d-a8ba-99a812b1f2a1")


@dataclass(frozen=True)
class IntegrationTaskResult:
    result: str
    code: str
    deal_id: str
    task_id: str | None = None


def format_integration_task_description(
    *,
    deal_id: str,
    deal_url: str | None = None,
    client_phone: str | None = None,
) -> str:
    return "\n".join(
        [
            f"Номер сделки: {deal_id}",
            f"Ссылка на сделку: {deal_url or 'не указана'}",
            f"Номер клиента: {client_phone or 'не указан'}",
        ]
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def cleanup_old_integration_logs(db: Session, *, now: datetime | None = None) -> int:
    cutoff = _as_utc(now or datetime.now(timezone.utc)) - INTEGRATION_LOG_RETENTION
    deleted = db.query(IntegrationLog).filter(IntegrationLog.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def cleanup_old_integration_logs_on_startup() -> None:
    from ..db import SessionLocal

    with SessionLocal() as db:
        cleanup_old_integration_logs(db)


def _log(
    db: Session,
    *,
    result: str,
    code: str,
    description: str,
    deal_id: str | None,
    task_id: str | None = None,
) -> IntegrationLog:
    row = IntegrationLog(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        source=INTEGRATION_SOURCE_EXTERNAL_API,
        event_type=INTEGRATION_EVENT_TASK_CREATE,
        result=result,
        code=code,
        description=description,
        deal_id=deal_id,
        task_id=task_id,
    )
    db.add(row)
    db.flush()
    return row


def log_invalid_payload(db: Session, *, deal_id: str | None = None) -> None:
    _log(
        db,
        result="error",
        code="invalid_payload",
        description="Некорректный payload Integration API.",
        deal_id=deal_id or None,
    )
    db.commit()


def _acquire_deal_lock(db: Session, deal_id: str) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": f"integration_task:{deal_id}"})


def _acquire_transaction_lock(db: Session, lock_key: str) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})


def _last_success_for_deal(db: Session, deal_id: str) -> IntegrationLog | None:
    return db.execute(
        select(IntegrationLog)
        .where(
            and_(
                IntegrationLog.source == INTEGRATION_SOURCE_EXTERNAL_API,
                IntegrationLog.event_type == INTEGRATION_EVENT_TASK_CREATE,
                IntegrationLog.result == "success",
                IntegrationLog.deal_id == deal_id,
            )
        )
        .order_by(desc(IntegrationLog.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _first_company(db: Session) -> Company | None:
    return db.execute(
        select(Company)
        .where(Company.is_active.is_(True))
        .order_by(Company.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def _integration_client(db: Session, company_id: str) -> Client:
    stable_client_id = str(uuid.uuid5(INTEGRATION_CLIENT_UUID_NAMESPACE, company_id))
    client = db.execute(
        select(Client)
        .where(and_(Client.company_id == company_id, Client.id == stable_client_id))
        .limit(1)
    ).scalar_one_or_none()
    if client is not None:
        if client.display_name != INTEGRATION_SYSTEM_CLIENT_NAME:
            client.display_name = INTEGRATION_SYSTEM_CLIENT_NAME
        return client

    existing_named_client = db.execute(
        select(Client)
        .where(
            and_(
                Client.company_id == company_id,
                Client.display_name.in_([INTEGRATION_SYSTEM_CLIENT_NAME, LEGACY_INTEGRATION_SYSTEM_CLIENT_NAME]),
            )
        )
        .order_by(Client.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if existing_named_client is not None:
        if existing_named_client.display_name != INTEGRATION_SYSTEM_CLIENT_NAME:
            existing_named_client.display_name = INTEGRATION_SYSTEM_CLIENT_NAME
        return existing_named_client

    lock_key = f"integration_client:{company_id}"
    _acquire_transaction_lock(db, lock_key)
    client = db.execute(
        select(Client)
        .where(
            and_(
                Client.company_id == company_id,
                Client.display_name.in_([INTEGRATION_SYSTEM_CLIENT_NAME, LEGACY_INTEGRATION_SYSTEM_CLIENT_NAME]),
            )
        )
        .order_by(Client.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if client is not None:
        if client.display_name != INTEGRATION_SYSTEM_CLIENT_NAME:
            client.display_name = INTEGRATION_SYSTEM_CLIENT_NAME
        return client

    client = Client(
        id=stable_client_id,
        company_id=company_id,
        display_name=INTEGRATION_SYSTEM_CLIENT_NAME,
    )
    db.add(client)
    db.flush()
    return client


def _append_description_message(db: Session, ticket: Ticket, body: str) -> None:
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=MessageType.CLIENT.value,
            author_user_id=None,
            body=body,
            created_at=datetime.utcnow(),
        )
    )
    ticket.updated_at = datetime.utcnow()
    db.flush()


def _create_system_task(
    db: Session,
    *,
    deal_id: str,
    deal_url: str | None,
    client_phone: str | None,
) -> Ticket:
    company = _first_company(db)
    if company is None:
        raise RuntimeError("No active company found for integration task")

    client = _integration_client(db, company.id)
    assignee = choose_online_agent(db, company.id)
    task = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=None,
        subject=INTEGRATION_TASK_SUBJECT,
        workflow_type=TicketWorkflowType.TASK.value,
        intake_state=TicketIntakeState.READY.value,
        task_kind="normal",
        agent_visible=True,
        load_counted=True,
        status=TicketStatus.UNASSIGNED.value,
        is_frozen=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    ensure_ticket_number(db, task)
    append_system_message(db, task, f"Задача создана: {INTEGRATION_TASK_SUBJECT}")
    if assignee is not None:
        assign_ticket_to_agent(db, task, assignee, notify_client=False)
    if task.assignee_id is None:
        append_system_message(db, task, "На распределении")
    _append_description_message(
        db,
        task,
        format_integration_task_description(deal_id=deal_id, deal_url=deal_url, client_phone=client_phone),
    )
    return task


def create_integration_task(
    db: Session,
    *,
    deal_id: str,
    deal_url: str | None = None,
    client_phone: str | None = None,
    now: datetime | None = None,
) -> IntegrationTaskResult:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    _acquire_deal_lock(db, deal_id)

    previous_success = _last_success_for_deal(db, deal_id)
    if previous_success is not None and _as_utc(previous_success.created_at) > current_time - INTEGRATION_DEDUP_WINDOW:
        _log(
            db,
            result="duplicate",
            code="duplicate_within_window",
            description="Задача по этой сделке уже создавалась в пределах окна дедупликации.",
            deal_id=deal_id,
            task_id=previous_success.task_id,
        )
        db.commit()
        return IntegrationTaskResult(
            result="duplicate",
            code="duplicate_within_window",
            deal_id=deal_id,
            task_id=previous_success.task_id,
        )

    try:
        task = _create_system_task(db, deal_id=deal_id, deal_url=deal_url, client_phone=client_phone)
        _log(
            db,
            result="success",
            code="task_created",
            description="Интеграционная задача создана.",
            deal_id=deal_id,
            task_id=task.id,
        )
        db.commit()
        return IntegrationTaskResult(result="success", code="task_created", deal_id=deal_id, task_id=task.id)
    except Exception as exc:
        db.rollback()
        try:
            _log(
                db,
                result="error",
                code="task_creation_failed",
                description="Не удалось создать интеграционную задачу.",
                deal_id=deal_id,
            )
            db.commit()
        except Exception:
            db.rollback()
        raise exc
