"""Automation fallback through real Telegram selection, assignment and staff routes."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.app.auth import get_current_user
from api.app.config import settings
from api.app.db import get_db
from api.app.models import (BotButtonSetting, BotMenuDestinationBinding, BotMenuProfile, ClientProfile,
                            TelegramNotificationAccount, Ticket, TicketMessage, TicketReviewCycle)
from api.app.routers import telegram, tickets
from api.app.services import assignment, employee_telegram_notifications as notifications, saved_files, ticket_attachments
from api.app.services.telegram_bot import TelegramApiError
from test_ticket_messages_outbound import _session_bundle
from test_ticket_review_telegram import _seed_base, _callback_payload


@pytest.fixture
def case(monkeypatch, tmp_path):
    engine, factory, db = _session_bundle()
    company, client, contact, agent, _ = _seed_base(db)
    row = BotButtonSetting(id=str(uuid4()), stable_button_id=str(uuid4()), button_key='menu-a|auto|Material',
        menu_type='menu-a', workflow_type='auto', button_path='Material', button_label='Материал',
        button_kind='terminal', parent_path='', currently_present=True, is_archived=False,
        client_text='Автоматический ответ', automation_key='message_only')
    task = Ticket(id=str(uuid4()), company_id=company.id, client_id=client.id, channel='telegram',
        workflow_type='ticket', task_kind='normal', intake_state='collecting', status='unassigned',
        subject='Выбор типа проблемы', menu_message_id='12', client_close_message_id='12')
    db.add_all([row, task,
        ClientProfile(id=str(uuid4()), client_id=client.id, is_verified=True, destination='Office'),
        BotMenuDestinationBinding(id=str(uuid4()), destination_value='Office', menu_type='menu-a'),
        BotMenuProfile(id=str(uuid4()), menu_type='menu-a', display_name='Menu', is_active=True),
        TelegramNotificationAccount(id=str(uuid4()), bound_user_id=agent.id, telegram_user_id='worker-user',
            telegram_chat_id='worker-chat', request_status='approved', delivery_available=True),
    ])
    db.commit()
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', 'synthetic-token')
    monkeypatch.setattr(settings, 'attachment_storage_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'saved_file_storage_dir', str(tmp_path / 'saved'))
    monkeypatch.setattr(notifications, 'SessionLocal', factory)
    employee = Mock(return_value={'result': {'message_id': 901}})
    monkeypatch.setattr(notifications, 'send_message', employee)
    sent, files = [], []
    def send(chat, text, **kwargs):
        sent.append((chat, text, kwargs))
        return {'result': {'message_id': 900}}
    monkeypatch.setattr(telegram, 'send_message', send)
    monkeypatch.setattr(tickets, 'send_message', send)
    monkeypatch.setattr(telegram, 'answer_callback_query', Mock())
    monkeypatch.setattr(telegram, 'remove_inline_keyboard', Mock())
    monkeypatch.setattr(telegram, '_safe_delete_message', Mock(return_value=True))
    monkeypatch.setattr(tickets, 'remove_inline_keyboard', Mock())
    assignee_notice = Mock(side_effect=AssertionError('Tasks must not announce the assignee to the client'))
    monkeypatch.setattr(telegram, 'notify_client_about_assignee', assignee_notice)
    monkeypatch.setattr(assignment, 'notify_client_about_assignee', assignee_notice)
    monkeypatch.setattr(ticket_attachments, 'send_document', lambda chat, path, **kw:
                        files.append((chat, path.read_bytes(), kw)) or {'result': {'message_id': 902}})
    app = FastAPI()
    app.include_router(tickets.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=agent.id, role='agent', company_id=company.id)
    with TestClient(app) as http:
        yield SimpleNamespace(db=db, http=http, row=row, task=task, agent=agent, contact=contact,
                              sent=sent, files=files, employee=employee, assignee_notice=assignee_notice, send=send)
    db.close()
    engine.dispose()


def select_button(case):
    payload = _callback_payload(update_id=100, contact=case.contact, data=f'btnid:{case.row.id}', message_id=12)
    return payload, telegram.process_telegram_update(case.db, payload, notify_client=True)


@pytest.mark.parametrize('failure', ['message_only', 'send_files_text', 'file', 'file_send', 'unavailable'])
@pytest.mark.parametrize('real_client', [False, True])
def test_fallback_assignment_card_history_and_final_answer(case, monkeypatch, failure, real_client):
    if real_client:
        original = telegram._append_client_message(case.db, case.task, 'Настоящее описание клиента', '501')
        case.db.commit()
    case.row.automation_key = 'send_files' if failure in {'file', 'file_send', 'send_files_text'} else (
        'message_only' if failure == 'message_only' else 'unavailable-automation')
    if failure == 'file':
        case.row.files_json = [f'saved:{uuid4()}']  # Real resolver: unavailable material, no network.
    if failure == 'file_send':
        saved = saved_files.create_saved_file(case.db, content=b'material', display_filename='material.pdf')
        case.row.files_json = [f'saved:{saved.id}']
        monkeypatch.setattr(telegram, 'send_document', Mock(side_effect=TelegramApiError('synthetic file failure')))
    case.db.commit()
    if failure in {'message_only', 'send_files_text'}:
        def fail_auto_text(chat, text, **kwargs):
            if text == 'Автоматический ответ':
                raise TelegramApiError('synthetic failure')
            return case.send(chat, text, **kwargs)
        monkeypatch.setattr(telegram, 'send_message', fail_auto_text)
    ready = telegram._ready_ticket_for_distribution
    def check_context_before_distribution(db, task, button):
        request = db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == task.id,
                                                       TicketMessage.message_type == 'client'))
        assert request is not None
        if not real_client:
            assert request.status_note.startswith('automation_fallback:')
        return ready(db, task, button)
    monkeypatch.setattr(telegram, '_ready_ticket_for_distribution', check_context_before_distribution)
    payload, result = select_button(case)
    assert result['ticket_id'] == case.task.id and result['workflow_type'] == 'task'
    case.db.refresh(case.task)
    assert (case.task.workflow_type, case.task.task_kind, case.task.intake_state) == ('task', 'normal', 'ready')
    assert case.task.assignee_id == case.agent.id and case.task.status == 'new'
    requests = case.db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == case.task.id,
                                                         TicketMessage.message_type == 'client')).all()
    assert len(requests) == 1
    request, = requests
    expected = 'Настоящее описание клиента' if real_client else 'Запрос по кнопке: Материал'
    assert request.body == expected
    assert request.telegram_message_id == ('501' if real_client else None)
    if real_client:
        assert request.id == original.id and request.status_note is None
    system = case.db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == case.task.id,
                                                        TicketMessage.message_type == 'system')).all()
    context, = [m.body for m in system if 'Исходные данные автоматизации:' in m.body]
    assert '"workflow_type": "auto"' in context and case.row.button_key in context
    assert case.row.automation_key in context and 'не выполнена:' in context
    case.employee.assert_called_once()
    assert case.employee.call_args.args[0] == 'worker-chat'
    assert 'задача' in case.employee.call_args.args[1].lower()
    assert 'Материал' in case.employee.call_args.args[1]
    case.assignee_notice.assert_not_called()
    assert any('передан' in text.lower() for _, text, _ in case.sent)
    assert telegram.process_telegram_update(case.db, payload, notify_client=True) == {'ok': True, 'duplicate': True}
    assert len(case.db.scalars(select(Ticket)).all()) == 1
    assert len(case.db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all()) == 1
    case.employee.assert_called_once()
    card = case.http.get(f'/api/v1/tickets/{case.task.id}')
    assert card.status_code == 200, card.text
    visible, = [m for m in card.json()['messages'] if m['message_type'] == 'client']
    assert visible['body'] == expected and visible['telegram_message_id'] == request.telegram_message_id
    assert visible['status_note'] == request.status_note
    before_reply = len(case.sent)
    for text, filename in [('Intermediate answer', 'early.pdf'), ('Final answer', 'final.pdf')]:
        response = case.http.post(f'/api/v1/tickets/{case.task.id}/reply',
            data={'text': text}, files={'files': (filename, filename.encode(), 'application/pdf')})
        assert response.status_code == 200, response.text
    assert len(case.sent) == before_reply and case.files == []
    response = case.http.post(f'/api/v1/tickets/{case.task.id}/close')
    assert response.status_code == 200, response.text
    final_messages = case.sent[before_reply:]
    assert len(final_messages) == 1
    assert expected in final_messages[0][1] and 'Final answer' in final_messages[0][1]
    assert 'Intermediate answer' not in final_messages[0][1]
    assert [(body, kw['filename']) for _, body, kw in case.files] == [(b'final.pdf', 'final.pdf')]
    labels = [b['text'] for row in final_messages[0][2]['reply_markup']['inline_keyboard'] for b in row]
    assert labels == ['Предложения и замечания']
    assert not case.db.scalars(select(TicketReviewCycle)).all()
    history = case.http.get('/api/v1/tickets/closed')
    assert history.status_code == 200, history.text
    assert case.task.id in history.text
    saved_card = case.http.get(f'/api/v1/tickets/{case.task.id}')
    assert saved_card.status_code == 200
    assert any(m['id'] == request.id and m['body'] == expected for m in saved_card.json()['messages'])


def test_fallback_without_online_staff_keeps_request_and_removes_close_button(case):
    case.agent.availability_status = 'offline'
    case.row.automation_key = 'unavailable-automation'
    case.db.commit()
    _, result = select_button(case)
    assert result['workflow_type'] == 'task' and result['status'] == 'unassigned'
    assert case.task.client_close_message_id is None
    assert case.task.assignee_id is None
    assert tickets._first_client_message(case.db, case.task).body == 'Запрос по кнопке: Материал'
    case.employee.assert_not_called()


def test_successful_auto_does_not_create_client_description_or_assignment(case):
    _, result = select_button(case)
    assert result['workflow_type'] == 'auto'
    case.db.refresh(case.task)
    assert case.task.workflow_type == 'auto' and case.task.status == 'closed'
    assert case.task.assignee_id is None
    assert tickets._first_client_message(case.db, case.task) is None
    case.employee.assert_not_called()
