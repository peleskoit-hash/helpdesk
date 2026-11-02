"""Initial WEB task uploads; isolated DB/storage and no real Telegram calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.auth import get_current_user
from api.app.config import settings
from api.app.models import Ticket, TicketAttachment, TicketMessage
from api.app.routers import tickets
from api.app.routers.web_history import router as history_router
from api.app.services import web_task_attachments as uploads_service, web_tasks
from test_web_tasks import case, engine, choose, description, BASE, ORIGIN


@pytest.fixture
def uploads(case, tmp_path, monkeypatch):
    directory = tmp_path / 'attachments'
    directory.mkdir()
    monkeypatch.setattr(settings, 'attachment_storage_dir', str(directory))
    monkeypatch.setattr(settings, 'telegram_staging_chat_id', 'synthetic-staging')
    archived = []
    def archive(**kwargs):
        data = Path(kwargs['local_path']).read_bytes()
        message_id = str(500 + len(archived))
        archived.append((kwargs['original_filename'], data))
        kwargs['on_receipt']({'result': {'message_id': int(message_id)}})
        return 'archive-file-' + message_id, 'unique-' + message_id, message_id
    sender = Mock(side_effect=archive)
    deleted = Mock()
    summary = Mock(return_value={'ok': True, 'result': {'message_id': 800}})
    monkeypatch.setattr(uploads_service, '_send_message_with_retry', summary)
    monkeypatch.setattr(uploads_service, '_send_staging_document_from_path', sender)
    monkeypatch.setattr(uploads_service, '_delete_archive_message_by_id', deleted)
    case.http.app.include_router(history_router)
    case.http.app.include_router(tickets.router)
    case.http.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='agent', role='agent', company_id='co', full_name='Agent')
    return SimpleNamespace(directory=directory, archived=archived, sender=sender, deleted=deleted, summary=summary)


def post(case, task_id, files=None, text='Client request', headers=ORIGIN):
    files = [('files', item) for item in (files if files is not None else [('a.txt', b'file', 'text/plain')])]
    return case.http.post(f'{BASE}/{task_id}/description', headers=headers, data={'text': text}, files=files)


def rows(case):
    with Session(case.engine) as db:
        return (db.scalars(select(TicketMessage).where(TicketMessage.message_type == 'client')).all(),
                db.scalars(select(TicketAttachment)).all())


def test_legacy_json_description_still_has_no_uploads(case, uploads):
    task_id = choose(case).json()['task_id']
    result = description(case, task_id)
    assert result.status_code == 200 and result.json()['state'] == 'ready'
    assert description(case, task_id).json() == result.json()
    assert len(rows(case)[0]) == 1 and rows(case)[1] == []
    uploads.sender.assert_not_called()
    uploads.summary.assert_not_called()


def test_multiple_files_staff_and_client_history_same_initial_message(case, uploads):
    task_id = choose(case).json()['task_id']
    files = [('request.txt', b'notes', 'text/plain'), ('ordinary.webp', b'image', 'image/webp')]
    result = post(case, task_id, files)
    assert result.status_code == 200, result.text
    messages, attachments = rows(case)
    assert len(messages) == 1 and len(attachments) == 2
    assert all(a.message_id == messages[0].id and a.ticket_id == task_id and a.source == 'web' for a in attachments)
    assert all(a.telegram_file_id and a.telegram_archive_message_id and not a.telegram_message_id for a in attachments)
    assert uploads.archived == [('request.txt', b'notes'), ('ordinary.webp', b'image')]
    uploads.summary.assert_called_once()
    summary_args = uploads.summary.call_args
    assert summary_args.args[0] == 'synthetic-staging'
    assert all(text in summary_args.args[1] for text in ['Заявка', 'Клиент: client', 'Файлы:', 'request.txt', 'ordinary.webp', 'получено от клиента'])
    assert {a.telegram_archive_info_message_id for a in attachments} == {'800'}
    employee = case.http.get('/api/v1/tickets/' + task_id)
    assert employee.status_code == 200, employee.text
    employee_message = next(m for m in employee.json()['messages'] if m['id'] == messages[0].id)
    assert {a['id'] for a in employee_message['attachments']} == {a.id for a in attachments}
    history = case.http.get(f'/api/v1/web/history/{task_id}/messages')
    assert history.status_code == 200
    initial, = history.json()['items']
    assert initial['text'] == 'Client request' and initial['id'] == messages[0].id
    assert {f['preview_kind'] for f in initial['files']} == {None, 'image'}
    assert {case.http.get(f['download_url']).content for f in initial['files']} == {b'notes', b'image'}
    assert post(case, task_id, files).json() == result.json()
    assert uploads.sender.call_count == 2 and len(rows(case)[1]) == 2
    assert uploads.summary.call_count == 1
    assert case.staff.call_count == 1
    case.http.cookies.set('esdesk_web_session', 'token-peer')
    assert case.http.get(initial['files'][0]['download_url']).status_code == 404


@pytest.mark.parametrize('change', ['bytes', 'name', 'mime', 'missing', 'text'])
def test_retry_requires_same_text_and_files(case, uploads, change):
    task_id = choose(case).json()['task_id']
    assert post(case, task_id).status_code == 200
    files = [('b.txt' if change == 'name' else 'a.txt', b'edit' if change == 'bytes' else b'file',
              'application/pdf' if change == 'mime' else 'text/plain')]
    result = (description(case, task_id, 'Client request') if change == 'missing'
              else post(case, task_id, files, text='Changed' if change == 'text' else 'Client request'))
    assert result.status_code == 409
    assert len(rows(case)[0]) == len(rows(case)[1]) == uploads.sender.call_count == 1


@pytest.mark.parametrize('size,code', [(20 * 1024 * 1024, 200), (20 * 1024 * 1024 + 1, 413)])
def test_twenty_mib_limit_is_per_file(case, uploads, size, code):
    task_id = choose(case).json()['task_id']
    result = post(case, task_id, [('a.bin', b'x' * size, 'application/octet-stream'), ('b.txt', b'second', 'text/plain')])
    assert result.status_code == code, result.text
    if code == 413:
        assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
        uploads.sender.assert_not_called()
        assert post(case, task_id).status_code == 200
    else:
        assert len(rows(case)[1]) == 2 and uploads.sender.call_count == 2


def test_second_archive_failure_rolls_back_all_bindings_and_can_retry(case, uploads):
    task_id = choose(case).json()['task_id']
    original = uploads.sender.side_effect
    def fail_second(**kwargs):
        if len(uploads.archived) == 1:
            raise RuntimeError('PRIVATE TOKEN LOCAL PATH')
        return original(**kwargs)
    uploads.sender.side_effect = fail_second
    files = [('a.txt', b'first', 'text/plain'), ('b.txt', b'second', 'text/plain')]
    result = post(case, task_id, files)
    assert result.status_code == 502 and 'PRIVATE' not in result.text
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    uploads.deleted.assert_called_once_with('500')
    with Session(case.engine) as db:
        ticket = db.get(Ticket, task_id)
        assert ticket.intake_state == 'collecting' and 'description_files' not in web_tasks.state(ticket)
    assert case.staff.call_count == 0
    uploads.sender.side_effect = original
    assert post(case, task_id, files).status_code == 200
    assert len(rows(case)[0]) == 1 and len(rows(case)[1]) == 2


@pytest.mark.parametrize('mode,code', [('peer', 404), ('foreign', 404), ('company', 404), ('unauthorized', 401),
                                      ('csrf', 403), ('ticket', 404), ('confirmation', 409)])
def test_scope_and_session_checked_before_saving(case, uploads, mode, code):
    task_id = choose(case).json()['task_id']
    if mode in {'peer', 'foreign'}:
        case.http.cookies.set('esdesk_web_session', 'token-' + mode)
    if mode == 'unauthorized':
        case.http.cookies.clear()
    with Session(case.engine) as db:
        task = db.get(Ticket, task_id)
        if mode == 'company': task.company_id = 'other-co'
        if mode == 'ticket': task.workflow_type = 'ticket'
        if mode == 'confirmation':
            task.quick_action_state_json = {web_tasks.STATE_KEY: {**web_tasks.state(task), 'confirmed': False}}
        db.commit()
    assert post(case, task_id, headers={} if mode == 'csrf' else ORIGIN).status_code == code
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    uploads.sender.assert_not_called()


@pytest.mark.parametrize('filename,mime', [('sticker.tgs', 'application/octet-stream'), ('file.bin', 'application/x-tgsticker')])
def test_sticker_type_rejected(case, uploads, filename, mime):
    task_id = choose(case).json()['task_id']
    assert post(case, task_id, [(filename, b'no', mime)]).status_code == 422
    assert rows(case) == ([], [])
    uploads.sender.assert_not_called()


def test_concurrent_retries_save_and_archive_once(case, uploads):
    task_id = choose(case).json()['task_id']
    barrier = Barrier(2)
    def send():
        barrier.wait()
        return post(case, task_id)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    assert len(rows(case)[0]) == len(rows(case)[1]) == uploads.sender.call_count == case.staff.call_count == 1
    assert uploads.summary.call_count == 1


def test_failure_after_archiving_removes_files_before_ready(case, uploads, monkeypatch):
    task_id = choose(case).json()['task_id']
    def fail(*args):
        raise HTTPException(409, detail='Failed to finish intake')
    monkeypatch.setattr(web_tasks.telegram, '_ready_ticket_for_distribution', fail)
    assert post(case, task_id).status_code == 409
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    assert [call.args[0] for call in uploads.deleted.call_args_list] == ['500', '800']


def test_summary_failure_rolls_back_and_retry_does_not_leave_partial_bindings(case, uploads):
    task_id = choose(case).json()['task_id']
    uploads.summary.side_effect = RuntimeError('PRIVATE summary failure')
    result = post(case, task_id)
    assert result.status_code == 502 and 'PRIVATE' not in result.text
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    uploads.deleted.assert_called_once_with('500')
    uploads.summary.side_effect = None
    assert post(case, task_id).status_code == 200
    assert len(rows(case)[0]) == len(rows(case)[1]) == 1


def test_summary_without_receipt_is_not_a_success(case, uploads):
    task_id = choose(case).json()['task_id']
    uploads.summary.return_value = {'ok': True, 'result': {}}
    assert post(case, task_id).status_code == 502
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []


def test_storage_failure_removes_partial_local_file(case, uploads, monkeypatch):
    task_id = choose(case).json()['task_id']
    write = Path.write_bytes
    def fail(path, data):
        result = write(path, data[:2])
        if path.parent == uploads.directory:
            raise OSError('PRIVATE disk path')
        return result
    monkeypatch.setattr(Path, 'write_bytes', fail)
    result = post(case, task_id)
    assert result.status_code == 502 and 'PRIVATE' not in result.text
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    uploads.sender.assert_not_called()


def test_known_archive_receipt_cleaned_even_if_archive_helper_fails(case, uploads):
    task_id = choose(case).json()['task_id']
    def broken(**kwargs):
        kwargs['on_receipt']({'result': {'message_id': 702}})
        raise RuntimeError('Incomplete remote receipt')
    uploads.sender.side_effect = broken
    assert post(case, task_id).status_code == 502
    uploads.deleted.assert_called_once_with('702')
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []


@pytest.mark.parametrize('data', [{'text': ''}, {'text': 'request', 'client_id': 'peer'},
                                  {'text': 'request', 'source': 'sticker'}])
def test_invalid_form_rejected_before_saving(case, uploads, data):
    task_id = choose(case).json()['task_id']
    response = case.http.post(f'{BASE}/{task_id}/description', headers=ORIGIN, data=data,
                             files=[('files', ('a.txt', b'file', 'text/plain'))])
    assert response.status_code == 422
    assert rows(case) == ([], []) and list(uploads.directory.iterdir()) == []
    uploads.sender.assert_not_called()
