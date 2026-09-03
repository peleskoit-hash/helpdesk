from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.config import settings
from api.app.db import Base, get_db
from api.app.models import Client, Company, IntegrationLog, MessageType, Ticket, TicketIntakeState, TicketMessage, TicketStatus, TicketTaskKind, TicketWorkflowType, User
from api.app.routers.integrations import router as integrations_router
from api.app.services import integration_tasks
from api.app.services.assignment import assign_unassigned_fairly

TEST_KEY = "test-integration-key"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _client(session):
    app = FastAPI()
    app.include_router(integrations_router)
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def _seed_company(session) -> Company:
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    session.add(company)
    session.commit()
    return company


def _seed_agent(session, company_id: str, *, name: str = "Agent", availability_status: str = "online") -> User:
    now = datetime.utcnow()
    agent = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=f"{name.lower()}@local.test",
        login=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
        password_hash="hash",
        full_name=name,
        role="agent",
        is_active=True,
        availability_status=availability_status,
        legacy_ticket_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.commit()
    return agent


def _post_task(client: TestClient, deal_id="123456", *, key=TEST_KEY, deal_url="https://example.local/deal/123456", client_phone="+79990000000"):
    headers = {"Authorization": f"Bearer {key}"} if key is not None else {}
    return client.post(
        "/api/v1/integrations/tasks",
        headers=headers,
        json={"deal_id": deal_id, "deal_url": deal_url, "client_phone": client_phone},
    )


def test_correct_api_key_allows_endpoint_and_creates_task(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    agent = _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session))

    assert response.status_code == 201
    body = response.json()
    assert body["result"] == "success"
    assert body["code"] == "task_created"
    assert body["deal_id"] == "123456"
    assert body["task_id"]

    tickets = session.execute(select(Ticket)).scalars().all()
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.id == body["task_id"]
    assert ticket.workflow_type == TicketWorkflowType.TASK.value
    assert ticket.intake_state == TicketIntakeState.READY.value
    assert ticket.task_kind == TicketTaskKind.NORMAL.value
    assert ticket.agent_visible is True
    assert ticket.load_counted is True
    assert ticket.is_frozen is False
    assert ticket.subject == integration_tasks.INTEGRATION_TASK_SUBJECT
    assert ticket.status == TicketStatus.NEW.value
    assert ticket.assignee_id == agent.id

    messages = session.execute(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)).scalars().all()
    creation_message = next(message for message in messages if "Задача создана" in message.body)
    assert creation_message.message_type == MessageType.SYSTEM.value
    description = next(message for message in messages if "Номер сделки: 123456" in message.body)
    assert description.message_type == MessageType.CLIENT.value
    assert "Ссылка на сделку: https://example.local/deal/123456" in description.body
    assert "Номер клиента: +79990000000" in description.body
    assert description.message_type != MessageType.SYSTEM.value

    logs = session.execute(select(IntegrationLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].result == "success"
    assert logs[0].code == "task_created"
    assert logs[0].deal_id == "123456"
    assert logs[0].task_id == ticket.id


def test_missing_api_key_returns_401(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), key=None)

    assert response.status_code == 401
    assert session.execute(select(IntegrationLog)).first() is None


def test_wrong_api_key_returns_401(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), key="wrong-key")

    assert response.status_code == 401
    assert TEST_KEY not in response.text
    assert session.execute(select(IntegrationLog)).first() is None


def test_task_without_online_agent_stays_in_existing_distribution(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session))

    assert response.status_code == 201
    ticket = session.get(Ticket, response.json()["task_id"])
    assert ticket is not None
    assert ticket.workflow_type == TicketWorkflowType.TASK.value
    assert ticket.intake_state == TicketIntakeState.READY.value
    assert ticket.task_kind == TicketTaskKind.NORMAL.value
    assert ticket.agent_visible is True
    assert ticket.load_counted is True
    assert ticket.is_frozen is False
    assert ticket.status == TicketStatus.UNASSIGNED.value
    assert ticket.assignee_id is None
    messages = session.execute(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)).scalars().all()
    assert any(message.body == "На распределении" for message in messages)


def test_unassigned_integration_task_is_assigned_by_existing_fair_distribution(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="redistribute")
    ticket = session.get(Ticket, response.json()["task_id"])
    assert ticket is not None
    assert ticket.status == TicketStatus.UNASSIGNED.value
    assert ticket.assignee_id is None

    agent = _seed_agent(session, company.id, name="Late Agent", availability_status="online")
    assigned = assign_unassigned_fairly(session, company.id, notify_client=False)
    session.commit()
    session.refresh(ticket)

    assert assigned == 1
    assert ticket.assignee_id == agent.id
    assert ticket.status == TicketStatus.NEW.value
    assert len(session.execute(select(Ticket)).scalars().all()) == 1


def test_duplicate_within_window_does_not_create_second_task_and_logs_duplicate(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)
    client = _client(session)

    first = _post_task(client, deal_id="deal-1", deal_url="url-a", client_phone="phone-a")
    duplicate = _post_task(client, deal_id="deal-1", deal_url="url-b", client_phone="phone-b")

    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "result": "duplicate",
        "code": "duplicate_within_window",
        "deal_id": "deal-1",
        "task_id": first.json()["task_id"],
    }
    assert len(session.execute(select(Ticket)).scalars().all()) == 1
    logs = session.execute(select(IntegrationLog).order_by(IntegrationLog.created_at.asc())).scalars().all()
    assert [log.result for log in logs] == ["success", "duplicate"]
    assert logs[1].task_id == first.json()["task_id"]


def test_same_deal_after_dedup_window_creates_new_task(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)
    client = _client(session)

    first = _post_task(client, deal_id="deal-window")
    old_log = session.execute(
        select(IntegrationLog).where(IntegrationLog.result == "success", IntegrationLog.deal_id == "deal-window")
    ).scalar_one()
    old_log.created_at = datetime.utcnow() - integration_tasks.INTEGRATION_DEDUP_WINDOW - timedelta(seconds=1)
    session.commit()

    second = _post_task(client, deal_id="deal-window")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["task_id"] != second.json()["task_id"]
    assert len(session.execute(select(Ticket)).scalars().all()) == 2


def _seed_previous_success(session, *, deal_id: str, created_at: datetime, task_id: str = "previous-task-id") -> None:
    session.add(
        IntegrationLog(
            id=str(uuid.uuid4()),
            created_at=created_at,
            source=integration_tasks.INTEGRATION_SOURCE_EXTERNAL_API,
            event_type=integration_tasks.INTEGRATION_EVENT_TASK_CREATE,
            result="success",
            code="task_created",
            description="previous success",
            deal_id=deal_id,
            task_id=task_id,
        )
    )
    session.commit()


def test_dedup_boundary_less_than_thirty_days_is_duplicate():
    Session = _session_factory()
    session = Session()
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    _seed_previous_success(
        session,
        deal_id="boundary-less",
        created_at=now - integration_tasks.INTEGRATION_DEDUP_WINDOW + timedelta(seconds=1),
    )

    result = integration_tasks.create_integration_task(session, deal_id="boundary-less", now=now)

    assert result.result == "duplicate"
    assert result.code == "duplicate_within_window"
    assert len(session.execute(select(Ticket)).scalars().all()) == 0


def test_dedup_boundary_exactly_thirty_days_allows_new_task():
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    _seed_previous_success(
        session,
        deal_id="boundary-exact",
        created_at=now - integration_tasks.INTEGRATION_DEDUP_WINDOW,
    )

    result = integration_tasks.create_integration_task(session, deal_id="boundary-exact", now=now)

    assert result.result == "success"
    assert result.code == "task_created"
    assert len(session.execute(select(Ticket)).scalars().all()) == 1


def test_dedup_boundary_more_than_thirty_days_allows_new_task():
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    _seed_previous_success(
        session,
        deal_id="boundary-more",
        created_at=now - integration_tasks.INTEGRATION_DEDUP_WINDOW - timedelta(seconds=1),
    )

    result = integration_tasks.create_integration_task(session, deal_id="boundary-more", now=now)

    assert result.result == "success"
    assert result.code == "task_created"
    assert len(session.execute(select(Ticket)).scalars().all()) == 1


def test_dedup_handles_postgresql_aware_created_at_with_default_now():
    Session = _session_factory()
    session = Session()
    _seed_previous_success(
        session,
        deal_id="aware-postgres",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    result = integration_tasks.create_integration_task(session, deal_id="aware-postgres")

    assert result.result == "duplicate"
    assert result.code == "duplicate_within_window"
    assert len(session.execute(select(Ticket)).scalars().all()) == 0


def test_previous_error_does_not_block_next_attempt(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)
    session.add(
        IntegrationLog(
            id=str(uuid.uuid4()),
            created_at=datetime.utcnow(),
            source=integration_tasks.INTEGRATION_SOURCE_EXTERNAL_API,
            event_type=integration_tasks.INTEGRATION_EVENT_TASK_CREATE,
            result="error",
            code="task_creation_failed",
            description="old error",
            deal_id="retry-deal",
        )
    )
    session.commit()

    response = _post_task(_client(session), deal_id="retry-deal")

    assert response.status_code == 201
    assert len(session.execute(select(Ticket)).scalars().all()) == 1


def test_empty_deal_id_is_rejected_and_logged_without_api_key(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="   ")

    assert response.status_code == 422
    assert TEST_KEY not in response.text
    logs = session.execute(select(IntegrationLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].result == "error"
    assert logs[0].code == "invalid_payload"
    assert TEST_KEY not in logs[0].description


def test_malformed_json_is_rejected_and_logged_without_api_key(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _client(session).post(
        "/api/v1/integrations/tasks",
        headers={"Authorization": f"Bearer {TEST_KEY}", "Content-Type": "application/json"},
        content=b'{"deal_id": ',
    )

    assert response.status_code == 422
    assert TEST_KEY not in response.text
    logs = session.execute(select(IntegrationLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].code == "invalid_payload"
    assert TEST_KEY not in logs[0].description


def test_json_array_is_rejected_and_logged_without_api_key(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _client(session).post(
        "/api/v1/integrations/tasks",
        headers={"Authorization": f"Bearer {TEST_KEY}", "Content-Type": "application/json"},
        json=[{"deal_id": "123"}],
    )

    assert response.status_code == 422
    assert TEST_KEY not in response.text
    logs = session.execute(select(IntegrationLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].code == "invalid_payload"


def test_old_helpdeskeddy_fields_are_rejected(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _client(session).post(
        "/api/v1/integrations/tasks",
        headers={"Authorization": f"Bearer {TEST_KEY}"},
        json={"deal_id": "123", "type_id": 1, "department_id": 2, "priority_id": 3, "status_id": 4, "user_id": 5, "custom_fields": {}},
    )

    assert response.status_code == 422
    assert session.execute(select(IntegrationLog).where(IntegrationLog.code == "invalid_payload")).scalar_one()


def test_different_deals_reuse_single_technical_integration_client(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)
    client = _client(session)

    first = _post_task(client, deal_id="client-a")
    second = _post_task(client, deal_id="client-b")

    assert first.status_code == 201
    assert second.status_code == 201
    tasks = session.execute(select(Ticket).order_by(Ticket.created_at.asc())).scalars().all()
    assert len(tasks) == 2
    assert tasks[0].client_id == tasks[1].client_id
    integration_clients = session.execute(
        select(Client).where(
            Client.company_id == company.id,
            Client.display_name == integration_tasks.INTEGRATION_SYSTEM_CLIENT_NAME,
        )
    ).scalars().all()
    assert len(integration_clients) == 1
    assert integration_clients[0].display_name == integration_tasks.INTEGRATION_SYSTEM_CLIENT_NAME


def test_new_technical_client_uses_current_display_name(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="new-client-name")

    assert response.status_code == 201
    ticket = session.get(Ticket, response.json()["task_id"])
    technical_client = session.get(Client, ticket.client_id)
    assert technical_client.display_name == "Аналитическая база Bitrix"


def test_existing_stable_technical_client_with_legacy_name_is_renamed_and_reused(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    stable_client_id = str(uuid.uuid5(integration_tasks.INTEGRATION_CLIENT_UUID_NAMESPACE, company.id))
    legacy_client = Client(
        id=stable_client_id,
        company_id=company.id,
        display_name=integration_tasks.LEGACY_INTEGRATION_SYSTEM_CLIENT_NAME,
    )
    session.add(legacy_client)
    session.commit()
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="stable-legacy-name")

    assert response.status_code == 201
    session.refresh(legacy_client)
    assert legacy_client.id == stable_client_id
    assert legacy_client.display_name == integration_tasks.INTEGRATION_SYSTEM_CLIENT_NAME
    assert session.get(Ticket, response.json()["task_id"]).client_id == stable_client_id
    assert len(session.execute(select(Client).where(Client.company_id == company.id)).scalars().all()) == 1


def test_existing_random_uuid_legacy_technical_client_is_renamed_without_duplicate(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    legacy_client_id = str(uuid.uuid4())
    legacy_client = Client(
        id=legacy_client_id,
        company_id=company.id,
        display_name=integration_tasks.LEGACY_INTEGRATION_SYSTEM_CLIENT_NAME,
    )
    session.add(legacy_client)
    session.commit()
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="random-legacy-client")

    assert response.status_code == 201
    session.refresh(legacy_client)
    assert legacy_client.id == legacy_client_id
    assert legacy_client.display_name == integration_tasks.INTEGRATION_SYSTEM_CLIENT_NAME
    assert session.get(Ticket, response.json()["task_id"]).client_id == legacy_client_id
    assert len(session.execute(select(Client).where(Client.company_id == company.id)).scalars().all()) == 1


def test_existing_current_named_technical_client_is_reused(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    current_client_id = str(uuid.uuid4())
    current_client = Client(
        id=current_client_id,
        company_id=company.id,
        display_name=integration_tasks.INTEGRATION_SYSTEM_CLIENT_NAME,
    )
    session.add(current_client)
    session.commit()
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    response = _post_task(_client(session), deal_id="current-client")

    assert response.status_code == 201
    assert session.get(Ticket, response.json()["task_id"]).client_id == current_client_id
    assert len(session.execute(select(Client).where(Client.company_id == company.id)).scalars().all()) == 1


def test_integration_task_description_is_client_message_without_telegram_delivery(monkeypatch):
    Session = _session_factory()
    session = Session()
    company = _seed_company(session)
    _seed_agent(session, company.id)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    def fail_send_message(*args, **kwargs):
        raise AssertionError("Telegram delivery should not be called for integration task description")

    monkeypatch.setattr("api.app.services.telegram_bot.send_message", fail_send_message)
    monkeypatch.setattr("api.app.services.assignment.send_message", fail_send_message)

    response = _post_task(
        _client(session),
        deal_id="message-type",
        deal_url="https://example.local/deal/message-type",
        client_phone="+70000000001",
    )

    assert response.status_code == 201
    ticket = session.get(Ticket, response.json()["task_id"])
    messages = session.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket.id).order_by(TicketMessage.created_at.asc())
    ).scalars().all()
    creation_message = next(message for message in messages if "Задача создана" in message.body)
    description = next(message for message in messages if "Номер сделки: message-type" in message.body)
    assert creation_message.message_type == MessageType.SYSTEM.value
    assert description.message_type == MessageType.CLIENT.value
    assert description.message_type != MessageType.SYSTEM.value
    assert "https://example.local/deal/message-type" in description.body
    assert "+70000000001" in description.body


def test_integration_service_uses_only_transaction_level_advisory_locks():
    source = (REPO_ROOT / "api" / "app" / "services" / "integration_tasks.py").read_text(encoding="utf-8")

    assert "pg_advisory_xact_lock" in source
    assert "pg_advisory_lock" not in source.replace("pg_advisory_xact_lock", "")
    assert "pg_advisory_unlock" not in source


def test_task_creation_error_is_logged_and_does_not_expose_stack(monkeypatch):
    Session = _session_factory()
    session = Session()
    _seed_company(session)
    monkeypatch.setattr(settings, "integration_api_key", TEST_KEY)

    def fail_create(*args, **kwargs):
        raise RuntimeError("secret internal stack details")

    monkeypatch.setattr(integration_tasks, "_create_system_task", fail_create)

    response = _post_task(_client(session), deal_id="boom")

    assert response.status_code == 500
    assert "secret internal stack details" not in response.text
    log = session.execute(select(IntegrationLog)).scalar_one()
    assert log.result == "error"
    assert log.code == "task_creation_failed"
    assert log.deal_id == "boom"


def test_cleanup_removes_logs_older_than_six_months_and_keeps_fresh():
    Session = _session_factory()
    session = Session()
    now = datetime.utcnow()
    old_log = IntegrationLog(
        id=str(uuid.uuid4()),
        created_at=now - integration_tasks.INTEGRATION_LOG_RETENTION - timedelta(days=1),
        source="external_api",
        event_type="task_create",
        result="success",
        code="task_created",
        description="old",
        deal_id="old",
    )
    fresh_log = IntegrationLog(
        id=str(uuid.uuid4()),
        created_at=now - timedelta(days=5),
        source="external_api",
        event_type="task_create",
        result="success",
        code="task_created",
        description="fresh",
        deal_id="fresh",
    )
    session.add_all([old_log, fresh_log])
    session.commit()

    deleted = integration_tasks.cleanup_old_integration_logs(session, now=now)

    assert deleted == 1
    remaining = session.execute(select(IntegrationLog)).scalar_one()
    assert remaining.id == fresh_log.id
