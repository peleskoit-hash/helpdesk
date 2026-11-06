"""Technical chat persistence and HTTP checks on isolated SQLite/PostgreSQL schemas."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.app.models import Client, Company, Ticket, TicketMessage, WebChatMessage
from api.app.routers.web_chat import router
from api.app.routers.web_history import router as history_router
from api.app.services import schema_evolution, web_chat
from test_web_auth import case, engine, login, LOGOUT, ORIGIN

BASE = '/api/v1/web/chat'


@pytest.fixture
def chat(case):
    case.app.include_router(router)
    case.app.include_router(history_router)
    with Session(case.engine) as db:
        db.add(Company(id='other', name='Other'))
        db.flush()
        db.add_all([Client(id='peer', company_id='company', display_name='Peer'),
                    Client(id='foreign', company_id='other', display_name='Foreign')])
        db.commit()
    assert login(case).status_code == 200
    return case


def write(case, client_id='client', **changes):
    values = dict(message_type='system', author='ES DESK+', text='Technical event', created_at=case.clock[0])
    values.update(changes)
    with Session(case.engine) as db:
        row = web_chat.record_message(db, client_id, **values)
        message_id = str(row.id)
        db.commit()
    return message_id


def test_persistence_order_pagination_and_relogin(chat):
    later = write(chat, created_at=chat.clock[0] + timedelta(seconds=1))
    first = write(chat, text='Первое сообщение', message_type='client', author='Вы')
    second = write(chat, text='Bot', message_type='bot')
    # Reopen pooled connections as well as each service/HTTP session.
    chat.engine.dispose()
    response = chat.http.get(BASE + '?page_size=2')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert (data['total'], data['page'], data['page_size']) == (3, 1, 2)
    assert [row['id'] for row in data['items']] == [first, second]
    assert data['items'][0]['text'] == 'Первое сообщение'
    assert set(data['items'][0]) == {'id', 'created_at', 'message_type', 'author', 'text'}
    assert data['items'][0]['created_at'].endswith('Z')
    assert [row['id'] for row in chat.http.get(BASE + '?page=2&page_size=2').json()['items']] == [later]
    assert chat.http.get(BASE + '?page=3&page_size=2').json()['items'] == []
    assert chat.http.get(BASE).json()['page_size'] == 50
    for query in ['page=0', 'page=x', 'page_size=0', 'page_size=101']:
        assert chat.http.get(BASE + '?' + query).status_code == 422
    assert chat.http.post(LOGOUT, headers=ORIGIN).status_code == 204
    assert chat.http.get(BASE).status_code == 401
    assert login(chat).status_code == 200
    assert [row['id'] for row in chat.http.get(BASE).json()['items']] == [first, second, later]


def test_owner_isolation_and_ticket_history_unchanged(chat):
    own = write(chat)
    write(chat, 'peer', text='PRIVATE peer')
    write(chat, 'foreign', text='PRIVATE foreign')
    response = chat.http.get(BASE + '?client_id=foreign&company_id=other')
    assert [row['id'] for row in response.json()['items']] == [own]
    assert response.json()['total'] == 1 and 'PRIVATE' not in response.text
    with Session(chat.engine) as db:
        for client_id in ['peer', 'foreign']:
            page = web_chat.list_messages(db, SimpleNamespace(id=client_id))
            assert page['total'] == 1 and page['items'][0]['text'] == 'PRIVATE ' + client_id
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.scalar(select(func.count()).select_from(TicketMessage)) == 0
    assert chat.http.get('/api/v1/web/history').json()['total'] == 0
    assert chat.http.post(BASE, json={'message_type': 'system', 'text': 'Injected'}).status_code == 405
    chat.http.cookies.clear()
    assert chat.http.get(BASE).status_code == 401


def test_event_key_is_per_client_optional_and_first_write_wins(chat):
    first = write(chat, event_key='event-1', text='Original')
    assert write(chat, event_key='event-1', text='Changed', created_at=chat.clock[0] + timedelta(days=1)) == first
    assert write(chat, 'peer', event_key='event-1') != first
    assert write(chat) != write(chat)
    rows = chat.http.get(BASE).json()['items']
    assert len(rows) == 3 and rows[0]['text'] == 'Original'
    assert 'event_key' not in rows[0]
    with Session(chat.engine) as db:
        with pytest.raises(IntegrityError):
            db.add(WebChatMessage(client_id='client', event_key='event-1', message_type='system',
                                  author='ES DESK+', text='Direct duplicate', created_at=chat.clock[0]))
            db.commit()


def test_concurrent_retries_and_caller_rollback(chat):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: write(chat, event_key='concurrent'), range(4)))
    assert len(set(ids)) == 1
    with Session(chat.engine) as db:
        web_chat.record_message(db, 'client', message_type='system', author='ES DESK+', text='Rolled back')
        db.rollback()
    assert chat.http.get(BASE).json()['total'] == 1


def test_retains_last_50_by_event_time_then_id(chat):
    # Insert the newest timestamp first: trimming must use event time, not just ID.
    latest = write(chat, created_at=chat.clock[0] + timedelta(hours=1))
    ids = [write(chat) for _ in range(49)]
    assert chat.http.get(BASE).json()['total'] == 50
    for _ in range(2):
        ids.append(write(chat))
        ids.pop(0)
        page = chat.http.get(BASE).json()
        assert page['total'] == 50
        assert [row['id'] for row in page['items']] == ids + [latest]
    # A late arrival older than all retained events is itself outside the limit.
    write(chat, created_at=chat.clock[0] - timedelta(days=1))
    assert [row['id'] for row in chat.http.get(BASE).json()['items']] == ids + [latest]
    with Session(chat.engine) as db:
        # Eviction also removes cached ORM rows when several writes share a session.
        old = web_chat.record_message(db, 'client', message_type='system', author='ES DESK+',
            text='Too old', created_at=chat.clock[0] - timedelta(days=1))
        kept = web_chat.record_message(db, 'client', message_type='system', author='ES DESK+',
            text='Retained', created_at=chat.clock[0])
        assert old.text == 'Too old' and kept.text == 'Retained'
        db.rollback()


def test_retention_is_per_client_and_rolls_back_with_insert(chat):
    own = [write(chat) for _ in range(50)]
    peer = [write(chat, 'peer') for _ in range(50)]
    foreign = write(chat, 'foreign')
    with Session(chat.engine) as db:
        web_chat.record_message(db, 'client', message_type='system', author='ES DESK+',
                                text='Rolled back', created_at=chat.clock[0])
        assert web_chat.list_messages(db, SimpleNamespace(id='client'))['total'] == 50
        assert db.get(WebChatMessage, int(own[0])) is None
        db.rollback()
    assert [row['id'] for row in chat.http.get(BASE).json()['items']] == own
    added = write(chat)
    assert [row['id'] for row in chat.http.get(BASE).json()['items']] == own[1:] + [added]
    with Session(chat.engine) as db:
        assert [row['id'] for row in web_chat.list_messages(db, SimpleNamespace(id='peer'))['items']] == peer
        assert web_chat.list_messages(db, SimpleNamespace(id='foreign'))['items'][0]['id'] == foreign


def test_existing_event_at_limit_does_not_evict_or_change_original(chat):
    original = write(chat, event_key='original', text='Original')
    for _ in range(49):
        write(chat)
    before = chat.http.get(BASE).json()
    assert write(chat, event_key='original', text='Changed', created_at=chat.clock[0] + timedelta(days=1)) == original
    assert chat.http.get(BASE).json() == before


def test_concurrent_writers_and_retries_at_limit(chat):
    for _ in range(50):
        write(chat)
    barrier = Barrier(8)

    def concurrent_write(index):
        barrier.wait(timeout=10)
        return write(chat, event_key=f'parallel-{index}', created_at=chat.clock[0] + timedelta(seconds=1))

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(concurrent_write, range(8)))
    page = chat.http.get(BASE).json()
    assert page['total'] == 50
    assert set(ids) <= {row['id'] for row in page['items']}

    def retry(_):
        barrier.wait(timeout=10)
        return write(chat, event_key='parallel-0', text='Changed')

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(retry, range(8))) == {ids[0]}
    assert chat.http.get(BASE).json() == page


def test_explicit_migration_idempotent_and_preserves_data(chat, monkeypatch):
    WebChatMessage.__table__.drop(chat.engine)
    monkeypatch.setattr(schema_evolution, 'engine', chat.engine)
    schema_evolution.ensure_web_chat_messages_table()
    saved = write(chat, event_key='preserved')
    schema_evolution.ensure_web_chat_messages_table()
    assert chat.http.get(BASE).json()['items'][0]['id'] == saved
    inspector = inspect(chat.engine)
    assert inspector.get_foreign_keys('web_chat_messages')[0]['referred_table'] == 'clients'
    assert 'ix_web_chat_messages_client_order' in {
        index['name'] for index in inspector.get_indexes('web_chat_messages')}
    with Session(chat.engine) as db:
        with pytest.raises(IntegrityError):
            web_chat.record_message(db, 'missing', message_type='system', author='ES DESK+', text='Orphan')
    assert write(chat, event_key='preserved') == saved


def test_startup_does_not_create_chat_table(chat, monkeypatch):
    from api.app import main

    WebChatMessage.__table__.drop(chat.engine)
    monkeypatch.setattr(main, 'engine', chat.engine)
    monkeypatch.setattr(main.settings.__class__, 'validate_runtime_guards', lambda self: None)

    class StopAfterCreateAll(Exception):
        pass

    def stop():
        raise StopAfterCreateAll

    # Stop immediately after the real startup create_all, before any runtime services.
    monkeypatch.setattr(main, 'ensure_client_profile_columns', stop)
    with pytest.raises(StopAfterCreateAll):
        asyncio.run(main.on_startup())
    assert not inspect(chat.engine).has_table('web_chat_messages')
    assert any(route.path == BASE and 'GET' in route.methods for route in main.app.routes)


@pytest.mark.parametrize('event', ['cancelled', 'expired'])
def test_terminal_task_events_have_no_retention_exception(chat, event):
    ids = [write(chat, event_key=f'web-task:{index}:{event}') for index in range(65)]
    with Session(chat.engine) as db:
        assert db.scalar(select(func.count()).select_from(WebChatMessage).where(WebChatMessage.client_id == 'client')) == 50
    first = chat.http.get(BASE + '?page_size=25').json()
    second = chat.http.get(BASE + '?page=2&page_size=25').json()
    assert first['total'] == second['total'] == 50
    assert [row['id'] for row in first['items'] + second['items']] == ids[-50:]
