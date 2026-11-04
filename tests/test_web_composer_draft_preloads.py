"""Draft file HTTP/archive regression checks with synthetic Telegram and isolated DBs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from api.app.models import Ticket, TicketAttachmentPreload, TicketNumberCounter, WebClientDraft
from api.app.routers.web_composer_draft import router
from api.app.services import schema_evolution, telegram_outbound_delivery as pipeline, telegram_upload_state as state
from api.app.services import web_composer_draft_attachments as service
from test_web_task_preloads import case, engine, preloads, choose, cancel, ORIGIN

BASE = '/api/v1/web/composer-draft/attachment-preloads'


@pytest.fixture
def draft(case, preloads, monkeypatch):
    case.http.app.include_router(router)
    edits = []
    monkeypatch.setattr(pipeline, 'edit_message_text', lambda chat, mid, body, **kw: edits.append((str(mid), body)))
    return case, preloads, edits


def upload(case, upload_id=None, data=b'contents', name='a.txt', mime='text/plain', **fields):
    return case.http.post(BASE, headers=ORIGIN, data={'upload_id': str(upload_id or uuid4()), **fields},
                          files={'file': (name, data, mime)})


def test_upload_without_ticket_restore_content_idempotency_and_numbering(draft):
    case, (calls, deleted, _), edits = draft
    with Session(case.engine) as db:
        db.add(TicketNumberCounter(id=1, last_number=71))
        db.commit()
    key = uuid4()
    response = upload(case, key)
    assert response.status_code == 200, response.text
    row_id = response.json()['id']
    assert response.json()['status'] == 'uploading'
    item = case.http.get(f'{BASE}/{row_id}').json()
    assert item['status'] == 'ready' and item['file_size'] == 8
    assert item['original_filename'] == 'a.txt'
    assert case.http.get(BASE).json() == [item]
    content = case.http.get(item['content_url'])
    assert content.status_code == 200 and content.content == b'contents'
    assert content.headers['cache-control'] == 'no-store'
    assert content.headers['x-content-type-options'] == 'nosniff'
    assert upload(case, key).json()['id'] == row_id
    assert upload(case, key, data=b'changed').status_code == 409
    assert [kind for kind, _ in calls] == ['info', 'file']
    assert 'Клиент: client' in calls[0][1] and 'a.txt' in calls[0][1]
    assert all('№' not in body and '<a ' not in body for body in [calls[0][1], *[body for _, body in edits]])
    assert 'готов к отправке' in edits[-1][1]
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        assert row.ticket_id is None and row.user_id is None and row.client_id == 'client'
        draft_row = db.get(WebClientDraft, row.composer_draft_id)
        assert row.archive_group_id == draft_row.id and draft_row.text == '' and draft_row.version == 0
        assert Path(row.local_path).is_file()
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.get(TicketNumberCounter, 1).last_number == 71
    assert deleted == []


@pytest.mark.parametrize('viewer', ['peer', 'foreign', 'unauthorized'])
@pytest.mark.parametrize('method', ['get', 'content', 'retry', 'delete'])
def test_client_isolation(draft, viewer, method):
    case, (_, deleted, _), _ = draft
    row = upload(case).json()['id']
    if viewer == 'unauthorized':
        case.http.cookies.clear()
    else:
        case.http.cookies.set('esdesk_web_session', 'token-' + viewer)
    url = f'{BASE}/{row}'
    response = (case.http.get(url) if method == 'get' else case.http.get(url + '/content') if method == 'content'
        else case.http.post(url + '/retry', headers=ORIGIN, json={}) if method == 'retry'
        else case.http.delete(url, headers=ORIGIN))
    assert response.status_code == (401 if viewer == 'unauthorized' else 404)
    listing = case.http.get(BASE)
    assert listing.status_code == (401 if viewer == 'unauthorized' else 200)
    if viewer != 'unauthorized': assert listing.json() == []
    assert deleted == []


def test_auth_csrf_and_spoofed_owner(draft):
    case, (calls, _, _), _ = draft
    assert upload(case, client_id='peer').status_code == 400
    assert case.http.post(BASE, files={'file': ('a.txt', b'a')}, data={'upload_id': str(uuid4())}).status_code == 403
    case.http.cookies.clear()
    assert upload(case).status_code == 401
    assert calls == []


@pytest.mark.parametrize('size,mime,name,code', [(20*1024*1024, 'text/plain', 'a.txt', 200),
    (20*1024*1024+1, 'text/plain', 'a.txt', 413), (4, 'application/x-tgsticker', 'a.tgs', 422)])
def test_limits(draft, size, mime, name, code):
    case, _, _ = draft
    response = upload(case, data=b'x'*size, mime=mime, name=name)
    assert response.status_code == code, response.text
    with Session(case.engine) as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.scalar(select(func.count()).select_from(TicketNumberCounter)) == 0


def test_delete_one_last_and_repeat_upload_cannot_resurrect(draft):
    case, (calls, deleted, _), edits = draft
    key = uuid4()
    first = upload(case, key).json()['id']
    second = upload(case, name='b.txt').json()['id']
    assert [kind for kind, _ in calls] == ['info', 'file', 'file']
    with Session(case.engine) as db:
        a = db.get(TicketAttachmentPreload, first)
        b = db.get(TicketAttachmentPreload, second)
        info, file_a, file_b, path_a, path_b = (a.telegram_staging_info_message_id,
            a.telegram_staging_file_message_id, b.telegram_staging_file_message_id, a.local_path, b.local_path)
        assert b.telegram_staging_info_message_id == info
    assert case.http.delete(f'{BASE}/{first}', headers=ORIGIN).status_code == 200
    assert file_a in deleted and info not in deleted and file_b not in deleted
    assert not Path(path_a).exists() and Path(path_b).exists()
    assert 'b.txt' in edits[-1][1] and 'a.txt' not in edits[-1][1]
    assert upload(case, key).status_code == 409
    assert case.http.delete(f'{BASE}/{first}', headers=ORIGIN).status_code == 200
    assert case.http.delete(f'{BASE}/{second}', headers=ORIGIN).status_code == 200
    assert info in deleted and file_b in deleted and not Path(path_b).exists()
    assert case.http.get(BASE).json() == []
    assert case.http.get(f'{BASE}/{second}/content').status_code == 404


@pytest.mark.parametrize('timeout', [False, True])
def test_survives_intake_cancel_timeout_and_stale_cleanup(draft, timeout):
    case, (_, deleted, _), _ = draft
    row_id = upload(case).json()['id']
    task = choose(case).json()['task_id']
    if timeout:
        case.clock[0] += timedelta(minutes=11)
        assert case.http.get('/api/v1/web/tasks/active').json() is None
    else:
        assert cancel(case, task).status_code == 200
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        row.created_at = datetime.utcnow() - timedelta(days=30)
        db.commit()
        pipeline.cleanup_stale_attachment_preloads(db)
    assert case.http.get(f'{BASE}/{row_id}').json()['status'] == 'ready'
    assert case.http.get(f'{BASE}/{row_id}/content').content == b'contents'
    assert deleted == []


def test_uncertain_retry_requires_confirmation_and_no_double_send(draft, monkeypatch):
    case, (calls, _, _), edits = draft
    original = pipeline.send_document
    def failed(*args, **kw):
        raise pipeline.TelegramApiError('Synthetic upload outcome unknown')
    monkeypatch.setattr(pipeline, 'send_document', failed)
    row_id = upload(case).json()['id']
    result = case.http.get(f'{BASE}/{row_id}').json()
    assert result['status'] == 'failed' and result['uncertain']
    assert 'ошибка подготовки' in edits[-1][1]
    assert case.http.get(f'{BASE}/{row_id}/content').content == b'contents'
    url = f'{BASE}/{row_id}/retry'
    assert case.http.post(url, headers=ORIGIN, json={}).status_code == 409
    monkeypatch.setattr(pipeline, 'send_document', original)
    assert case.http.post(url, headers=ORIGIN, json={'confirm_uncertain': True}).status_code == 200
    assert case.http.get(f'{BASE}/{row_id}').json()['status'] == 'ready'
    assert case.http.post(url, headers=ORIGIN, json={}).status_code == 200
    assert [kind for kind, _ in calls] == ['info', 'file']


def test_content_can_be_restored_from_telegram(draft, monkeypatch):
    case, (_, _, contents), _ = draft
    row_id = upload(case).json()['id']
    with Session(case.engine) as db:
        Path(db.get(TicketAttachmentPreload, row_id).local_path).unlink()
    from api.app.services import ticket_attachments
    monkeypatch.setattr(ticket_attachments, 'get_file', lambda *a, **kw: {'result': {'file_path': 'synthetic'}})
    monkeypatch.setattr(ticket_attachments, 'download_file', lambda _, path, **kw: Path(path).write_bytes(b'contents'))
    assert case.http.get(f'{BASE}/{row_id}/content').content == b'contents'


def test_concurrent_upload_id_is_one_row_and_one_archive_delivery(draft):
    case, (calls, _, _), _ = draft
    barrier, key = Barrier(2), uuid4()
    from starlette.datastructures import UploadFile, Headers
    from io import BytesIO
    from types import SimpleNamespace
    def worker():
        barrier.wait(timeout=10)
        with Session(case.engine) as db:
            file = UploadFile(BytesIO(b'contents'), filename='a.txt', headers=Headers({'content-type': 'text/plain'}))
            row, schedule = service.create_preload(db, SimpleNamespace(id='client'), file, key)
            return row.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: worker(), range(2)))
    assert rows[0] == rows[1]
    pipeline.deliver_attachment_preload(preload_id=rows[0])
    pipeline.deliver_attachment_preload(preload_id=rows[1])
    assert [kind for kind, _ in calls] == ['info', 'file']


def test_explicit_migration_is_idempotent_and_preserves_legacy_rows(case, monkeypatch):
    task = choose(case).json()['task_id']
    with case.engine.begin() as conn:
        # A legacy table with both staff/client ownership and ticket_id NOT NULL.
        conn.execute(text('DROP TABLE ticket_attachment_preloads'))
        conn.execute(text("CREATE TABLE ticket_attachment_preloads (id VARCHAR(36) PRIMARY KEY, "
            "ticket_id VARCHAR(36) NOT NULL REFERENCES tickets(id), user_id VARCHAR(36), client_id VARCHAR(36), "
            "local_path VARCHAR(1000) NOT NULL)"))
        conn.execute(text("INSERT INTO ticket_attachment_preloads(id,ticket_id,client_id,local_path) "
                          "VALUES ('legacy',:task,'client','unchanged')"), {'task': task})
        before = conn.execute(text('SELECT * FROM ticket_attachment_preloads')).all()
    assert 'ensure_composer_draft_attachment_preloads' not in (Path(__file__).parents[1] / 'api/app/main.py').read_text(encoding='utf-8')
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    schema_evolution.ensure_composer_draft_attachment_preloads()
    schema_evolution.ensure_composer_draft_attachment_preloads()
    with case.engine.begin() as conn:
        columns = {col['name']: col for col in inspect(conn).get_columns('ticket_attachment_preloads')}
        assert columns['ticket_id']['nullable'] and columns['composer_draft_id']['nullable']
        assert any(fk['constrained_columns'] == ['composer_draft_id'] and fk['referred_table'] == 'web_client_drafts'
                   for fk in inspect(conn).get_foreign_keys('ticket_attachment_preloads'))
        assert conn.execute(text('SELECT id,ticket_id,user_id,client_id,local_path FROM ticket_attachment_preloads')).all() == before
        assert conn.scalar(text("SELECT composer_draft_id FROM ticket_attachment_preloads WHERE id='legacy'")) is None


@pytest.mark.parametrize('receipt', [False, True])
def test_interrupted_upload_status_and_retry(draft, monkeypatch, receipt):
    case, (calls, _, _), _ = draft
    from api.app.routers import web_composer_draft as routes
    monkeypatch.setattr(routes, 'deliver_attachment_preload', lambda **kw: None)
    row_id = upload(case).json()['id']
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        if receipt:
            state.patch(row, receipt={'accepted': True, 'media_type': 'document', 'file_id': 'saved', 'message_id': '999'},
                        staging_attempt={'active': True, 'token': 'expired', 'started_at': 1})
        else:
            state.patch(row, queued_at=1)
        db.commit()
    result = case.http.get(f'{BASE}/{row_id}').json()
    assert result['status'] == 'failed' and not result['uncertain']
    assert result['error_code'] == ('staging_interrupted' if receipt else 'staging_not_started')
    monkeypatch.setattr(routes, 'deliver_attachment_preload', pipeline.deliver_attachment_preload)
    response = case.http.post(f'{BASE}/{row_id}/retry', headers=ORIGIN, json={})
    assert response.status_code == 200, response.text
    assert case.http.get(f'{BASE}/{row_id}').json()['status'] == 'ready'
    assert [kind for kind, _ in calls] == (['info'] if receipt else ['info', 'file'])


def test_delete_during_upload_removes_late_file_and_info(draft, monkeypatch):
    case, (_, deleted, _), _ = draft
    from api.app.routers import web_composer_draft as routes
    ids, started, release = [], Event(), Event()
    original = pipeline.send_document
    def worker(*, preload_id):
        ids.append(preload_id)
        pipeline.deliver_attachment_preload(preload_id=preload_id)
    monkeypatch.setattr(routes, 'deliver_attachment_preload', worker)
    def paused(*args, **kw):
        response = original(*args, **kw)
        started.set()
        assert release.wait(10)
        return response
    monkeypatch.setattr(pipeline, 'send_document', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, case)
        try:
            assert started.wait(10)
            result = case.http.delete(f'{BASE}/{ids[0]}', headers=ORIGIN)
            assert result.status_code == 200, result.text
        finally:
            release.set()
        assert future.result(timeout=15).status_code == 200
    assert set(deleted) == {'101', '102'}
    assert case.http.get(BASE).json() == []
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, ids[0])
        assert row.status == 'cancelled' and not Path(row.local_path).exists()
        assert row.telegram_staging_info_message_id is None and row.telegram_staging_file_message_id is None


def test_draft_upload_does_not_mutate_existing_intake_or_counter(draft):
    case, _, _ = draft
    task_id = choose(case).json()['task_id']
    def snapshot(db):
        return (db.execute(select(Ticket.__table__)).mappings().all(),
                db.execute(select(TicketNumberCounter.__table__)).mappings().all())
    with Session(case.engine) as db:
        before = snapshot(db)
    assert upload(case).status_code == 200
    with Session(case.engine) as db:
        assert snapshot(db) == before
        assert db.get(Ticket, task_id).ticket_number is None


def test_migration_creates_missing_table(case, monkeypatch):
    with case.engine.begin() as conn:
        conn.execute(text('DROP TABLE ticket_attachment_preloads'))
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    schema_evolution.ensure_composer_draft_attachment_preloads()
    schema_evolution.ensure_composer_draft_attachment_preloads()
    assert inspect(case.engine).has_table('ticket_attachment_preloads')
    # Exercise the new table through the actual API after the explicit migration.
    case.http.app.include_router(router)
    from io import BytesIO
    from starlette.datastructures import UploadFile, Headers
    from types import SimpleNamespace
    with Session(case.engine) as db:
        row, _ = service.create_preload(db, SimpleNamespace(id='client'),
            UploadFile(BytesIO(b'a'), filename='a.txt', headers=Headers({'content-type': 'text/plain'})), uuid4())
        assert row.ticket_id is None and row.composer_draft_id is not None
