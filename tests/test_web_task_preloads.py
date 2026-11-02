"""WEB preload routes use isolated DB/storage and the actual archive worker with fake Telegram."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.models import Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, WebClientSession
from api.app.routers import web_tasks as routes
from api.app.routers.web_history import router as history_router
from api.app.services import telegram_outbound_delivery as pipeline, telegram_upload_state as state
from api.app.services import web_auth, web_history_messages, web_tasks
from test_web_tasks import case, engine, choose, cancel, BASE, ORIGIN


@pytest.fixture
def preloads(case, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'attachment_storage_dir', str(tmp_path / 'files'))
    monkeypatch.setattr(settings, 'telegram_staging_chat_id', 'technical-group')
    monkeypatch.setattr(pipeline, 'SessionLocal', lambda **kw: Session(kw.get('bind', case.engine), expire_on_commit=False))
    calls, deleted, contents = [], [], {}
    def summary(chat, body, **kw):
        calls.append(('info', body))
        return {'ok': True, 'result': {'message_id': len(calls) + 100}}
    def document(chat, path, **kw):
        data = Path(path).read_bytes()
        calls.append(('file', data))
        file_id = 'file-' + str(len(calls))
        contents[file_id] = data
        return {'ok': True, 'result': {'message_id': len(calls) + 100,
            'document': {'file_id': file_id, 'file_unique_id': file_id + '-unique'}}}
    monkeypatch.setattr(pipeline, 'send_message', summary)
    monkeypatch.setattr(pipeline, 'send_document', document)
    monkeypatch.setattr(pipeline, 'edit_message_text', lambda *a, **kw: {'ok': True})
    monkeypatch.setattr(pipeline, 'delete_message', lambda chat, mid: deleted.append(str(mid)) or {'ok': True})
    def local(attachment, *args):
        path = Path(attachment.local_path)
        if not path.exists():
            path.write_bytes(contents[attachment.telegram_file_id])
        return path
    monkeypatch.setattr(web_history_messages, '_ensure_local_attachment_file', local)
    case.http.app.include_router(history_router)
    return calls, deleted, contents


def upload(case, task, upload_id=None, *, data=b'contents', name='a.txt', mime='text/plain', headers=ORIGIN, **fields):
    return case.http.post(f'{BASE}/{task}/attachment-preloads', headers=headers,
        data={'upload_id': str(upload_id or uuid4()), **fields}, files={'file': (name, data, mime)})


def url(task, row):
    return f'{BASE}/{task}/attachment-preloads/{row}'


def describe(case, task, ids, text='Initial request'):
    return case.http.post(f'{BASE}/{task}/description', headers=ORIGIN, json={'text': text, 'preload_ids': ids})


def test_create_status_and_stable_upload_id_do_not_rearchive_or_change_task(case, preloads):
    task = choose(case).json()['task_id']
    upload_id = uuid4()
    first = upload(case, task, upload_id)
    assert first.status_code == 200, first.text
    row_id = first.json()['id']
    assert first.json()['status'] == 'uploading'
    status = case.http.get(url(task, row_id))
    assert status.status_code == 200 and status.json()['status'] == 'ready'
    assert 'file-' not in status.text and 'technical-group' not in status.text
    again = upload(case, task, upload_id)
    assert again.status_code == 200 and again.json()['id'] == row_id
    assert again.json()['status'] == 'ready'
    calls, _, _ = preloads
    assert [kind for kind, _ in calls] == ['info', 'file']
    assert 'Клиент: client' in calls[0][1] and 'Заявка №1' in calls[0][1]
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        assert row.client_id == 'client' and row.user_id is None
        assert db.get(Ticket, task).intake_state == 'collecting'
        assert len(list(db.scalars(select(Ticket.id)))) == 1
        assert not list(db.scalars(select(TicketAttachment.id)))
        assert not list(db.scalars(select(TicketMessage.id).where(TicketMessage.message_type == 'client')))


@pytest.mark.parametrize('change', ['data', 'name', 'mime'])
def test_upload_id_rejects_different_content(case, preloads, change):
    task = choose(case).json()['task_id']
    key = uuid4()
    assert upload(case, task, key).status_code == 200
    args = {'data': b'new'} if change == 'data' else {change: 'new.txt' if change == 'name' else 'application/pdf'}
    assert upload(case, task, key, **args).status_code == 409
    assert len(preloads[0]) == 2


@pytest.mark.parametrize('size,code', [(20 * 1024 * 1024, 200), (20 * 1024 * 1024 + 1, 413)])
def test_web_twenty_mib_limit(case, preloads, size, code):
    task = choose(case).json()['task_id']
    result = upload(case, task, data=b'x' * size)
    assert result.status_code == code, result.text
    with Session(case.engine) as db:
        assert len(list(db.scalars(select(TicketAttachmentPreload.id)))) == (1 if code == 200 else 0)
    if code == 413:
        assert preloads[0] == []
        assert not Path(settings.attachment_storage_dir).exists()


@pytest.mark.parametrize('mode,code', [('unauthorized', 401), ('csrf', 403), ('peer', 404), ('foreign', 404),
    ('company', 404), ('ticket', 404), ('ready', 409), ('closed', 409), ('spoof', 400), ('invalid_id', 422)])
def test_creation_auth_scope_and_state(case, preloads, mode, code):
    task = choose(case).json()['task_id']
    if mode in {'peer', 'foreign'}:
        case.http.cookies.set('esdesk_web_session', 'token-' + mode)
    elif mode == 'unauthorized':
        case.http.cookies.clear()
    elif mode in {'company', 'ticket', 'ready', 'closed'}:
        with Session(case.engine) as db:
            ticket = db.get(Ticket, task)
            if mode == 'company': ticket.company_id = 'other-co'
            if mode == 'ticket': ticket.workflow_type = 'ticket'
            if mode == 'ready': ticket.intake_state = 'ready'
            if mode == 'closed': ticket.status = 'closed'
            db.commit()
    kwargs = {'client_id': 'peer', 'user_id': 'agent'} if mode == 'spoof' else {}
    result = upload(case, task, 'invalid' if mode == 'invalid_id' else None,
        headers={} if mode == 'csrf' else ORIGIN, **kwargs)
    assert result.status_code == code, result.text
    assert preloads[0] == []


@pytest.mark.parametrize('method', ['get', 'retry', 'delete'])
@pytest.mark.parametrize('viewer', ['peer', 'foreign', 'unauthorized'])
def test_all_file_routes_reject_other_sessions(case, preloads, method, viewer):
    task = choose(case).json()['task_id']
    row = upload(case, task).json()['id']
    if viewer == 'unauthorized': case.http.cookies.clear()
    else: case.http.cookies.set('esdesk_web_session', 'token-' + viewer)
    target = url(task, row)
    result = (case.http.get(target) if method == 'get' else
        case.http.post(target + '/retry', headers=ORIGIN, json={}) if method == 'retry' else
        case.http.delete(target, headers=ORIGIN))
    assert result.status_code == (401 if viewer == 'unauthorized' else 404)
    assert preloads[1] == []


def test_polling_does_not_extend_session_or_task_deadline(case, preloads):
    task = choose(case).json()['task_id']
    row = upload(case, task).json()['id']
    with Session(case.engine) as db:
        previous = db.get(WebClientSession, web_auth.token_hash('token-client')).last_activity_at
        deadline = web_tasks.state(db.get(Ticket, task))['expires_at']
    case.clock[0] += timedelta(minutes=2)
    for _ in range(2):
        assert case.http.get(url(task, row)).status_code == 200
    with Session(case.engine) as db:
        assert db.get(WebClientSession, web_auth.token_hash('token-client')).last_activity_at == previous
        assert web_tasks.state(db.get(Ticket, task))['expires_at'] == deadline


def test_retry_uses_existing_receipt_and_ready_retry_is_noop(case, preloads):
    task = choose(case).json()['task_id']
    row_id = upload(case, task).json()['id']
    with Session(case.engine) as db:
        state.fail(db.get(TicketAttachmentPreload, row_id), 'test_failure')
        db.commit()
    assert case.http.get(url(task, row_id)).json()['status'] == 'failed'
    for _ in range(2):
        result = case.http.post(url(task, row_id) + '/retry', headers=ORIGIN, json={})
        assert result.status_code == 200, result.text
    assert case.http.get(url(task, row_id)).json()['status'] == 'ready'
    assert len(preloads[0]) == 2


def test_network_failure_then_retry_uses_same_record(case, preloads, monkeypatch):
    task = choose(case).json()['task_id']
    original = pipeline.send_document
    def unavailable(*a, **kw): raise RuntimeError('secret transport detail')
    monkeypatch.setattr(pipeline, 'send_document', unavailable)
    row_id = upload(case, task).json()['id']
    status = case.http.get(url(task, row_id))
    assert status.json()['status'] == 'failed' and 'secret' not in status.text
    monkeypatch.setattr(pipeline, 'send_document', original)
    assert case.http.post(url(task, row_id) + '/retry', headers=ORIGIN, json={}).status_code == 409
    assert case.http.post(url(task, row_id) + '/retry', headers=ORIGIN, json={'confirm_uncertain': True}).status_code == 200
    assert case.http.get(url(task, row_id)).json()['status'] == 'ready'
    assert [kind for kind, _ in preloads[0]] == ['info', 'file']


def test_delete_one_file_preserves_other_and_cannot_resurrect_old_upload(case, preloads):
    task = choose(case).json()['task_id']
    key = uuid4()
    first = upload(case, task, key).json()['id']
    second = upload(case, task, name='b.txt').json()['id']
    assert case.http.delete(url(task, first), headers=ORIGIN).status_code == 200
    assert case.http.delete(url(task, first), headers=ORIGIN).status_code == 200
    assert case.http.get(url(task, first)).status_code == 404
    assert upload(case, task, key).status_code == 409
    assert case.http.get(url(task, second)).json()['status'] == 'ready'
    assert preloads[1] == ['102']
    assert describe(case, task, [second]).status_code == 200


def test_several_preloads_attach_to_one_message_and_repeat_does_not_send_again(case, preloads):
    task = choose(case).json()['task_id']
    ids = [upload(case, task).json()['id'], upload(case, task, name='image.webp', mime='image/webp').json()['id']]
    before = list(preloads[0])
    result = describe(case, task, ids)
    assert result.status_code == 200 and result.json()['state'] == 'ready', result.text
    assert describe(case, task, list(reversed(ids))).json() == result.json()
    assert describe(case, task, ids, text='different').status_code == 409
    assert describe(case, task, ids[:1]).status_code == 409
    with Session(case.engine) as db:
        messages = list(db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')))
        files = list(db.scalars(select(TicketAttachment)))
        assert len(messages) == 1 and len(files) == 2
        assert {file.message_id for file in files} == {messages[0].id}
        assert all(file.source == 'web' and file.telegram_file_id and file.telegram_archive_message_id for file in files)
        assert all(db.get(TicketAttachmentPreload, row).final_attachment_id for row in ids)
    for row in ids:
        assert case.http.get(url(task, row)).json()['claimed']
        assert case.http.delete(url(task, row), headers=ORIGIN).status_code == 409
    history = case.http.get(f'/api/v1/web/history/{task}/messages')
    assert history.status_code == 200, history.text
    initial, = history.json()['items']
    assert len(initial['files']) == 2 and {f['preview_kind'] for f in initial['files']} == {None, 'image'}
    assert {case.http.get(f['download_url']).content for f in initial['files']} == {b'contents'}
    assert preloads[0] == before and preloads[1] == [] and case.staff.call_count == 1


@pytest.mark.parametrize('bad', ['uploading', 'failed', 'missing_file_id', 'foreign', 'staff', 'duplicate'])
def test_description_rejects_unready_or_unowned_preloads_atomically(case, preloads, bad):
    task = choose(case).json()['task_id']
    ids = [upload(case, task).json()['id'], upload(case, task).json()['id']]
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, ids[1])
        if bad in {'uploading', 'failed'}: row.status = bad
        elif bad == 'missing_file_id': row.telegram_file_id = None
        elif bad == 'foreign': row.client_id = 'peer'
        elif bad == 'staff': row.client_id, row.user_id = None, 'agent'
        db.commit()
    if bad == 'duplicate': ids.append(ids[0])
    result = describe(case, task, ids)
    assert result.status_code == (404 if bad in {'foreign', 'staff'} else 422 if bad == 'duplicate' else 409), result.text
    with Session(case.engine) as db:
        assert not list(db.scalars(select(TicketAttachment.id)))
        assert not list(db.scalars(select(TicketMessage.id).where(TicketMessage.message_type == 'client')))
        assert db.get(Ticket, task).intake_state == 'collecting'
        assert db.get(TicketAttachmentPreload, ids[0]).final_attachment_id is None


def test_multipart_cannot_mix_preload_ids_and_files(case, preloads):
    task = choose(case).json()['task_id']
    row = upload(case, task).json()['id']
    result = case.http.post(f'{BASE}/{task}/description', headers=ORIGIN,
        data={'text': 'Description', 'preload_ids': row}, files={'files': ('new.txt', b'new', 'text/plain')})
    assert result.status_code == 422 and len(preloads[0]) == 2


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_cancel_during_background_upload_cleans_late_file_and_summary(case, preloads, monkeypatch, action):
    task = choose(case).json()['task_id']
    row_ids = []
    def worker(*, preload_id):
        row_ids.append(preload_id)
        pipeline.deliver_attachment_preload(preload_id=preload_id)
    monkeypatch.setattr(routes, 'deliver_attachment_preload', worker)
    original = pipeline.send_document
    started, release = Event(), Event()
    def paused(*a, **kw):
        response = original(*a, **kw)
        started.set()
        assert release.wait(10)
        return response
    monkeypatch.setattr(pipeline, 'send_document', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, case, task)
        try:
            assert started.wait(10)
            result = cancel(case, task) if action == 'cancel' else case.http.delete(url(task, row_ids[0]), headers=ORIGIN)
            assert result.status_code == 200, result.text
        finally:
            release.set()
        assert future.result(timeout=15).status_code == 200
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_ids[0]) is None
        assert db.get(Ticket, task).intake_state == ('cancelled' if action == 'cancel' else 'collecting')
    assert set(preloads[1]) == {'101', '102'}
    assert not list(Path(settings.attachment_storage_dir).glob('*'))


def test_parallel_retries_of_upload_id_only_archive_once(case, preloads):
    from threading import Barrier
    task = choose(case).json()['task_id']
    key, start = uuid4(), Barrier(2)
    def send():
        start.wait(timeout=10)
        return upload(case, task, key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()['id'] == results[1].json()['id']
    assert [kind for kind, _ in preloads[0]] == ['info', 'file']
    with Session(case.engine) as db:
        assert len(list(db.scalars(select(TicketAttachmentPreload.id)))) == 1


def test_parallel_description_retries_only_claim_once(case, preloads):
    from threading import Barrier
    task = choose(case).json()['task_id']
    row = upload(case, task).json()['id']
    start = Barrier(2)
    def send():
        start.wait(timeout=10)
        return describe(case, task, [row])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    with Session(case.engine) as db:
        assert len(list(db.scalars(select(TicketAttachment.id)))) == 1
        assert len(list(db.scalars(select(TicketMessage.id).where(TicketMessage.message_type == 'client')))) == 1
    assert len(preloads[0]) == 2 and case.staff.call_count == 1


def test_description_failure_rolls_back_claim_and_keeps_prepared_files(case, preloads, monkeypatch):
    from fastapi import HTTPException
    from api.app.routers import telegram
    task = choose(case).json()['task_id']
    row = upload(case, task).json()['id']
    original = telegram._ready_ticket_for_distribution
    def fail(*a, **kw): raise HTTPException(409, detail='Synthetic distribution failure')
    monkeypatch.setattr(telegram, '_ready_ticket_for_distribution', fail)
    assert describe(case, task, [row]).status_code == 409
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row).final_attachment_id is None
        assert db.get(TicketAttachmentPreload, row).status == 'ready'
        assert db.get(Ticket, task).intake_state == 'collecting'
        assert not list(db.scalars(select(TicketAttachment.id)))
    assert preloads[1] == []
    monkeypatch.setattr(telegram, '_ready_ticket_for_distribution', original)
    assert describe(case, task, [row]).status_code == 200
    assert len(preloads[0]) == 2


def test_cancel_before_worker_starts_cannot_recreate_upload(case, preloads, monkeypatch):
    task = choose(case).json()['task_id']
    scheduled = []
    monkeypatch.setattr(routes, 'deliver_attachment_preload', lambda **kw: scheduled.append(kw['preload_id']))
    key = uuid4()
    row = upload(case, task, key).json()['id']
    assert scheduled == [row]
    assert case.http.get(url(task, row)).json()['status'] == 'uploading'
    assert cancel(case, task).status_code == 200
    pipeline.deliver_attachment_preload(preload_id=row)
    assert preloads[0] == []
    assert upload(case, task, key).status_code == 409
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row) is None
    assert not list(Path(settings.attachment_storage_dir).glob('*'))


def test_submitting_subset_cleans_only_unclaimed_preloads(case, preloads):
    task = choose(case).json()['task_id']
    first = upload(case, task).json()['id']
    unused = upload(case, task, name='unused.txt').json()['id']
    assert describe(case, task, [first]).status_code == 200
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, first).final_attachment_id
        assert db.get(TicketAttachmentPreload, unused) is None
    assert preloads[1] == ['103']
    assert case.http.get(url(task, first)).json()['status'] == 'ready'


def test_local_storage_failure_returns_error_without_preload_or_archive(case, preloads, monkeypatch):
    from api.app.services import ticket_attachments
    task = choose(case).json()['task_id']
    def unavailable(*a, **kw): raise OSError('PRIVATE storage path')
    monkeypatch.setattr(ticket_attachments, '_store_upload_file', unavailable)
    result = upload(case, task)
    assert result.status_code == 503 and 'PRIVATE' not in result.text
    assert preloads[0] == []
    with Session(case.engine) as db:
        assert not list(db.scalars(select(TicketAttachmentPreload.id)))
        assert db.get(Ticket, task).intake_state == 'collecting'


@pytest.mark.parametrize('commit_succeeded', [False, True])
def test_upload_commit_error_can_be_retried_without_orphans_or_duplicates(case, preloads, monkeypatch, commit_succeeded):
    task = choose(case).json()['task_id']
    original = Session.commit
    attempted = []
    def commit(db):
        uploading = any(isinstance(row, TicketAttachmentPreload) for row in db.new)
        if uploading and not attempted:
            attempted.append(True)
            if commit_succeeded:
                original(db)
            raise RuntimeError('synthetic commit acknowledgement failure')
        return original(db)
    monkeypatch.setattr(Session, 'commit', commit)
    key = uuid4()
    with pytest.raises(RuntimeError, match='synthetic commit'):
        upload(case, task, key)
    with Session(case.engine) as db:
        rows = list(db.scalars(select(TicketAttachmentPreload)))
        assert len(rows) == int(commit_succeeded)
    assert len(list(Path(settings.attachment_storage_dir).glob('*'))) == int(commit_succeeded)
    assert preloads[0] == []
    monkeypatch.setattr(Session, 'commit', original)
    result = upload(case, task, key)
    assert result.status_code == 200, result.text
    assert [kind for kind, _ in preloads[0]] == ['info', 'file']
    with Session(case.engine) as db:
        assert len(list(db.scalars(select(TicketAttachmentPreload)))) == 1
