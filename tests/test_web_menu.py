"""Isolated menu checks: synthetic SQLite data, no DEV/PROD DB or Telegram calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from api.app.db import Base, get_db
from api.app.models import (BotButtonSetting, BotMenuDestinationBinding, BotMenuLevelLayout,
                            BotMenuProfile, Client, ClientProfile, Company, TelegramContact, WebClientSession)
from api.app.routers.web_menu import router
from api.app.routers import telegram
from api.app.services import web_auth
from api.app.services.button_config import build_level_layout_key
from api.app.services.web_menu import menu_name_for_client, read_client_menu


@pytest.fixture
def case(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'menu.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(web_auth, 'utcnow', lambda: now)
    monkeypatch.setattr(telegram, 'send_message', lambda *a, **kw: pytest.fail('Telegram must not be called'))
    with Session(engine) as db:
        db.add(Company(id='co', name='Synthetic'))
        db.flush()
        db.add(Client(id='client', company_id='co', display_name='Synthetic'))
        db.flush()
        db.add_all([
            ClientProfile(id='profile', client_id='client', is_verified=True, destination='Sales',
                          web_login_normalized='synthetic', web_password_hash='test-only', web_credentials_version=1),
            TelegramContact(id='tg', client_id='client', company_id='co', external_user_id='123', external_chat_id='123'),
            WebClientSession(token_hash=web_auth.token_hash('menu-token'), client_id='client', credentials_version=1,
                             created_at=now, last_activity_at=now, normal_expires_at=now + timedelta(hours=1)),
        ])
        db.commit()
    app = FastAPI()
    app.include_router(router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as http, Session(engine) as db:
        yield SimpleNamespace(engine=engine, db=db, http=http, now=now)
    engine.dispose()


def button(db, key, **changes):
    values = dict(id=key, button_key=key, menu_type='Базовое', button_path=key, button_label=key,
                  parent_path='', button_kind='terminal', workflow_type='ticket', created_at=datetime(2026, 1, 1),
                  updated_at=datetime(2026, 1, 1))
    values.update(changes)
    row = BotButtonSetting(**values)
    db.add(row)
    db.commit()
    return row


def test_access_and_no_writes_or_session_extension(case):
    button(case.db, 'public', instruction_text='PRIVATE', automation_key='PRIVATE', files_json=[{'file': 'PRIVATE'}])
    assert case.http.get('/api/v1/web/menu').status_code == 401
    case.http.cookies.set('esdesk_session', 'staff-cookie')
    assert case.http.get('/api/v1/web/menu').status_code == 401
    case.http.cookies.set('esdesk_web_session', 'menu-token')
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(case.engine, 'before_cursor_execute', record)
    try:
        response = case.http.get('/api/v1/web/menu?client_id=someone-else&menu_type=Secret')
        again = case.http.get('/api/v1/web/menu')
    finally:
        event.remove(case.engine, 'before_cursor_execute', record)
    assert response.status_code == again.status_code == 200
    assert response.json() == again.json()
    assert response.headers['cache-control'] == 'no-store'
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    assert 'PRIVATE' not in response.text
    assert set(response.json()) == {'menu_type', 'rows'}
    assert set(response.json()['rows'][0][0]) == {'id', 'label', 'style', 'children'}
    session = case.db.scalar(select(WebClientSession))
    assert web_auth.as_utc(session.last_activity_at) == case.now
    assert case.http.post('/api/v1/web/menu', headers={'Origin': 'http://evil.example'}).status_code == 405


@pytest.mark.parametrize('field,value', [('credentials_version', 0), ('normal_expires_at', datetime(2020, 1, 1))])
def test_invalid_session_is_rejected(case, field, value):
    session = case.db.scalar(select(WebClientSession))
    setattr(session, field, value)
    case.db.commit()
    case.http.cookies.set('esdesk_web_session', 'menu-token')
    assert case.http.get('/api/v1/web/menu').status_code == 401


@pytest.mark.parametrize('verified,destination,temporary,binding,active,expected', [
    (False, 'Sales', False, 'Special', True, 'Базовое'),
    (True, None, False, None, True, 'Базовое'),
    (True, 'Unknown', False, None, True, 'Базовое'),
    (True, ' Sales ', False, 'Special', True, 'Special'),
    (True, 'Sales', False, None, True, 'Sales'),
    (True, 'Sales', False, 'Special', False, 'Sales'),
    (True, 'Sales', True, 'Special', True, 'Special'),
])
def test_profile_matches_telegram_without_contact(case, verified, destination, temporary, binding, active, expected):
    profile = case.db.get(ClientProfile, 'profile')
    profile.is_verified = verified
    profile.destination = destination
    profile.custom_fields = {'custom_vremennyy_rop': temporary}
    case.db.delete(case.db.get(TelegramContact, 'tg'))
    if binding:
        case.db.add(BotMenuDestinationBinding(id='binding', destination_value='Руководство' if temporary else 'Sales', menu_type=binding))
        case.db.add(BotMenuProfile(id='special', menu_type='Special', display_name='Special', is_active=active))
    case.db.commit()
    button(case.db, 'sales', menu_type='Sales')
    assert menu_name_for_client(case.db, 'client') == expected
    assert menu_name_for_client(case.db, 'client') == telegram._menu_name_for_contact(case.db, SimpleNamespace(client_id='client'))
    assert menu_name_for_client(case.db, 'missing-profile') == 'Базовое'


def test_tree_visibility_order_colors_and_telegram_parity(case, monkeypatch):
    button(case.db, 'a', button_label='Original', label_overridden=True, label_override='Renamed', button_style=' PRIMARY ')
    button(case.db, 'b', button_style='success', is_enabled=False)  # No visibility override: still visible.
    button(case.db, 'hidden', visibility_overridden=True, is_enabled=False)
    button(case.db, 'archived', is_archived=True)
    button(case.db, 'absent', currently_present=False)
    button(case.db, 'section', button_kind='section')
    button(case.db, 'child', parent_path='section', button_path='section/child', button_style='danger')
    button(case.db, 'empty', button_kind='group_parent')
    button(case.db, 'hidden-child', parent_path='empty', visibility_overridden=True, is_enabled=False)
    button(case.db, 'group', button_kind='section', workflow_type='group_task')
    button(case.db, 'group-child', parent_path='group')
    button(case.db, 'hidden-parent', button_kind='section', visibility_overridden=True, is_enabled=False)
    button(case.db, 'unreachable', parent_path='hidden-parent')
    button(case.db, 'invalid-color', button_style='url(evil)', label_overridden=False, label_override='Not shown')
    button(case.db, 'other', menu_type='Other')
    case.db.add(BotMenuLevelLayout(id='layout', menu_type='Базовое', level_key=build_level_layout_key('Базовое'),
        layout_json=[{'columns': 2, 'button_keys': ['b', 'a', 'hidden']}, {'columns': 1, 'button_keys': ['section']},
                     {'button_keys': ['b', 'nonexistent', 'group']}]))
    case.db.commit()
    result = read_client_menu(case.db, 'client')
    rows = result['rows']
    assert [[item['id'] for item in row] for row in rows] == [['b', 'a'], ['section'], ['group'], ['invalid-color']]
    assert rows[0][1]['label'] == 'Renamed'
    assert [item['style'] for item in rows[0]] == ['success', 'primary']
    assert rows[1][0]['children'][0][0]['style'] == 'danger'
    assert rows[2][0]['children'] is None
    assert rows[3][0]['style'] is None and rows[3][0]['label'] == 'invalid-color'
    case.http.cookies.set('esdesk_web_session', 'menu-token')
    assert case.http.get('/api/v1/web/menu').json() == result
    sent = []
    monkeypatch.setattr(telegram, 'send_message', lambda *a, **kw: sent.append(kw['reply_markup']['inline_keyboard']))
    for menu_key, web_rows in [(None, rows), ('section', rows[1][0]['children'])]:
        telegram._send_problem_menu_db_only(case.db, 'synthetic', menu_name='Базовое', menu_key=menu_key)
        expected = [[(b['text'], b.get('style')) for b in row] for row in sent[-1] if row[0]['text'] != '⬅ Назад']
        assert [[(b['label'], b['style']) for b in row] for row in web_rows] == expected


def test_empty_menu(case):
    assert read_client_menu(case.db, 'client') == {'menu_type': 'Базовое', 'rows': []}
