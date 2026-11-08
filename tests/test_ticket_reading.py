from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.app.models import TicketView
from api.app.schemas import TicketReadPayload
from api.app.routers import tickets as routes
from test_task_panel import _current_user
from test_ticket_list_unread import case, listing, message


def open_card(case, role="agent"):
    return routes.get_ticket(case.ticket.id, db=case.db, user=_current_user(case.agent.id, role))


def confirm(case, ids, user=None):
    return routes.confirm_ticket_read(case.ticket.id,
        TicketReadPayload(message_id=ids[-1], message_ids=ids), db=case.db,
        user=user or _current_user(case.agent.id))


def test_get_and_poll_keep_unread_and_first_open_fact(case):
    first = message(case, minutes=0)
    second = message(case)
    card = open_card(case)
    view = case.db.scalar(select(TicketView))
    assert view.first_opened_at == case.clock.now
    assert view.last_opened_at < case.ticket.created_at
    assert card.unread_message_ids == [first.id, second.id]
    for _ in range(3):
        case.clock.now += timedelta(minutes=1)
        assert open_card(case).unread_message_ids == card.unread_message_ids
    assert listing(case).items[0].seen_by_agent is True
    assert listing(case).items[0].unread_count == 2


@pytest.mark.parametrize("role", ["agent", "trainee"])
def test_partial_confirmation_reduces_count_and_cannot_move_backwards(case, role):
    ids = [message(case, minutes=i).id for i in range(1, 6)]
    card = open_card(case, role)
    assert card.unread_message_ids == ids
    confirm(case, ids[:2], _current_user(case.agent.id, role))
    assert listing(case).items[0].unread_count == 3
    assert open_card(case, role).unread_message_ids == ids[2:]
    view = case.db.scalar(select(TicketView))
    boundary = view.last_opened_at
    confirm(case, ids[:1], _current_user(case.agent.id, role))
    case.db.refresh(view)
    assert view.last_opened_at == boundary
    confirm(case, ids[2:], _current_user(case.agent.id, role))
    assert listing(case).items[0].unread_count == 0
    new = message(case, minutes=6)
    assert open_card(case, role).unread_message_ids == [new.id]


def test_ack_requires_contiguous_prefix_including_equal_timestamps(case):
    first = message(case)
    second = message(case)
    open_card(case)
    with pytest.raises(HTTPException) as error:
        confirm(case, [first.id])
    assert error.value.status_code == 409
    case.db.rollback()
    assert listing(case).items[0].unread_count == 2
    confirm(case, [first.id, second.id])
    assert listing(case).items[0].unread_count == 0


def test_unloaded_message_at_same_timestamp_is_not_silently_read(case):
    first = message(case)
    open_card(case)
    second = message(case)  # arrives between GET and acknowledgement
    with pytest.raises(HTTPException) as error:
        confirm(case, [first.id])
    assert error.value.status_code == 409
    case.db.rollback()
    assert listing(case).items[0].unread_count == 2
    confirm(case, [first.id, second.id])


@pytest.mark.parametrize("kind,author,values", [
    ("system", "admin", {}), ("agent", "agent", {}),
    ("internal", "admin", {"agent_visible": False}),
    ("agent", "admin", {"message_status": "deleted"}),
    ("client", None, {"minutes": 120}),
])
def test_rejects_ineligible_and_future_boundaries(case, kind, author, values):
    item = message(case, kind, getattr(case, author) if author else None, **values)
    assert open_card(case).unread_message_ids == ([item.id] if values.get("minutes") else [])
    with pytest.raises(HTTPException) as error:
        confirm(case, [item.id])
    assert error.value.status_code == 422


def test_admin_and_other_agent_cannot_confirm_responsible_agents_reading(case):
    item = message(case)
    open_card(case)
    view = case.db.scalar(select(TicketView))
    original = view.last_opened_at
    routes.get_ticket(case.ticket.id, db=case.db, user=_current_user(case.admin.id, "admin"))
    for user in [_current_user(case.admin.id, "admin"), _current_user(case.other.id)]:
        with pytest.raises(HTTPException) as error:
            confirm(case, [item.id], user)
        assert error.value.status_code == 403
    case.db.refresh(view)
    assert view.last_opened_at == original
    assert listing(case).items[0].unread_count == 1
