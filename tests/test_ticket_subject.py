from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.app.db import Base
from api.app.models import BotButtonSetting, Client, Company, Ticket, TicketStatus, TicketTaskKind, TicketWorkflowType
from api.app.services.ticket_subject import build_ticket_subject_map, resolve_ticket_subject


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


def _base_entities(session) -> tuple[Company, Client]:
    company = Company(id=str(uuid.uuid4()), name="ACME", created_at=datetime.now(UTC))
    client = Client(id=str(uuid.uuid4()), company_id=company.id, display_name="Client")
    session.add_all([company, client])
    session.flush()
    return company, client


def _seed_setting(session, *, button_key: str, button_path: str, button_label: str = "Leaf A") -> BotButtonSetting:
    now = datetime.now(UTC)
    setting = BotButtonSetting(
        id=str(uuid.uuid4()),
        button_key=button_key,
        menu_type=button_key.split("|", 1)[0],
        workflow_type=button_key.split("|", 2)[1] if "|" in button_key else "ticket",
        button_path=button_path,
        button_label=button_label,
        currently_present=True,
        button_kind="terminal",
        parent_path=button_path.rsplit("/", 1)[0] if "/" in button_path else "",
        created_at=now,
        updated_at=now,
    )
    session.add(setting)
    session.flush()
    return setting


def _seed_ticket(
    session,
    *,
    subject: str,
    button_key: str | None = None,
    workflow_type: str = TicketWorkflowType.TICKET.value,
    task_kind: str = TicketTaskKind.NORMAL.value,
) -> Ticket:
    company, client = _base_entities(session)
    now = datetime.now(UTC)
    ticket = Ticket(
        id=str(uuid.uuid4()),
        company_id=company.id,
        client_id=client.id,
        subject=subject,
        workflow_type=workflow_type,
        task_kind=task_kind,
        status=TicketStatus.NEW.value,
        button_config_key=button_key,
        instruction_button_key=button_key,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.commit()
    return ticket


def _assert_map_matches_resolver(session, *tickets: Ticket):
    subject_map = build_ticket_subject_map(session, list(tickets))
    for ticket in tickets:
        assert subject_map[ticket.id] == resolve_ticket_subject(session, ticket)


def test_button_path_slash_renders_full_menu_path():
    session = _session()
    key = "menu-a|ticket|Submenu/Leaf A"
    _seed_setting(session, button_key=key, button_path="Submenu/Leaf A", button_label="Leaf A")
    ticket = _seed_ticket(session, subject="Leaf A", button_key=key)

    assert resolve_ticket_subject(session, ticket) == "Submenu → Leaf A"
    _assert_map_matches_resolver(session, ticket)


def test_button_path_backslash_is_preserved_as_user_text():
    session = _session()
    key = "menu-a|ticket|Замена\\Выдача техники"
    _seed_setting(session, button_key=key, button_path="Замена\\Выдача техники", button_label="Замена\\Выдача техники")
    ticket = _seed_ticket(session, subject="Замена\\Выдача техники", button_key=key)

    assert resolve_ticket_subject(session, ticket) == "Замена\\Выдача техники"
    assert "→" not in resolve_ticket_subject(session, ticket)
    _assert_map_matches_resolver(session, ticket)


def test_button_path_combines_slash_separator_and_backslash_text():
    session = _session()
    key = "menu-a|ticket|Submenu/Замена\\Выдача техники"
    _seed_setting(
        session,
        button_key=key,
        button_path="Submenu/Замена\\Выдача техники",
        button_label="Замена\\Выдача техники",
    )
    ticket = _seed_ticket(session, subject="Замена\\Выдача техники", button_key=key)

    assert resolve_ticket_subject(session, ticket) == "Submenu → Замена\\Выдача техники"
    _assert_map_matches_resolver(session, ticket)


def test_technical_key_fallback_still_formats_slash_as_menu_separator():
    session = _session()
    key = "menu-a|ticket|Submenu/Leaf A"
    ticket = _seed_ticket(session, subject="Leaf A", button_key=key)

    assert resolve_ticket_subject(session, ticket) == "Submenu → Leaf A"
    _assert_map_matches_resolver(session, ticket)


def test_ticket_subject_fallback_preserves_backslash():
    session = _session()
    ticket = _seed_ticket(session, subject="Замена\\Выдача техники")

    assert resolve_ticket_subject(session, ticket) == "Замена\\Выдача техники"
    assert build_ticket_subject_map(session, [ticket])[ticket.id] == "Замена\\Выдача техники"


def test_group_parent_and_child_keep_stored_subject_without_path_resolution():
    session = _session()
    parent_key = "menu-a|group_task|Submenu/Parent"
    child_key = "menu-a|group_task_child|Submenu/Child"
    _seed_setting(session, button_key=parent_key, button_path="Submenu/Parent", button_label="Parent")
    _seed_setting(session, button_key=child_key, button_path="Submenu/Child", button_label="Child")
    parent = _seed_ticket(
        session,
        subject="Parent",
        button_key=parent_key,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_PARENT.value,
    )
    child = _seed_ticket(
        session,
        subject="Child",
        button_key=child_key,
        workflow_type=TicketWorkflowType.TASK.value,
        task_kind=TicketTaskKind.GROUP_CHILD.value,
    )

    assert resolve_ticket_subject(session, parent) == "Parent"
    assert resolve_ticket_subject(session, child) == "Child"
    _assert_map_matches_resolver(session, parent, child)
