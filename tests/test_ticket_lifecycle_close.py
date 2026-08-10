from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import MessageType, Ticket, TicketMessage, TicketStatus, TicketWorkflowType
from api.app.services.ticket_lifecycle import close_ticket_by_actor


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _add_message(db, ticket, message_type, body, author_user_id, **kwargs):
    db.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=message_type.value if hasattr(message_type, "value") else message_type,
            body=body,
            author_user_id=author_user_id,
            agent_visible=kwargs.get("agent_visible", True),
            created_at=datetime.utcnow(),
        )
    )


def _seed_ticket(session, **overrides) -> Ticket:
    now = datetime.utcnow()
    payload = {
        "id": str(uuid.uuid4()),
        "company_id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()),
        "subject": "Need help",
        "status": TicketStatus.PENDING.value,
        "workflow_type": TicketWorkflowType.TICKET.value,
        "auto_close_started_at": now,
        "auto_close_due_at": now,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    ticket = Ticket(**payload)
    session.add(ticket)
    session.commit()
    return ticket


def test_manual_ticket_close_clears_auto_close_timer():
    session = _session()
    ticket = _seed_ticket(session)
    agent_message = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        message_type=MessageType.AGENT.value,
        body="Done",
        author_user_id="agent-1",
        agent_visible=True,
        created_at=datetime.utcnow(),
    )
    session.add(agent_message)
    session.commit()

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="agent-1", role="agent"),
        force_without_agent_reply_for_task=False,
        is_admin_fn=lambda actor: False,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: agent_message,
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_: "Agent",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.flush()
    session.refresh(ticket)

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert ticket.auto_close_started_at is None
    assert ticket.auto_close_due_at is None
    assert ticket.auto_closed_at is None


def test_admin_ticket_force_close_without_agent_reply_clears_auto_close_timer():
    session = _session()
    ticket = _seed_ticket(session)

    result = close_ticket_by_actor(
        session,
        ticket,
        SimpleNamespace(id="admin-1", role="admin"),
        force_without_agent_reply_for_task=True,
        is_admin_fn=lambda actor: True,
        get_or_create_client_profile_fn=lambda *_: None,
        last_agent_message_fn=lambda *_: None,
        first_client_message_fn=lambda *_: None,
        send_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        actor_display_name_fn=lambda *_: "Admin",
        add_message_fn=_add_message,
        message_attachments_fn=lambda *_: [],
        send_attachment_to_telegram_client_fn=lambda *_args, **_kwargs: None,
        onsite_close_reminder_text="onsite",
        ticket_closed_client_text="closed",
    )
    session.flush()
    session.refresh(ticket)

    assert result == "closed"
    assert ticket.status == TicketStatus.CLOSED.value
    assert ticket.auto_close_started_at is None
    assert ticket.auto_close_due_at is None
    assert ticket.auto_closed_at is None
