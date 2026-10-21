from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.models import TicketMessage
from api.app.routers import tickets
from test_ticket_messages_outbound import _seed_ticket, _session_bundle


START_NOTE = "Клиент нажал /start и пытается создать новое обращение"


@pytest.mark.parametrize("agent_visible", [False, True, None])
def test_start_note_requires_explicit_agent_visibility(agent_visible):
    message = TicketMessage(body=START_NOTE, agent_visible=agent_visible)
    assert tickets._is_support_visible_system_message(message) is (agent_visible is True)


@pytest.mark.parametrize("body", [
    *tickets.SYSTEM_HIDDEN_PREFIXES_FOR_SUPPORT,
    *(prefix + " подробности" for prefix in tickets.SYSTEM_HIDDEN_PREFIXES_FOR_SUPPORT),
    " " + START_NOTE,
    START_NOTE + " ",
    START_NOTE + " дополнение",
])
@pytest.mark.parametrize("agent_visible", [False, True])
def test_other_hidden_system_messages_remain_hidden(body, agent_visible):
    message = TicketMessage(body=body, agent_visible=agent_visible)
    assert tickets._is_support_visible_system_message(message) is False


@pytest.mark.parametrize("body,expected", [(None, False), ("  ", False), ("Обычное событие", True)])
def test_other_system_message_filtering_is_unchanged(body, expected):
    assert tickets._is_support_visible_system_message(TicketMessage(body=body)) is expected


@pytest.mark.parametrize("role", ["agent", "trainee", "admin"])
def test_ticket_api_start_note_visibility_by_role(role):
    engine, _, db = _session_bundle()
    try:
        ticket = _seed_ticket(db)
        ticket.assignee_id = "support"
        ticket.auto_close_disabled = True
        rows = [
            ("start-visible", START_NOTE, True),
            ("start-hidden", START_NOTE, False),
            ("ordinary", "Обычное событие", True),
            ("start-extra", START_NOTE + " дополнение", True),
            *((f"hidden-{index}", prefix, True)
              for index, prefix in enumerate(tickets.SYSTEM_HIDDEN_PREFIXES_FOR_SUPPORT)),
        ]
        db.add_all([
            TicketMessage(id=id_, ticket_id=ticket.id, message_type="system",
                          body=body, agent_visible=visible)
            for id_, body, visible in rows
        ])
        db.commit()
        app = FastAPI()
        app.include_router(tickets.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="support", role=role)
        with TestClient(app) as client:
            response = client.get(f"/api/v1/tickets/{ticket.id}")
        assert response.status_code == 200, response.text
        ids = {message["id"] for message in response.json()["messages"]}
        expected = {id_ for id_, _, _ in rows} if role == "admin" else {"start-visible", "ordinary"}
        assert ids == expected
    finally:
        db.close()
        engine.dispose()
