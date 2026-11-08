from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.app.db import Base
from api.app.models import BotButtonSetting, Client, ClientProfile, Company, MessageType, Ticket, TicketMessage, TicketStatus, TicketView, TicketWorkflowType, User
from api.app.routers.tickets import get_task_panel, get_ticket


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _current_user(user_id: str, role: str = "agent"):
    return SimpleNamespace(id=user_id, role=role, real_user_id=user_id, real_role=role, is_real_admin=False)


def _seed_base(session):
    company = Company(id=str(uuid.uuid4()), name="ACME", is_active=True, created_at=datetime.utcnow())
    agent = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Agent", role="agent", is_active=True, availability_status="online")
    other = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Other", role="agent", is_active=True, availability_status="online")
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    session.add_all([company, agent, other, client])
    session.commit()
    return company, agent, other, client


def _ticket(
    session,
    *,
    company,
    client,
    assignee,
    subject,
    status=TicketStatus.NEW.value,
    is_frozen=False,
    agent_visible=True,
    is_special=False,
    button_config_key=None,
):
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        assignee_id=assignee.id if assignee else None,
        subject=subject,
        status=status,
        is_frozen=is_frozen,
        agent_visible=agent_visible,
        is_special=is_special,
        button_config_key=button_config_key,
        workflow_type=TicketWorkflowType.TICKET.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(ticket)
    return ticket


def _view(session, *, ticket, user, first_opened_at, last_opened_at):
    session.add(
        TicketView(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            user_id=user.id,
            first_opened_at=first_opened_at,
            last_opened_at=last_opened_at,
        )
    )


def _message(session, *, ticket, created_at, message_type=MessageType.CLIENT.value, author_user_id=None):
    session.add(
        TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            message_type=message_type,
            author_user_id=author_user_id,
            body="message",
            created_at=created_at,
        )
    )


def _bot_button(session, *, button_key, button_path):
    session.add(
        BotButtonSetting(
            id=str(uuid.uuid4()),
            button_key=button_key,
            menu_type="main",
            workflow_type=TicketWorkflowType.TICKET.value,
            button_path=button_path,
            button_label="Button",
        )
    )


def test_task_panel_filters_orders_and_marks_unread_from_existing_ticket_views():
    session = _session()
    company, agent, other, client = _seed_base(session)
    base = datetime(2026, 10, 2, 10, 0, 0)

    older = _ticket(session, company=company, client=client, assignee=agent, subject="Older opened")
    newer = _ticket(session, company=company, client=client, assignee=agent, subject="Newer opened")
    pending = _ticket(session, company=company, client=client, assignee=agent, subject="Pending opened", status=TicketStatus.PENDING.value)
    frozen = _ticket(session, company=company, client=client, assignee=agent, subject="Frozen opened", is_frozen=True)
    frozen_pending = _ticket(session, company=company, client=client, assignee=agent, subject="Frozen pending", status=TicketStatus.PENDING.value, is_frozen=True)
    equal_message = _ticket(session, company=company, client=client, assignee=agent, subject="Equal message")
    no_messages = _ticket(session, company=company, client=client, assignee=agent, subject="No messages")
    resolved_subject = _ticket(session, company=company, client=client, assignee=agent, subject="Raw subject", button_config_key="resolver-key")
    own_agent_message = _ticket(session, company=company, client=client, assignee=agent, subject="Own agent message")
    other_agent_message = _ticket(session, company=company, client=client, assignee=agent, subject="Other agent message")
    client_message = _ticket(session, company=company, client=client, assignee=agent, subject="Client message")
    system_message = _ticket(session, company=company, client=client, assignee=agent, subject="System message")
    closed = _ticket(session, company=company, client=client, assignee=agent, subject="Closed", status=TicketStatus.CLOSED.value)
    unassigned_with_agent = _ticket(session, company=company, client=client, assignee=agent, subject="Unassigned with agent", status=TicketStatus.UNASSIGNED.value)
    special = _ticket(session, company=company, client=client, assignee=agent, subject="Special", is_special=True)
    unseen = _ticket(session, company=company, client=client, assignee=agent, subject="No view")
    other_agent = _ticket(session, company=company, client=client, assignee=other, subject="Other agent")
    hidden = _ticket(session, company=company, client=client, assignee=agent, subject="Hidden", agent_visible=False)

    _view(session, ticket=older, user=agent, first_opened_at=base, last_opened_at=base + timedelta(minutes=20))
    _view(session, ticket=newer, user=agent, first_opened_at=base + timedelta(minutes=5), last_opened_at=base + timedelta(hours=5))
    _view(session, ticket=pending, user=agent, first_opened_at=base + timedelta(minutes=4), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=frozen, user=agent, first_opened_at=base + timedelta(minutes=3), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=frozen_pending, user=agent, first_opened_at=base + timedelta(minutes=2, seconds=30), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=equal_message, user=agent, first_opened_at=base + timedelta(minutes=2), last_opened_at=base + timedelta(minutes=30))
    _view(session, ticket=no_messages, user=agent, first_opened_at=base + timedelta(minutes=1), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=resolved_subject, user=agent, first_opened_at=base + timedelta(seconds=30), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=own_agent_message, user=agent, first_opened_at=base - timedelta(minutes=1), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=other_agent_message, user=agent, first_opened_at=base - timedelta(minutes=2), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=client_message, user=agent, first_opened_at=base - timedelta(minutes=3), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=system_message, user=agent, first_opened_at=base - timedelta(minutes=4), last_opened_at=base + timedelta(minutes=10))
    _view(session, ticket=closed, user=agent, first_opened_at=base + timedelta(minutes=10), last_opened_at=base)
    _view(session, ticket=unassigned_with_agent, user=agent, first_opened_at=base + timedelta(minutes=9), last_opened_at=base)
    _view(session, ticket=special, user=agent, first_opened_at=base + timedelta(minutes=8), last_opened_at=base)
    _view(session, ticket=other_agent, user=agent, first_opened_at=base + timedelta(minutes=9), last_opened_at=base)
    _view(session, ticket=hidden, user=agent, first_opened_at=base + timedelta(minutes=8), last_opened_at=base)

    _message(session, ticket=older, created_at=base + timedelta(minutes=30))
    _message(session, ticket=newer, created_at=base + timedelta(hours=1))
    _message(session, ticket=frozen, created_at=base + timedelta(minutes=11))
    _message(session, ticket=equal_message, created_at=base + timedelta(minutes=30))
    _message(session, ticket=closed, created_at=base + timedelta(minutes=11))
    _message(session, ticket=unseen, created_at=base + timedelta(minutes=12))
    _message(
        session,
        ticket=own_agent_message,
        created_at=base + timedelta(minutes=12),
        message_type=MessageType.AGENT.value,
        author_user_id=agent.id,
    )
    _message(
        session,
        ticket=other_agent_message,
        created_at=base + timedelta(minutes=12),
        message_type=MessageType.AGENT.value,
        author_user_id=other.id,
    )
    _message(session, ticket=client_message, created_at=base + timedelta(minutes=12), message_type=MessageType.CLIENT.value)
    _message(session, ticket=system_message, created_at=base + timedelta(minutes=12), message_type=MessageType.SYSTEM.value)
    _bot_button(session, button_key="resolver-key", button_path="Root / Display subject")
    session.commit()

    items = get_task_panel(db=session, user=_current_user(agent.id))

    assert [item.subject for item in items] == [
        "Newer opened",
        "Pending opened",
        "Frozen opened",
        "Frozen pending",
        "Equal message",
        "No messages",
        "Root → Display subject",
        "Older opened",
        "Own agent message",
        "Other agent message",
        "Client message",
        "System message",
    ]
    by_subject = {item.subject: item for item in items}
    assert by_subject["Older opened"].has_unread is True
    assert by_subject["Frozen opened"].has_unread is True
    assert by_subject["Newer opened"].has_unread is False
    assert by_subject["Pending opened"].has_unread is False
    assert by_subject["Frozen pending"].has_unread is False
    assert by_subject["Equal message"].has_unread is False
    assert by_subject["No messages"].has_unread is False
    assert by_subject["Own agent message"].has_unread is False
    assert by_subject["Other agent message"].has_unread is True
    assert by_subject["Client message"].has_unread is True
    assert by_subject["System message"].has_unread is True
    assert "Raw subject" not in by_subject
    assert "Closed" not in by_subject
    assert "Unassigned with agent" not in by_subject
    assert "Special" not in by_subject
    assert "No view" not in by_subject
    assert "Other agent" not in by_subject
    assert "Hidden" not in by_subject


def test_ticket_view_lifecycle_tracks_agent_and_trainee_only_without_affecting_task_panel():
    session = _session()
    company, agent, _other, client = _seed_base(session)
    trainee = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Trainee", role="trainee", is_active=True, availability_status="online")
    admin = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Admin", role="admin", is_active=True, availability_status="online")
    chiki = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Readonly", role="chiki_piki", is_active=True, availability_status="online")
    session.add_all([trainee, admin, chiki])
    session.add(
        ClientProfile(
            id=str(uuid.uuid4()),
            client_id=client.id,
            destination="Колл Центр",
        )
    )
    ticket = _ticket(session, company=company, client=client, assignee=agent, subject="Lifecycle ticket")
    trainee_ticket = _ticket(session, company=company, client=client, assignee=trainee, subject="Trainee lifecycle ticket")
    session.commit()

    get_ticket(ticket.id, db=session, user=_current_user(agent.id, "agent"))
    agent_view = session.execute(
        select(TicketView).where(TicketView.ticket_id == ticket.id, TicketView.user_id == agent.id)
    ).scalar_one()
    assert agent_view.first_opened_at is not None
    assert agent_view.last_opened_at is not None

    original_agent_first = agent_view.first_opened_at
    agent_view.last_opened_at = original_agent_first - timedelta(minutes=30)
    session.commit()

    get_ticket(ticket.id, db=session, user=_current_user(agent.id, "agent"))
    session.refresh(agent_view)
    assert agent_view.first_opened_at == original_agent_first
    assert agent_view.last_opened_at == original_agent_first - timedelta(minutes=30)

    get_ticket(trainee_ticket.id, db=session, user=_current_user(trainee.id, "trainee"))
    trainee_view = session.execute(
        select(TicketView).where(TicketView.ticket_id == trainee_ticket.id, TicketView.user_id == trainee.id)
    ).scalar_one()
    assert trainee_view.user_id == trainee.id
    original_trainee_first = trainee_view.first_opened_at
    trainee_view.last_opened_at = original_trainee_first - timedelta(minutes=20)
    session.commit()

    get_ticket(trainee_ticket.id, db=session, user=_current_user(trainee.id, "trainee"))
    session.refresh(trainee_view)
    assert trainee_view.first_opened_at == original_trainee_first
    assert trainee_view.last_opened_at == original_trainee_first - timedelta(minutes=20)

    agent_first_before_admin = agent_view.first_opened_at
    agent_last_before_admin = agent_view.last_opened_at
    trainee_last_before_admin = trainee_view.last_opened_at

    get_ticket(ticket.id, db=session, user=_current_user(admin.id, "admin"))
    assert session.execute(
        select(TicketView).where(TicketView.ticket_id == ticket.id, TicketView.user_id == admin.id)
    ).scalar_one_or_none() is None
    get_ticket(trainee_ticket.id, db=session, user=_current_user(admin.id, "admin"))
    assert session.execute(
        select(TicketView).where(TicketView.ticket_id == trainee_ticket.id, TicketView.user_id == admin.id)
    ).scalar_one_or_none() is None
    session.refresh(agent_view)
    session.refresh(trainee_view)
    assert agent_view.first_opened_at == agent_first_before_admin
    assert agent_view.last_opened_at == agent_last_before_admin
    assert trainee_view.last_opened_at == trainee_last_before_admin

    get_ticket(ticket.id, db=session, user=_current_user(chiki.id, "chiki_piki"))
    assert session.execute(
        select(TicketView).where(TicketView.ticket_id == ticket.id, TicketView.user_id == chiki.id)
    ).scalar_one_or_none() is None
    session.refresh(agent_view)
    assert agent_view.first_opened_at == agent_first_before_admin
    assert agent_view.last_opened_at == agent_last_before_admin

    panel_items = get_task_panel(db=session, user=_current_user(agent.id, "agent"))
    assert [item.id for item in panel_items] == [ticket.id]
