from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select

from api.app.models import TicketMessage, TicketView, User
from api.app.schemas import TicketReadPayload
from api.app.routers import tickets as routes
from api.app.services.ticket_list import _unread_message_counts, list_tickets_paginated
from test_task_panel import _current_user, _seed_base, _session, _ticket


@pytest.fixture
def case(monkeypatch):
    db = _session()
    company, agent, other, client = _seed_base(db)
    admin = User(id=str(uuid.uuid4()), company_id=company.id, full_name="Admin", role="admin", is_active=True)
    db.add(admin)
    base = datetime(2026, 10, 1, 8)
    ticket = _ticket(db, company=company, client=client, assignee=agent, subject="Unread ticket")
    ticket.created_at = base
    ticket.auto_close_disabled = True
    db.commit()
    clock = SimpleNamespace(now=base + timedelta(hours=1))
    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return clock.now
    monkeypatch.setattr(routes, "datetime", Clock)
    yield SimpleNamespace(db=db, company=company, agent=agent, other=other, admin=admin,
                          client=client, ticket=ticket, base=base, clock=clock)
    db.close()


def message(case, kind="client", author=None, minutes=1, **values):
    item = TicketMessage(id=str(uuid.uuid4()), ticket_id=case.ticket.id, message_type=kind,
                         author_user_id=author.id if author else None, body="Message",
                         created_at=case.base + timedelta(minutes=minutes), agent_visible=True)
    for key, value in values.items():
        setattr(item, key, value)
    case.db.add(item)
    case.db.commit()
    return item


def listing(case, user=None, **options):
    return list_tickets_paginated(db=case.db, user=user or _current_user(case.agent.id),
        statuses=["new"], is_frozen=False, sort="created_at", order="desc", page=1, limit=50,
        filter_admin_assignees=False, **options)


def test_never_opened_counts_visible_client_staff_and_admin_comments(case):
    message(case)
    message(case, "agent", case.admin)
    message(case, "internal", case.admin)
    message(case, "agent", case.other)
    message(case, "internal", case.other)
    message(case, "agent", case.agent)
    message(case, "internal", case.agent)
    message(case, "system", agent_visible=True)
    message(case, "client", agent_visible=False)
    message(case, "internal", case.admin, agent_visible=False)
    message(case, "agent", case.admin, message_status="deleted")
    message(case, minutes=-1)
    item = listing(case).items[0]
    assert item.unread_count == 5
    assert item.seen_by_agent is False
    assert item.model_dump()["unread_count"] == 5


def test_initial_message_at_ticket_creation_is_counted(case):
    message(case, minutes=0)
    assert listing(case).items[0].unread_count == 1


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_new_messages_after_previous_open_reappear_without_changing_seen_flag(case, role):
    user = _current_user(case.agent.id, role)
    routes.get_ticket(case.ticket.id, db=case.db, user=user)
    assert listing(case, user).items[0].unread_count == 0
    view = case.db.scalar(select(TicketView).where(TicketView.ticket_id == case.ticket.id))
    old = message(case, minutes=59)
    equal = message(case, minutes=60)
    routes.confirm_ticket_read(case.ticket.id, TicketReadPayload(message_id=equal.id, message_ids=[old.id, equal.id]), db=case.db, user=user)
    message(case, minutes=61)
    message(case, "agent", case.admin, minutes=62)
    message(case, "internal", case.admin, minutes=63)
    item = listing(case, user).items[0]
    assert item.unread_count == 3 and item.seen_by_agent is True
    original_first = view.first_opened_at
    case.clock.now = case.base + timedelta(minutes=65)
    card = routes.get_ticket(case.ticket.id, db=case.db, user=user)
    case.db.refresh(view)
    assert view.first_opened_at == original_first
    assert view.last_opened_at < case.clock.now
    routes.confirm_ticket_read(case.ticket.id, TicketReadPayload(message_id=card.unread_message_ids[-1], message_ids=card.unread_message_ids), db=case.db, user=user)
    item = listing(case, user).items[0]
    assert item.unread_count == 0 and item.seen_by_agent is True
    message(case, minutes=66)
    assert listing(case, user).items[0].unread_count == 1


def test_admin_open_does_not_reset_agent_count(case):
    message(case)
    routes.get_ticket(case.ticket.id, db=case.db, user=_current_user(case.admin.id, "admin"))
    assert listing(case).items[0].unread_count == 1
    assert case.db.scalar(select(TicketView)) is None
    routes.get_ticket(case.ticket.id, db=case.db, user=_current_user(case.agent.id))
    message(case, minutes=61)
    view = case.db.scalar(select(TicketView))
    before = view.last_opened_at
    case.clock.now += timedelta(minutes=5)
    routes.get_ticket(case.ticket.id, db=case.db, user=_current_user(case.admin.id, "admin"))
    case.db.refresh(view)
    assert view.last_opened_at == before
    assert listing(case).items[0].unread_count == 2


@pytest.mark.parametrize("role", ["admin", "chiki_piki"])
def test_no_counts_for_non_support_roles(case, role):
    message(case)
    assert _unread_message_counts(case.db, [case.ticket], _current_user(case.admin.id, role)) == {}
    if role == "admin":
        assert listing(case, _current_user(case.admin.id, role)).items[0].unread_count == 0


def test_no_count_for_another_assignees_ticket(case):
    message(case)
    assert _unread_message_counts(case.db, [case.ticket], _current_user(case.other.id)) == {}


def test_one_batched_count_query_for_multiple_tickets(case):
    for index in range(12):
        ticket = _ticket(case.db, company=case.company, client=case.client, assignee=case.agent, subject=str(index))
        ticket.created_at = case.base
        case.db.flush()
        case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id,
            message_type="client", body="Unread", agent_visible=True, created_at=case.base))
    case.db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "count(ticket_messages.id)" in statement.lower():
            statements.append(statement)
    event.listen(case.db.bind, "before_cursor_execute", capture)
    try:
        result = listing(case)
    finally:
        event.remove(case.db.bind, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert len(result.items) == 13
    assert sum(item.unread_count for item in result.items) == 12


@pytest.mark.parametrize("total", [0, 1, 5, 12])
def test_list_serializes_exact_unread_count(case, total):
    for _ in range(total):
        message(case)
    assert listing(case).items[0].model_dump()["unread_count"] == total
