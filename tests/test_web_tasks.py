"""Synthetic HTTP/concurrency tests; never start main, its workers, or DEV data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import get_db
from api.app.models import (AutomaticMessageTemplate, BotButtonSetting, Client, ClientProfile, Company,
                            TelegramContact, TelegramNotificationAccount, TelegramUpdate, Ticket, TicketMessage, User, WebClientSession)
from api.app.routers.web_tasks import router
from api.app.routers import telegram
from api.app.services import assignment, employee_telegram_notifications as notices, telegram_bot, web_auth, web_tasks
from test_web_auth import engine

ORIGIN = {'Origin': 'http://testserver'}
BASE = '/api/v1/web/tasks'


@pytest.fixture
def case(engine, monkeypatch):
    clock = [datetime(2026, 10, 30, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(web_auth, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(settings, 'web_public_base_url', 'http://testserver')
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', 'synthetic-employee-token')
    client_send = Mock(side_effect=AssertionError('No client Telegram delivery'))
    for module, name in [(telegram, 'send_message'), (telegram, 'remove_inline_keyboard'),
                         (assignment, 'send_message'), (telegram_bot, 'telegram_request')]:
        monkeypatch.setattr(module, name, client_send)
    staff_send = Mock(return_value={'ok': True})
    monkeypatch.setattr(notices, 'send_message', staff_send)
    monkeypatch.setattr(notices, 'SessionLocal', lambda: Session(engine))
    with Session(engine) as db:
        db.add_all([Company(id='co', name='Synthetic'), Company(id='other-co', name='Synthetic other')])
        db.flush()
        for name, company in [('client', 'co'), ('peer', 'co'), ('foreign', 'other-co')]:
            db.add(Client(id=name, company_id=company, display_name=name))
            db.flush()
            db.add_all([
                ClientProfile(id=f'profile-{name}', client_id=name, is_verified=True, web_login_normalized=name,
                              web_password_hash='synthetic-hash', web_credentials_version=1),
                TelegramContact(id=f'contact-{name}', client_id=name, company_id=company,
                                external_user_id=name, external_chat_id=f'customer-{name}'),
                WebClientSession(token_hash=web_auth.token_hash(f'token-{name}'), client_id=name, credentials_version=1,
                                 created_at=clock[0], last_activity_at=clock[0], normal_expires_at=clock[0]+timedelta(hours=2)),
            ])
        db.add(User(id='agent', company_id='co', full_name='Synthetic Agent', role='agent', is_active=True,
                    availability_status='online', login='synthetic-agent'))
        db.flush()
        db.add(TelegramNotificationAccount(id='staff-account', bound_user_id='agent', telegram_user_id='staff',
            telegram_chat_id='staff-only', request_status='approved', delivery_available=True))
        db.add(BotButtonSetting(id='button', button_key='Базовое|task|Task', menu_type='Базовое', workflow_type='task',
            button_kind='terminal', button_path='Task', parent_path='', button_label='Original',
            label_overridden=True, label_override='Task subject', system_text='Description: {{client_message}}'))
        for key, body in [('telegram.task.request_text', 'Describe {{button_label}} within {{timeout_minutes}}'),
                          ('telegram.task.created', 'Created {{ticket_id}}'),
                          ('telegram.intake.task_timeout', 'Expired after {{timeout_minutes}}'),
                          ('telegram.ticket.close.unassigned.client', 'Cancelled')]:
            db.add(AutomaticMessageTemplate(id=key, key=key, body=body, is_overridden=True, html_enabled=False))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app) as http:
        http.cookies.set('esdesk_web_session', 'token-client')
        yield SimpleNamespace(engine=engine, http=http, clock=clock, staff=staff_send, client_send=client_send)
    client_send.assert_not_called()


def choose(case, request_id=None, **extra):
    return case.http.post(BASE+'/select', headers=ORIGIN,
        json={'button_id': 'button', 'request_id': str(request_id or uuid4()), **extra})


def description(case, task_id, text='One description'):
    return case.http.post(f'{BASE}/{task_id}/description', headers=ORIGIN, json={'text': text})


def cancel(case, task_id):
    return case.http.post(f'{BASE}/{task_id}/cancel', headers=ORIGIN)


def test_create_description_ready_and_real_staff_notification(case):
    request_id = uuid4()
    selected = choose(case, request_id)
    assert selected.status_code == 200, selected.text
    payload = selected.json()
    assert payload['state'] == 'collecting' and payload['text'] == 'Describe Task subject within 10'
    assert choose(case, request_id).json() == payload
    assert choose(case).status_code == 409
    assert case.staff.call_count == 0
    created = description(case, payload['task_id'])
    assert created.status_code == 200, created.text
    assert created.json()['state'] == 'ready'
    assert created.json()['text'] == f"Created {payload['task_id']}"
    assert description(case, payload['task_id']).json() == created.json()
    assert choose(case, request_id).json() == created.json()
    assert description(case, payload['task_id'], 'Different description').status_code == 409
    assert cancel(case, payload['task_id']).status_code == 409
    with Session(case.engine) as db:
        ticket = db.get(Ticket, payload['task_id'])
        assert (ticket.channel, ticket.workflow_type, ticket.task_kind, ticket.intake_state, ticket.assignee_id) == ('web','task','normal','ready','agent')
        assert (ticket.company_id, ticket.client_id, ticket.subject) == ('co','client','Task subject')
        assert not ticket.menu_message_id and not ticket.client_close_message_id
        messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)).all()
        assert [m.body for m in messages if m.message_type == 'client'] == ['One description']
        assert all(m.telegram_message_id is None for m in messages)
        assert any(m.body == 'Description: One description' for m in messages)
        assert len(db.scalars(select(Ticket)).all()) == 1
        assert web_auth.has_open_web_ticket(db, 'client') is False
        assert not db.scalars(select(TelegramUpdate)).all()
    assert case.staff.call_count == 1
    assert case.staff.call_args.args[0] == 'staff-only'


def test_cancel_and_repeated_cancel_only_touch_web_draft(case):
    with Session(case.engine) as db:
        db.add(Ticket(id='telegram-draft', company_id='co', client_id='client', subject='Telegram', channel='telegram',
                      workflow_type='task', intake_state='collecting', status='unassigned'))
        db.commit()
    request_id = uuid4()
    selected = choose(case, request_id).json()
    assert cancel(case, selected['task_id']).json()['state'] == 'cancelled'
    assert cancel(case, selected['task_id']).status_code == 200
    assert choose(case, request_id).json()['state'] == 'cancelled'
    assert description(case, selected['task_id']).status_code == 409
    assert choose(case).json()['state'] == 'collecting'
    with Session(case.engine) as db:
        assert db.get(Ticket, 'telegram-draft').intake_state == 'collecting'
        assert not db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == 'telegram-draft')).all()
    assert case.staff.call_count == 0


@pytest.mark.parametrize('background', [False, True])
def test_ten_minute_timeout_and_late_retries(case, background):
    request_id = uuid4()
    selected = choose(case, request_id).json()
    with Session(case.engine) as db:
        db.add(Ticket(id='telegram-pending', company_id='co', client_id='client', channel='telegram',
            workflow_type='task', intake_state='collecting', status='unassigned', subject='Telegram pending',
            quick_action_state_json=dict(db.get(Ticket,selected['task_id']).quick_action_state_json)))
        db.commit()
    case.clock[0] += timedelta(minutes=10)
    if background:
        with Session(case.engine) as db:
            assert web_tasks.cleanup_expired_web_tasks(db) == 1
            assert web_tasks.cleanup_expired_web_tasks(db) == 0
    expired = description(case, selected['task_id'])
    assert expired.status_code == 410, expired.text
    assert expired.json()['detail']['error']['message'] == 'Expired after 10'
    assert choose(case, request_id).status_code == 410
    assert cancel(case, selected['task_id']).status_code == 410
    with Session(case.engine) as db:
        task = db.get(Ticket, selected['task_id'])
        assert (task.status, task.intake_state) == ('closed', 'cancelled')
        assert db.get(Ticket,'telegram-pending').intake_state == 'collecting'
        assert not db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == task.id, TicketMessage.message_type == 'client')).all()
    assert case.staff.call_count == 0


def test_confirmation_and_button_client_text_rules(case):
    with Session(case.engine) as db:
        row = db.get(BotButtonSetting, 'button')
        row.confirmation_enabled, row.confirmation_text = True, 'Confirm task'
        row.client_text = 'Button description prompt'
        db.scalar(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == 'telegram.task.request_text')).is_overridden = False
        db.commit()
    request_id = uuid4()
    selected = choose(case, request_id).json()
    assert selected['state'] == 'confirmation_required' and selected['text'] == 'Confirm task'
    assert description(case, selected['task_id']).status_code == 409
    confirmed = choose(case, request_id, confirmed=True)
    assert confirmed.json()['state'] == 'collecting' and confirmed.json()['text'] == 'Button description prompt'
    assert description(case, selected['task_id']).json()['state'] == 'ready'


@pytest.mark.parametrize('changes', [dict(workflow_type='group_task'),dict(workflow_type='auto'),dict(workflow_type='ticket'),
    dict(button_kind='group_parent'),dict(automation_key='telegram_group'),dict(is_archived=True),
    dict(currently_present=False),dict(visibility_overridden=True,is_enabled=False),dict(menu_type='Other')])
def test_selection_rejects_inaccessible_and_unsupported_button(case, changes):
    with Session(case.engine) as db:
        row = db.get(BotButtonSetting, 'button')
        for name, value in changes.items():
            setattr(row,name,value)
        db.commit()
    assert choose(case).status_code == 409
    with Session(case.engine) as db:
        assert not db.scalars(select(Ticket)).all()


def test_rechecks_menu_and_workflow_before_description(case):
    selected = choose(case).json()
    with Session(case.engine) as db:
        db.get(BotButtonSetting, 'button').workflow_type = 'auto'
        db.commit()
    assert description(case, selected['task_id']).status_code == 409
    with Session(case.engine) as db:
        assert db.get(Ticket, selected['task_id']).intake_state == 'collecting'


@pytest.mark.parametrize('owner', ['peer','foreign'])
def test_no_access_to_same_or_other_company_client_tasks(case, owner):
    selected = choose(case).json()
    case.http.cookies.set('esdesk_web_session', f'token-{owner}')
    assert description(case, selected['task_id']).status_code == 404
    assert cancel(case, selected['task_id']).status_code == 404


def test_session_csrf_server_identity_and_empty_description(case):
    assert case.http.post(BASE+'/select', json={'button_id':'button','request_id':str(uuid4())}).status_code == 403
    assert case.http.post(BASE+'/select', headers={'Origin':'http://foreign.example'}, json={}).status_code == 403
    for extra in [{'client_id':'foreign'}, {'company_id':'other-co'}, {'workflow_type':'auto'}, {'assignee_id':'agent'}]:
        assert choose(case, **extra).status_code == 422
    selected = choose(case).json()
    assert description(case, selected['task_id'], '   ').status_code == 422
    case.http.cookies.clear()
    assert choose(case).status_code == 401
    assert description(case, selected['task_id']).status_code == 401
    assert cancel(case, selected['task_id']).status_code == 401


@pytest.mark.parametrize('channel', ['web','telegram'])
def test_active_ticket_constraint_remains_cross_channel(case, channel):
    with Session(case.engine) as db:
        db.add(Ticket(id='active', company_id='co', client_id='client', subject='Active', channel=channel,
                      workflow_type='ticket', intake_state='ready', status='unassigned'))
        db.commit()
    result = choose(case)
    assert result.status_code == 409 and result.json()['detail']['error']['code'] == 'client_has_active_ticket'


@pytest.mark.parametrize('mode', ['same-selection','different-selection','description','cancel-description'])
def test_concurrent_requests_do_not_duplicate_task_message_or_notifications(case, mode):
    request_id = uuid4()
    if mode in {'description','cancel-description'}:
        selected = choose(case, request_id).json()
        task_id = selected['task_id']
        actions = [lambda: description(case, task_id), lambda: description(case, task_id)]
        if mode == 'cancel-description':
            actions[0] = lambda: cancel(case, task_id)
    else:
        actions = [lambda: choose(case, request_id), lambda: choose(case, request_id if mode == 'same-selection' else uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        barrier = Barrier(2)
        def start(action):
            barrier.wait(timeout=10)
            return action()
        responses = list(pool.map(start, actions))
    assert all(r.status_code in {200,409} for r in responses), [r.text for r in responses]
    assert any(r.status_code == 200 for r in responses)
    with Session(case.engine) as db:
        tasks = db.scalars(select(Ticket)).all()
        assert len(tasks) == 1
        messages = db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all()
        assert len(messages) <= 1
        assert case.staff.call_count == len(messages)


def test_fixed_assignment_and_freeze_use_existing_rules(case):
    with Session(case.engine) as db:
        db.get(User,'agent').availability_status = 'offline'
        row = db.get(BotButtonSetting,'button')
        row.fixed_assignee_enabled, row.fixed_assignee_user_id, row.freeze_on_create = True, 'agent', True
        db.commit()
    selected = choose(case).json()
    assert description(case, selected['task_id']).status_code == 200
    with Session(case.engine) as db:
        task = db.get(Ticket,selected['task_id'])
        assert task.assignee_id == 'agent' and task.is_frozen


def test_cleanup_loop_invokes_independent_web_cleanup(monkeypatch):
    once = Mock()
    monkeypatch.setattr(web_tasks, '_cleanup_once', once)
    async def stop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(web_tasks.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(web_tasks.web_task_cleanup_loop())
    once.assert_called_once()


def test_employee_closed_draft_is_never_reopened_or_expired(case):
    request_id = uuid4()
    selected = choose(case, request_id).json()
    with Session(case.engine) as db:
        task = db.get(Ticket,selected['task_id'])
        task.status, task.closed_at = 'closed', case.clock[0]
        db.commit()
        before = len(db.scalars(select(TicketMessage)).all())
    case.clock[0] += timedelta(minutes=11)
    with Session(case.engine) as db:
        assert web_tasks.cleanup_expired_web_tasks(db) == 0
    assert description(case, selected['task_id']).status_code == 409
    assert cancel(case, selected['task_id']).status_code == 409
    assert choose(case, request_id).status_code == 409
    with Session(case.engine) as db:
        assert db.get(Ticket,selected['task_id']).status == 'closed'
        assert len(db.scalars(select(TicketMessage)).all()) == before


def test_request_key_cannot_be_reused_for_another_button(case):
    request_id = uuid4()
    choose(case, request_id)
    assert choose(case, request_id, button_id='another-button').status_code == 409


def test_replays_do_not_extend_intake_deadline_and_cleanup_preserves_ready(case):
    request_id = uuid4()
    selected = choose(case, request_id).json()
    case.clock[0] += timedelta(minutes=9, seconds=59)
    assert choose(case, request_id).json()['expires_at'] == selected['expires_at']
    assert description(case, selected['task_id']).json()['state'] == 'ready'
    case.clock[0] += timedelta(minutes=1)
    with Session(case.engine) as db:
        assert web_tasks.cleanup_expired_web_tasks(db) == 0
        assert db.get(Ticket,selected['task_id']).intake_state == 'ready'


def test_no_online_agent_uses_existing_distribution_queue(case):
    with Session(case.engine) as db:
        db.get(User,'agent').availability_status = 'offline'
        db.commit()
    selected = choose(case).json()
    assert description(case,selected['task_id']).json()['state'] == 'ready'
    with Session(case.engine) as db:
        task = db.get(Ticket,selected['task_id'])
        assert task.assignee_id is None and task.status == 'unassigned'
        assert db.scalar(select(TicketMessage.id).where(TicketMessage.ticket_id == task.id,TicketMessage.body == 'На распределении'))
    case.staff.assert_not_called()
