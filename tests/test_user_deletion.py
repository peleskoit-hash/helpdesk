import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app import models as m
from api.app.services import user_deletion as deletion


@pytest.fixture
def sessions(tmp_path, monkeypatch, request):
    postgres_url = os.environ.get("USER_DELETION_TEST_DATABASE_URL")
    schema = f"user_deletion_test_{uuid.uuid4().hex}"
    if postgres_url:
        admin_engine = create_engine(postgres_url)
        with admin_engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(postgres_url, connect_args={"options": f"-c search_path={schema}"})
        request.addfinalizer(lambda: drop_test_schema(admin_engine, schema))
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'deletion.db'}")

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    request.addfinalizer(engine.dispose)

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(deletion, "SessionLocal", factory)
    # SQLite serializes all writes. PostgreSQL progress updates lock a separate row.
    update_operation = deletion._update_operation
    stages = {}

    def update_status(operation_id, **values):
        if "stage" in values:
            stages[operation_id] = values["stage"]
        else:
            update_operation(operation_id, stage=stages.get(operation_id, "precheck"), **values)

    if not postgres_url:
        monkeypatch.setattr(deletion, "_update_operation", update_status)
    with factory.begin() as db:
        db.add(m.Company(id="company", name="Desk"))
        db.flush()
        db.add_all([
            m.User(id="admin", company_id="company", full_name="Admin", role="admin"),
            m.User(id="staff", company_id="company", full_name="Иванов Иван Иванович", role="agent"),
            m.Client(id="client", company_id="company", display_name="Client"),
        ])
        db.flush()
        db.add(m.UserDeletionOperation(
            id="operation", company_id="company", target_user_id="staff",
            target_full_name="Иванов И. (wasted)", requested_by_user_id="admin",
        ))
    return factory


def drop_test_schema(engine, schema):
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    finally:
        engine.dispose()


def seed_history(sessions, path, *, ticket_status="closed"):
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        db.add(m.Ticket(
            id="ticket", company_id="company", client_id="client", subject="Subject", status=ticket_status,
            assignee_id="staff", special_assignee_id="staff", closed_by_user_id="staff", completed_by_user_id="staff",
        ))
        db.add(m.InternalTaskDefinition(
            id="definition", company_id="company", title="Task", description="Task",
            creator_admin_id="staff", responsible_admin_id="staff", display_author=True,
            assignment_mode="direct", assignee_user_id="staff", notify_on_completion=True,
            schedule_kind="scheduled", timezone_name="UTC", scheduled_for_utc=now,
        ))
        db.flush()
        db.add_all([
            m.TicketMessage(id="message", ticket_id="ticket", body="History", message_type="agent", author_user_id="staff"),
            m.QuickReply(id="reply", short_title="Reply", body="Reply", author_user_id="staff"),
            m.InternalTaskOccurrence(
                id="occurrence", definition_id="definition", ticket_id="ticket", occurrence_at_utc=now,
                title_snapshot="Task", description_snapshot="Task", creator_admin_id="staff",
                responsible_admin_id="staff", display_author=True, effective_assignment_mode="direct",
                effective_assignee_user_id="staff", notify_on_completion=True, materialized_at_utc=now,
            ),
            m.TicketView(id="view", ticket_id="ticket", user_id="staff"),
            m.EmployeeNotificationPreference(id="preference", user_id="staff"),
            m.EmployeeNotificationPeriod(id="period", user_id="staff", work_date=now.date(), chat_id="10"),
        ])
        db.flush()
        db.add_all([
            m.TicketAttachment(
                id="attachment", ticket_id="ticket", message_id="message", source="helpdesk",
                original_filename="attachment.txt", local_path=str(path),
            ),
            m.TicketAttachmentPreload(
                id="preload", ticket_id="ticket", user_id="staff", local_path=str(path),
                final_attachment_id="attachment",
            ),
            m.QuickReplyUsage(user_id="staff", quick_reply_id="reply", last_used_at=now),
            m.EmployeeNotificationBacklog(
                id=1, period_id="period", text="notice", send_silently=False, disable_link_preview=False,
            ),
            m.StatsUserStatusInterval(
                id="interval", company_id="company", user_id="staff", status="offline", started_at=now,
            ),
        ])


def test_delete_preserves_history_and_files(sessions, tmp_path):
    path = tmp_path / "attachment.txt"
    path.write_bytes(b"payload")
    seed_history(sessions, path)
    deletion.run_user_deletion_operation("operation")
    with sessions() as db:
        operation = db.get(m.UserDeletionOperation, "operation")
        assert operation.status == "succeeded", operation.error_message
        assert operation.stage == "user_record"
        assert db.get(m.User, "staff") is None
        snapshot = "Иванов И. (wasted)"
        ticket = db.get(m.Ticket, "ticket")
        assert ticket.status == "closed"
        assert not any((ticket.assignee_id, ticket.special_assignee_id, ticket.closed_by_user_id, ticket.completed_by_user_id))
        assert ticket.assignee_name_snapshot == ticket.special_assignee_name_snapshot == ticket.closed_by_name_snapshot == snapshot
        message = db.get(m.TicketMessage, "message")
        assert (message.body, message.author_user_id, message.author_name_snapshot) == ("History", None, snapshot)
        assert db.get(m.TicketAttachment, "attachment").local_path == str(path)
        assert path.read_bytes() == b"payload"
        reply = db.get(m.QuickReply, "reply")
        assert (reply.author_user_id, reply.author_name_snapshot) == (None, snapshot)
        definition = db.get(m.InternalTaskDefinition, "definition")
        assert (definition.assignment_mode, definition.assignee_user_id) == ("distribution", None)
        assert (definition.responsible_admin_id, definition.creator_admin_id) == (None, None)
        assert definition.responsible_admin_name_snapshot == snapshot
        occurrence = db.get(m.InternalTaskOccurrence, "occurrence")
        assert occurrence.effective_assignment_mode == "direct"
        assert occurrence.responsible_admin_name_snapshot == occurrence.effective_assignee_name_snapshot == snapshot
        for model, pk in (
            (m.TicketAttachmentPreload, "preload"), (m.TicketView, "view"),
            (m.QuickReplyUsage, ("staff", "reply")), (m.EmployeeNotificationPreference, "preference"),
            (m.EmployeeNotificationPeriod, "period"), (m.EmployeeNotificationBacklog, 1),
            (m.StatsUserStatusInterval, "interval"),
        ):
            assert db.get(model, pk) is None


@pytest.mark.parametrize("reason", ["ticket", "task", "transaction"])
def test_failure_keeps_employee_data(sessions, tmp_path, monkeypatch, reason):
    seed_history(
        sessions, tmp_path / "attachment.txt",
        ticket_status="pending" if reason in {"ticket", "task"} else "closed",
    )
    if reason == "task":
        with sessions.begin() as db:
            ticket = db.get(m.Ticket, "ticket")
            ticket.assignee_id = ticket.special_assignee_id = None
    if reason == "ticket":
        with sessions.begin() as db:
            occurrence = db.get(m.InternalTaskOccurrence, "occurrence")
            occurrence.responsible_admin_id = occurrence.effective_assignee_user_id = None
    if reason == "transaction":
        original = deletion._remove_personal_data

        def fail_after_removal(db, user_id):
            original(db, user_id)
            raise RuntimeError("forced rollback")

        monkeypatch.setattr(deletion, "_remove_personal_data", fail_after_removal)
    deletion.run_user_deletion_operation("operation")
    with sessions() as db:
        operation = db.get(m.UserDeletionOperation, "operation")
        assert operation.status == "failed"
        assert {"ticket": "незакрытая заявка", "task": "внутренняя задача",
                "transaction": "Изменения отменены"}[reason] in operation.error_message
        assert db.get(m.User, "staff") is not None
        assert db.get(m.TicketMessage, "message").author_user_id == "staff"
        assert db.get(m.TicketMessage, "message").author_name_snapshot is None
        assert db.get(m.TicketAttachmentPreload, "preload").user_id == "staff"
        assert db.get(m.EmployeeNotificationBacklog, 1) is not None


def test_cannot_delete_self(sessions):
    with sessions.begin() as db:
        operation = db.get(m.UserDeletionOperation, "operation")
        operation.target_user_id = "admin"
    deletion.run_user_deletion_operation("operation")
    with sessions() as db:
        operation = db.get(m.UserDeletionOperation, "operation")
        assert operation.status == "failed"
        assert "Нельзя удалить собственную учётную запись." == operation.error_message
        assert db.get(m.User, "admin") is not None


def test_special_assignee_has_user_fk():
    assert {key.target_fullname for key in m.Ticket.__table__.c.special_assignee_id.foreign_keys} == {"users.id"}


def test_postgres_special_assignee_evolution(sessions, monkeypatch):
    from api.app.services import schema_evolution

    engine = sessions.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL schema evolution")
    monkeypatch.setattr(schema_evolution, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tickets DROP CONSTRAINT tickets_special_assignee_id_fkey"))
    with sessions.begin() as db:
        db.add(m.Ticket(
            id="orphan", company_id="company", client_id="client", subject="Subject",
            special_assignee_id="missing",
        ))
    with pytest.raises(RuntimeError, match="1 orphan references"):
        schema_evolution.ensure_user_deletion_schema()
    with sessions.begin() as db:
        ticket = db.get(m.Ticket, "orphan")
        assert ticket.special_assignee_id == "missing"
        ticket.special_assignee_id = "staff"
    schema_evolution.ensure_user_deletion_schema()
    schema_evolution.ensure_user_deletion_schema()
    constraints = inspect(engine).get_foreign_keys("tickets")
    assert sum(fk["constrained_columns"] == ["special_assignee_id"] for fk in constraints) == 1


def test_admin_api_starts_and_reads_background_operation(sessions):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.app.auth import CurrentUser, get_current_user
    from api.app.db import get_db
    from api.app.routers import settings_users

    app = FastAPI()
    app.include_router(settings_users.router)

    def get_session():
        with sessions() as db:
            yield db

    def admin():
        return CurrentUser(id="admin", role="admin", real_user_id="admin", real_role="admin", is_real_admin=True)

    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_current_user] = admin
    with sessions.begin() as db:
        db.delete(db.get(m.UserDeletionOperation, "operation"))
        db.add(m.Company(id="another-company", name="Other"))
        db.flush()
        db.add(m.User(id="outside", company_id="another-company", full_name="Outside", role="admin"))
    prefix = "/api/v1/settings"
    with TestClient(app) as client:
        assert client.post(f"{prefix}/users/admin/deletion").status_code == 409
        assert client.post(f"{prefix}/users/outside/deletion").status_code == 404
        response = client.post(f"{prefix}/users/staff/deletion")
        assert response.status_code == 202
        operation = response.json()
        assert operation["status"] == "queued"
        with sessions() as db:
            saved_operation = db.get(m.UserDeletionOperation, operation["operation_id"])
            assert saved_operation.target_full_name == "Иванов И. (wasted)"
        url = f"{prefix}/user-deletion-operations/{operation['operation_id']}"
        assert client.get(url).json()["status"] == "succeeded"
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="outside", role="admin", real_user_id="outside", real_role="admin", is_real_admin=True,
        )
        assert client.get(url).status_code == 404
