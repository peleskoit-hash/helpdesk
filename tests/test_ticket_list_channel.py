"""Regression through the real staff list routes, on isolated test databases."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.auth import get_current_user
from api.app.db import get_db
from api.app.routers.tickets import router
from test_ticket_requester_search import case, seed


@pytest.mark.parametrize('tab', ['active', 'unassigned', 'frozen', 'closed', 'special'])
def test_staff_list_serializes_stored_channel_for_web_and_telegram_tickets_and_tasks(case, tab):
    expected = {}
    status = 'unassigned' if tab == 'unassigned' else 'closed' if tab == 'closed' else 'new'
    for channel in ['web', 'telegram']:
        for workflow in ['ticket', 'task']:
            ticket = seed(case, channel=channel, workflow_type=workflow, status=status,
                          is_frozen=tab == 'frozen', is_special=tab == 'special')
            expected[ticket.id] = (channel, workflow)
    case.db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: case.db
    app.dependency_overrides[get_current_user] = lambda: case.admin
    with TestClient(app) as http:
        response = http.get(f'/api/v1/tickets/{tab}', params={'assignee_ids': 'agent'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 4
    assert {row['id'] for row in body['items']} == set(expected)
    for row in body['items']:
        assert (row['channel'], row['workflow_type']) == expected[row['id']]
