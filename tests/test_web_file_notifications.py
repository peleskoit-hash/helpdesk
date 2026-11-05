"""Metadata-only rejected-file notices through real WEB authorization."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.models import (AutomaticMessageTemplate, Ticket, TicketAttachment, TicketAttachmentPreload,
                            TicketNumberCounter, WebChatMessage, WebClientDraft)
from api.app.routers.web_chat import router
from api.app.services import web_auth, web_chat
from test_web_tasks import case, engine, ORIGIN

BASE = '/api/v1/web/chat/attachment-too-large'
LIMIT = 20*1024*1024


@pytest.fixture
def notices(case, monkeypatch):
    case.http.app.include_router(router)
    from api.app.services import telegram_bot, telegram_attachment_policy
    def forbidden(*args, **kw):
        pytest.fail('A WEB notice must never call Telegram')
    monkeypatch.setattr(telegram_bot, 'urlopen', forbidden)
    monkeypatch.setattr(telegram_attachment_policy, 'send_message', forbidden)
    with Session(case.engine) as db:
        db.add(AutomaticMessageTemplate(id='oversize-template', key='telegram.attachment.too_large',
            body='<b>DB notice</b>: {{file_name}} — <i>{{file_size}}</i> КиБ &amp; end',
            is_overridden=True, html_enabled=True))
        db.commit()
    return case


def post(case, event_id=None, name='large.mp4', size=LIMIT+1, **extra):
    return case.http.post(BASE, headers=ORIGIN, json={
        'file_name': name, 'file_size': size, 'event_id': str(event_id or uuid4()), **extra})


def chat(case):
    return case.http.get('/api/v1/web/chat').json()['items']


def test_actual_template_kib_plain_text_persistence_and_no_file_entities(notices):
    first = post(notices, name='Видео & тест.mp4')
    assert first.status_code == 200 and first.json()['ok'] is True, first.text
    assert first.headers['cache-control'] == 'no-store'
    expected = 'DB notice: Видео & тест.mp4 — 20481 КиБ & end'
    assert chat(notices)[0]['text'] == expected
    assert chat(notices)[0]['author'] == 'Elite Sochi Support'
    assert chat(notices)[0]['message_type'] == 'bot'
    notices.engine.dispose()
    notices.http.cookies.clear()
    assert notices.http.get('/api/v1/web/chat').status_code == 401
    notices.http.cookies.set('esdesk_web_session', 'token-client')
    assert chat(notices)[0]['text'] == expected
    with Session(notices.engine) as db:
        for model in (Ticket, TicketAttachment, TicketAttachmentPreload, TicketNumberCounter, WebClientDraft):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.get(AutomaticMessageTemplate, 'oversize-template').body.startswith('<b>DB notice</b>')
        db.get(AutomaticMessageTemplate, 'oversize-template').body = 'Changed {{file_name}}: {{file_size}}'
        db.commit()
    assert post(notices, name='other.zip', size=28*1024*1024).status_code == 200
    assert chat(notices)[1]['text'] == 'Changed other.zip: 28672'


def test_idempotency_and_separate_attempts(notices):
    key = uuid4()
    first = post(notices, key)
    assert post(notices, key).json() == first.json()
    assert post(notices).status_code == 200
    assert len(chat(notices)) == 2
    assert len({row['id'] for row in chat(notices)}) == 2


@pytest.mark.parametrize('changes', [{'size':LIMIT}, {'size':0}, {'size':-1}, {'size':True}, {'size':float(LIMIT+1)},
    {'size':9007199254740992}, {'name':''}, {'name':'a'*256}, {'name':'bad\nfile'}, {'client_id':'peer'},
    {'text':'arbitrary message'}, {'event_id':'not-a-uuid'}])
def test_invalid_metadata_does_not_create_a_message(notices, changes):
    assert post(notices, **changes).status_code in {400,422}
    assert chat(notices) == []


def test_auth_csrf_and_client_isolation(notices):
    key = uuid4()
    assert post(notices, key).status_code == 200
    notices.http.cookies.set('esdesk_web_session','token-peer')
    assert chat(notices) == []
    assert post(notices, key).status_code == 200  # Same event UUID is scoped to its actual owner.
    assert len(chat(notices)) == 1
    notices.http.cookies.set('esdesk_web_session','token-client')
    assert len(chat(notices)) == 1
    assert notices.http.post(BASE, json={'file_name':'big','file_size':LIMIT+1,'event_id':str(uuid4())}).status_code == 403
    notices.http.cookies.clear()
    assert post(notices).status_code == 401


def test_ordinary_chat_messages_do_not_consume_notice_limit(notices):
    with Session(notices.engine) as db:
        for index in range(24):
            message_type = ('client', 'system', 'bot')[index % 3]
            # Include unkeyed messages and keys containing (but not starting with) the notice prefix.
            event_key = None if index % 2 else f'other-{web_chat.FILE_SIZE_NOTICE_PREFIX}{index}'
            web_chat.record_message(db, 'client', message_type=message_type,
                author='Client' if message_type == 'client' else 'Elite Sochi Support',
                text=f'Ordinary message {index}', created_at=notices.clock[0], event_key=event_key)
        db.commit()
    assert post(notices).status_code == 200
    assert len(chat(notices)) == 25


def test_rate_limit_and_retry_are_transactional(notices):
    keys = [uuid4() for _ in range(20)]
    for key in keys:
        assert post(notices, key).status_code == 200
    repeated = post(notices, keys[-1])
    assert repeated.status_code == 200
    assert repeated.json()['message_id'] == chat(notices)[-1]['id']
    limited = post(notices)
    assert limited.status_code == 429 and limited.headers['retry-after'] == '60'
    assert len(chat(notices)) == 20
    notices.http.cookies.set('esdesk_web_session', 'token-peer')
    assert post(notices).status_code == 200
    assert len(chat(notices)) == 1
    notices.http.cookies.set('esdesk_web_session', 'token-client')
    assert post(notices).status_code == 429
    notices.clock[0] += timedelta(seconds=61)
    assert post(notices).status_code == 200


def test_parallel_retries_create_one_message(notices):
    key, barrier = uuid4(), Barrier(4)
    def send():
        barrier.wait(timeout=10)
        return post(notices, key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: send(), range(4)))
    assert all(reply.status_code == 200 for reply in replies)
    assert len({reply.json()['message_id'] for reply in replies}) == 1
    assert len(chat(notices)) == 1


def test_parallel_new_events_cannot_bypass_rate_limit(notices):
    for _ in range(19):
        assert post(notices).status_code == 200
    barrier = Barrier(4)
    def send():
        barrier.wait(timeout=10)
        return post(notices).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: send(), range(4)))
    assert statuses.count(200) == 1 and statuses.count(429) == 3
    assert len(chat(notices)) == 20
