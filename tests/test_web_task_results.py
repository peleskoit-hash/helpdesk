"""Result publication through the real staff close path, on isolated test DBs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from api.app.auth import get_current_user
from api.app.config import settings
from api.app.db import get_db
from api.app.models import (Company, Client, ClientProfile, TelegramContact, Ticket, TicketMessage,
                            TicketAttachment, User, WebClientSession, WebTaskResult, WebTaskResultFile)
from api.app.routers import tickets
from api.app.routers.web_task_results import router
from api.app.services import web_auth, web_task_results, schema_evolution
from api.app.services.ticket_lifecycle import close_ticket_by_actor
from test_web_auth import engine

BASE = '/api/v1/web/task-results'
ORIGIN = {'Origin': 'http://testserver'}


@pytest.fixture
def case(engine, tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 30, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(web_auth, 'utcnow', lambda: clock[0])
    monkeypatch.setattr(settings, 'web_public_base_url', 'http://testserver')
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', '')
    directory = tmp_path / 'attachments'
    directory.mkdir()
    monkeypatch.setattr(settings, 'attachment_storage_dir', str(directory))
    forbidden = Mock(side_effect=AssertionError('WEB results must not send Telegram client messages'))
    monkeypatch.setattr(tickets, '_send_to_telegram_client', forbidden)
    monkeypatch.setattr(tickets, '_send_attachment_to_telegram_client', forbidden)
    with Session(engine) as db:
        db.add_all([Company(id='co', name='Synthetic'), Company(id='other', name='Synthetic other')])
        db.flush()
        for client, company in [('client','co'),('peer','co'),('foreign','other')]:
            db.add(Client(id=client, company_id=company, display_name=client))
            db.flush()
            db.add_all([
                ClientProfile(id=f'profile-{client}', client_id=client, is_verified=True, web_login_normalized=client,
                              web_password_hash='synthetic', web_credentials_version=1),
                TelegramContact(id=f'contact-{client}', client_id=client, company_id=company,
                                external_user_id=client, external_chat_id=client),
                WebClientSession(token_hash=web_auth.token_hash(client), client_id=client, credentials_version=1,
                    created_at=clock[0], last_activity_at=clock[0], normal_expires_at=clock[0]+timedelta(hours=2)),
            ])
        db.add(User(id='agent', company_id='co', role='agent', full_name='Synthetic agent', login='synthetic-agent',
                    is_active=True, availability_status='online'))
        db.commit()
    actor = SimpleNamespace(id='agent', company_id='co', role='agent', full_name='Synthetic agent')
    app = FastAPI()
    app.include_router(router)
    app.include_router(tickets.router)
    app.dependency_overrides[get_current_user] = lambda: actor
    def session():
        with Session(engine, autoflush=False) as db:
            yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app) as http:
        http.cookies.set('esdesk_web_session', 'client')
        yield SimpleNamespace(engine=engine, http=http, directory=directory, clock=clock, actor=actor)
    forbidden.assert_not_called()


def seed_task(case, **changes):
    values = dict(id=str(uuid4()), company_id='co', client_id='client', channel='web', workflow_type='task',
                  task_kind='normal', intake_state='ready', status='pending', subject='Synthetic task', assignee_id='agent')
    values.update(changes)
    with Session(case.engine) as db:
        task = Ticket(**values)
        db.add(task)
        db.flush()
        db.add(TicketMessage(id=str(uuid4()), ticket_id=task.id, message_type='client', body='Description'))
        db.commit()
    return values['id']


def message(case, task_id, body, *, kind='agent', status='normal', sequence=1):
    msg_id = str(uuid4())
    with Session(case.engine) as db:
        db.add(TicketMessage(id=msg_id, ticket_id=task_id, body=body, message_type=kind,
                            message_status=status, author_user_id='agent',
                            created_at=case.clock[0]+timedelta(seconds=sequence)))
        db.commit()
    return msg_id


def file(case, task_id, message_id, filename, data=b'content'):
    attachment_id = str(uuid4())
    path = case.directory / attachment_id
    path.write_bytes(data)
    with Session(case.engine) as db:
        db.add(TicketAttachment(id=attachment_id, ticket_id=task_id, message_id=message_id, source='upload',
            local_path=str(path), original_filename=filename, mime_type='application/pdf', file_size=len(data),
            telegram_file_id='PRIVATE-TELEGRAM-ID', telegram_file_path='PRIVATE-REMOTE-PATH'))
        db.commit()
    return attachment_id, path


def close(case, task_id):
    return case.http.post(f'/api/v1/tickets/{task_id}/close')


def close_in_transaction(db, task_id, *, force=False, skip=False):
    task = db.get(Ticket,task_id)
    return close_ticket_by_actor(db, task, SimpleNamespace(id='agent', role='admin' if force else 'agent'),
        force_without_agent_reply_for_task=force, skip_client_notification=skip,
        is_admin_fn=lambda actor: actor.role == 'admin', get_or_create_client_profile_fn=lambda *a: None,
        last_agent_message_fn=tickets._last_agent_message, first_client_message_fn=tickets._first_client_message,
        send_to_telegram_client_fn=lambda *a, **kw: pytest.fail('No WEB Telegram delivery'),
        actor_display_name_fn=lambda *a: 'Synthetic agent', add_message_fn=tickets._add_message,
        message_attachments_fn=tickets._message_attachments,
        send_attachment_to_telegram_client_fn=lambda *a, **kw: pytest.fail('No WEB Telegram delivery'),
        onsite_close_reminder_text='onsite', ticket_closed_client_text='closed')


def test_last_agent_only_and_immutable_snapshot_survives_connection_restart(case):
    task_id = seed_task(case)
    early = message(case,task_id,'Intermediate',sequence=1)
    file(case,task_id,early,'early.pdf',b'early')
    internal = message(case,task_id,'PRIVATE INTERNAL',kind='internal',sequence=2)
    file(case,task_id,internal,'internal.pdf',b'private')
    final = message(case,task_id,'Final answer',sequence=3)
    _, path = file(case,task_id,final,'Ответ.pdf',b'final-file')
    file(case,task_id,final,'Second.pdf',b'final-two')
    message(case,task_id,'Deleted later reply',status='deleted',sequence=4)
    message(case,task_id,'PRIVATE SYSTEM',kind='system',sequence=5)
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404
    assert case.http.get(BASE).json()['unread_count'] == 0
    response = close(case,task_id)
    assert response.status_code == 200, response.text
    result = case.http.get(f'{BASE}/{task_id}')
    assert result.status_code == 200
    data = result.json()
    assert data['text'] == 'Final answer' and data['message_id'] == final
    assert [f['filename'] for f in data['files']] == ['Ответ.pdf','Second.pdf']
    assert data['read_at'] is None
    assert all(secret not in result.text for secret in ['PRIVATE', 'local_path', str(case.directory), 'telegram_file', 'Intermediate'])
    downloaded = case.http.get(data['files'][0]['download_url'])
    assert downloaded.content == b'final-file'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert downloaded.headers['x-content-type-options'] == 'nosniff'
    assert downloaded.headers['cache-control'] == 'no-store'
    with Session(case.engine) as db:
        task = db.get(Ticket,task_id)
        assert web_auth.as_utc(task.closed_at) == web_auth.as_utc(db.get(WebTaskResult,task_id).closed_at)
        db.get(TicketMessage,final).body = 'Changed after close'
        db.get(TicketMessage,final).message_status = 'deleted'
        db.execute(delete(TicketAttachment).where(TicketAttachment.message_id == final))
        db.commit()
    path.unlink()
    case.engine.dispose()  # New connections must read PostgreSQL/SQLite, not Python memory.
    assert case.http.get(f'{BASE}/{task_id}').json() == data
    assert case.http.get(data['files'][0]['download_url']).content == b'final-file'
    assert close(case,task_id).status_code == 409
    with Session(case.engine) as db:
        assert len(db.scalars(select(WebTaskResult)).all()) == 1
        assert len(db.scalars(select(WebTaskResultFile)).all()) == 2


def test_files_only_answer(case):
    task_id = seed_task(case)
    final = message(case,task_id,'')
    file(case,task_id,final,'only.pdf',b'only-file')
    assert close(case,task_id).status_code == 200
    data = case.http.get(f'{BASE}/{task_id}').json()
    assert data['text'] == '' and len(data['files']) == 1
    assert case.http.get(data['files'][0]['download_url']).content == b'only-file'


def test_internal_comment_cannot_replace_required_agent_answer(case):
    task_id = seed_task(case)
    internal = message(case, task_id, 'Private comment', kind='internal')
    file(case, task_id, internal, 'private.pdf')
    assert close(case, task_id).status_code == 409
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404


def test_internal_task_keeps_existing_completion_without_client_publication(case, monkeypatch):
    from api.app.services import ticket_lifecycle

    task_id = seed_task(case)
    message(case, task_id, 'Internal task result')
    notification = Mock()
    monkeypatch.setattr(ticket_lifecycle, 'is_internal_task_ticket', lambda *_: True)
    monkeypatch.setattr(ticket_lifecycle, 'schedule_internal_task_completion_notification', notification)
    assert close(case, task_id).status_code == 200
    notification.assert_called_once()
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404


@pytest.mark.parametrize('changes', [
    {'channel': 'telegram'}, {'workflow_type': 'auto'}, {'workflow_type': 'ticket'},
    {'task_kind': 'group_parent'}, {'task_kind': 'group_child'}, {'status': 'pending'},
])
def test_publisher_excludes_other_scenarios(case, changes):
    values = dict(status='closed', closed_at=case.clock[0])
    values.update(changes)
    task_id = seed_task(case, **values)
    final = message(case, task_id, 'Answer')
    with Session(case.engine) as db:
        web_task_results.publish_result(db, db.get(Ticket, task_id), db.get(TicketMessage, final), [])
        db.commit()
        assert db.get(WebTaskResult, task_id) is None


def test_rollback_removes_publication_and_blob_together(case):
    task_id = seed_task(case)
    final = message(case,task_id,'Rollback answer')
    file(case,task_id,final,'rollback.pdf')
    with Session(case.engine) as db:
        assert close_in_transaction(db,task_id) == 'closed'
        db.flush()
        assert db.get(WebTaskResult,task_id) is not None
        assert case.http.get(f'{BASE}/{task_id}').status_code == 404
        db.rollback()
    with Session(case.engine) as db:
        assert db.get(Ticket,task_id).status == 'pending'
        assert db.get(WebTaskResult,task_id) is None
        assert not db.scalars(select(WebTaskResultFile)).all()
    assert close(case,task_id).status_code == 200


def test_read_count_and_idempotent_read_are_scoped_to_session(case):
    task_id = seed_task(case)
    message(case,task_id,'Answer')
    assert close(case,task_id).status_code == 200
    page = case.http.get(BASE+'?client_id=foreign&company_id=other&limit=1').json()
    assert page['total'] == page['unread_count'] == 1 and len(page['items']) == 1
    assert case.http.get(f'{BASE}/{task_id}').json()['read_at'] is None
    assert case.http.post(f'{BASE}/{task_id}/read').status_code == 403
    first = case.http.post(f'{BASE}/{task_id}/read', headers=ORIGIN)
    assert first.status_code == 200
    case.clock[0] += timedelta(minutes=1)
    assert case.http.post(f'{BASE}/{task_id}/read', headers=ORIGIN).json() == first.json()
    assert case.http.get(BASE).json()['unread_count'] == 0
    assert case.http.get(BASE+'?limit=101').status_code == 422


@pytest.mark.parametrize('viewer',['peer','foreign',None])
def test_other_clients_and_anonymous_cannot_read_or_download(case,viewer):
    task_id = seed_task(case)
    final = message(case,task_id,'Private answer')
    file(case,task_id,final,'private.pdf')
    assert close(case,task_id).status_code == 200
    data = case.http.get(f'{BASE}/{task_id}').json()
    case.http.cookies.clear()
    if viewer:
        case.http.cookies.set('esdesk_web_session',viewer)
    expected = 404 if viewer else 401
    assert case.http.get(f'{BASE}/{task_id}').status_code == expected
    assert case.http.get(data['files'][0]['download_url']).status_code == expected
    assert case.http.post(f'{BASE}/{task_id}/read',headers=ORIGIN).status_code == expected
    if viewer:
        assert case.http.get(BASE).json() == {'items':[],'total':0,'unread_count':0}


def test_attachment_id_must_belong_to_this_publication(case):
    ids=[]
    for _ in range(2):
        task_id=seed_task(case)
        final=message(case,task_id,'Answer')
        original_id,_=file(case,task_id,final,'result.pdf')
        assert close(case,task_id).status_code == 200
        ids.append(case.http.get(f'{BASE}/{task_id}').json())
    assert case.http.get(f"{BASE}/{ids[0]['task_id']}/files/{ids[1]['files'][0]['id']}").status_code == 404
    assert case.http.get(f"{BASE}/{ids[1]['task_id']}/files/{original_id}").status_code == 404


@pytest.mark.parametrize('suppress,force,skip,answer',[(True,False,False,True),(False,True,True,True),(False,True,False,False)])
def test_suppressed_and_forced_without_answer_never_invent_results(case,suppress,force,skip,answer):
    task_id=seed_task(case,suppress_client_answer=suppress)
    if answer:
        message(case,task_id,'Answer')
    with Session(case.engine) as db:
        assert close_in_transaction(db,task_id,force=force,skip=skip) == 'closed'
        db.commit()
    assert case.http.get(f'{BASE}/{task_id}').status_code == 404
    assert case.http.get(BASE).json()['unread_count'] == 0


def test_missing_file_rolls_back_close_without_exposing_storage(case):
    task_id=seed_task(case)
    final=message(case,task_id,'Answer')
    _,path=file(case,task_id,final,'missing.pdf')
    path.unlink()
    # Never contact Telegram in this test: the existing resolver reports an unavailable file.
    from unittest.mock import patch
    with patch('api.app.services.web_task_results._ensure_local_attachment_file',side_effect=OSError('PRIVATE-TOKEN')):
        response=close(case,task_id)
    assert response.status_code == 409
    assert 'PRIVATE-TOKEN' not in response.text and str(path) not in response.text
    assert case.http.get(BASE).json()['total'] == 0
    with Session(case.engine) as db:
        assert db.get(Ticket,task_id).status == 'pending'


def test_schema_evolution_is_additive_and_idempotent(case,monkeypatch):
    WebTaskResultFile.__table__.drop(case.engine)
    WebTaskResult.__table__.drop(case.engine)
    monkeypatch.setattr(schema_evolution,'engine',case.engine)
    schema_evolution.ensure_web_task_results_tables()
    schema_evolution.ensure_web_task_results_tables()
    info=inspect(case.engine)
    assert {'web_task_results','web_task_result_files','tickets','ticket_messages'}.issubset(info.get_table_names())
    assert info.get_pk_constraint('web_task_results')['constrained_columns'] == ['task_id']
