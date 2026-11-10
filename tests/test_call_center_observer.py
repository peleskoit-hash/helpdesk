"""Observer permissions use real routes and an isolated database; no external calls."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app import auth
from api.app.db import get_db
from api.app.models import ClientProfile, TicketMessage, User
from api.app.routers import tickets, settings_users, users
from test_ticket_messages_outbound import _session_bundle, _seed_ticket


@pytest.fixture
def case(monkeypatch):
    engine, _, db = _session_bundle()
    ticket = _seed_ticket(db)
    viewer = User(id="viewer", company_id=ticket.company_id, full_name="Observer", login="observer", role="chiki_piki")
    db.add(viewer)
    ticket.ticket_number = 1
    ticket.assignee_id = "viewer"
    ticket.status = "new"
    ticket.agent_visible = True
    db.add(ClientProfile(id="profile", client_id=ticket.client_id, destination="Колл Центр"))
    db.add(TicketMessage(id="source", ticket_id=ticket.id, message_type="client", body="Original", telegram_message_id="123"))
    db.commit()
    user = SimpleNamespace(id="viewer", role="chiki_piki", real_user_id="viewer", real_role="chiki_piki", is_real_admin=False)
    unfreezes = []

    def forbidden(*args, **kwargs):
        pytest.fail("An internal comment must not send to the client or change its timers/buttons")

    monkeypatch.setattr(tickets, "_send_to_telegram_client", forbidden)
    monkeypatch.setattr(tickets, "_remove_client_close_button", forbidden)
    monkeypatch.setattr(tickets, "refresh_ticket_auto_close", forbidden)
    monkeypatch.setattr(tickets, "unfreeze_ticket_on_message", lambda ticket: unfreezes.append(ticket.id))
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[auth.get_current_user] = lambda: user
    try:
        with TestClient(app) as http:
            yield SimpleNamespace(db=db, ticket=ticket, user=user, viewer=viewer, http=http,
                                  base=f"/api/v1/tickets/{ticket.id}", unfreezes=unfreezes)
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("role", ["admin", "agent", "trainee", "chiki_piki"])
@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_comment_and_quote_permissions_are_scoped_to_observer(case, role, workflow):
    case.user.role = role
    case.ticket.workflow_type = workflow
    case.db.commit()
    response = case.http.post(case.base + "/comment", json={"text": "Private comment", "quote_message_id": "source"})
    assert response.status_code == 200, response.text
    comment = case.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "internal"))
    assert comment.body == "Private comment" and comment.author_user_id == "viewer"
    assert comment.quote_message_id == ("source" if role == "chiki_piki" else None)
    assert case.unfreezes == []
    assert case.ticket.status == "new" and case.ticket.assignee_id == "viewer"
    assert case.viewer.role == "chiki_piki"  # No role records are rewritten.


@pytest.mark.parametrize("workflow", ["ticket", "task"])
def test_observer_cannot_reply_or_manage_and_cannot_unfreeze_by_comment(case, workflow):
    case.ticket.workflow_type = workflow
    case.ticket.is_frozen = True
    case.ticket.freeze_until = datetime.utcnow() + timedelta(days=1)
    case.db.commit()
    before = (case.ticket.status, case.ticket.assignee_id, case.ticket.is_frozen, case.ticket.freeze_until)
    for action, payload in [
        ("reply", {"text": "Forbidden reply", "quote_message_id": "source"}),
        ("close", {}), ("restore", {}), ("unfreeze", {}),
        ("freeze", {"freeze_until": "2099-01-01T00:00:00"}),
        ("greeting", {}),
    ]:
        response = case.http.post(case.base + "/" + action, json=payload)
        assert response.status_code == 403, (action, response.text)
    response = case.http.post(case.base + "/comment", json={"text": "Observation"})
    assert response.status_code == 200, response.text
    assert case.unfreezes == []
    assert (case.ticket.status, case.ticket.assignee_id, case.ticket.is_frozen, case.ticket.freeze_until) == before
    assert case.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "agent")) is None


def test_observer_access_and_closed_restrictions_are_preserved(case):
    profile = case.db.get(ClientProfile, "profile")
    profile.destination = "Other"
    case.db.commit()
    for path in [case.base, case.base + "/comment"]:
        response = case.http.get(path) if path == case.base else case.http.post(path, json={"text": "Private"})
        assert response.status_code == 403
    profile.destination = "Колл Центр"
    case.ticket.status = "closed"
    case.db.commit()
    assert case.http.post(case.base + "/comment", json={"text": "Private"}).status_code == 409


def test_observer_can_save_only_comment_draft_and_cannot_use_quick_actions(case):
    assert case.http.patch(case.base + "/draft", json={"text": "Draft", "mode": "comment"}).status_code == 200
    assert case.http.patch(case.base + "/draft", json={"text": "Draft", "mode": "reply"}).status_code == 403
    assert case.http.post(case.base + "/comment", json={"text": "Action", "quick_action": "greeting"}).status_code == 403


@pytest.mark.parametrize("kind", ["system", "internal"])
def test_observer_cannot_quote_messages_outside_existing_quote_menu(case, kind):
    case.db.get(TicketMessage, "source").message_type = kind
    case.db.commit()
    assert case.http.post(case.base + "/comment", json={"text": "Private", "quote_message_id": "source"}).status_code == 409


def test_role_label_changes_without_identifier_or_other_permissions(case):
    assert settings_users.role_label("chiki_piki") == "Call Center Observer"
    assert users._status_out(case.viewer).role_label == "Call Center Observer"
    assert users._status_out(case.viewer).role == "chiki_piki"
    assert auth.can_comment(case.user)
    for check in [auth.can_reply, auth.can_manage_ticket, auth.can_assign, auth.can_edit_client_profile, auth.can_edit_messages]:
        assert not check(case.user), check.__name__
    for role in ["admin", "agent", "trainee"]:
        user = SimpleNamespace(role=role)
        assert auth.can_reply(user) and auth.can_comment(user) and auth.can_manage_ticket(user)
    assert not auth.can_comment(SimpleNamespace(role="unknown"))



def test_observer_raw_uploads_and_message_editing_stay_forbidden(case):
    response = case.http.post(case.base + "/comment", data={"text":"Private"}, files={"files":("test.txt",b"x","text/plain")})
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "comment_raw_files_not_allowed"
    assert case.http.post(case.base + "/comment", json={"text":"Private"}).status_code == 200
    comment = case.db.scalar(select(TicketMessage).where(TicketMessage.message_type == "internal"))
    assert case.http.patch(case.base + "/messages/" + comment.id, json={"text":"Edited"}).status_code == 403
    assert case.http.delete(case.base + "/messages/" + comment.id).status_code == 403
