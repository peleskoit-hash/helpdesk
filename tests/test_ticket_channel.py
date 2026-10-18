import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.app.db import Base
from api.app.models import Client, Company, Ticket, User
from api.app.services import schema_evolution


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, monkeypatch):
    if request.param == "sqlite":
        engine = create_engine("sqlite://")
        admin = None
    else:
        url = os.environ.get("TICKET_CHANNEL_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TICKET_CHANNEL_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        admin = create_engine(url)
        schema = "ticket_channel_" + uuid.uuid4().hex
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(schema_evolution, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def test_new_schema_model_and_database_defaults(engine):
    Base.metadata.create_all(engine, tables=[Company.__table__, Client.__table__, User.__table__, Ticket.__table__])
    schema_evolution.ensure_ticket_columns()
    schema_evolution.ensure_ticket_columns()
    with Session(engine) as db:
        db.add(Company(id="company", name="Test"))
        db.flush()
        db.add(Client(id="client", company_id="company", display_name="Test"))
        db.flush()
        for workflow in ("ticket", "task", "auto"):
            ticket = Ticket(id=workflow, company_id="company", client_id="client", subject=workflow,
                            workflow_type=workflow)
            db.add(ticket)
            db.flush()
            assert ticket.channel == "telegram"
        web = Ticket(id="web", company_id="company", client_id="client", subject="Web", channel="web")
        db.add(web)
        db.commit()
        db.expire_all()
        assert db.get(Ticket, "web").channel == "web"
    with engine.begin() as conn:
        # Core insert bypasses the ORM's Python default.
        conn.execute(text("INSERT INTO tickets (id, company_id, client_id, subject, workflow_type, intake_state, "
                          "task_kind, suppress_client_answer, agent_visible, load_counted, onsite_triggered, "
                          "is_special, auto_close_disabled, draft_revision, status, is_frozen, created_at, updated_at) "
                          "SELECT 'sql', company_id, client_id, subject, workflow_type, intake_state, task_kind, "
                          "suppress_client_answer, agent_visible, load_counted, onsite_triggered, is_special, "
                          "auto_close_disabled, draft_revision, status, is_frozen, created_at, updated_at "
                          "FROM tickets WHERE id = 'ticket'"))
        assert conn.execute(text("SELECT channel FROM tickets WHERE id = 'sql'")).scalar_one() == "telegram"
    for value in ("email", None):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("UPDATE tickets SET channel = :value WHERE id = 'sql'"), {"value": value})
    column = next(c for c in inspect(engine).get_columns("tickets") if c["name"] == "channel")
    assert not column["nullable"]


@pytest.mark.parametrize("has_channel", [False, True])
def test_legacy_schema_backfill_preserves_web_and_is_repeatable(engine, has_channel):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP" +
                          (", channel VARCHAR(20)" if has_channel else "") + ")"))
        conn.execute(text("INSERT INTO tickets (id) VALUES ('old')"))
        if has_channel:
            conn.execute(text("INSERT INTO tickets (id, channel) VALUES ('web', 'web'), ('tg', 'telegram')"))
    schema_evolution.ensure_ticket_columns()
    with engine.begin() as conn:
        assert conn.execute(text("SELECT channel FROM tickets WHERE id = 'old'")).scalar_one() == "telegram"
        conn.execute(text("UPDATE tickets SET channel = 'web' WHERE id = 'old'"))
        if not has_channel:
            conn.execute(text("INSERT INTO tickets (id) VALUES ('new')"))
            assert conn.execute(text("SELECT channel FROM tickets WHERE id = 'new'")).scalar_one() == "telegram"
    schema_evolution.ensure_ticket_columns()
    schema_evolution.ensure_ticket_columns()
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT id, channel FROM tickets")).all())
    assert rows == ({"old": "web", "web": "web", "tg": "telegram"} if has_channel
                    else {"old": "web", "new": "telegram"})
