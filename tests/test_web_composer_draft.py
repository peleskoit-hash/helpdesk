"""Client-owned drafts through real WEB auth on isolated database fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.app.models import (Client, ClientProfile, Company, TelegramContact, Ticket, TicketNumberCounter,
                            WebClientDraft, WebClientSession)
from api.app.routers.web_composer_draft import router
from api.app.services import schema_evolution, web_auth, web_composer_draft
from test_web_auth import case, engine, login, LOGOUT, ORIGIN, PASSWORD

BASE = '/api/v1/web/composer-draft'


@pytest.fixture
def draft(case):
    case.app.include_router(router)
    assert login(case).status_code == 200
    return case


def put(case, text, version):
    return case.http.put(BASE, json={'text': text, 'version': version}, headers=ORIGIN)


def test_creation_exact_text_empty_and_restore_after_relogin(draft):
    first = draft.http.get(BASE)
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
    original = first.json()
    assert set(original) == {'id', 'text', 'version', 'created_at', 'updated_at'}
    assert UUID(original['id']).version == 4
    assert original['text'] == '' and original['version'] == 0
    assert original['created_at'] == original['updated_at']
    assert draft.http.get(BASE).json() == original
    text = '  Первая строка \r\n\n\tВторая строка 🙂  \n'
    response = put(draft, text, 0)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    saved = response.json()
    assert saved['id'] == original['id'] and saved['text'] == text and saved['version'] == 1
    assert saved['created_at'] == original['created_at']
    with Session(draft.engine) as db:
        row = db.get(WebClientDraft, original['id'])
        assert row.client_id == 'client' and row.text == text and row.version == 1
    # New DB connections and a new WEB session still address the same draft.
    draft.engine.dispose()
    assert draft.http.post(LOGOUT, headers=ORIGIN).status_code == 204
    assert login(draft).status_code == 200
    assert draft.http.get(BASE).json() == saved
    cleared = put(draft, '', 1)
    assert cleared.status_code == 200
    assert cleared.json()['id'] == original['id'] and cleared.json()['version'] == 2
    assert draft.http.get(BASE).json()['text'] == ''


def test_late_put_cannot_overwrite_newer_text(draft):
    original = draft.http.get(BASE).json()
    saved = put(draft, 'New text', original['version']).json()
    for text in ['Old text', '', 'New text']:
        response = put(draft, text, original['version'])
        assert response.status_code == 409 and response.headers['cache-control'] == 'no-store'
        assert response.json()['detail']['error']['code'] == 'web_draft_version_conflict'
        assert draft.http.get(BASE).json() == saved
    assert put(draft, 'Next text', saved['version']).json()['version'] == 2


def test_first_put_and_validation(draft):
    # A PUT can create the draft without an earlier GET.
    response = put(draft, '🙂' * 10000, 0)
    assert response.status_code == 200 and response.json()['text'] == '🙂' * 10000
    for payload in [{'text': 'x' * 10001, 'version': 1}, {'text': None, 'version': 1},
                    {'text': 'NUL' + chr(0), 'version': 1}, {'text': 4, 'version': 1}, {'text': 'x'}, {'version': 1},
                    {'text': 'x', 'version': -1}, {'text': 'x', 'version': True},
                    {'text': 'x', 'version': '1'}, {'text': 'x', 'version': 1, 'client_id': 'peer'}]:
        invalid = draft.http.put(BASE, json=payload, headers=ORIGIN)
        assert invalid.status_code == 422 and invalid.headers['cache-control'] == 'no-store'
    assert draft.http.get(BASE).json() == response.json()


def test_clients_are_isolated(draft):
    own = put(draft, 'Private owner', 0).json()
    with Session(draft.engine) as db:
        password_hash = db.get(ClientProfile, 'profile').web_password_hash
        db.add(Company(id='other', name='Other'))
        db.flush()
        db.add(Client(id='peer', company_id='other', display_name='Peer'))
        db.flush()
        db.add(ClientProfile(id='peer-profile', client_id='peer', is_verified=True,
            web_login_normalized='peer', web_password_hash=password_hash, web_credentials_version=1))
        db.add(TelegramContact(id='peer-contact', client_id='peer', company_id='other',
                              external_user_id='peer', external_chat_id='peer'))
        db.commit()
    assert draft.http.post(LOGOUT, headers=ORIGIN).status_code == 204
    assert login(draft, login='peer').status_code == 200
    peer = draft.http.get(BASE + '?client_id=client').json()
    assert peer['id'] != own['id'] and peer['text'] == ''
    assert put(draft, 'Private peer', 0).status_code == 200
    assert draft.http.post(LOGOUT, headers=ORIGIN).status_code == 204
    assert login(draft).status_code == 200
    assert draft.http.get(BASE + '?client_id=peer').json() == own
    with Session(draft.engine) as db:
        assert db.scalar(select(func.count()).select_from(WebClientDraft)) == 2


def test_authentication_csrf_and_session_expiry(case):
    case.app.include_router(router)
    assert case.http.get(BASE).status_code == 401
    assert put(case, 'Unauthenticated', 0).status_code == 401
    with Session(case.engine) as db:
        assert not db.scalars(select(WebClientDraft)).all()
    assert login(case).status_code == 200
    own = case.http.get(BASE).json()
    for headers in [{}, {'Origin': 'https://foreign.example'}, {**ORIGIN, 'Sec-Fetch-Site': 'cross-site'}]:
        assert case.http.put(BASE, json={'text': 'Foreign', 'version': 0}, headers=headers).status_code == 403
    case.clock[0] += timedelta(hours=2)
    assert case.http.get(BASE).status_code == 401
    assert put(case, 'Expired', 0).status_code == 401
    with Session(case.engine) as db:
        row = db.get(WebClientDraft, own['id'])
        assert row.text == '' and row.version == 0


@pytest.mark.parametrize('initialized', [False, True])
def test_draft_does_not_create_tickets_change_counter_or_renew_session(draft, initialized):
    with Session(draft.engine) as db:
        if initialized:
            db.add(TicketNumberCounter(id=1, last_number=99))
            db.commit()
        session = db.scalar(select(WebClientSession))
        activity = web_auth.as_utc(session.last_activity_at)
        expires = web_auth.as_utc(session.normal_expires_at)
    draft.clock[0] += timedelta(minutes=5)
    draft.http.get(BASE)
    assert put(draft, 'Independent draft', 0).status_code == 200
    assert put(draft, '', 1).status_code == 200
    with Session(draft.engine) as db:
        assert not db.scalars(select(Ticket)).all()
        assert db.scalar(select(TicketNumberCounter.last_number)) == (99 if initialized else None)
        session = db.scalar(select(WebClientSession))
        assert web_auth.as_utc(session.last_activity_at) == activity
        assert web_auth.as_utc(session.normal_expires_at) == expires


def test_concurrent_creation_and_updates_and_rollback(draft):
    gate = Barrier(4)
    def create(_):
        with Session(draft.engine) as db:
            gate.wait(timeout=15)
            row = web_composer_draft.get_draft(db, 'client')
            db.commit()
            return row['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(4)))
    assert len(set(ids)) == 1
    gate = Barrier(2)
    def save(text):
        with Session(draft.engine) as db:
            gate.wait(timeout=15)
            try:
                row = web_composer_draft.save_draft(db, 'client', text, 0)
                db.commit()
                return row['text']
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['First writer', 'Second writer']))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    saved = draft.http.get(BASE).json()
    assert saved['text'] == winners[0] and saved['version'] == 1 and saved['id'] == ids[0]
    with Session(draft.engine) as db:
        web_composer_draft.save_draft(db, 'client', 'Rolled back', 1)
        db.rollback()
    assert draft.http.get(BASE).json() == saved


def test_explicit_migration_is_idempotent_and_preserves_other_tables(draft, monkeypatch):
    with Session(draft.engine) as db:
        db.add(Ticket(id='old-ticket', company_id='company', client_id='client', subject='Existing', ticket_number=71))
        db.add(TicketNumberCounter(id=1, last_number=99))
        db.commit()
    before = {column['name']: str(column['type']) for column in inspect(draft.engine).get_columns('tickets')}
    WebClientDraft.__table__.drop(draft.engine)
    monkeypatch.setattr(schema_evolution, 'engine', draft.engine)
    schema_evolution.ensure_web_client_drafts_table()
    assert 'web_client_drafts' in inspect(draft.engine).get_table_names()
    saved = put(draft, 'Preserved draft', 0).json()
    schema_evolution.ensure_web_client_drafts_table()
    assert draft.http.get(BASE).json() == saved
    inspector = inspect(draft.engine)
    assert {column['name']: str(column['type']) for column in inspector.get_columns('tickets')} == before
    assert inspector.get_foreign_keys('web_client_drafts')[0]['referred_table'] == 'clients'
    assert any(constraint['column_names'] == ['client_id'] for constraint in inspector.get_unique_constraints('web_client_drafts'))
    with Session(draft.engine) as db:
        assert db.get(Ticket, 'old-ticket').ticket_number == 71
        assert db.get(TicketNumberCounter, 1).last_number == 99
        with pytest.raises(IntegrityError):
            db.add(WebClientDraft(id=str(uuid4()), client_id='client', text='Duplicate', version=0,
                                 created_at=draft.clock[0], updated_at=draft.clock[0]))
            db.flush()
