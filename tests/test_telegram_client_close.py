"""Client close rules through the Telegram runtime, using synthetic SQLite data."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import delete

from api.app.config import settings
from api.app.models import TicketMessage
from api.app.routers import telegram
from api.app.services.telegram_client_close import client_can_close_ticket
from test_ticket_review_telegram import (
    _session, _seed_base, _seed_ticket, _callback_payload, _message_payload,
)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', '')
    db = _session()
    company, client, contact, agent, _ = _seed_base(db)
    removed = Mock()
    monkeypatch.setattr(telegram, 'remove_inline_keyboard', removed)
    monkeypatch.setattr(telegram, '_safe_remove_inline_keyboard', removed)
    monkeypatch.setattr(telegram, 'send_message', Mock(return_value={'result': {'message_id': 99}}))
    monkeypatch.setattr(telegram, 'answer_callback_query', Mock())
    yield SimpleNamespace(db=db, company=company, client=client, contact=contact, agent=agent, removed=removed)
    db.close()
    db.get_bind().dispose()


def seed(case, *, workflow='ticket', intake='ready', assigned=False, answer=False):
    ticket, _ = _seed_ticket(case.db, case.company, case.client, case.agent if assigned else None,
                           workflow_type=workflow, with_agent_message=answer)
    ticket.channel = 'telegram'
    ticket.intake_state = intake
    ticket.status = 'pending' if assigned else 'unassigned'
    ticket.client_close_message_id = '12'
    if workflow == 'task' and intake == 'collecting':
        case.db.execute(delete(TicketMessage).where(TicketMessage.ticket_id == ticket.id,
                                                   TicketMessage.message_type == 'client'))
    case.db.commit()
    return ticket


def close(case, via, *, message_id=12):
    payload = (_callback_payload(update_id=2001, contact=case.contact, data='client_close', message_id=message_id)
               if via == 'button' else _message_payload(update_id=2001, contact=case.contact, text='/close'))
    return telegram.process_telegram_update(case.db, payload, notify_client=False)


@pytest.mark.parametrize('via', ['button', 'command'])
def test_collecting_task_can_be_cancelled(case, via):
    ticket = seed(case, workflow='task', intake='collecting')
    assert client_can_close_ticket(case.db, ticket)
    assert close(case, via)['closed'] is True
    case.db.refresh(ticket)
    assert (ticket.status, ticket.intake_state) == ('closed', 'cancelled')


@pytest.mark.parametrize('assigned', [False, True])
@pytest.mark.parametrize('via', ['button', 'command'])
def test_ready_task_is_not_closed_by_client(case, assigned, via):
    ticket = seed(case, workflow='task', assigned=assigned)
    assert close(case, via)['closed'] is False
    case.db.refresh(ticket)
    assert ticket.status != 'closed' and ticket.intake_state == 'ready'
    assert telegram._active_ticket_for_client(case.db, case.company.id, case.client.id) is None
    assert telegram._collecting_ticket_for_client(case.db, case.company.id, case.client.id) is None
    assert not client_can_close_ticket(case.db, ticket)


@pytest.mark.parametrize('via', ['button', 'command'])
@pytest.mark.parametrize('assigned', [False, True])
def test_normal_ticket_can_close_after_agent_answer(case, via, assigned):
    ticket = seed(case, assigned=assigned, answer=True)
    assert close(case, via)['closed'] is True
    case.db.refresh(ticket)
    assert ticket.status == 'closed'


@pytest.mark.parametrize('workflow,intake', [('ticket', 'ready'), ('task', 'collecting')])
def test_stale_button_does_not_close_new_ticket_or_draft(case, workflow, intake):
    old = seed(case)
    old.status = 'closed'
    case.db.commit()
    current = seed(case, workflow=workflow, intake=intake)
    current.client_close_message_id = '30'
    case.db.commit()
    assert close(case, 'button', message_id=12)['closed'] is False
    case.db.refresh(current)
    assert current.status != 'closed' and current.closed_at is None
    assert current.client_close_message_id == '30'


@pytest.mark.parametrize('assignment_failed', [False, True])
def test_ready_task_removes_close_button_even_without_distribution(case, monkeypatch, assignment_failed):
    ticket = seed(case, workflow='task', intake='collecting')
    monkeypatch.setattr(telegram, 'apply_call_center_employee_snapshot_on_ticket_create', Mock())
    monkeypatch.setattr(telegram, '_apply_fixed_assignee_from_setting', Mock(return_value=None))
    monkeypatch.setattr(telegram, '_try_forced_assignment', Mock(return_value=None))
    monkeypatch.setattr(telegram, 'choose_online_agent', Mock(return_value=case.agent if assignment_failed else None))
    monkeypatch.setattr(telegram, 'assign_ticket_to_agent', Mock(return_value=False))
    monkeypatch.setattr(telegram, '_apply_button_creation_flags', Mock())
    assert telegram._ready_ticket_for_distribution(case.db, ticket) is None
    assert ticket.intake_state == 'ready' and ticket.assignee_id is None
    assert ticket.client_close_message_id is None
    case.removed.assert_called_once_with(case.contact.external_chat_id, 12)
