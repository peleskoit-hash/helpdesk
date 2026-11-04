"""Owner compatibility of the existing preload pipeline; isolated DBs only."""
from datetime import datetime, timedelta
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.models import Client, Company, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, User
from api.app.routers import tickets
from api.app.services import schema_evolution, telegram_outbound_delivery as pipeline, telegram_upload_state as state
from api.app.services.attachment_preload_owners import owned_client_preload
from api.app.services.ticket_attachments import _save_attachment_preload
from test_web_auth import engine


@pytest.fixture
def case(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'attachment_storage_dir', str(tmp_path / 'files'))
    monkeypatch.setattr(settings, 'telegram_staging_chat_id', 'technical-group')
    monkeypatch.setattr(pipeline, 'SessionLocal', lambda **kwargs: Session(kwargs.get('bind', engine), expire_on_commit=False))
    sends, edits, deletes = [], [], []
    def summary(chat, body, **kwargs):
        sends.append(('info', body))
        return {'ok': True, 'result': {'message_id': 100 + len(sends)}}
    def document(chat, path, **kwargs):
        sends.append(('file', Path(path).read_bytes()))
        return {'ok': True, 'result': {'message_id': 100 + len(sends),
            'document': {'file_id': 'archive-' + str(len(sends)), 'file_unique_id': 'unique-' + str(len(sends))}}}
    monkeypatch.setattr(pipeline, 'send_message', summary)
    monkeypatch.setattr(pipeline, 'send_document', document)
    monkeypatch.setattr(pipeline, 'edit_message_text', lambda *args, **kwargs: edits.append(args) or {'ok': True})
    monkeypatch.setattr(pipeline, 'delete_message', lambda *args, **kwargs: deletes.append(args) or {'ok': True})
    with Session(engine) as db:
        db.add_all([Company(id='co', name='Co'), Company(id='other', name='Other')])
        db.flush()
        db.add_all([Client(id='same', company_id='co', display_name='Actual client'),
                    Client(id='peer', company_id='co', display_name='Peer'),
                    Client(id='foreign', company_id='other', display_name='Foreign'),
                    User(id='same', company_id='co', login='staff', role='admin', full_name='Actual employee'),
                    User(id='staff2', company_id='co', login='staff2', role='agent', full_name='Second employee')])
        db.flush()
        db.add_all([Ticket(id='task', company_id='co', client_id='same', channel='web', workflow_type='task',
                           intake_state='collecting', task_kind='normal', status='unassigned', subject='Collecting task'),
                    Ticket(id='peer-task', company_id='co', client_id='peer', channel='web', workflow_type='task',
                           intake_state='collecting', task_kind='normal', status='unassigned', subject='Peer task')])
        db.commit()
    return SimpleNamespace(engine=engine, sends=sends, edits=edits, deletes=deletes, directory=tmp_path / 'files')


def save(case, *, client=False, task='task', owner='same', group='shared'):
    with Session(case.engine) as db:
        upload = UploadFile(filename='client.txt' if client else 'employee.txt', file=io.BytesIO(b'content'))
        row = _save_attachment_preload(db, db.get(Ticket, task), None if client else owner, upload, group,
                                      client_id=owner if client else None)
        db.commit()
        return row.id


@pytest.mark.parametrize('client', [False, True])
def test_same_pipeline_prepares_both_owners_without_changing_ticket(case, client):
    row_id = save(case, client=client)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        assert row.status == 'ready' and row.telegram_file_id and row.telegram_staging_info_message_id
        assert (row.user_id, row.client_id) == ((None, 'same') if client else ('same', None))
        assert db.get(Ticket, 'task').intake_state == 'collecting'
        assert set(db.scalars(select(User.id))) == {'same', 'staff2'}
        assert db.scalar(select(Ticket.id).where(Ticket.id != 'task', Ticket.id != 'peer-task')) is None
    info, = [body for kind, body in case.sends if kind == 'info']
    assert ('Клиент: Actual client' if client else 'Сотрудник: Actual employee') in info
    assert ('Actual employee' if client else 'Actual client') not in info
    assert len([kind for kind, _ in case.sends if kind == 'file']) == 1


def test_archive_groups_do_not_merge_when_user_and_client_ids_match(case):
    staff, client = save(case), save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=staff)
    pipeline.deliver_attachment_preload(preload_id=client)
    with Session(case.engine) as db:
        staff_row, client_row = db.get(TicketAttachmentPreload, staff), db.get(TicketAttachmentPreload, client)
        assert staff_row.telegram_staging_info_message_id != client_row.telegram_staging_info_message_id
        assert [r.id for r in pipeline._load_group_preloads(db, ticket_id='task', user_id='same', archive_group_id='shared')] == [staff]
        assert [r.id for r in pipeline._load_group_preloads(db, ticket_id='task', user_id=None, client_id='same', archive_group_id='shared')] == [client]
        client_info = client_row.telegram_staging_info_message_id
        pipeline._discard_preload(db, staff_row, delete_row=True, unlink_local=True)
        assert db.get(TicketAttachmentPreload, client).status == 'ready'
    assert all(str(args[1]) != client_info for args in case.deletes)
    assert len([kind for kind, _ in case.sends if kind == 'info']) == 2


def test_claim_and_staff_api_cannot_cross_owner_types(case):
    staff, client = save(case), save(case, client=True)
    with Session(case.engine) as db:
        assert pipeline.claim_attachment_preload(db, preload_id=staff, ticket_id='task', user_id='same').id == staff
        assert pipeline.claim_attachment_preload(db, preload_id=client, ticket_id='task', client_id='same').id == client
        for row_id, owner in [(client, {'user_id': 'same'}), (staff, {'client_id': 'same'}),
                              (client, {'client_id': 'peer'}), (staff, {'user_id': 'staff2'}),
                              (client, {}), (client, {'client_id': 'same', 'user_id': 'same'})]:
            assert pipeline.claim_attachment_preload(db, preload_id=row_id, ticket_id='task', **owner) is None
        actor = SimpleNamespace(id='same', role='admin', company_id='co')
        with pytest.raises(HTTPException) as error:
            tickets.get_attachment_preload_status('task', client, db=db, user=actor)
        assert error.value.status_code == 404
        for row_id, ticket_id, client_id in [(staff, 'task', 'same'), (client, 'peer-task', 'same'),
                                             (client, 'task', 'peer'), (client, 'task', 'foreign')]:
            with pytest.raises(HTTPException) as error:
                owned_client_preload(db, row_id, ticket_id, client_id)
            assert error.value.status_code == 404


@pytest.mark.parametrize('change', [dict(intake_state='ready'), dict(intake_state='cancelled'), dict(workflow_type='ticket'),
    dict(channel='telegram'), dict(task_kind='group_child'), dict(company_id='other'), dict(client_id='peer')])
def test_client_creation_requires_own_existing_collecting_task(case, change):
    with Session(case.engine) as db:
        task = db.get(Ticket, 'task')
        for key, value in change.items(): setattr(task, key, value)
        db.commit()
    with pytest.raises(HTTPException) as error:
        save(case, client=True)
    assert error.value.status_code == 404
    assert not case.directory.exists()


@pytest.mark.parametrize('client', [False, True])
def test_retry_reuses_preload_and_receipt_and_does_not_reupload(case, client):
    row_id = save(case, client=client)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    before = list(case.sends)
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        state.fail(row, 'test_failure')
        db.commit()
        if client:
            for owner in ['peer', None]:
                with pytest.raises(HTTPException) as error:
                    state.retry_preload(db, row, client_id=owner)
                assert error.value.status_code == 404
                db.rollback()
        row, schedule = state.retry_preload(db, row, **({'client_id': 'same'} if client else {}))
        assert schedule and row.id == row_id
    pipeline.deliver_attachment_preload(preload_id=row_id)
    assert case.sends == before


def test_client_cannot_retry_staff_row_even_with_matching_string_id(case):
    row_id = save(case)
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        with pytest.raises(HTTPException) as error:
            state.retry_preload(db, row, client_id='same')
        assert error.value.status_code == 404


def test_worker_does_not_prepare_after_client_task_leaves_collecting(case):
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        db.get(Ticket, 'task').intake_state = 'cancelled'
        db.commit()
    pipeline.deliver_attachment_preload(preload_id=row_id)
    assert case.sends == []
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_id) is None


def test_cleanup_of_client_preload_preserves_staff_archive(case):
    staff, client = save(case), save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=staff)
    pipeline.deliver_attachment_preload(preload_id=client)
    with Session(case.engine) as db:
        staff_info = db.get(TicketAttachmentPreload, staff).telegram_staging_info_message_id
        db.get(TicketAttachmentPreload, client).created_at = datetime.utcnow() - timedelta(hours=25)
        db.commit()
        assert pipeline.cleanup_stale_attachment_preloads(db) == 1
        assert db.get(TicketAttachmentPreload, client) is None
        assert db.get(TicketAttachmentPreload, staff).status == 'ready'
    assert all(str(args[1]) != staff_info for args in case.deletes)


def test_scoped_client_delete_keeps_staff_preload_and_shared_group(case):
    staff, client = save(case), save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=staff)
    pipeline.deliver_attachment_preload(preload_id=client)
    with Session(case.engine) as db:
        row = owned_client_preload(db, client, 'task', 'same')
        staff_info = db.get(TicketAttachmentPreload, staff).telegram_staging_info_message_id
        pipeline._discard_preload(db, row, delete_row=True, unlink_local=True)
        assert db.get(TicketAttachmentPreload, client) is None
        assert db.get(TicketAttachmentPreload, staff).status == 'ready'
    assert all(str(args[1]) != staff_info for args in case.deletes)


@pytest.mark.parametrize('user_id,client_id', [(None, None), ('same', 'same'), (None, 'missing')])
def test_database_rejects_missing_double_and_nonexistent_client_owners(case, user_id, client_id):
    with Session(case.engine) as db:
        db.add(TicketAttachmentPreload(id='invalid', ticket_id='task', user_id=user_id, client_id=client_id,
                                      local_path='unused', status='uploading'))
        with pytest.raises(IntegrityError): db.commit()


def test_explicit_owner_migration_preserves_legacy_staff_data_and_is_idempotent(case, monkeypatch):
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    with case.engine.begin() as conn:
        conn.execute(text('DROP TABLE ticket_attachment_preloads'))
    schema_evolution.ensure_ticket_attachment_preload_table()
    with case.engine.begin() as conn:
        conn.execute(text("INSERT INTO ticket_attachment_preloads (id,ticket_id,user_id,local_path,status,telegram_upload_state_json) "
            "VALUES ('legacy','task','same','old-path','ready',:receipt)"), {'receipt': '{"receipt":{"file_id":"kept"}}'})
        conn.execute(text('CREATE INDEX legacy_preload_status ON ticket_attachment_preloads(status)'))
    schema_evolution.ensure_ticket_attachment_preload_owners()
    schema_evolution.ensure_ticket_attachment_preload_owners()
    # The current ORM also requires the separately applied draft extension.
    schema_evolution.ensure_composer_draft_attachment_preloads()
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, 'legacy')
        assert row.user_id == 'same' and row.client_id is None and row.local_path == 'old-path'
        assert row.telegram_upload_state_json == {'receipt': {'file_id': 'kept'}}
    assert save(case, client=True)
    schema = inspect(case.engine)
    assert 'legacy_preload_status' in {index['name'] for index in schema.get_indexes('ticket_attachment_preloads')}
    assert next(column for column in schema.get_columns('ticket_attachment_preloads') if column['name'] == 'user_id')['nullable']
    assert 'ck_attachment_preload_owner' in {check['name'] for check in schema.get_check_constraints('ticket_attachment_preloads')}
    assert any(fk['constrained_columns'] == ['client_id'] and fk['referred_table'] == 'clients'
               for fk in schema.get_foreign_keys('ticket_attachment_preloads'))
    assert 'ensure_ticket_attachment_preload_owners' not in (Path(__file__).parents[1] / 'api/app/main.py').read_text(encoding='utf-8')


def transition(case, action):
    from api.app.services.ticket_admin_actions import _delete_ticket_hard
    from api.app.services.ticket_lifecycle import close_ticket_by_actor
    with Session(case.engine) as db:
        task = db.get(Ticket, 'task')
        warnings = []
        if action == 'delete':
            warnings = _delete_ticket_hard(db, task)
        elif action == 'close':
            assert close_ticket_by_actor(db, task, SimpleNamespace(id='same', role='admin'),
                force_without_agent_reply_for_task=True, skip_client_notification=True,
                is_admin_fn=lambda actor: True, get_or_create_client_profile_fn=lambda *a: None,
                last_agent_message_fn=tickets._last_agent_message, first_client_message_fn=tickets._first_client_message,
                send_to_telegram_client_fn=lambda *a, **kw: pytest.fail('Unexpected client delivery'),
                actor_display_name_fn=lambda *a: 'Admin', add_message_fn=tickets._add_message,
                message_attachments_fn=tickets._message_attachments,
                send_attachment_to_telegram_client_fn=lambda *a, **kw: pytest.fail('Unexpected client delivery'),
                onsite_close_reminder_text='onsite', ticket_closed_client_text='closed') == 'closed'
        else:
            from api.app.services.web_tasks import close_draft
            close_draft(db, task, 'cancelled')
        db.commit()
        return warnings


@pytest.mark.parametrize('action', ['cancel', 'close', 'delete'])
@pytest.mark.parametrize('uploaded', [False, True])
def test_retired_client_preload_is_completely_removed(case, action, uploaded):
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        path = Path(db.get(TicketAttachmentPreload, row_id).local_path)
    if uploaded:
        pipeline.deliver_attachment_preload(preload_id=row_id)
    assert transition(case, action) == []
    pipeline.deliver_attachment_preload(preload_id=row_id)
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_id) is None
    assert not path.exists()
    assert {str(args[1]) for args in case.deletes} == ({'101', '102'} if uploaded else set())


@pytest.mark.parametrize('action', ['cancel', 'close', 'delete'])
@pytest.mark.parametrize('phase', ['send_document', 'send_message'])
def test_retirement_during_network_request_cleans_late_receipt(case, monkeypatch, action, phase):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started, release = Event(), Event()
    original = getattr(pipeline, phase)
    def paused(*args, **kwargs):
        response = original(*args, **kwargs)
        started.set()
        assert release.wait(10)
        return response
    monkeypatch.setattr(pipeline, phase, paused)
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        path = Path(db.get(TicketAttachmentPreload, row_id).local_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(pipeline.deliver_attachment_preload, preload_id=row_id)
        try:
            assert started.wait(10)
            assert transition(case, action) == []
        finally:
            release.set()
        worker.result(timeout=15)
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_id) is None
    assert not path.exists()
    assert {str(args[1]) for args in case.deletes} == ({'101', '102'} if phase == 'send_document' else {'101'})


def test_cancel_preserves_staff_preloads_and_files(case):
    staff, client = save(case), save(case, client=True)
    with Session(case.engine) as db:
        staff_path = Path(db.get(TicketAttachmentPreload, staff).local_path)
    pipeline.deliver_attachment_preload(preload_id=client)
    transition(case, 'cancel')
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, staff).status == 'uploading'
        assert db.get(TicketAttachmentPreload, client) is None
    assert staff_path.read_bytes() == b'content'


def test_delete_rollback_keeps_preloads_and_external_resources(case):
    from api.app.services.ticket_admin_actions import _delete_ticket_hard
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        path = Path(db.get(TicketAttachmentPreload, row_id).local_path)
        _delete_ticket_hard(db, db.get(Ticket, 'task'))
        db.rollback()
        assert db.get(TicketAttachmentPreload, row_id) is not None
        db.commit()
    assert path.exists() and case.deletes == []


def test_telegram_delete_failure_is_recorded_and_cleanup_can_retry(case, monkeypatch, caplog):
    row_id = save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    original = pipeline.delete_message
    def unavailable(*args, **kwargs):
        raise RuntimeError('temporary Telegram failure')
    monkeypatch.setattr(pipeline, 'delete_message', unavailable)
    transition(case, 'cancel')
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        assert row.status == 'cancelled'
        assert set(state.state(row)['cleanup_failed_message_ids']) == {'101', '102'}
        assert state.state(row)['error_code'] == 'archive_cleanup_failed'
    assert '101' in caplog.text and '102' in caplog.text
    monkeypatch.setattr(pipeline, 'delete_message', original)
    with Session(case.engine) as db:
        pipeline.cleanup_client_preloads(db, 'task')
        assert db.get(TicketAttachmentPreload, row_id) is None


def test_hard_delete_telegram_failure_returns_admin_warning(case, monkeypatch, caplog):
    row_id = save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    def unavailable(*args, **kwargs):
        raise RuntimeError('temporary Telegram failure')
    monkeypatch.setattr(pipeline, 'delete_message', unavailable)
    warnings = transition(case, 'delete')
    assert len(warnings) == 2 and '101' in caplog.text and '102' in caplog.text
    with Session(case.engine) as db:
        assert db.get(Ticket, 'task') is None


def bind_client_file(case, row_id):
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        db.add(TicketMessage(id='initial', ticket_id='task', message_type='client', body='Initial request'))
        db.flush()
        db.add(TicketAttachment(id='bound', ticket_id='task', message_id='initial', source='web',
            local_path=row.local_path, original_filename=row.original_filename, telegram_delivery_status='uploading'))
        db.flush()
        row.final_attachment_id = 'bound'
        task = db.get(Ticket, 'task')
        task.intake_state, task.status = 'ready', 'pending'
        db.commit()


def test_bound_client_upload_completes_after_ready_without_client_delivery(case, monkeypatch):
    row_id = save(case, client=True)
    bind_client_file(case, row_id)
    monkeypatch.setattr(pipeline, 'deliver_ticket_attachment', lambda **kw: pytest.fail('Client file is inbound'))
    pipeline.deliver_attachment_preload(preload_id=row_id)
    with Session(case.engine) as db:
        row, attachment = db.get(TicketAttachmentPreload, row_id), db.get(TicketAttachment, 'bound')
        assert row.status == 'ready' and attachment.telegram_delivery_status == 'ready'
        assert attachment.telegram_file_id and attachment.telegram_archive_message_id == '102'
        assert attachment.telegram_archive_info_message_id == '101'
        assert db.get(Ticket, 'task').intake_state == 'ready'
        state.fail(row, 'retry-test')
        db.commit()
        assert state.retry_preload(db, row, client_id='same')[1]
    pipeline.deliver_attachment_preload(preload_id=row_id)
    assert len(case.sends) == 2 and case.deletes == []
    with pytest.raises(HTTPException):
        save(case, client=True)


def test_sqlite_owner_rebuild_preserves_constraints_indexes_triggers_and_defaults(case, monkeypatch):
    if case.engine.dialect.name != 'sqlite':
        pytest.skip('SQLite rebuild only')
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    with case.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE ticket_attachment_preloads')
        conn.exec_driver_sql('''CREATE TABLE "ticket_attachment_preloads" (
            id VARCHAR(36) PRIMARY KEY, ticket_id VARCHAR(36) NOT NULL REFERENCES tickets(id),
            "user_id" VARCHAR(36) NOT NULL REFERENCES users(id), local_path TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'uploading' CHECK (status IN ('ready','uploading')),
            CONSTRAINT legacy_staff_present CHECK (user_id IS NOT NULL OR status = 'ready'))''')
        conn.exec_driver_sql('CREATE INDEX custom_status ON ticket_attachment_preloads(status)')
        conn.exec_driver_sql('CREATE TABLE audit_preloads (preload_id TEXT)')
        conn.exec_driver_sql('''CREATE TRIGGER custom_preload_insert AFTER INSERT ON ticket_attachment_preloads
            BEGIN INSERT INTO audit_preloads VALUES (NEW.id); END''')
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads(id,ticket_id,user_id,local_path) VALUES ('old','task','same','path')")
    for _ in range(2):
        schema_evolution.ensure_ticket_attachment_preload_owners()
    schema = inspect(case.engine)
    assert {i['name'] for i in schema.get_indexes('ticket_attachment_preloads')} >= {
        'custom_status', 'ix_ticket_attachment_preloads_ticket_user', 'ix_ticket_attachment_preloads_ticket_client'}
    assert {c['name'] for c in schema.get_check_constraints('ticket_attachment_preloads')} >= {'legacy_staff_present', 'ck_attachment_preload_owner'}
    with case.engine.begin() as conn:
        assert conn.exec_driver_sql('SELECT id,user_id,client_id,local_path,status FROM ticket_attachment_preloads').one() == ('old','same',None,'path','uploading')
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads(id,ticket_id,user_id,local_path) VALUES ('new','task','same','path2')")
        assert conn.exec_driver_sql('SELECT preload_id FROM audit_preloads ORDER BY preload_id').scalars().all() == ['new','old']
    with pytest.raises(IntegrityError), case.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads(id,ticket_id,user_id,local_path) VALUES ('dup','task','same','path')")
    with pytest.raises(IntegrityError), case.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads(id,ticket_id,user_id,local_path,status) VALUES ('invalid','task','same','path3','invalid')")


def test_sqlite_nullable_owner_check_is_not_rewritten_when_adding_missing_fk(case, monkeypatch):
    if case.engine.dialect.name != 'sqlite':
        pytest.skip('SQLite rebuild only')
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    with case.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE ticket_attachment_preloads')
        conn.exec_driver_sql('''CREATE TABLE ticket_attachment_preloads (
            id TEXT PRIMARY KEY, ticket_id TEXT REFERENCES tickets(id), user_id TEXT REFERENCES users(id),
            client_id TEXT, CONSTRAINT ck_attachment_preload_owner CHECK (
                (user_id IS NOT NULL AND client_id IS NULL) OR (user_id IS NULL AND client_id IS NOT NULL)))''')
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads VALUES ('old','task','same',NULL)")
    schema_evolution.ensure_ticket_attachment_preload_owners()
    schema_evolution.ensure_ticket_attachment_preload_owners()
    with case.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads VALUES ('client','task',NULL,'same')")
    with pytest.raises(IntegrityError), case.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads VALUES ('invalid','task','same','same')")
    with pytest.raises(IntegrityError), case.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads VALUES ('foreign','task',NULL,'missing')")


@pytest.mark.parametrize('failure', ['inbound_fk', 'invalid_legacy_owner'])
def test_sqlite_owner_migration_failure_rolls_back_whole_rebuild(case, monkeypatch, failure):
    if case.engine.dialect.name != 'sqlite':
        pytest.skip('SQLite rebuild only')
    monkeypatch.setattr(schema_evolution, 'engine', case.engine)
    with case.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE ticket_attachment_preloads')
        conn.exec_driver_sql('CREATE TABLE ticket_attachment_preloads (id TEXT PRIMARY KEY, ticket_id TEXT, user_id TEXT)')
        conn.exec_driver_sql("INSERT INTO ticket_attachment_preloads VALUES ('old','task'," + ("NULL" if failure == 'invalid_legacy_owner' else "'same'") + ")")
        conn.exec_driver_sql('CREATE INDEX preserved ON ticket_attachment_preloads(ticket_id)')
        if failure == 'inbound_fk':
            conn.exec_driver_sql('CREATE TABLE dependency (id TEXT REFERENCES ticket_attachment_preloads(id))')
            conn.exec_driver_sql("INSERT INTO dependency VALUES ('old')")
    with pytest.raises((RuntimeError, IntegrityError)):
        schema_evolution.ensure_ticket_attachment_preload_owners()
    schema = inspect(case.engine)
    assert 'client_id' not in {c['name'] for c in schema.get_columns('ticket_attachment_preloads')}
    assert 'ticket_attachment_preloads_owner_upgrade' not in schema.get_table_names()
    assert {i['name'] for i in schema.get_indexes('ticket_attachment_preloads')} == {'preserved'}
    with case.engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT id FROM ticket_attachment_preloads').scalar_one() == 'old'


def test_bound_upload_survives_collecting_to_ready_during_network_request(case, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started, release = Event(), Event()
    original = pipeline.send_document
    def paused(*args, **kwargs):
        response = original(*args, **kwargs)
        started.set()
        assert release.wait(10)
        return response
    monkeypatch.setattr(pipeline, 'send_document', paused)
    monkeypatch.setattr(pipeline, 'deliver_ticket_attachment', lambda **kw: pytest.fail('Client file is inbound'))
    row_id = save(case, client=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(pipeline.deliver_attachment_preload, preload_id=row_id)
        try:
            assert started.wait(10)
            bind_client_file(case, row_id)
        finally:
            release.set()
        worker.result(timeout=15)
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_id).status == 'ready'
        assert db.get(TicketAttachment, 'bound').telegram_archive_message_id == '102'
        assert db.get(Ticket, 'task').intake_state == 'ready'
    assert case.deletes == []


def test_closed_ready_task_keeps_bound_client_archive(case):
    row_id = save(case, client=True)
    bind_client_file(case, row_id)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    with Session(case.engine) as db:
        db.get(Ticket, 'task').status = 'closed'
        db.commit()
        pipeline.recover_ticket_uploads(db, 'task')
        assert db.get(TicketAttachmentPreload, row_id).status == 'ready'
        assert db.get(TicketAttachment, 'bound').telegram_archive_message_id == '102'
    assert case.deletes == []


def test_hard_delete_removes_bound_preloads_but_keeps_other_ticket_sources(case):
    row_id = save(case, client=True)
    bind_client_file(case, row_id)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    staff = save(case, task='peer-task')
    pipeline.deliver_attachment_preload(preload_id=staff)
    with Session(case.engine) as db:
        staff_row = db.get(TicketAttachmentPreload, staff)
        protected = {staff_row.telegram_staging_file_message_id, staff_row.telegram_staging_info_message_id}
    assert transition(case, 'delete') == []
    with Session(case.engine) as db:
        assert db.get(TicketAttachmentPreload, row_id) is None
        assert db.get(TicketAttachment, 'bound') is None
        assert db.get(TicketAttachmentPreload, staff).status == 'ready'
    assert {str(args[1]) for args in case.deletes} == {'101', '102'}
    assert protected.isdisjoint(str(args[1]) for args in case.deletes)


def test_admin_bulk_delete_response_includes_postcommit_archive_warnings(case, monkeypatch):
    row_id = save(case, client=True)
    pipeline.deliver_attachment_preload(preload_id=row_id)
    def unavailable(*args, **kwargs):
        raise RuntimeError('temporary Telegram failure')
    monkeypatch.setattr(pipeline, 'delete_message', unavailable)
    with Session(case.engine) as db:
        response = tickets.admin_delete_tickets(SimpleNamespace(ticket_ids=['task'], skip_client_notification=True),
            db=db, user=SimpleNamespace(id='same', role='admin', company_id='co'))
    assert response.deleted == 1 and len(response.file_delete_warnings) == 2


def test_failed_local_cleanup_keeps_retryable_cancelled_record(case, monkeypatch, caplog):
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        path = Path(db.get(TicketAttachmentPreload, row_id).local_path)
    original = Path.unlink
    def unavailable(target, *args, **kwargs):
        if target == path:
            raise OSError('busy file')
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unavailable)
    transition(case, 'cancel')
    with Session(case.engine) as db:
        row = db.get(TicketAttachmentPreload, row_id)
        assert row.status == 'cancelled' and state.state(row)['cleanup_local_failed']
    assert path.exists() and str(path) in caplog.text
    monkeypatch.setattr(Path, 'unlink', original)
    with Session(case.engine) as db:
        pipeline.recover_ticket_uploads(db, 'task')
        assert db.get(TicketAttachmentPreload, row_id) is None
    assert not path.exists()


def test_group_hard_delete_detaches_preloads_of_both_owners_before_ticket_fks(case):
    client = save(case, client=True)
    staff = save(case, task='peer-task')
    with Session(case.engine) as db:
        paths = [Path(db.get(TicketAttachmentPreload, row_id).local_path) for row_id in [client, staff]]
        child = db.get(Ticket, 'peer-task')
        child.parent_ticket_id, child.task_kind = 'task', 'group_child'
        db.commit()
    assert transition(case, 'delete') == []
    with Session(case.engine) as db:
        assert list(db.scalars(select(TicketAttachmentPreload.id))) == []
        assert list(db.scalars(select(Ticket.id))) == []
    assert all(not path.exists() for path in paths)


def test_foreign_final_attachment_cannot_authorize_ready_client_preload(case):
    row_id = save(case, client=True)
    with Session(case.engine) as db:
        own_path = Path(db.get(TicketAttachmentPreload, row_id).local_path)
        foreign_path = case.directory / 'foreign-file'
        foreign_path.write_bytes(b'foreign')
        db.add(TicketMessage(id='foreign-message', ticket_id='peer-task', message_type='client', body='Other client'))
        db.flush()
        db.add(TicketAttachment(id='foreign-attachment', ticket_id='peer-task', message_id='foreign-message',
            source='web', local_path=str(foreign_path), telegram_archive_message_id='foreign-archive'))
        db.flush()
        db.get(TicketAttachmentPreload, row_id).final_attachment_id = 'foreign-attachment'
        db.get(Ticket, 'task').intake_state = 'ready'
        db.commit()
        assert db.get(TicketAttachmentPreload, row_id) is None
        assert db.get(TicketAttachment, 'foreign-attachment') is not None
    assert foreign_path.read_bytes() == b'foreign' and not own_path.exists()
    assert case.deletes == []
