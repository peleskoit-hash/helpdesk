import pytest
from sqlalchemy import select

from api.app.models import Ticket, TicketMessage
from api.app.routers import tickets
from api.app.services.ticket_special import _handle_special_assignment
from api.app.services.assignment import append_system_message, assignee_change_message
from test_admin_bulk_freeze import _session, _company, _user, _contact, _ticket, _client
from test_internal_task_completion_notification import _base_entities

WARNING = "Обратите внимание, что Вы перенесли ЗАЯВКУ в особый список. Пока она не будет закрыта, клиент не сможет создавать новые заявки."


def messages(db, ticket):
    return db.execute(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id, TicketMessage.message_type == "system")).scalars().all()


@pytest.mark.parametrize("kind", ["ticket", "normal", "internal", "group_parent", "group_child"])
@pytest.mark.parametrize("status", ["unassigned", "new", "pending"])
def test_special_move_types_messages_and_links(monkeypatch, kind, status):
    db = _session()
    if kind == "internal":
        entities = _base_entities(db)
        ticket, company, admin, agent = entities.ticket, entities.company, entities.responsible, entities.agent
        occurrence_before = (entities.occurrence.ticket_id, entities.occurrence.definition_id, entities.occurrence.effective_assignee_user_id)
        peers = []
    else:
        company = _company(db)
        admin, agent = _user(db, company, "admin"), _user(db, company, "agent")
        contact = _contact(db, company)
        ticket = _ticket(db, company, contact, assignee=agent, workflow_type="ticket" if kind == "ticket" else "task")
        ticket.task_kind = kind if kind in ["group_parent", "group_child"] else "normal"
        peers = []
        if kind in ["group_parent", "group_child"]:
            peer = _ticket(db, company, contact, assignee=agent, workflow_type="task")
            peer.task_kind = "group_child" if kind == "group_parent" else "group_parent"
            if kind == "group_parent":
                peer.parent_ticket_id = ticket.id
            else:
                ticket.parent_ticket_id = peer.id
            peers = [peer]
    if kind == "group_parent":
        ticket.agent_visible = False
    ticket.status = status
    if status == "unassigned":
        ticket.assignee_id = None
    db.commit()
    links_before = [(t.id, t.parent_ticket_id, t.task_kind) for t in [ticket] + peers]
    peer_before = [(t.assignee_id, t.status, t.is_special) for t in peers]
    ids_before = set(db.scalars(select(Ticket.id)))
    employee_calls = []
    monkeypatch.setattr(tickets, "schedule_employee_assignment_notification", lambda *a, **kw: employee_calls.append(kw))
    def unexpected_client_delivery(*a, **kw):
        pytest.fail("move to special must not contact the client")
    monkeypatch.setattr(tickets, "_send_to_telegram_client", unexpected_client_delivery)
    monkeypatch.setattr("api.app.services.assignment.notify_client_about_assignee", unexpected_client_delivery)
    client = _client(db, admin)
    response = client.post(f"/api/v1/tickets/{ticket.id}/special")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    db.refresh(ticket)
    assert ticket.is_special and ticket.special_at is not None
    assert ticket.assignee_id == ticket.special_assignee_id == admin.id
    assert ticket.status == ("pending" if status == "pending" else "new")
    assert [(t.id, t.parent_ticket_id, t.task_kind) for t in [ticket] + peers] == links_before
    assert [(t.assignee_id, t.status, t.is_special) for t in peers] == peer_before
    assert set(db.scalars(select(Ticket.id))) == ids_before
    if kind == "internal":
        db.refresh(entities.occurrence)
        assert (entities.occurrence.ticket_id, entities.occurrence.definition_id, entities.occurrence.effective_assignee_user_id) == occurrence_before
    history = messages(db, ticket)
    label = "Заявка" if kind == "ticket" else "Задача"
    assert f"{label} переведена в особые. Ответственный администратор: {admin.full_name}" in [m.body for m in history]
    assert sum(m.body == WARNING for m in history) == (1 if kind == "ticket" else 0)
    assert all(m.agent_visible for m in history)
    before = [(m.id, m.body) for m in history]
    response = client.post(f"/api/v1/tickets/{ticket.id}/special")
    assert response.json() == {"ok": True, "already_special": True}
    assert [(m.id, m.body) for m in messages(db, ticket)] == before
    assert len(employee_calls) == 1
    response = client.get("/api/v1/tickets/special")
    assert response.status_code == 200, response.text
    assert ticket.id in {item["id"] for item in response.json()["items"]}
    db.close()


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("role", ["agent", "trainee", "chiki_piki", "admin"])
def test_special_permissions_and_closed_rejection(workflow, role):
    db = _session()
    company = _company(db)
    actor = _user(db, company, role)
    ticket = _ticket(db, company, _contact(db, company), workflow_type=workflow, status="closed" if role == "admin" else "new")
    db.commit()
    response = _client(db, actor).post(f"/api/v1/tickets/{ticket.id}/special")
    assert response.status_code == (409 if role == "admin" else 403)
    db.refresh(ticket)
    assert not ticket.is_special
    assert not messages(db, ticket)
    db.close()


@pytest.mark.parametrize("workflow", ["ticket", "task"])
@pytest.mark.parametrize("new_role", ["agent", "trainee", "admin"])
def test_special_assignment_messages_name_type(workflow, new_role):
    db = _session()
    company = _company(db)
    admin = _user(db, company, "admin")
    assignee = _user(db, company, new_role)
    ticket = _ticket(db, company, _contact(db, company), assignee=admin, workflow_type=workflow, is_special=True)
    db.commit()
    assert _handle_special_assignment(db, ticket, assignee, admin, "new", append_system_message_fn=append_system_message,
        assignee_change_message_fn=assignee_change_message, refresh_auto_close_timer_fn=lambda *a, **kw: None,
        notify_client_about_assignee_fn=lambda *a: pytest.fail("unexpected client delivery"), error_fn=lambda *a: pytest.fail(str(a)))
    db.flush()
    expected = (f"Особая {'задача' if workflow == 'task' else 'заявка'} передана сотруднику: {assignee.full_name}" if new_role != "admin" else
        f"Ответственный администратор особой {'задачи' if workflow == 'task' else 'заявки'} изменён: {assignee.full_name}")
    assert expected in [m.body for m in messages(db, ticket)]
    assert ticket.is_special == (new_role == "admin")
    assert ticket.assignee_id == assignee.id
    db.close()
