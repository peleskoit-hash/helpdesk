"""Transactional draft -> TASK transfer, with real WEB auth and fake Telegram only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.models import Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, TicketNumberCounter, WebClientDraft
from api.app.services import telegram_outbound_delivery as pipeline, telegram_upload_state as state, web_tasks
from test_web_composer_draft_preloads import case, engine, preloads, draft, upload, BASE, ORIGIN
from test_web_tasks import choose
from test_web_task_preloads import upload as legacy_upload, describe


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    from api.app.services import telegram_bot
    monkeypatch.setattr(telegram_bot, 'urlopen', lambda *a, **kw: pytest.fail('Unexpected Telegram network'))
    monkeypatch.setattr(settings, 'app_public_base_url', 'https://support.example.test')


def files(case, count=2):
    return [upload(case, name=f'{i}.txt').json()['id'] for i in range(count)]


def rows(case, ids):
    with Session(case.engine) as db:
        return [db.get(TicketAttachmentPreload, row_id) for row_id in ids]


def test_transfer_reuses_original_archive_and_is_idempotent(draft, monkeypatch):
    case, (calls, _, _), edits = draft
    ids = files(case)
    original = rows(case, ids)
    info = original[0].telegram_staging_info_message_id
    assert 'Статус: подготовка файла' in calls[0][1]
    assert 'Статус:' in edits[-1][1]
    with Session(case.engine) as db:
        assert db.get(TicketNumberCounter, 1) is None
        composer = db.get(WebClientDraft, original[0].composer_draft_id)
        composer_id = composer.id
        composer.text, composer.version = '  untouched draft text  ', 12
        db.commit()
    task = choose(case).json()['task_id']
    original_edit = pipeline.edit_message_text
    def edit_after_commit(chat, mid, body, **kw):
        with Session(case.engine) as observer:
            assert observer.get(Ticket, task).intake_state == 'ready'
            assert len(observer.scalars(select(TicketAttachment)).all()) == 2
        return original_edit(chat, mid, body, **kw)
    monkeypatch.setattr(pipeline, 'edit_message_text', edit_after_commit)
    assert describe(case, task, ids).status_code == 200
    sent = rows(case, ids)
    assert all(row.ticket_id == task and row.composer_draft_id is None for row in sent)
    assert all(row.telegram_staging_info_message_id == info for row in sent)
    assert [row.telegram_staging_file_message_id for row in sent] == [row.telegram_staging_file_message_id for row in original]
    with Session(case.engine) as db:
        attachments = db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == task)).all()
        assert len(attachments) == 2
        assert {a.telegram_archive_info_message_id for a in attachments} == {info}
        assert {a.telegram_archive_message_id for a in attachments} == {r.telegram_staging_file_message_id for r in original}
        assert {a.telegram_file_id for a in attachments} == {r.telegram_file_id for r in original}
        assert {a.local_path for a in attachments} == {r.local_path for r in original}
        assert all(a.source == 'web' for a in attachments)
        number = db.get(Ticket, task).ticket_number
        assert number == db.get(TicketNumberCounter, 1).last_number == 1
        assert db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == task, TicketMessage.message_type == 'client')).one().body == 'Initial request'
        assert db.get(WebClientDraft, composer_id).text == '  untouched draft text  '
        assert db.get(WebClientDraft, composer_id).version == 12
    assert edits[-1][0] == info and 'Заявка №1' in edits[-1][1]
    assert 'href="https://support.example.test/tickets/1"' in edits[-1][1]
    assert '0.txt' in edits[-1][1] and '1.txt' in edits[-1][1]
    assert 'Статус:' not in edits[-1][1]
    assert case.http.get(BASE).json() == []
    assert describe(case, task, list(reversed(ids))).status_code == 200
    assert [kind for kind, _ in calls] == ['info', 'file', 'file']
    with Session(case.engine) as db:
        assert len(db.scalars(select(TicketAttachment)).all()) == 2
        assert len(db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all()) == 1
        assert db.get(TicketNumberCounter, 1).last_number == 1


@pytest.mark.parametrize('selected', [(0, 1), (1, 2)])
def test_partial_snapshot_splits_info_and_new_uploads_join_remaining_group(draft, selected):
    case, (calls, deleted, _), edits = draft
    ids = files(case, 3)
    original = rows(case, ids)
    info = original[0].telegram_staging_info_message_id
    task = choose(case).json()['task_id']
    assert describe(case, task, [ids[i] for i in selected]).status_code == 200
    remaining_index = next(i for i in range(3) if i not in selected)
    remaining = rows(case, [ids[remaining_index]])[0]
    new_info = remaining.telegram_staging_info_message_id
    assert remaining.ticket_id is None and remaining.composer_draft_id == original[0].composer_draft_id
    assert remaining.archive_group_id != original[0].archive_group_id
    assert new_info and new_info != info
    assert all(r.telegram_staging_info_message_id == info for r in rows(case, [ids[i] for i in selected]))
    ticket_text = next(body for mid, body in reversed(edits) if mid == info)
    draft_text = next(body for mid, body in reversed(edits) if mid == new_info)
    assert 'Заявка №1' in ticket_text and f'{remaining_index}.txt' not in ticket_text
    assert 'WEB-черновик' in draft_text and '№' not in draft_text and '<a ' not in draft_text
    assert f'{remaining_index}.txt' in draft_text
    added = upload(case, name='other-tab.txt').json()['id']
    new_row = rows(case, [added])[0]
    assert new_row.archive_group_id == remaining.archive_group_id
    assert new_row.telegram_staging_info_message_id == new_info
    assert {item['id'] for item in case.http.get(BASE).json()} == {remaining.id, added}
    assert [kind for kind, _ in calls].count('info') == 2
    assert [kind for kind, _ in calls].count('file') == 4
    assert deleted == []


@pytest.mark.parametrize('bad', ['foreign', 'deleted', 'uploading', 'failed', 'no_file', 'claimed', 'wrong_draft', 'draft_has_ticket'])
def test_whole_snapshot_rejected_before_message_or_attachment(draft, bad):
    case, (_, deleted, _), _ = draft
    ids = files(case)
    task = choose(case).json()['task_id']
    if bad == 'foreign':
        case.http.cookies.set('esdesk_web_session', 'token-peer')
        ids[1] = upload(case).json()['id']
        case.http.cookies.set('esdesk_web_session', 'token-client')
    elif bad == 'deleted':
        assert case.http.delete(f'{BASE}/{ids[1]}', headers=ORIGIN).status_code == 200
    else:
        with Session(case.engine) as db:
            row = db.get(TicketAttachmentPreload, ids[1])
            if bad in {'failed', 'uploading'}: row.status = bad
            if bad == 'no_file': row.telegram_file_id = None
            if bad == 'claimed': row.final_attachment_id = str(uuid4())
            if bad == 'draft_has_ticket': row.ticket_id = task
            if bad == 'wrong_draft':
                other = WebClientDraft(id=str(uuid4()), client_id='peer', text='', version=0,
                    created_at=case.clock[0], updated_at=case.clock[0])
                db.add(other)
                db.flush()
                row.composer_draft_id = other.id
            db.commit()
    response = describe(case, task, ids)
    assert response.status_code in {404, 409}, response.text
    with Session(case.engine) as db:
        assert db.scalars(select(TicketAttachment)).all() == []
        assert db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all() == []
        assert db.get(Ticket, task).intake_state == 'collecting'
        assert db.get(TicketNumberCounter, 1) is None
        assert db.get(TicketAttachmentPreload, ids[0]).composer_draft_id is not None


def test_transaction_failure_keeps_files_and_old_summary(draft, monkeypatch):
    case, (calls, deleted, _), edits = draft
    ids = files(case, 3)
    before = rows(case, ids)
    task = choose(case).json()['task_id']
    original = web_tasks.telegram._ready_ticket_for_distribution
    def failed(*args, **kw):
        original(*args, **kw)
        raise RuntimeError('Synthetic transaction failure')
    monkeypatch.setattr(web_tasks.telegram, '_ready_ticket_for_distribution', failed)
    edit_count = len(edits)
    with pytest.raises(RuntimeError, match='Synthetic'):
        describe(case, task, ids[1:])
    assert len(edits) == edit_count and deleted == []
    after = rows(case, ids)
    assert [(r.ticket_id, r.composer_draft_id, r.archive_group_id, r.telegram_staging_info_message_id) for r in after] == [
        (r.ticket_id, r.composer_draft_id, r.archive_group_id, r.telegram_staging_info_message_id) for r in before]
    with Session(case.engine) as db:
        assert db.scalars(select(TicketAttachment)).all() == []
        assert db.get(TicketNumberCounter, 1) is None
    monkeypatch.setattr(web_tasks.telegram, '_ready_ticket_for_distribution', original)
    assert describe(case, task, ids[1:]).status_code == 200
    assert [kind for kind, _ in calls].count('file') == 3


def test_failed_edit_is_pending_and_identical_description_repairs_it(draft, monkeypatch):
    case, (calls, _, _), edits = draft
    ids = files(case)
    task = choose(case).json()['task_id']
    original = pipeline.edit_message_text
    monkeypatch.setattr(pipeline, 'edit_message_text', lambda *a, **kw: (_ for _ in ()).throw(pipeline.TelegramApiError('temporary')))
    assert describe(case, task, ids).status_code == 200
    assert all(state.state(row)['archive_reconcile_pending'] for row in rows(case, ids))
    monkeypatch.setattr(pipeline, 'edit_message_text', original)
    assert describe(case, task, ids).status_code == 200
    assert not any(state.state(row)['archive_reconcile_pending'] for row in rows(case, ids))
    assert [kind for kind, _ in calls] == ['info', 'file', 'file']


def test_mixed_legacy_and_draft_files(draft):
    case, (calls, _, _), edits = draft
    task = choose(case).json()['task_id']
    draft_id = upload(case).json()['id']
    legacy_id = legacy_upload(case, task, name='legacy.txt').json()['id']
    before = rows(case, [legacy_id, draft_id])
    assert describe(case, task, [legacy_id, draft_id]).status_code == 200
    after = rows(case, [legacy_id, draft_id])
    assert all(r.ticket_id == task and r.composer_draft_id is None for r in after)
    assert [r.telegram_staging_info_message_id for r in before] == [r.telegram_staging_info_message_id for r in after]
    assert [kind for kind, _ in calls] == ['info', 'file', 'info', 'file']
    assert all('Заявка №1' in next(body for mid, body in reversed(edits) if mid == r.telegram_staging_info_message_id) for r in after)


def test_other_tab_uploading_file_survives_split_and_finishes_in_new_summary(draft, monkeypatch):
    case, (calls, deleted, _), edits = draft
    from api.app.routers import web_composer_draft as routes
    selected = files(case)
    info = rows(case, selected)[0].telegram_staging_info_message_id
    started, release, queued = Event(), Event(), []
    original = pipeline.send_document
    def worker(*, preload_id):
        queued.append(preload_id)
        pipeline.deliver_attachment_preload(preload_id=preload_id)
    def paused(*args, **kw):
        started.set()
        assert release.wait(10)
        return original(*args, **kw)
    monkeypatch.setattr(routes, 'deliver_attachment_preload', worker)
    monkeypatch.setattr(pipeline, 'send_document', paused)
    task = choose(case).json()['task_id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, case, name='other-tab.txt')
        try:
            assert started.wait(10)
            assert describe(case, task, selected).status_code == 200
        finally:
            release.set()
        assert future.result(timeout=15).status_code == 200
    remaining = rows(case, queued)[0]
    assert remaining.status == 'ready' and remaining.ticket_id is None
    assert remaining.telegram_staging_info_message_id != info
    assert all(r.telegram_staging_info_message_id == info for r in rows(case, selected))
    assert 'other-tab.txt' not in next(body for mid, body in reversed(edits) if mid == info)
    assert 'other-tab.txt' in next(body for mid, body in reversed(edits) if mid == remaining.telegram_staging_info_message_id)
    assert [kind for kind, _ in calls].count('info') == 2
    assert [kind for kind, _ in calls].count('file') == 3 and deleted == []


@pytest.mark.parametrize('committed', [False, True])
def test_commit_error_preserves_draft_or_retries_committed_transfer(draft, monkeypatch, committed):
    case, (calls, _, _), edits = draft
    ids = files(case)
    task = choose(case).json()['task_id']
    original = Session.commit
    failed = []
    def commit(db):
        row = db.get(Ticket, task)
        if not failed and row is not None and row.intake_state == 'ready':
            failed.append(True)
            if committed:
                original(db)
            raise RuntimeError('Synthetic commit acknowledgement lost')
        return original(db)
    before_edits = len(edits)
    monkeypatch.setattr(Session, 'commit', commit)
    with pytest.raises(RuntimeError, match='acknowledgement'):
        describe(case, task, ids)
    assert len(edits) == before_edits
    with Session(case.engine) as db:
        assert (db.get(Ticket, task).intake_state == 'ready') is committed
        assert (db.get(TicketAttachmentPreload, ids[0]).composer_draft_id is None) is committed
        assert len(db.scalars(select(TicketAttachment)).all()) == (2 if committed else 0)
    assert describe(case, task, ids).status_code == 200
    assert [kind for kind, _ in calls] == ['info', 'file', 'file']
    with Session(case.engine) as db:
        assert len(db.scalars(select(TicketAttachment)).all()) == 2
        assert db.get(TicketNumberCounter, 1).last_number == 1


def test_second_task_waits_for_inflight_remaining_summary_without_duplicating_it(draft, monkeypatch):
    case, (calls, deleted, _), edits = draft
    ids = files(case, 3)
    task = choose(case).json()['task_id']
    started, release = Event(), Event()
    original = pipeline._send_preload_archive_info
    def paused(**kw):
        started.set()
        assert release.wait(10)
        return original(**kw)
    monkeypatch.setattr(pipeline, '_send_preload_archive_info', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(describe, case, task, ids[:2])
        try:
            assert started.wait(10)
            next_task = choose(case).json()['task_id']
            assert next_task != task
            assert describe(case, next_task, ids[2:]).status_code == 409
            with Session(case.engine) as db:
                assert db.get(Ticket, next_task).intake_state == 'collecting'
                assert db.get(TicketNumberCounter, 1).last_number == 1
        finally:
            release.set()
        assert future.result(timeout=15).status_code == 200
    second_info = rows(case, ids[2:])[0].telegram_staging_info_message_id
    monkeypatch.setattr(pipeline, '_send_preload_archive_info', original)
    assert describe(case, next_task, ids[2:]).status_code == 200
    assert rows(case, ids[2:])[0].telegram_staging_info_message_id == second_info
    assert [kind for kind, _ in calls].count('info') == 2
    assert [kind for kind, _ in calls].count('file') == 3 and deleted == []


def test_concurrent_identical_descriptions_create_one_message_and_attachment_set(draft):
    case, (calls, _, _), edits = draft
    ids = files(case)
    task = choose(case).json()['task_id']
    barrier = Barrier(2)
    def send():
        barrier.wait(timeout=10)
        return describe(case, task, ids)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send) for _ in range(2)]
        responses = [future.result(timeout=30) for future in futures]
    assert [r.status_code for r in responses] == [200, 200]
    with Session(case.engine) as db:
        assert len(db.scalars(select(TicketAttachment)).all()) == 2
        assert len(db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all()) == 1
        assert db.get(TicketNumberCounter, 1).last_number == 1
    assert [kind for kind, _ in calls] == ['info', 'file', 'file']


@pytest.mark.parametrize('channel,workflow,kind,intake,number,client_file,public_url,show_status', [
    ('web', 'task', 'normal', 'ready', 7, True, True, False),
    ('web', 'task', 'normal', 'collecting', None, True, True, True),
    ('web', 'task', 'normal', 'collecting', 7, True, True, True),
    ('web', 'task', 'normal', 'ready', None, True, True, True),
    ('web', 'task', 'normal', 'ready', 7, True, False, True),
    ('web', 'ticket', 'normal', 'ready', 7, True, True, True),
    ('telegram', 'task', 'normal', 'ready', 7, True, True, True),
    ('web', 'task', 'group_child', 'ready', 7, True, True, True),
    ('web', 'task', 'normal', 'ready', 7, False, True, True),
])
@pytest.mark.parametrize('status_text', ['подготовка файла', 'ошибка подготовки'])
def test_archive_status_is_hidden_only_for_created_client_web_task(
        monkeypatch, channel, workflow, kind, intake, number, client_file, public_url, show_status, status_text):
    from datetime import datetime
    from types import SimpleNamespace
    from api.app.models import Client, User
    monkeypatch.setattr(settings, 'app_public_base_url', 'https://support.example.test' if public_url else '')
    ticket = Ticket(id=str(uuid4()), channel=channel, workflow_type=workflow, task_kind=kind,
                    intake_state=intake, ticket_number=number, subject='Test')
    row = SimpleNamespace(id=str(uuid4()), client_id='client' if client_file else None,
                          original_filename='file.txt', created_at=datetime(2026, 11, 4))
    actor = Client(display_name='Client') if client_file else User(full_name='Staff')
    body = pipeline._archive_info_text(ticket=ticket, user=actor, group_rows=[row], status_text=status_text)
    assert ('Статус:' in body) is show_status
    if show_status:
        assert f'Статус: {status_text}' in body
