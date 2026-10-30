import asyncio
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import threading

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from api.app.models import Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, User
from api.app.routers import tickets as routes
from api.app.services import telegram_bot, telegram_outbound_delivery as outbound, telegram_upload_state as upload, schema_evolution
from api.app.services.ticket_messages import _attachment_from_preload
from test_ticket_messages_outbound import _session_bundle, _seed_ticket, _threaded_session_bundle


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    _, factory, db = _threaded_session_bundle(tmp_path)
    ticket = _seed_ticket(db)
    user = User(id='agent-1', company_id=ticket.company_id, full_name='Agent', role='agent')
    ticket.assignee_id = user.id
    path = tmp_path / 'original.webp'; path.write_bytes(b'original bytes')
    row = TicketAttachmentPreload(id=str(uuid4()), ticket_id=ticket.id, user_id=user.id,
        original_filename='original.webp', mime_type='image/webp', file_size=14, local_path=str(path),
        status='uploading', archive_group_id=str(uuid4()), created_at=datetime.utcnow())
    db.add_all([user, row]); db.commit()
    calls = []
    monkeypatch.setattr(outbound, 'SessionLocal', factory)
    monkeypatch.setattr(outbound.settings, 'telegram_staging_chat_id', 'isolated-staging')
    monkeypatch.setattr(outbound, 'send_message', lambda *a, **kw: calls.append(('text', a, kw)) or {'result': {'message_id': 100}})
    monkeypatch.setattr(outbound, 'edit_message_text', lambda *a, **kw: calls.append(('edit', a, kw)) or {'ok': True})
    monkeypatch.setattr(outbound, 'delete_message', lambda *a, **kw: calls.append(('delete', a, kw)) or {'ok': True})
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: calls.append(('binary', a, kw)) or media_response('document'))
    monkeypatch.setattr(outbound, 'send_document_by_file_id', lambda *a, **kw: calls.append(('sendDocument', a, kw)) or {'result': {'message_id': 600}})
    monkeypatch.setattr(telegram_bot, 'telegram_request', lambda method, **kw: calls.append((method, (), kw)) or {'result': {'message_id': 600}})
    yield SimpleNamespace(db=db, factory=factory, ticket=ticket, user=user, row=row, path=path, calls=calls)
    db.close()


def media_response(kind):
    media = {'file_id': kind + '-file', 'file_unique_id': kind + '-unique'}
    if kind == 'photo':
        media = [{'file_id': 'small', 'width': 10, 'height': 10}, {**media, 'width': 100, 'height': 80}]
    return {'ok': True, 'result': {'message_id': 301, kind: media}}


@pytest.mark.parametrize('filename', ['photo.jpg', 'photo.png', 'image.webp', 'report.pdf', 'clip.mp4', 'animation.gif'])
def test_document_receipt_and_final_dispatch_without_duplicates(prepared, monkeypatch, filename):
    p = prepared
    p.row.original_filename = filename; p.db.commit()
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); saved = p.db.get(TicketAttachmentPreload, p.row.id)
    assert saved.status == 'ready' and saved.telegram_file_id == 'document-file'
    assert saved.telegram_upload_state_json['receipt']['media_type'] == 'document'
    assert not p.path.exists()
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent', body='Caption', author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=saved, delivery_status='sending')
    p.db.commit()
    kwargs = dict(attachment_id=attachment.id, message_id=message.id, ticket_id=p.ticket.id, prefer_caption=True, preload_id=saved.id)
    outbound.deliver_ticket_attachment(**kwargs); outbound.deliver_ticket_attachment(**kwargs)
    p.db.expire_all()
    assert p.db.get(TicketAttachment, attachment.id).telegram_delivery_status == 'sent'
    assert len([c for c in p.calls if c[0] == 'sendDocument']) == 1


@pytest.mark.parametrize('kind', ['photo', 'sticker', 'video', 'audio', 'animation', 'voice', 'video_note'])
def test_unexpected_receipt_stays_failed_until_confirmed_retry_as_document(prepared, monkeypatch, kind):
    p = prepared
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: p.calls.append(('binary', a, kw)) or media_response(kind))
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); saved = p.db.get(TicketAttachmentPreload, p.row.id)
    assert saved.status == 'failed' and not saved.telegram_file_id
    assert saved.telegram_upload_state_json['receipt']['media_type'] == kind
    assert saved.telegram_staging_file_message_id == '301' and p.path.read_bytes() == b'original bytes'
    actor = SimpleNamespace(id=p.user.id, role='agent')
    assert routes.get_ticket(p.ticket.id, db=p.db, user=actor).active_attachment_preloads[0]['status'] == 'failed'
    with pytest.raises(HTTPException) as error:
        routes.retry_attachment_preload(p.ticket.id, saved.id, BackgroundTasks(), False, db=p.db, user=actor)
    assert error.value.status_code == 409
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: p.calls.append(('binary', a, kw)) or media_response('document'))
    routes.retry_attachment_preload(p.ticket.id, saved.id, BackgroundTasks(), True, db=p.db, user=actor)
    outbound.deliver_attachment_preload(preload_id=saved.id)
    p.db.expire_all(); saved = p.db.get(TicketAttachmentPreload, saved.id)
    assert saved.status == 'ready' and not p.path.exists()
    assert saved.telegram_upload_state_json['previous_receipts'][0]['media_type'] == kind
    assert len([c for c in p.calls if c[0] == 'binary']) == 2
    assert not any(c[0].startswith('send') for c in p.calls)



def test_failed_receipt_keeps_file_ids_and_tile_until_manual_delete(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: {'ok': True, 'result': {'message_id': 333, 'unsupported': {'file_id': 'not-a-document'}}})
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert row.status == 'failed' and p.path.read_bytes() == b'original bytes'
    assert row.telegram_staging_file_message_id == '333'
    assert row.telegram_upload_state_json['receipt']['accepted'] is True
    assert row.telegram_upload_state_json['error']
    actor = SimpleNamespace(id=p.user.id, role='agent')
    card = routes.get_ticket(p.ticket.id, db=p.db, user=actor)
    assert card.active_attachment_preloads[0]['status'] == 'failed'
    with pytest.raises(HTTPException) as exc:
        routes.retry_attachment_preload(p.ticket.id, row.id, BackgroundTasks(), False, db=p.db, user=actor)
    assert exc.value.status_code == 409
    routes.delete_attachment_preload(p.ticket.id, row.id, db=p.db, user=actor)
    assert p.db.get(TicketAttachmentPreload, row.id) is None and not p.path.exists()
    assert any(c[0] == 'delete' and str(c[1][1]) == '333' for c in p.calls)


def test_registration_error_reuses_accepted_receipt_and_same_preload(prepared, monkeypatch):
    p = prepared
    parse = outbound.upload_state.usable_receipt
    gate = {'fail': True}
    monkeypatch.setattr(outbound.upload_state, 'usable_receipt', lambda value: False if gate['fail'] else parse(value))
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert row.status == 'failed' and row.telegram_upload_state_json['receipt']['file_id'] == 'document-file'
    assert p.path.exists()
    gate['fail'] = False
    actor = SimpleNamespace(id=p.user.id, role='agent')
    # Confirm the conservative uncertainty warning. Known usable receipt means no second binary upload.
    first, second = BackgroundTasks(), BackgroundTasks()
    routes.retry_attachment_preload(p.ticket.id, row.id, first, True, db=p.db, user=actor)
    routes.retry_attachment_preload(p.ticket.id, row.id, second, True, db=p.db, user=actor)
    assert len(first.tasks) == 1 and not second.tasks
    outbound.deliver_attachment_preload(preload_id=row.id)
    p.db.expire_all(); assert p.db.get(TicketAttachmentPreload, row.id).status == 'ready'
    assert len([c for c in p.calls if c[0] == 'binary']) == 1


def test_worker_concurrency_stages_one_binary(prepared, monkeypatch):
    p = prepared; entered = threading.Event(); release = threading.Event()
    def send(*args, **kwargs):
        p.calls.append(('binary', args, kwargs)); entered.set(); assert release.wait(5); return media_response('document')
    monkeypatch.setattr(outbound, 'send_document', send)
    worker = threading.Thread(target=outbound.deliver_attachment_preload, kwargs={'preload_id': p.row.id})
    worker.start(); assert entered.wait(5)
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    release.set(); worker.join(5); assert not worker.is_alive()
    assert len([c for c in p.calls if c[0] == 'binary']) == 1
    p.db.expire_all(); assert p.db.get(TicketAttachmentPreload, p.row.id).status == 'ready'


def test_staging_error_is_visible_safe_and_retry_stages_once(prepared, monkeypatch, caplog):
    p = prepared
    def fail(*args, **kwargs): raise outbound.TelegramApiError('https://api.telegram.org/botSECRET/token')
    monkeypatch.setattr(outbound, 'send_document', fail)
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert row.status == 'failed' and p.path.exists()
    assert 'SECRET' not in str(row.telegram_upload_state_json) and 'SECRET' not in caplog.text
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: p.calls.append(('binary', a, kw)) or media_response('document'))
    actor = SimpleNamespace(id=p.user.id, role='agent')
    routes.retry_attachment_preload(p.ticket.id, row.id, BackgroundTasks(), True, db=p.db, user=actor)
    outbound.deliver_attachment_preload(preload_id=row.id)
    p.db.expire_all(); assert p.db.get(TicketAttachmentPreload, row.id).status == 'ready'


def test_duplicate_browser_upload_id_does_not_store_another_file(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(routes.settings, 'attachment_storage_dir', str(p.path.parent / 'uploads'))
    actor = SimpleNamespace(id=p.user.id, role='agent')
    key = str(uuid4())
    def call():
        return asyncio.run(routes.create_attachment_preload(ticket_id=p.ticket.id, background_tasks=BackgroundTasks(),
            file=UploadFile(file=BytesIO(b'WEBP'), filename='new.webp', headers={'content-type': 'image/webp'}),
            archive_group_id=None, upload_id=key, db=p.db, user=actor))
    assert call()['id'] == call()['id'] == key
    assert len(list((p.path.parent / 'uploads').iterdir())) == 1


def test_schema_evolution_adds_nullable_state_idempotently_without_changing_rows(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    monkeypatch.setattr(schema_evolution, 'engine', engine)
    schema_evolution.ensure_ticket_attachment_table(); schema_evolution.ensure_ticket_attachment_preload_table()
    with engine.begin() as conn:
        for table in ['ticket_attachments', 'ticket_attachment_preloads']:
            conn.execute(text(f'ALTER TABLE {table} DROP COLUMN telegram_upload_state_json'))
        conn.execute(text("INSERT INTO ticket_attachments(id,ticket_id,message_id,source,local_path) VALUES ('before','t','m','helpdesk','existing.png')"))
        conn.execute(text("INSERT INTO ticket_attachment_preloads(id,ticket_id,user_id,local_path,status) VALUES ('before','t','u','existing.webp','failed')"))
    schema_evolution.ensure_ticket_attachment_table(); schema_evolution.ensure_ticket_attachment_preload_table()
    schema_evolution.ensure_ticket_attachment_table(); schema_evolution.ensure_ticket_attachment_preload_table()
    for table in ['ticket_attachments', 'ticket_attachment_preloads']:
        assert [c['name'] for c in inspect(engine).get_columns(table)].count('telegram_upload_state_json') == 1
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT telegram_upload_state_json FROM {table} WHERE id='before'")).scalar() is None
    engine.dispose()

@pytest.mark.parametrize('filename,mime,kind', [('photo.jpg','image/jpeg','document'), ('photo.png','image/png','document'), ('report.pdf','application/pdf','document'), ('clip.mp4','video/mp4','document'), ('animated.gif','image/gif','document')])
def test_other_formats_keep_original_name_and_mime(prepared, monkeypatch, filename, mime, kind):
    p = prepared; p.row.original_filename = filename; p.row.mime_type = mime; p.db.commit()
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: p.calls.append(('binary', a, kw)) or media_response(kind))
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert row.status == 'ready' and row.original_filename == filename and row.mime_type == mime
    binary = next(c for c in p.calls if c[0] == 'binary')
    assert binary[2]['filename'] == filename and binary[2]['mime_type'] == mime


def failed_claimed_task(p, monkeypatch, body='Task text'):
    p.ticket.workflow_type = 'task'
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent', body=body,
                           author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=p.row, delivery_status='uploading')
    p.db.commit()
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: (_ for _ in ()).throw(outbound.TelegramApiError('synthetic failure')))
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload, p.row.id).status == 'failed'
    assert p.db.get(TicketAttachment, attachment.id).telegram_delivery_status == 'failed'
    return message, attachment


def test_failed_already_claimed_task_retries_without_another_message_or_client_send(prepared, monkeypatch):
    p = prepared; message, attachment = failed_claimed_task(p, monkeypatch)
    actor = SimpleNamespace(id=p.user.id, role='agent')
    card = routes.get_ticket(p.ticket.id, db=p.db, user=actor)
    assert card.active_attachment_preloads[0]['claimed'] is True
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: media_response('document'))
    routes.retry_attachment_preload(p.ticket.id, p.row.id, BackgroundTasks(), True, db=p.db, user=actor)
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload, p.row.id).final_attachment_id == attachment.id
    assert p.db.get(TicketAttachment, attachment.id).telegram_delivery_status == 'ready'
    assert p.db.get(TicketMessage, message.id) is not None
    assert not any(c[0] in ['sendDocument'] for c in p.calls)
    assert routes.get_ticket(p.ticket.id, db=p.db, user=actor).active_attachment_preloads == []


@pytest.mark.parametrize('body', ['', 'Keep this text'])
def test_manual_removal_of_claimed_failed_task_keeps_other_message_content(prepared, monkeypatch, body):
    p = prepared; message, attachment = failed_claimed_task(p, monkeypatch, body)
    routes.delete_attachment_preload(p.ticket.id, p.row.id, db=p.db, user=SimpleNamespace(id=p.user.id, role='agent'))
    assert p.db.get(TicketAttachmentPreload, p.row.id) is None and p.db.get(TicketAttachment, attachment.id) is None
    assert not p.path.exists()
    p.db.refresh(message); assert message.body == body
    assert message.message_status == ('deleted' if not body else 'normal')


def test_expired_attempt_status_is_stable_and_requires_confirmed_retry(prepared):
    p = prepared
    upload.patch(p.row, staging_attempt={'token':'interrupted','active':True,'started_at':0}, binary_started=True)
    p.db.commit()
    actor = SimpleNamespace(id=p.user.id, role='agent')
    a = routes.get_attachment_preload_status(p.ticket.id, p.row.id, db=p.db, user=actor)
    b = routes.get_attachment_preload_status(p.ticket.id, p.row.id, db=p.db, user=actor)
    assert a == b and a['status'] == 'failed' and a['uncertain'] is True
    with pytest.raises(HTTPException): routes.retry_attachment_preload(p.ticket.id, p.row.id, BackgroundTasks(), False, db=p.db, user=actor)
    assert not any(c[0] == 'binary' for c in p.calls)


def test_final_delivery_concurrency_sends_once(prepared, monkeypatch):
    p = prepared
    outbound.deliver_attachment_preload(preload_id=p.row.id); p.db.expire_all()
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent', body='', author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=p.row, delivery_status='sending'); p.db.commit()
    entered = threading.Event(); release = threading.Event(); sent = []
    def send(*args, **kwargs): entered.set(); sent.append(args); assert release.wait(5); return {'result': {'message_id':600}}
    monkeypatch.setattr(outbound, 'send_document_by_file_id', send)
    args = dict(attachment_id=attachment.id, message_id=message.id, ticket_id=p.ticket.id, preload_id=p.row.id)
    first = threading.Thread(target=outbound.deliver_ticket_attachment, kwargs=args); first.start(); assert entered.wait(5)
    outbound.deliver_ticket_attachment(**args); release.set(); first.join(5)
    assert not first.is_alive() and len(sent) == 1


def test_client_text_acknowledgement_survives_file_error_without_duplicate_retry(prepared, monkeypatch):
    p = prepared
    outbound.deliver_attachment_preload(preload_id=p.row.id); p.db.expire_all()
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent', body='Text before file', author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=p.row, delivery_status='sending'); p.db.commit()
    attempts = []
    def fail(*args, **kwargs): attempts.append(args); raise outbound.TelegramApiError('synthetic unknown delivery')
    monkeypatch.setattr(outbound, 'send_document_by_file_id', fail)
    args = dict(attachment_id=attachment.id, message_id=message.id, ticket_id=p.ticket.id, preload_id=p.row.id)
    outbound.deliver_ticket_attachment(**args); outbound.deliver_ticket_attachment(**args)
    p.db.expire_all(); saved = p.db.get(TicketAttachment, attachment.id)
    assert saved.telegram_delivery_status == 'failed'
    assert saved.telegram_upload_state_json['sent_text_message_id'] == '100'
    assert saved.telegram_upload_state_json['delivery_pending'] == 'file'
    assert len(attempts) == 1


def test_retry_rejects_other_owner_and_closed_ticket(prepared):
    p = prepared; upload.fail(p.row); p.db.commit(); key = p.row.id
    with pytest.raises(HTTPException) as exc:
        routes.retry_attachment_preload(p.ticket.id, key, BackgroundTasks(), False, db=p.db, user=SimpleNamespace(id='other', role='admin'))
    assert exc.value.status_code == 404
    p.ticket.status = 'closed'; p.db.commit()
    with pytest.raises(HTTPException) as exc:
        routes.retry_attachment_preload(p.ticket.id, key, BackgroundTasks(), False, db=p.db, user=SimpleNamespace(id=p.user.id, role='agent'))
    assert exc.value.status_code == 409

@pytest.mark.parametrize('method', ['sendMessage', 'sendDocument'])
def test_unknown_html_transport_error_is_not_implicitly_retried(monkeypatch, method):
    calls = []
    def fail(name, **kwargs): calls.append((name, kwargs)); raise telegram_bot.TelegramApiError('https://api.telegram.org/bot123456:SECRETSECRETSECRETSECRET/sendDocument timeout')
    monkeypatch.setattr(telegram_bot, 'telegram_request', fail)
    with pytest.raises(telegram_bot.TelegramApiError) as exc:
        if method == 'sendMessage': telegram_bot.send_message('fake', '<b>Text</b>', parse_mode='HTML', retry_only_parse_errors=True)
        else: telegram_bot.send_document_by_file_id('fake', 'id', caption='<b>Text</b>', parse_mode='HTML', retry_only_parse_errors=True)
    assert len(calls) == 1 and 'SECRET' not in str(exc.value)


def test_explicit_html_rejection_can_fall_back_without_uncertain_resend(monkeypatch):
    calls = []
    def request(name, **kwargs):
        calls.append(kwargs['payload'])
        if len(calls) == 1: raise telegram_bot.TelegramApiError("Bad Request: can't parse entities")
        return {'result': {'message_id': 500}}
    monkeypatch.setattr(telegram_bot, 'telegram_request', request)
    telegram_bot.send_document_by_file_id('fake', 'id', caption='<b>Text</b>', parse_mode='HTML', retry_only_parse_errors=True)
    assert len(calls) == 2 and calls[1]['caption'] == 'Text' and 'parse_mode' not in calls[1]


@pytest.mark.parametrize('response', [{'ok': True, 'result': {'message_id': 4, 'document': {'file_id': ' '}}},
                                     {'ok': True, 'result': {'message_id': 'invalid', 'document': {'file_id': 'file'}}},
                                     {'ok': False, 'result': {'message_id': 4, 'document': {'file_id': 'file'}}}])
def test_malformed_receipts_never_become_ready(prepared, monkeypatch, response):
    p = prepared
    monkeypatch.setattr(outbound, 'send_document', lambda *a, **kw: response)
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert row.status == 'failed' and p.path.exists()
    assert row.telegram_upload_state_json['receipt']


@pytest.mark.parametrize("failures", [1, 2])
def test_database_receipt_commit_failure_preserves_accepted_media_and_reuses_it(prepared, monkeypatch, failures):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    p = prepared; original_commit = Session.commit; gate = {'count': 0}
    def commit(db):
        if gate['count'] < failures and any(isinstance(row, TicketAttachmentPreload) and (row.telegram_upload_state_json or {}).get('receipt') for row in db.dirty):
            gate['count'] += 1
            raise OperationalError('synthetic registration failure', None, Exception('DB unavailable'))
        return original_commit(db)
    monkeypatch.setattr(Session, 'commit', commit)
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all(); row = p.db.get(TicketAttachmentPreload, p.row.id)
    assert gate['count'] == failures and row.status == 'failed' and p.path.exists()
    assert row.telegram_staging_file_message_id == '301'
    assert row.telegram_upload_state_json['receipt']['file_id'] == 'document-file'
    routes.retry_attachment_preload(p.ticket.id, row.id, BackgroundTasks(), False, db=p.db, user=SimpleNamespace(id=p.user.id, role='agent'))
    outbound.deliver_attachment_preload(preload_id=row.id)
    p.db.expire_all(); assert p.db.get(TicketAttachmentPreload, row.id).status == 'ready'
    assert len([c for c in p.calls if c[0] == 'binary']) == 1


def test_dead_worker_before_start_becomes_failed_without_telegram_calls(prepared):
    p = prepared
    upload.patch(p.row, queued_at=1); p.db.commit()
    payload = routes.get_attachment_preload_status(p.ticket.id, p.row.id, db=p.db, user=SimpleNamespace(id=p.user.id, role='agent'))
    assert payload['status'] == 'failed' and payload['uncertain'] is False and p.path.exists()
    assert not p.calls


def test_expired_client_delivery_is_failed_instead_of_repeated(prepared):
    p = prepared
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent', body='', author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=p.row, delivery_status='sending')
    upload.patch(attachment, delivery_attempt={'active':True,'token':'dead','started_at':0}, delivery_pending='file')
    p.db.commit()
    outbound.deliver_ticket_attachment(attachment_id=attachment.id, message_id=message.id, ticket_id=p.ticket.id)
    p.db.expire_all()
    assert p.db.get(TicketAttachment, attachment.id).telegram_delivery_status == 'failed'
    assert not p.calls


def test_concurrent_browser_uploads_share_one_record_and_file(prepared, monkeypatch):
    p = prepared; upload_dir = p.path.parent / 'concurrent'; key = str(uuid4())
    monkeypatch.setattr(routes.settings, 'attachment_storage_dir', str(upload_dir))
    ticket_id, user_id = p.ticket.id, p.user.id
    barrier = threading.Barrier(2); results = []; errors = []
    def post():
        try:
            barrier.wait(5)
            with p.factory() as db:
                results.append(asyncio.run(routes.create_attachment_preload(ticket_id=ticket_id,
                    background_tasks=BackgroundTasks(), file=UploadFile(file=BytesIO(b'WEBP'), filename='same.webp',
                    headers={'content-type':'image/webp'}), archive_group_id=None, upload_id=key, db=db,
                    user=SimpleNamespace(id=user_id, role='agent'))))
        except Exception as exc: errors.append(exc)
    threads=[threading.Thread(target=post) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10)
    assert not errors and len(results) == 2 and {value['id'] for value in results} == {key}
    assert len(list(upload_dir.iterdir())) == 1


def test_manual_failed_delete_is_idempotent(prepared):
    p = prepared; upload.fail(p.row); p.db.commit(); key=p.row.id
    actor=SimpleNamespace(id=p.user.id,role='agent')
    assert routes.delete_attachment_preload(p.ticket.id,key,db=p.db,user=actor)=={'ok':True}
    assert routes.delete_attachment_preload(p.ticket.id,key,db=p.db,user=actor)=={'ok':True}
    assert not p.path.exists()


def test_confirmed_retry_retains_old_message_ids_for_manual_cleanup(prepared, monkeypatch):
    p=prepared
    monkeypatch.setattr(outbound,'send_document',lambda *a,**kw:{'ok':True,'result':{'message_id':333,'unknown':{'file_id':'unknown-id'}}})
    outbound.deliver_attachment_preload(preload_id=p.row.id); p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,p.row.id).telegram_upload_state_json['receipt']['unrecognized_file_ids']==['unknown-id']
    actor=SimpleNamespace(id=p.user.id,role='agent')
    routes.retry_attachment_preload(p.ticket.id,p.row.id,BackgroundTasks(),True,db=p.db,user=actor)
    monkeypatch.setattr(outbound,'send_document',lambda *a,**kw:media_response('document'))
    outbound.deliver_attachment_preload(preload_id=p.row.id); p.db.expire_all()
    routes.delete_attachment_preload(p.ticket.id,p.row.id,db=p.db,user=actor)
    deleted={str(c[1][1]) for c in p.calls if c[0]=='delete'}
    assert {'301','333'}.issubset(deleted)


def test_waiting_for_staging_slot_keeps_attempt_alive_and_releases_slot(prepared,monkeypatch):
    class Gate:
        def __init__(self): self.calls=0;self.released=0
        def acquire(self,timeout): self.calls+=1;return self.calls>1
        def release(self):self.released+=1
    p=prepared;gate=Gate();monkeypatch.setattr(outbound,'STAGING_BINARY_UPLOAD_SEMAPHORE',gate)
    outbound.deliver_attachment_preload(preload_id=p.row.id);p.db.expire_all()
    row=p.db.get(TicketAttachmentPreload,p.row.id)
    assert row.status=='ready' and gate.calls==2 and gate.released==1
    assert row.telegram_upload_state_json['staging_attempt']['started_at']>0
    assert row.telegram_upload_state_json['staging_attempt']['active'] is False


def test_expired_claimed_task_can_retry_directly_without_waiting_for_poll(prepared):
    p=prepared;p.ticket.workflow_type='task'
    message=TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='Saved task',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=p.row,delivery_status='uploading')
    upload.patch(p.row,staging_attempt={'token':'dead','active':True,'started_at':0},binary_started=True);p.db.commit()
    jobs=BackgroundTasks()
    result=routes.retry_attachment_preload(p.ticket.id,p.row.id,jobs,True,db=p.db,user=SimpleNamespace(id=p.user.id,role='agent'))
    assert result['status']=='uploading' and result['claimed'] is True and len(jobs.tasks)==1
    duplicate=BackgroundTasks()
    assert routes.retry_attachment_preload(p.ticket.id,p.row.id,duplicate,True,db=p.db,user=SimpleNamespace(id=p.user.id,role='agent'))['status']=='uploading'
    assert not duplicate.tasks
    p.db.refresh(attachment);assert attachment.telegram_delivery_status=='uploading'


@pytest.mark.parametrize('failure', ['unexpected', 'invalid_saved_receipt'])
def test_claimed_task_registration_failures_remain_visible_and_retryable(prepared, monkeypatch, failure):
    p = prepared
    p.ticket.workflow_type = 'task'
    message = TicketMessage(id=str(uuid4()), ticket_id=p.ticket.id, message_type='agent',
        body='Keep task text', author_user_id=p.user.id, created_at=datetime.utcnow())
    p.db.add(message); p.db.flush()
    attachment = _attachment_from_preload(db=p.db, ticket=p.ticket, message=message, preload=p.row, delivery_status='uploading')
    if failure == 'unexpected':
        monkeypatch.setattr(outbound, '_deliver_attachment_preload', lambda **kw: (_ for _ in ()).throw(ValueError('synthetic registration error')))
    else:
        upload.patch(p.row, receipt={'accepted': True, 'message_id': '301'})
    p.db.commit()
    outbound.deliver_attachment_preload(preload_id=p.row.id)
    p.db.expire_all()
    assert p.db.get(TicketAttachment, attachment.id).telegram_delivery_status == 'failed'
    actor = SimpleNamespace(id=p.user.id, role='agent')
    card = routes.get_ticket(p.ticket.id, db=p.db, user=actor)
    assert card.active_attachment_preloads[0]['status'] == 'failed'
    assert p.path.exists()
    jobs = BackgroundTasks()
    assert routes.retry_attachment_preload(p.ticket.id, p.row.id, jobs, True, db=p.db, user=actor)['status'] == 'uploading'
    assert len(jobs.tasks) == 1


@pytest.mark.parametrize('body,mime', [(b'VEBP', 'image/webp'), (b'LONGER WEBP', 'image/webp'), (b'WEBP', 'application/pdf')])
def test_upload_id_rejects_changed_bytes_size_or_mime(prepared, monkeypatch, body, mime):
    p = prepared
    monkeypatch.setattr(routes.settings, 'attachment_storage_dir', str(p.path.parent / 'uploads'))
    key = str(uuid4()); actor = SimpleNamespace(id=p.user.id, role='agent')
    def post(data, content_type):
        return asyncio.run(routes.create_attachment_preload(p.ticket.id, BackgroundTasks(),
            UploadFile(file=BytesIO(data), filename='same.webp', headers={'content-type':content_type}),
            None, key, p.db, actor))
    assert post(b'WEBP','image/webp')['id'] == key
    with pytest.raises(HTTPException) as error:
        post(body,mime)
    assert error.value.status_code == 409
    p.db.rollback()
    assert len(list((p.path.parent / 'uploads').iterdir())) == 1


def test_upload_id_identical_bytes_work_after_source_cleanup(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(routes.settings, 'attachment_storage_dir', str(p.path.parent / 'uploads'))
    key = str(uuid4()); actor = SimpleNamespace(id=p.user.id, role='agent')
    def post():
        return asyncio.run(routes.create_attachment_preload(p.ticket.id, BackgroundTasks(),
            UploadFile(file=BytesIO(b'WEBP'), filename='same.webp', headers={'content-type':'image/webp'}),
            None, key, p.db, actor))
    post(); outbound.deliver_attachment_preload(preload_id=key); p.db.expire_all()
    row = p.db.get(TicketAttachmentPreload,key)
    assert not Path(row.local_path).exists()
    assert post() == upload.preload_payload(row)
    assert len([c for c in p.calls if c[0]=='binary']) == 1


def delivery_fixture(p, info, old=True):
    from datetime import timedelta
    p.row.status = 'sending'
    p.row.telegram_staging_info_message_id = '100'
    message = TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='Answer',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachment = _attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=p.row,delivery_status='sending')
    if old: attachment.created_at = datetime.utcnow()-timedelta(seconds=500)
    upload.patch(attachment,**info);p.db.commit()
    return message,attachment


def test_restart_before_delivery_worker_does_not_leave_sending_forever(prepared):
    p=prepared
    _,attachment=delivery_fixture(p,{})
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    p.db.refresh(attachment);p.db.refresh(p.row)
    assert attachment.telegram_delivery_status==p.row.status=='failed'
    assert any(c[0]=='edit' for c in p.calls) and not any(c[0] in ['binary','sendDocument'] for c in p.calls)


@pytest.mark.parametrize('pending,expected', [(None,'sent'),('file','failed')])
def test_restart_reconciles_file_ack_preload_and_archive_without_delivery(prepared,pending,expected):
    p=prepared
    message,attachment=delivery_fixture(p,{'delivery_attempt':{'token':'dead','active':True,'started_at':0},
        'sent_text_message_id':'501','sent_file_message_id':'502','delivery_pending':pending})
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    p.db.refresh(attachment);p.db.refresh(p.row);p.db.refresh(message)
    assert attachment.telegram_delivery_status==p.row.status==expected
    if expected=='sent': assert attachment.telegram_message_id=='502' and message.telegram_message_id=='501'
    assert any(c[0]=='edit' for c in p.calls) and not any(c[0] in ['binary','sendDocument'] for c in p.calls)
    edits=len(p.calls);outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert len(p.calls)==edits


@pytest.mark.parametrize('active', [False,True])
def test_restart_cleans_cancelled_worker_only_after_lease_expiry(prepared,active):
    import time
    p=prepared;key=p.row.id;p.row.status='cancelled'
    upload.patch(p.row,staging_attempt={'active':True,'token':'cancel','started_at':time.time() if active else 0})
    p.db.commit();outbound.recover_ticket_uploads(p.db,p.ticket.id)
    if active: assert p.db.get(TicketAttachmentPreload,key) is not None and p.path.exists()
    else: assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()


def test_restart_releases_document_source_after_ready_commit(prepared):
    p=prepared;p.row.status='ready';p.row.telegram_file_id='document-file'
    upload.patch(p.row,receipt=upload.receipt(media_response('document')));p.db.commit()
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert not p.path.exists() and p.db.get(TicketAttachmentPreload,p.row.id).status=='ready'
    assert not any(c[0]=='delete' for c in p.calls)


def test_source_cleanup_waits_for_other_unarchived_reference(prepared):
    p=prepared;p.row.status='ready';p.row.telegram_file_id='document-file'
    upload.patch(p.row,receipt=upload.receipt(media_response('document')))
    other=TicketAttachmentPreload(id=str(uuid4()),ticket_id=p.ticket.id,user_id=p.user.id,original_filename='shared.webp',
        local_path=str(p.path),status='failed',created_at=datetime.utcnow())
    p.db.add(other);p.db.commit();outbound._release_prepared_source(p.row,str(p.path))
    assert p.path.exists()
    p.db.delete(other);p.db.commit();outbound._release_prepared_source(p.row,str(p.path))
    assert not p.path.exists()


def test_group_copy_retains_archive_metadata_and_shared_source_until_last_ticket(prepared):
    from api.app.services import ticket_lifecycle as lifecycle
    from api.app.services.ticket_admin_actions import _delete_ticket_hard
    p=prepared;p.ticket.workflow_type='task';p.ticket.task_kind='group_child'
    parent=Ticket(id=str(uuid4()),company_id=p.ticket.company_id,client_id=p.ticket.client_id,subject='Group',workflow_type='task',task_kind='group_parent',status='pending');p.db.add(parent);p.db.flush();p.ticket.parent_ticket_id=parent.id
    message=TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='Child answer',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=p.row,delivery_status='ready')
    attachment.telegram_file_id='document-file';attachment.telegram_archive_message_id='301'
    upload.patch(attachment,receipt=upload.receipt(media_response('document')));p.db.commit()
    def add(db,ticket,kind,body,user_id):
        result=TicketMessage(id=str(uuid4()),ticket_id=ticket.id,message_type=kind.value,body=body,author_user_id=user_id,created_at=datetime.utcnow())
        db.add(result);db.flush();return result
    assert lifecycle._close_group_child_if_ready(p.db,p.ticket,message,SimpleNamespace(id=p.user.id),
        add_message_fn=add,finalize_group_parent_if_ready_fn=lambda *a,**kw:None)
    p.db.commit()
    from sqlalchemy import select
    copied=p.db.scalar(select(TicketAttachment).where(TicketAttachment.ticket_id==parent.id))
    assert copied.local_path==str(p.path) and copied.telegram_archive_message_id=='301'
    assert upload.state(copied)['receipt']['media_type']=='document'
    _delete_ticket_hard(p.db,p.ticket);p.db.commit();assert p.path.exists()
    _delete_ticket_hard(p.db,parent);p.db.commit();assert not p.path.exists()


def test_true_stickers_are_excluded_from_summaries_but_webp_documents_remain(prepared):
    from api.app.services import ticket_lifecycle as lifecycle, employee_telegram_notifications as notifications, telegram_attachments as inbound
    p=prepared
    message=TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='Answer',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachments=[]
    for source,name in [('sticker','actual-sticker.webp'),('document','ordinary.webp')]:
        attachment=TicketAttachment(id=str(uuid4()),ticket_id=p.ticket.id,message_id=message.id,source=source,
            original_filename=name,mime_type='image/webp',local_path='unused',created_at=datetime.utcnow())
        p.db.add(attachment);attachments.append(attachment)
    p.db.commit()
    values=[lifecycle._agent_answer_with_attachment_list(p.db,message,'Answer'),notifications._composite_message_body(message,attachments),
        inbound._inbound_group_archive_info_text(p.ticket,attachments,'Client')]
    for value in values:
        assert 'ordinary.webp' in value and 'actual-sticker.webp' not in value
    assert len(attachments)==2 and p.db.get(TicketAttachment,attachments[0].id).source=='sticker'



def test_recovered_archive_status_retries_edit_without_resending_file(prepared,monkeypatch):
    p=prepared
    _,attachment=delivery_fixture(p,{'delivery_attempt':{'active':True,'token':'dead','started_at':0}})
    monkeypatch.setattr(outbound,'edit_message_text',lambda *a,**kw:(_ for _ in ()).throw(outbound.TelegramApiError('synthetic timeout')))
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert upload.state(p.row)['archive_reconcile_pending'] is True
    assert attachment.telegram_delivery_status=='failed'
    monkeypatch.setattr(outbound,'edit_message_text',lambda *a,**kw:p.calls.append(('edit',a,kw)) or {'ok':True})
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert upload.state(p.row)['archive_reconcile_pending'] is False
    assert not any(c[0] in ['binary','sendDocument'] for c in p.calls)


@pytest.mark.parametrize('active', [False,True])
def test_card_recovery_keeps_old_failed_source_and_live_attempt(prepared,active):
    from datetime import timedelta
    import time
    p=prepared;key=p.row.id;p.row.created_at=datetime.utcnow()-timedelta(hours=25)
    p.row.status='uploading' if active else 'failed'
    if active: upload.patch(p.row,staging_attempt={'active':True,'token':'live','started_at':time.time()})
    p.db.commit();outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert p.path.exists()
    assert p.db.get(TicketAttachmentPreload,key) is not None


def test_document_restores_original_bytes_from_telegram_after_local_cleanup(prepared,monkeypatch):
    from api.app.services import ticket_attachments as files
    p=prepared;outbound.deliver_attachment_preload(preload_id=p.row.id);p.db.expire_all()
    message=TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='File',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=p.row,delivery_status='ready');p.db.commit()
    assert not p.path.exists()
    monkeypatch.setattr(files,'get_file',lambda file_id,**kw:{'result':{'file_path':'documents/original.webp'}})
    monkeypatch.setattr(files,'download_file',lambda path,destination,**kw:Path(destination).write_bytes(b'original bytes'))
    restored=files._ensure_local_attachment_file(attachment)
    assert restored.read_bytes()==b'original bytes'
    assert not any(c[0]=='delete' for c in p.calls)



def test_restart_releases_stale_archive_reservation_and_requires_confirmed_retry(prepared):
    p=prepared
    p.row.telegram_staging_info_message_id='pending:info:dead-worker'
    upload.patch(p.row,staging_attempt={'active':True,'token':'dead','started_at':0})
    p.db.commit();outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert p.row.status=='failed' and p.row.telegram_staging_info_message_id is None
    assert upload.state(p.row)['info_receipt_uncertain'] is True and p.path.exists()
    actor=SimpleNamespace(id=p.user.id,role='agent')
    with pytest.raises(HTTPException) as error:
        routes.retry_attachment_preload(p.ticket.id,p.row.id,BackgroundTasks(),False,db=p.db,user=actor)
    assert error.value.status_code==409
    routes.retry_attachment_preload(p.ticket.id,p.row.id,BackgroundTasks(),True,db=p.db,user=actor)
    outbound.deliver_attachment_preload(preload_id=p.row.id);p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,p.row.id).status=='ready'



def test_duplicate_upload_hash_check_cannot_overwrite_concurrent_receipt(prepared,monkeypatch):
    import time
    p=prepared;ticket_id,user_id=p.ticket.id,p.user.id;key=str(uuid4())
    monkeypatch.setattr(routes.settings,'attachment_storage_dir',str(p.path.parent/'concurrent-hash'))
    def post(db):
        return asyncio.run(routes.create_attachment_preload(ticket_id,BackgroundTasks(),
            UploadFile(file=BytesIO(b'WEBP'),filename='same.webp',headers={'content-type':'image/webp'}),
            None,key,db,SimpleNamespace(id=user_id,role='agent')))
    post(p.db)
    entered=threading.Event();release=threading.Event();stored=threading.Event();errors=[]
    digest=routes._stream_sha256
    def slow(stream):
        entered.set();assert release.wait(5);return digest(stream)
    monkeypatch.setattr(routes,'_stream_sha256',slow)
    def repeat():
        try:
            with p.factory() as db: post(db)
        except Exception as error: errors.append(error)
    def receipt_writer():
        try:
            with p.factory() as db:
                row=upload.lock_row(db,TicketAttachmentPreload,key)
                upload.patch(row,receipt=upload.receipt(media_response('document')))
                row.telegram_file_id='document-file';row.status='ready';db.commit();stored.set()
        except Exception as error: errors.append(error)
    a=threading.Thread(target=repeat);a.start();assert entered.wait(5)
    b=threading.Thread(target=receipt_writer);b.start();time.sleep(.05)
    assert not stored.is_set();release.set();a.join(10);b.join(10)
    assert not errors and stored.is_set()
    p.db.expire_all();info=upload.state(p.db.get(TicketAttachmentPreload,key))
    assert info['receipt']['media_type']=='document' and info['content_sha256']



@pytest.mark.parametrize('data,expected', [(b'WEBP',200),(b'VEBP',409)])
def test_upload_id_stays_idempotent_after_terminal_preload_cleanup(prepared,monkeypatch,data,expected):
    p=prepared;key=str(uuid4());actor=SimpleNamespace(id=p.user.id,role='agent')
    monkeypatch.setattr(routes.settings,'attachment_storage_dir',str(p.path.parent/'terminal'))
    def post(content):
        return asyncio.run(routes.create_attachment_preload(p.ticket.id,BackgroundTasks(),
            UploadFile(file=BytesIO(content),filename='same.webp',headers={'content-type':'image/webp'}),
            None,key,p.db,actor))
    post(b'WEBP');outbound.deliver_attachment_preload(preload_id=key);p.db.expire_all()
    row=p.db.get(TicketAttachmentPreload,key)
    message=TicketMessage(id=str(uuid4()),ticket_id=p.ticket.id,message_type='agent',body='Answer',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(message);p.db.flush()
    attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=row,delivery_status='sending');p.db.commit()
    outbound.deliver_ticket_attachment(attachment_id=attachment.id,message_id=message.id,ticket_id=p.ticket.id,preload_id=key)
    p.db.expire_all();assert p.db.get(TicketAttachmentPreload,key) is None
    if expected==200:
        result=post(data);assert result['id']==key and result['claimed'] is True
    else:
        with pytest.raises(HTTPException) as error: post(data)
        assert error.value.status_code==409
    assert len([c for c in p.calls if c[0]=='binary'])==1
    assert len([c for c in p.calls if c[0]=='sendDocument'])==1



@pytest.mark.parametrize('workflow', ['ticket','task'])
@pytest.mark.parametrize('receipt_kind', [None,'document','sticker'])
def test_open_failed_preload_survives_age_cleanup_and_card_reload(prepared,workflow,receipt_kind):
    from datetime import timedelta
    p=prepared;p.ticket.workflow_type=workflow;p.row.status='failed'
    p.row.created_at=datetime.utcnow()-timedelta(days=30)
    if receipt_kind: upload.patch(p.row,receipt=upload.receipt(media_response(receipt_kind)))
    p.db.commit()
    assert outbound.cleanup_stale_attachment_preloads(p.db)==0
    card=routes.get_ticket(p.ticket.id,db=p.db,user=SimpleNamespace(id=p.user.id,role='agent'))
    assert card.active_attachment_preloads[0]['id']==p.row.id
    assert p.path.read_bytes()==b'original bytes' and not any(c[0]=='delete' for c in p.calls)


def seed_close_answer(p,ticket):
    p.db.add(TicketMessage(id=str(uuid4()),ticket_id=ticket.id,message_type='client',body='Request',created_at=datetime.utcnow()))
    answer=TicketMessage(id=str(uuid4()),ticket_id=ticket.id,message_type='agent',body='Answer',author_user_id=p.user.id,created_at=datetime.utcnow())
    p.db.add(answer);return answer


@pytest.mark.parametrize('kind', ['ticket','task','group_child','group_parent'])
def test_real_close_cleans_failed_only_and_preserves_archive(prepared,monkeypatch,kind):
    p=prepared;key=p.row.id;ticket_id=p.ticket.id
    p.ticket.channel='web';p.ticket.workflow_type='ticket' if kind=='ticket' else 'task'
    p.row.status='failed';p.row.telegram_staging_file_message_id='301';p.row.telegram_staging_info_message_id='100'
    upload.patch(p.row,receipt=upload.receipt(media_response('sticker')))
    seed_close_answer(p,p.ticket)
    parent_key=None;parent_path=None;parent_id=None
    if kind=='group_child':
        parent=Ticket(id=str(uuid4()),company_id=p.ticket.company_id,client_id=p.ticket.client_id,subject='Group',channel='web',workflow_type='task',task_kind='group_parent',status='pending')
        p.db.add(parent);p.db.flush();parent_id=parent.id
        p.db.add(TicketMessage(id=str(uuid4()),ticket_id=parent.id,message_type='client',body='Group request',created_at=datetime.utcnow()))
        p.ticket.task_kind='group_child';p.ticket.parent_ticket_id=parent.id
        parent_path=p.path.parent/'parent-failed.webp';parent_path.write_bytes(b'parent')
        parent_key=str(uuid4())
        p.db.add(TicketAttachmentPreload(id=parent_key,ticket_id=parent.id,user_id=p.user.id,original_filename='parent.webp',local_path=str(parent_path),status='failed',created_at=datetime.utcnow()))
    elif kind=='group_parent': p.ticket.task_kind='group_parent'
    ready_path=p.path.parent/'ready.pdf';ready_path.write_bytes(b'ready')
    ready_id=str(uuid4())
    p.db.add(TicketAttachmentPreload(id=ready_id,ticket_id=ticket_id,user_id=p.user.id,original_filename='ready.pdf',local_path=str(ready_path),status='ready',telegram_file_id='ready-document',telegram_staging_file_message_id='302',created_at=datetime.utcnow()))
    p.db.commit()
    monkeypatch.setattr(routes,'_send_to_telegram_client',lambda *a,**kw:pytest.fail('WEB must not deliver Telegram messages'))
    assert routes.close(ticket_id,db=p.db,user=SimpleNamespace(id=p.user.id,role='admin'))=={'ok':True}
    p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()
    assert p.db.get(TicketAttachmentPreload,ready_id).status=='ready' and ready_path.read_bytes()==b'ready'
    assert not any(c[0]=='delete' for c in p.calls)
    if parent_key:
        assert p.db.get(Ticket,parent_id).status=='closed'
        assert p.db.get(TicketAttachmentPreload,parent_key) is None and not parent_path.exists()


def test_close_rollback_does_not_cleanup_failed_source(prepared):
    p=prepared;key=p.row.id;p.row.status='failed';p.db.commit()
    p.ticket.status='closed';p.db.flush()
    assert p.path.exists();p.db.rollback()
    p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key).status=='failed' and p.path.exists()
    assert not any(c[0]=='delete' for c in p.calls)


def test_closed_failed_cleanup_preserves_published_attachment_and_identifiers(prepared):
    p=prepared;key=p.row.id
    message=seed_close_answer(p,p.ticket);p.db.flush()
    attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=message,preload=p.row,delivery_status='failed')
    attachment_id=attachment.id;message_id=message.id
    p.row.status='failed';p.row.telegram_staging_file_message_id='301'
    upload.patch(p.row,receipt=upload.receipt(media_response('sticker')),previous_receipts=[{'message_id':'300','media_type':'photo'}])
    p.db.commit();p.ticket.status='closed';p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()
    saved=p.db.get(TicketAttachment,attachment_id)
    assert saved.telegram_archive_message_id=='301' and upload.state(saved)['receipt']['media_type']=='sticker'
    assert upload.state(saved)['previous_receipts'][0]['message_id']=='300'
    assert p.db.get(TicketMessage,message_id).message_status!='deleted'
    assert not any(c[0]=='delete' for c in p.calls)


def test_failed_cleanup_shared_source_waits_for_other_open_ticket(prepared):
    p=prepared;key=p.row.id;p.row.status='failed'
    other=Ticket(id=str(uuid4()),company_id=p.ticket.company_id,client_id=p.ticket.client_id,subject='Other',status='pending')
    p.db.add(other);p.db.flush();other_id=other.id;other_key=str(uuid4())
    p.db.add(TicketAttachmentPreload(id=other_key,ticket_id=other_id,user_id=p.user.id,original_filename='shared.webp',local_path=str(p.path),status='failed',created_at=datetime.utcnow()))
    p.db.commit();p.ticket.status='closed';p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is None and p.path.exists()
    assert p.db.get(TicketAttachmentPreload,other_key) is not None
    p.db.get(Ticket,other_id).status='closed';p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,other_key) is None and not p.path.exists()
    assert not any(c[0]=='delete' for c in p.calls)


def test_closed_failed_cleanup_waits_for_active_attempt_then_recovers(prepared):
    import time
    p=prepared;key=p.row.id;p.row.status='failed'
    upload.patch(p.row,staging_attempt={'active':True,'token':'finishing','started_at':time.time()})
    p.db.commit();p.ticket.status='closed';p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is not None and p.path.exists()
    row=p.db.get(TicketAttachmentPreload,key);upload.end_attempt(row,'staging_attempt','finishing');p.db.commit()
    outbound.recover_ticket_uploads(p.db,p.ticket.id)
    assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()
    assert not any(c[0]=='delete' for c in p.calls)



def test_group_failed_attachment_copy_keeps_shared_source_until_parent_closes(prepared):
    p=prepared;p.row.status='failed';p.ticket.workflow_type='task';key=p.row.id
    answer=seed_close_answer(p,p.ticket);p.db.flush()
    child_attachment=_attachment_from_preload(db=p.db,ticket=p.ticket,message=answer,preload=p.row,delivery_status='failed')
    child_id=child_attachment.id
    parent=Ticket(id=str(uuid4()),company_id=p.ticket.company_id,client_id=p.ticket.client_id,subject='Parent',workflow_type='task',task_kind='group_parent',status='pending')
    p.db.add(parent);p.db.flush();parent_id=parent.id
    parent_answer=seed_close_answer(p,parent);p.db.flush()
    copied=TicketAttachment(id=str(uuid4()),ticket_id=parent_id,message_id=parent_answer.id,source='helpdesk',local_path=str(p.path),telegram_delivery_status='failed',original_filename='copy.webp',created_at=datetime.utcnow())
    copied_id=copied.id;p.db.add(copied);p.db.commit()
    p.ticket.status='closed';p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is None and p.path.exists()
    p.db.get(Ticket,parent_id).status='closed';p.db.commit();p.db.expire_all()
    assert not p.path.exists()
    assert p.db.get(TicketAttachment,copied_id) is not None and p.db.get(TicketAttachment,child_id) is not None
    assert not any(c[0]=='delete' for c in p.calls)


def test_close_savepoint_rollback_keeps_failed_preload(prepared):
    p=prepared;key=p.row.id;p.row.status='failed';p.db.commit()
    nested=p.db.begin_nested();p.ticket.status='closed';p.db.flush();nested.rollback();p.db.commit();p.db.expire_all()
    assert p.db.get(TicketAttachmentPreload,key) is not None and p.path.exists()


def test_other_stale_unclaimed_draft_still_expires(prepared):
    from datetime import timedelta
    p=prepared;key=p.row.id;p.row.status='ready';p.row.created_at=datetime.utcnow()-timedelta(days=2);p.db.commit()
    assert outbound.cleanup_stale_attachment_preloads(p.db)==1
    assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()



def test_auto_close_cleans_failed_preload_without_telegram_archive_deletion(prepared,monkeypatch):
    from datetime import timedelta
    from api.app.services import auto_close
    p=prepared;key=p.row.id;p.row.status='failed'
    p.ticket.channel='web';p.ticket.intake_state='ready';p.ticket.auto_close_due_at=datetime.utcnow()-timedelta(minutes=1)
    p.db.commit()
    monkeypatch.setattr(auto_close,'_send_auto_close_to_client',lambda *a:None)
    assert auto_close.cleanup_auto_close_tickets(p.db)==1
    p.db.expire_all();assert p.ticket.status=='closed'
    assert p.db.get(TicketAttachmentPreload,key) is None and not p.path.exists()
    assert not any(c[0]=='delete' for c in p.calls)



@pytest.mark.parametrize('filename,mime,is_webp', [
    ('original.webp','image/webp',True), ('original.WEBP',None,True),
    ('photo.jpg','image/jpeg',False), ('photo.png','image/png',False),
    ('report.pdf','application/pdf',False), ('clip.mp4','video/mp4',False),
])
@pytest.mark.parametrize('source', ['bytes','path'])
def test_send_document_webp_multipart_preserves_original(monkeypatch,tmp_path,filename,mime,is_webp,source):
    from email.parser import BytesParser
    from email.policy import default
    import json

    original=b'Original binary\x00\xff\r\nunchanged'
    value=original
    if source=='path':
        value=tmp_path/filename;value.write_bytes(original)
    requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps(media_response('document')).encode()
    def capture(request,**kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(telegram_bot,'is_test_chat_id',lambda _:False)
    monkeypatch.setattr(telegram_bot,'_api_url',lambda method,**kw:'https://example.invalid/'+method)
    monkeypatch.setattr(telegram_bot,'urlopen',capture)
    telegram_bot.send_document('synthetic-chat',value,filename=filename,mime_type=mime)
    assert len(requests)==1
    request=requests[0]
    assert request.full_url=='https://example.invalid/sendDocument'
    content_type=request.get_header('Content-type')
    assert content_type.startswith('multipart/form-data; boundary=')
    message=BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+request.data)
    parts={part.get_param('name',header='content-disposition'):part for part in message.iter_parts()}
    assert parts['document'].get_filename()==filename
    assert parts['document'].get_content_type()==(mime or 'application/octet-stream')
    assert parts['document'].get_payload(decode=True)==original
    assert parts['document'].get_content_disposition()=='form-data'
    if is_webp:
        assert parts['disable_content_type_detection'].get_payload(decode=True)==b'true'
        assert parts['disable_content_type_detection'].get_filename() is None
    else:
        assert 'disable_content_type_detection' not in parts
