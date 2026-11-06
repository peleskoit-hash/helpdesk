"""Uncreated task removal uses synthetic DBs and mocked Telegram."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from api.app.models import Ticket, TicketMessage, TicketAttachment, TicketAttachmentPreload, StatsCompletion, StatsWorkInterval
from api.app.routers import telegram, tickets
from api.app.schemas import AdminBulkTicketsPayload
from api.app.services.ticket_admin_actions import is_uncreated_normal_task
from test_telegram_preliminary_cancel import case, draft, cancel
from test_ticket_review_telegram import _message_payload


@pytest.mark.parametrize('channel,via', [('telegram', 'command'), ('telegram', 'button'),
    ('telegram', 'timeout'), ('telegram', 'message-timeout'), ('telegram', 'admin'), ('web', 'admin')])
def test_uncreated_task_is_physically_removed(case, tmp_path, channel, via):
    ticket = draft(case)
    ticket.workflow_type, ticket.channel = 'task', channel
    # A number is deliberately present: it is not evidence of a client description.
    ticket.ticket_number = 51
    task_id = ticket.id
    path = tmp_path / 'preload.txt'
    path.write_text('pending')
    message = TicketMessage(id=str(uuid.uuid4()), ticket_id=task_id, message_type='system', body='Selected')
    case.db.add(message)
    case.db.flush()
    case.db.add_all([
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=task_id, message_id=message.id,
                         source='telegram', local_path=str(path), telegram_archive_message_id='501'),
        TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=task_id, client_id=case.client.id,
                                local_path=str(path), status='uploading', telegram_staging_file_message_id='502'),
        StatsCompletion(id=str(uuid.uuid4()), company_id=case.company.id, object_id=task_id, object_type='task'),
        StatsWorkInterval(id=str(uuid.uuid4()), company_id=case.company.id, object_id=task_id,
                          object_type='task', user_id=case.agent.id, started_at=datetime.utcnow()),
    ])
    if via in ('timeout', 'message-timeout'):
        ticket.updated_at = datetime.utcnow() - timedelta(minutes=11)
    case.db.commit()
    if via == 'admin':
        payload = AdminBulkTicketsPayload(ticket_ids=[task_id])
        assert tickets.admin_close_tickets(payload, db=case.db, user=case.admin).closed == 1
        assert tickets.admin_close_tickets(payload, db=case.db, user=case.admin).not_found == [task_id]
    elif via == 'message-timeout':
        payload = _message_payload(update_id=3001, contact=case.contact, text='Late description')
        assert telegram.process_telegram_update(case.db, payload, notify_client=True)['task_pending_expired']
    else:
        cancel(case, ticket, via)
    case.db.expire_all()
    assert case.db.get(Ticket, task_id) is None
    assert task_id not in case.db.scalars(select(Ticket.id)).all()
    history = tickets.list_closed(sort='created_at', order='desc', page=1, limit=20,
        query=None, assignee_ids=None, db=case.db, user=case.admin)
    assert task_id not in [item.id for item in history.items]
    for model in (TicketMessage, TicketAttachment, TicketAttachmentPreload):
        assert case.db.scalar(select(model.id).where(model.ticket_id == task_id)) is None
    for model in (StatsCompletion, StatsWorkInterval):
        assert case.db.scalar(select(model.id).where(model.object_id == task_id)) is None
    assert not path.exists()
    assert {call.args[0] for call in case.archive_delete.call_args_list} == {'501', '502'}
    if channel == 'telegram':
        assert case.send.called
    assert telegram.cleanup_expired_task_intents(case.db) == 0


@pytest.mark.parametrize('changes', [ {'workflow_type': 'ticket'}, {'workflow_type': 'auto'},
    {'channel': 'internal'}, {'task_kind': 'group_parent'}, {'task_kind': 'group_child'},
    {'parent_ticket_id': 'parent'}, {'is_special': True}, {'assignee_id': 'agent'},
    {'status': 'pending'}, {'intake_state': 'ready'}])
def test_strict_task_guard(case, changes):
    ticket = draft(case)
    ticket.workflow_type = 'task'
    assert is_uncreated_normal_task(case.db, ticket)
    for key, value in changes.items():
        setattr(ticket, key, value)
    assert not is_uncreated_normal_task(case.db, ticket)


@pytest.mark.parametrize('channel', ['telegram', 'web'])
def test_client_description_preserves_task_on_force_close(case, channel):
    ticket = draft(case)
    ticket.workflow_type, ticket.channel = 'task', channel
    case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, message_type='client', body='Description'))
    case.db.commit()
    assert not is_uncreated_normal_task(case.db, ticket)
    assert tickets.admin_close_tickets(AdminBulkTicketsPayload(ticket_ids=[ticket.id]), db=case.db, user=case.admin).closed == 1
    case.db.refresh(ticket)
    assert ticket.status == 'closed'
    assert case.db.scalar(select(TicketMessage.id).where(TicketMessage.ticket_id == ticket.id,
        TicketMessage.message_type == 'client')) is not None


@pytest.fixture
def forbid_deleted_task_access(monkeypatch):
    from api.app.services import web_tasks, telegram_client_close
    deleted = set()
    columns = set(Ticket.__table__.columns.keys())
    get_attribute, set_attribute = Ticket.__getattribute__, Ticket.__setattr__

    def get_guard(ticket, key):
        if (id(ticket) in deleted and key in columns
                and get_attribute(ticket, '_sa_instance_state').was_deleted):
            pytest.fail(f'Read of deleted task: {key}')
        return get_attribute(ticket, key)

    def set_guard(ticket, key, value):
        if id(ticket) in deleted and key in columns:
            pytest.fail(f'Write to deleted task: {key}')
        return set_attribute(ticket, key, value)

    monkeypatch.setattr(Ticket, '__getattribute__', get_guard)
    monkeypatch.setattr(Ticket, '__setattr__', set_guard)
    for module in (web_tasks, telegram_client_close, telegram):
        original = module.delete_uncreated_normal_task
        def remove(db, ticket, original=original):
            original(db, ticket)
            deleted.add(id(ticket))
        monkeypatch.setattr(module, 'delete_uncreated_normal_task', remove)
    return deleted


@pytest.mark.parametrize('via', ['command', 'button', 'timeout', 'message-timeout'])
def test_telegram_notifications_do_not_read_deleted_task(case, via, forbid_deleted_task_access):
    ticket = draft(case)
    ticket.workflow_type = 'task'
    task_id = ticket.id
    if via in ('timeout', 'message-timeout'):
        ticket.updated_at = datetime.utcnow() - timedelta(minutes=11)
    case.db.commit()
    if via == 'message-timeout':
        payload = _message_payload(update_id=3101, contact=case.contact, text='Late description')
        assert telegram.process_telegram_update(case.db, payload, notify_client=True)['task_pending_expired']
    else:
        cancel(case, ticket, via)
    assert forbid_deleted_task_access
    assert case.db.get(Ticket, task_id) is None
    assert case.send.called
    if via in ('command', 'button'):
        assert case.send.call_args.kwargs['remove_keyboard'] is True
        assert case.send.call_args.kwargs['reply_markup'] is None


@pytest.mark.parametrize('via', ['command', 'button'])
@pytest.mark.parametrize('overridden', [False, True])
def test_client_task_cancellation_uses_own_template(case, via, overridden):
    from api.app.models import AutomaticMessageTemplate
    from api.app.services.automatic_messages import TASK_CREATION_CANCELLED_TEXT
    ticket = draft(case)
    ticket.workflow_type = 'task'
    task_id = ticket.id
    case.db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key='telegram.ticket.close.client',
        body='WRONG: review and suggestions', is_overridden=True, html_enabled=False))
    if overridden:
        case.db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key='telegram.task.creation_cancelled.client',
            body='Настроенная отмена задачи.', is_overridden=True, html_enabled=False))
    case.db.commit()
    cancel(case, ticket, via)
    assert case.db.get(Ticket, task_id) is None
    assert case.send.call_count == 1
    args, kwargs = case.send.call_args
    assert (args[1] if len(args) > 1 else kwargs['text']) == (
        'Настроенная отмена задачи.' if overridden else TASK_CREATION_CANCELLED_TEXT)
    assert kwargs['remove_keyboard'] is True and kwargs['reply_markup'] is None


@pytest.mark.parametrize('skip', [False, True])
@pytest.mark.parametrize('overridden', [False, True])
def test_admin_uncreated_task_uses_closed_without_answer_template(case, skip, overridden):
    from api.app.models import AutomaticMessageTemplate
    from api.app.services.ticket_lifecycle import format_task_closed_without_answer_text
    ticket = draft(case)
    ticket.workflow_type = 'task'
    task_id = ticket.id
    if overridden:
        case.db.add(AutomaticMessageTemplate(id=str(uuid.uuid4()), key='telegram.task.closed_without_answer.client',
            body='Настроенное закрытие задачи.', is_overridden=True, html_enabled=False))
    case.db.commit()
    expected = format_task_closed_without_answer_text(case.db, ticket, None)
    result = tickets.admin_close_tickets(AdminBulkTicketsPayload(ticket_ids=[task_id], skip_client_notification=skip),
        db=case.db, user=case.admin)
    assert result.closed == 1 and case.db.get(Ticket, task_id) is None
    if skip:
        case.send.assert_not_called()
    else:
        case.send.assert_called_once()
        assert case.send.call_args.args[1] == expected
        assert case.send.call_args.kwargs['remove_keyboard'] is True
        assert case.send.call_args.kwargs.get('reply_markup') is None
        if overridden:
            assert expected == 'Настроенное закрытие задачи.'
