"""Customer message/file access on isolated databases, with no Telegram traffic."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.models import InternalTaskDefinition, InternalTaskOccurrence, Ticket, TicketAttachment, TicketMessage
from api.app.services import web_history_messages as content
from api.app.services.ticket_admin_actions import _delete_ticket_hard
from test_web_task_results import case, engine, seed_task, message, file, close, BASE
from api.app.models import WebTaskResult

HISTORY = '/api/v1/web/history'


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('is_special', [False, True])
def test_wrong_problem_hint_requires_publication_and_is_read_only(case, existing, is_special):
    # Existing closed publication reproduces DEV #820 without touching its DB.
    ticket_id = '4fd147bd-83ac-59da-b150-dafea1287845'
    hint = '<b>Верный пункт</b>\n<blockquote><i>Расшифровка</i> → Найти сделку но номеру☎️</blockquote>'
    ticket = seed(case, id=ticket_id, wrong_problem_hint=hint, is_special=is_special)
    final = message(case, ticket, 'фыв')
    assert [row['text'] for row in detail(case, ticket).json()['items']] == ['Description']
    assert case.http.get(f'{BASE}/{ticket}').status_code == 404
    if existing:
        with Session(case.engine) as db:
            task = db.get(Ticket, ticket)
            task.status, task.closed_at = 'closed', case.clock[0]
            db.add(WebTaskResult(task_id=ticket, company_id='co', client_id='client', message_id=final,
                                 text='фыв', closed_at=case.clock[0]))
            db.commit()
    else:
        assert close(case, ticket).status_code == 200
    with Session(case.engine) as db:
        before = [(row.id, row.body) for row in db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket))]
    for _ in range(3):
        result = case.http.get(f'{BASE}/{ticket}').json()
        assert result['text'] == 'фыв'
        assert result['wrong_problem_hint'] == 'Верный пункт\nРасшифровка → Найти сделку но номеру☎️'
        rows = detail(case, ticket).json()['items']
        assert [row['text'] for row in rows] == ['Description', 'фыв']
        assert rows[-1]['wrong_problem_hint'] == result['wrong_problem_hint']
        assert rows[0].get('wrong_problem_hint') is None
        assert case.http.get(BASE).json()['unread_count'] == 1
    with Session(case.engine) as db:
        assert db.get(Ticket, ticket).wrong_problem_hint == hint
        assert [(row.id, row.body) for row in db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket))] == before


@pytest.mark.parametrize('changes', [dict(suppress_client_answer=True), dict(status='closed'), dict(task_kind='group_parent')])
def test_unpublished_hint_never_becomes_visible(case, changes):
    ticket = seed(case, wrong_problem_hint='<b>Hidden hint</b>', **changes)
    message(case, ticket, 'Answer')
    if changes.get('suppress_client_answer'):
        assert close(case, ticket).status_code == 200
    assert case.http.get(f'{BASE}/{ticket}').status_code == 404
    assert 'Hidden hint' not in detail(case, ticket).text


@pytest.mark.parametrize('hint,expected', [
    (None, None),
    ('<b>Bold</b> <i>italic</i> <u>underlined</u> <s>old</s>\n<blockquote>Quote &amp; text</blockquote>',
     'Bold italic underlined old\nQuote & text'),
    ('<pre><code>x &lt; y</code></pre> <a href="javascript:alert(1)">Menu</a><img src=x onerror=alert(1)>',
     'x < y Menu'),
    ('&lt;script&gt;alert(1)&lt;/script&gt;', '<script>alert(1)</script>'),
])
def test_hint_html_is_returned_as_plain_text_without_changing_answer(case, hint, expected):
    ticket = seed(case, wrong_problem_hint=hint)
    message(case, ticket, '<b>Original answer</b>')
    assert close(case, ticket).status_code == 200
    result = case.http.get(f'{BASE}/{ticket}').json()
    assert result['text'] == '<b>Original answer</b>'
    assert result['wrong_problem_hint'] == expected
    assert detail(case, ticket).json()['items'][-1].get('wrong_problem_hint') == expected


def test_hint_hidden_if_existing_publication_answer_is_suppressed(case):
    ticket = seed(case, wrong_problem_hint='Hidden hint')
    message(case, ticket, 'Answer')
    assert close(case, ticket).status_code == 200
    with Session(case.engine) as db:
        db.get(Ticket, ticket).suppress_client_answer = True
        db.commit()
    assert case.http.get(f'{BASE}/{ticket}').json()['wrong_problem_hint'] is None
    assert 'Hidden hint' not in detail(case, ticket).text


def seed(case, **changes):
    ticket_id = seed_task(case, **changes)
    with Session(case.engine) as db:
        initial = db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id))
        initial.created_at = case.clock[0] - timedelta(seconds=1)
        initial.telegram_message_id = 'initial' if changes.get('channel') == 'telegram' else None
        db.commit()
    return ticket_id


def detail(case, ticket_id):
    return case.http.get(f'{HISTORY}/{ticket_id}/messages')


def download_url(ticket_id, file_id):
    return f'{HISTORY}/{ticket_id}/attachments/{file_id}/download'


@pytest.mark.parametrize('changes', [dict(client_id='peer'), dict(client_id='foreign', company_id='other'),
    dict(company_id='other'), dict(intake_state='collecting'), dict(intake_state='cancelled'),
    dict(intake_state='telegram_group_copy'), dict(task_kind='group_child')])
def test_forbidden_ticket_and_direct_file_urls(case, changes, monkeypatch):
    ticket = seed(case, **changes)
    msg = message(case, ticket, 'Secret', kind='client')
    attachment, _ = file(case, ticket, msg, 'secret.pdf')
    monkeypatch.setattr(content, '_ensure_local_attachment_file', lambda *a: pytest.fail('Forbidden file read'))
    assert detail(case, ticket).status_code == 404
    assert case.http.get(download_url(ticket, attachment)).status_code == 404
    assert case.http.get(HISTORY).json()['total'] == 0


@pytest.mark.parametrize('channel', ['web', 'telegram'])
@pytest.mark.parametrize('status', ['pending', 'closed'])
def test_plain_ticket_live_text_and_private_messages(case, channel, status):
    ticket = seed(case, channel=channel, workflow_type='ticket', status=status)
    agent = message(case, ticket, 'Old public answer')
    forbidden = [message(case, ticket, 'PRIVATE', kind=kind, status=state, sequence=i + 2)
                 for i, (kind, state) in enumerate([('internal', 'normal'), ('system', 'normal'),
                                                   ('client', 'deleted'), ('agent', 'deleted')])]
    unpublished = message(case, ticket, 'Undelivered', sequence=8)
    with Session(case.engine) as db:
        row = db.get(TicketMessage, agent)
        row.body, row.message_status, row.agent_visible = 'Edited public answer', 'edited', False
        row.telegram_message_id = 'delivered'
        db.commit()
    public, _ = file(case, ticket, agent, 'public.pdf')
    with Session(case.engine) as db:
        db.get(TicketAttachment, public).telegram_message_id = 'file-delivered'
        db.get(TicketAttachment, public).telegram_delivery_status = 'sent'
        db.commit()
    private_files = [file(case, ticket, msg, 'private.pdf')[0] for msg in forbidden]
    result = detail(case, ticket)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    rows = result.json()['items']
    assert [row['text'] for row in rows] == (['Description', 'Edited public answer'] if channel == 'telegram' else ['Description'])
    if channel == 'telegram':
        assert set(rows[1]) == {'id', 'message_type', 'text', 'created_at', 'files'}
        assert case.http.get(rows[1]['files'][0]['download_url']).content == b'content'
    else:
        assert case.http.get(download_url(ticket, public)).status_code == 404
    assert all(secret not in result.text for secret in ['author_user', 'telegram_file', 'local_path', 'PRIVATE', 'Undelivered'])
    for attachment in private_files:
        assert case.http.get(download_url(ticket, attachment)).status_code == 404
    if unpublished:
        attachment, _ = file(case, ticket, unpublished, 'unsent.pdf')
        assert case.http.get(download_url(ticket, attachment)).status_code == 404
    with Session(case.engine) as db:
        db.get(TicketMessage, agent).message_status = 'deleted'
        db.commit()
    assert len(detail(case, ticket).json()['items']) == 1
    assert case.http.get(download_url(ticket, public)).status_code == 404


def test_web_task_published_final_only_and_original_download(case):
    ticket = seed(case)
    early = message(case, ticket, 'Intermediate')
    early_file, _ = file(case, ticket, early, 'early.pdf')
    final = message(case, ticket, 'Published final', sequence=2)
    raw_final, path = file(case, ticket, final, 'final.pdf', b'final bytes')
    assert [row['text'] for row in detail(case, ticket).json()['items']] == ['Description']
    assert case.http.get(download_url(ticket, early_file)).status_code == 404
    assert case.http.get(download_url(ticket, raw_final)).status_code == 404
    assert close(case, ticket).status_code == 200
    rows = detail(case, ticket).json()['items']
    assert [row['text'] for row in rows] == ['Description', 'Published final']
    published = rows[1]['files'][0]
    assert published['source'] == 'web_result' and published['preview_kind'] is None
    assert published['download_url'].startswith(f'{BASE}/{ticket}/files/')
    with Session(case.engine) as db:
        db.get(TicketMessage, final).body = 'Later edit'
        db.get(TicketMessage, final).message_status = 'deleted'
        db.commit()
    path.unlink()
    assert detail(case, ticket).json()['items'] == rows
    response = case.http.get(published['download_url'])
    assert response.content == b'final bytes'
    assert response.headers['content-disposition'].startswith('attachment;')
    case.http.cookies.set('esdesk_web_session', 'peer')
    assert detail(case, ticket).status_code == 404
    assert case.http.get(published['download_url']).status_code == 404


@pytest.mark.parametrize('source,mime,preview', [('sticker', 'image/webp', 'sticker'),
    ('sticker', 'application/x-tgsticker', 'sticker'), ('sticker', 'video/webm', 'sticker'),
    ('document', 'image/webp', 'image'), ('photo', 'image/png', 'image'),
    ('document', 'application/pdf', None), ('video', 'video/webm', None), ('audio', 'audio/ogg', None)])
def test_media_metadata_and_authenticated_download(case, source, mime, preview, monkeypatch):
    ticket = seed(case, channel='telegram', workflow_type='ticket')
    with Session(case.engine) as db:
        initial = db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket))
        msg = initial.id
    attachment, path = file(case, ticket, msg, 'ordinary.webp', b'media')
    with Session(case.engine) as db:
        row = db.get(TicketAttachment, attachment)
        row.source, row.mime_type = source, mime
        db.commit()
    item = detail(case, ticket).json()['items'][0]['files'][0]
    assert item['source'] == source and item['preview_kind'] == preview
    assert item['size'] == 5 and item['mime_type'] == mime
    original = content._ensure_local_attachment_file
    calls = []
    def recover(row, chat):
        calls.append(chat)
        return original(row, chat)
    monkeypatch.setattr(content, '_ensure_local_attachment_file', recover)
    response = case.http.get(item['download_url'])
    assert response.content == b'media' and calls == ['client']
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-disposition'].startswith('attachment;')
    case.http.cookies.clear()
    assert detail(case, ticket).status_code == 401
    assert case.http.get(item['download_url']).status_code == 401


def test_deleted_ticket_mismatched_file_and_unsafe_storage_path(case, monkeypatch):
    ticket = seed(case, workflow_type='ticket')
    other = seed(case, workflow_type='ticket')
    msg = message(case, ticket, 'Client text', kind='client')
    attachment, _ = file(case, ticket, msg, 'file.pdf')
    assert case.http.get(download_url(other, attachment)).status_code == 404
    with Session(case.engine) as db:
        db.get(TicketAttachment, attachment).local_path = str(case.directory.parent / 'outside.pdf')
        db.commit()
    monkeypatch.setattr(content, '_ensure_local_attachment_file', lambda *a: pytest.fail('Unsafe storage read'))
    assert case.http.get(download_url(ticket, attachment)).status_code == 404
    with Session(case.engine) as db:
        _delete_ticket_hard(db, db.get(Ticket, ticket))
        db.commit()
    assert detail(case, ticket).status_code == 404
    assert case.http.get(download_url(ticket, attachment)).status_code == 404


@pytest.mark.parametrize('change', [dict(task_kind='group_child'), dict(intake_state='cancelled')])
def test_existing_publication_direct_url_obeys_history_eligibility(case, change):
    ticket = seed(case)
    msg = message(case, ticket, 'Final')
    file(case, ticket, msg, 'final.pdf')
    assert close(case, ticket).status_code == 200
    url = detail(case, ticket).json()['items'][-1]['files'][0]['download_url']
    with Session(case.engine) as db:
        for key, value in change.items():
            setattr(db.get(Ticket, ticket), key, value)
        db.commit()
    assert detail(case, ticket).status_code == 404
    assert case.http.get(url).status_code == 404
    assert case.http.get(f'{BASE}/{ticket}').status_code == 404


def test_storage_error_does_not_disclose_remote_secrets(case, monkeypatch):
    ticket = seed(case, workflow_type='ticket')
    msg = message(case, ticket, 'Client file', kind='client')
    attachment, _ = file(case, ticket, msg, 'file.pdf')
    def unavailable(*args):
        raise RuntimeError('PRIVATE bot-token local-path Telegram-URL')
    monkeypatch.setattr(content, '_ensure_local_attachment_file', unavailable)
    response = case.http.get(download_url(ticket, attachment))
    assert response.status_code == 404 and 'PRIVATE' not in response.text


def test_internal_occurrence_hidden_even_without_suppression_flag(case):
    ticket = seed(case, workflow_type='ticket')
    msg = message(case, ticket, 'Private task', kind='client')
    attachment, _ = file(case, ticket, msg, 'private.pdf')
    with Session(case.engine) as db:
        definition = InternalTaskDefinition(id=str(uuid4()), company_id='co', title='Internal', description='Private',
            creator_admin_id='agent', responsible_admin_id='agent', assignment_mode='direct',
            display_author=False, notify_on_completion=False, timezone_name='Europe/Moscow',
            schedule_kind='immediate', state='finished')
        db.add(definition)
        db.flush()
        db.add(InternalTaskOccurrence(id=str(uuid4()), definition_id=definition.id, ticket_id=ticket,
            occurrence_at_utc=case.clock[0], title_snapshot='Internal', description_snapshot='Private',
            creator_admin_id='agent', responsible_admin_id='agent', display_author=False,
            effective_assignment_mode='direct', notify_on_completion=False, materialized_at_utc=case.clock[0]))
        db.commit()
    assert detail(case, ticket).status_code == 404
    assert case.http.get(download_url(ticket, attachment)).status_code == 404
    assert case.http.get(HISTORY).json()['total'] == 0


@pytest.mark.parametrize('status', ['pending', 'closed'])
def test_telegram_task_no_confirmed_final_and_original_album(case, status):
    ticket = seed(case, channel='telegram', status=status)
    with Session(case.engine) as db:
        initial = db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket))
        initial_id = initial.id
    first_file, _ = file(case, ticket, initial_id, 'first.pdf')
    album = message(case, ticket, '', kind='client', sequence=1)
    album_file, _ = file(case, ticket, album, 'second.pdf')
    later = message(case, ticket, 'Later client text', kind='client', sequence=2)
    final = message(case, ticket, 'Unconfirmed final', sequence=3)
    final_file, _ = file(case, ticket, final, 'archive.pdf')
    with Session(case.engine) as db:
        for attachment in [first_file, album_file]:
            db.get(TicketAttachment, attachment).telegram_media_group_id = 'original-album'
        db.get(TicketMessage, album).telegram_message_id = 'album-member'
        # Archive success is not a receipt of final delivery to the client.
        row = db.get(TicketAttachment, final_file)
        row.telegram_delivery_status, row.telegram_archive_message_id = 'sent', 'archive-receipt'
        db.get(Ticket, ticket).closed_at = case.clock[0] if status == 'closed' else None
        db.commit()
    rows = detail(case, ticket).json()['items']
    assert [row['id'] for row in rows] == [initial_id, album]
    assert {f['id'] for row in rows for f in row['files']} == {first_file, album_file}
    assert case.http.get(download_url(ticket, final_file)).status_code == 404
    with Session(case.engine) as db:
        db.get(TicketMessage, initial_id).message_status = 'deleted'
        db.commit()
    assert [row['id'] for row in detail(case, ticket).json()['items']] == [album]
    assert case.http.get(download_url(ticket, first_file)).status_code == 404


def test_recover_missing_telegram_file_through_existing_server_helper(case, monkeypatch):
    from api.app.services import ticket_attachments
    ticket = seed(case, channel='telegram', workflow_type='ticket')
    msg = message(case, ticket, 'File', kind='client')
    attachment, path = file(case, ticket, msg, 'remote.pdf')
    path.unlink()
    with Session(case.engine) as db:
        db.get(TicketAttachment, attachment).telegram_file_path = None
        db.commit()
    calls = []
    def get_file(file_id, *, chat_id):
        calls.append(('get', file_id, chat_id))
        return {'result': {'file_path': 'remote/private.pdf'}}
    def download(remote, destination, *, chat_id):
        calls.append(('download', remote, chat_id))
        destination.write_bytes(b'recovered')
    monkeypatch.setattr(ticket_attachments, 'get_file', get_file)
    monkeypatch.setattr(ticket_attachments, 'download_file', download)
    response = case.http.get(download_url(ticket, attachment))
    assert response.content == b'recovered'
    assert calls == [('get', 'PRIVATE-TELEGRAM-ID', 'client'), ('download', 'remote/private.pdf', 'client')]
    assert 'remote/private.pdf' not in str(response.headers)


@pytest.mark.parametrize('channel', ['web', 'telegram'])
def test_closed_without_answer_stays_in_history(case, channel):
    ticket = seed(case, channel=channel, workflow_type='ticket', status='closed', suppress_client_answer=True)
    assert ticket in {row['id'] for row in case.http.get(HISTORY).json()['items']}
    assert [row['text'] for row in detail(case, ticket).json()['items']] == ['Description']


@pytest.mark.parametrize('channel', ['web', 'telegram'])
@pytest.mark.parametrize('delivery_status', [None, 'unknown', 'ready', 'uploading', 'sending', 'failed', 'sent'])
def test_outgoing_file_requires_confirmed_client_delivery(case, channel, delivery_status):
    ticket = seed(case, channel=channel, workflow_type='ticket')
    msg = message(case, ticket, '')
    attachment, _ = file(case, ticket, msg, 'outgoing.pdf')
    with Session(case.engine) as db:
        row = db.get(TicketAttachment, attachment)
        row.telegram_delivery_status = delivery_status
        row.telegram_message_id = 'client-file-id'
        row.telegram_archive_message_id = 'archive-id'
        db.commit()
    visible = channel == 'telegram' and delivery_status == 'sent'
    rows = detail(case, ticket).json()['items']
    assert (msg in {row['id'] for row in rows}) == visible
    assert case.http.get(download_url(ticket, attachment)).status_code == (200 if visible else 404)


def test_confirmed_text_survives_failed_or_unknown_file_delivery(case):
    ticket = seed(case, channel='telegram', workflow_type='ticket')
    msg = message(case, ticket, 'Delivered text')
    files = [file(case, ticket, msg, 'file.pdf')[0] for _ in range(3)]
    with Session(case.engine) as db:
        db.get(TicketMessage, msg).telegram_message_id = 'client-text-id'
        for attachment, status in zip(files, [None, 'failed', 'unknown']):
            row = db.get(TicketAttachment, attachment)
            row.telegram_message_id, row.telegram_delivery_status = 'file-id', status
        db.commit()
    row = detail(case, ticket).json()['items'][-1]
    assert row['id'] == msg and row['text'] == 'Delivered text' and row['files'] == []
    for attachment in files:
        assert case.http.get(download_url(ticket, attachment)).status_code == 404


def test_sent_archive_or_file_does_not_confirm_unsent_text(case):
    ticket = seed(case, channel='telegram', workflow_type='ticket')
    msg = message(case, ticket, 'Unconfirmed text')
    attachment, _ = file(case, ticket, msg, 'file.pdf')
    with Session(case.engine) as db:
        row = db.get(TicketAttachment, attachment)
        row.telegram_delivery_status, row.telegram_archive_message_id = 'sent', 'archive-id'
        db.commit()
    assert [row['text'] for row in detail(case, ticket).json()['items']] == ['Description']
    with Session(case.engine) as db:
        db.get(TicketAttachment, attachment).telegram_message_id = 'client-file-id'
        db.commit()
    assert [row['text'] for row in detail(case, ticket).json()['items']] == ['Description']
    assert case.http.get(download_url(ticket, attachment)).status_code == 404
