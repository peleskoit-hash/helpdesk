"""Cancellation uses synthetic SQLite and mocked Telegram only."""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text

from api.app.config import settings
from api.app.models import (
    BotButtonSetting, Client, ClientProfile, StatsCompletion, StatsWorkInterval,
    TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketFeedback,
    TicketFeedbackInput, TicketMessage, TicketNumberCounter, TicketReviewCycle, TicketView,
)
from api.app.routers import telegram, tickets
from api.app.schemas import AdminBulkTicketsPayload
from api.app.services import telegram_menu_flow, telegram_outbound_delivery
from api.app.services.ticket_admin_actions import delete_preliminary_telegram_ticket, is_preliminary_telegram_ticket
from test_ticket_review_telegram import _session, _seed_base, _seed_ticket, _callback_payload, _message_payload


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', '')
    db = _session()
    company, client, contact, agent, admin = _seed_base(db)
    db.execute(text('PRAGMA foreign_keys=ON'))
    profile = ClientProfile(id=str(uuid.uuid4()), client_id=client.id, is_verified=True, destination='Support')
    db.add_all([profile, TicketNumberCounter(id=1, last_number=50)])
    db.commit()
    monkeypatch.setattr(telegram, 'schedule_avatar_refresh', Mock())
    send = Mock(return_value={'ok': True, 'result': {'message_id': 99}})
    removed = Mock()
    for module in (telegram, tickets, telegram_menu_flow):
        monkeypatch.setattr(module, 'send_message', send, raising=False)
        monkeypatch.setattr(module, 'remove_inline_keyboard', removed)
        monkeypatch.setattr(module, 'delete_message', removed, raising=False)
    monkeypatch.setattr(telegram, '_safe_remove_inline_keyboard', removed)
    monkeypatch.setattr(telegram, 'answer_callback_query', Mock())
    from api.app.services import telegram_bot
    monkeypatch.setattr(telegram_bot, 'remove_inline_keyboard', removed)
    archive_delete = Mock(return_value=True)
    monkeypatch.setattr(telegram_outbound_delivery, '_delete_archive_message_by_id', archive_delete)
    yield SimpleNamespace(db=db, company=company, client=client, contact=contact,
                          agent=agent, admin=admin, profile=profile, send=send,
                          removed=removed, archive_delete=archive_delete)
    db.close()
    db.get_bind().dispose()


def draft(case):
    ticket = telegram._create_collecting_ticket(case.db, case.company.id, case.client.id)
    ticket.client_close_message_id = '12'
    ticket.menu_message_id = '11'
    case.db.commit()
    return ticket


def cancel(case, ticket, via):
    if via == 'timeout':
        ticket.updated_at = datetime.utcnow() - timedelta(minutes=11)
        case.db.commit()
        assert telegram.cleanup_expired_task_intents(case.db) == 1
    elif via in ('command', 'button'):
        payload = (_message_payload(update_id=2001, contact=case.contact, text='/close') if via == 'command'
                   else _callback_payload(update_id=2001, contact=case.contact, data='client_close', message_id=12))
        assert telegram.process_telegram_update(case.db, payload, notify_client=True)['closed'] is True
    elif via in ('agent', 'admin'):
        assert tickets.close(ticket.id, db=case.db, user=getattr(case, via)) == {'ok': True}
    elif via == 'bulk':
        second = draft(case)
        second_id = second.id
        result = tickets.admin_close_tickets(AdminBulkTicketsPayload(ticket_ids=[ticket.id, second_id]),
                                             db=case.db, user=case.admin)
        assert result.closed == 2 and not result.not_found
        assert case.db.get(Ticket, second_id) is None
    elif via == 'restore':
        old = case.db.scalar(select(Ticket).where(Ticket.ticket_number == 50))
        assert tickets.restore(old.id, db=case.db, user=case.admin) == {'ok': True}


@pytest.mark.parametrize('via', ['timeout', 'command', 'button', 'agent', 'admin', 'bulk', 'restore'])
def test_cancel_removes_only_unformed_ticket_and_technical_data(case, tmp_path, via):
    history, history_message = _seed_ticket(case.db, case.company, case.client, case.agent)
    history.ticket_number = 50
    history.status = 'closed'
    case.db.commit()
    history_id, history_message_id = history.id, history_message.id
    ticket = draft(case)
    ticket_id = ticket.id
    assert ticket.status == 'unassigned' and ticket.ticket_number is None
    assert telegram._collecting_ticket_for_client(case.db, case.company.id, case.client.id).id == ticket_id
    assert case.db.get(TicketNumberCounter, 1).last_number == 50
    message = case.db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id))
    attached = tmp_path / 'attached.txt'
    preloaded = tmp_path / 'preloaded.txt'
    attached.write_text('attachment', encoding='utf-8')
    preloaded.write_text('preload', encoding='utf-8')
    case.db.add_all([
        TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket_id, message_id=message.id,
                         source='telegram', local_path=str(attached), telegram_archive_message_id='501'),
        TicketAttachmentPreload(id=str(uuid.uuid4()), ticket_id=ticket_id, client_id=case.client.id,
                                local_path=str(preloaded), status='uploading', telegram_staging_file_message_id='502'),
        TicketView(id=str(uuid.uuid4()), ticket_id=ticket_id, user_id=case.agent.id),
        StatsCompletion(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id, object_type='ticket'),
        StatsWorkInterval(id=str(uuid.uuid4()), company_id=case.company.id, object_id=ticket_id,
                          object_type='ticket', user_id=case.agent.id, started_at=datetime.utcnow()),
    ])
    case.db.commit()
    # An unflushed system message must not survive a bulk delete either.
    case.db.add(TicketMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, message_type='system', body='Pending'))
    cancel(case, ticket, via)

    assert case.db.get(Ticket, ticket_id) is None
    for model in (TicketMessage, TicketAttachment, TicketAttachmentPreload, TicketView,
                  TicketReviewCycle, TicketFeedback, TicketFeedbackInput):
        assert case.db.scalar(select(model.id).where(model.ticket_id == ticket_id)) is None
    for model in (StatsCompletion, StatsWorkInterval):
        assert case.db.scalar(select(model.id).where(model.object_id == ticket_id)) is None
    assert not attached.exists() and not preloaded.exists()
    assert {call.args[0] for call in case.archive_delete.call_args_list} == {'501', '502'}
    assert case.db.get(Client, case.client.id) is not None
    assert case.db.get(TelegramContact, case.contact.id) is not None
    assert case.db.get(ClientProfile, case.profile.id).destination == 'Support'
    assert case.db.get(Ticket, history_id) is not None
    assert case.db.get(TicketMessage, history_message_id) is not None
    assert case.db.get(TicketNumberCounter, 1).last_number == 50
    assert case.send.called and case.removed.called
    if via != 'restore':
        assert case.send.call_args.kwargs.get('reply_markup') is None
        assert case.send.call_args.kwargs.get('remove_keyboard') or via == 'timeout'


@pytest.mark.parametrize('changes', [
    {'channel': 'web'}, {'workflow_type': 'task'}, {'workflow_type': 'auto'},
    {'intake_state': 'ready'}, {'ticket_number': 51}, {'task_kind': 'group_parent'},
    {'task_kind': 'group_child'}, {'parent_ticket_id': 'parent'}, {'is_special': True},
    {'quick_action_state_json': {'client_origin': {'deadline': 0}}},
    {'quick_action_state_json': {'bitrix_deal_phone_lookup': {'deadline': 0}}},
    {'instruction_button_key': 'group'},
])
def test_strict_guard_preserves_other_workflows(case, changes):
    ticket = draft(case)
    case.db.add(BotButtonSetting(id=str(uuid.uuid4()), menu_type='Support', button_key='group',
                                 button_label='Group', button_path='Group', workflow_type='ticket', automation_key='telegram_group'))
    for key, value in changes.items():
        setattr(ticket, key, value)
    case.db.commit()
    assert not is_preliminary_telegram_ticket(case.db, ticket)


def test_submenu_draft_is_still_preliminary(case):
    ticket = draft(case)
    ticket.button_config_key = '__submenu__:Support'
    assert is_preliminary_telegram_ticket(case.db, ticket)


@pytest.mark.parametrize('via', ['command', 'button', 'agent', 'admin', 'bulk'])
def test_ready_ticket_close_keeps_ticket_and_number(case, via):
    ticket, _ = _seed_ticket(case.db, case.company, case.client, case.agent)
    ticket.ticket_number = 50
    case.db.commit()
    ticket_id = ticket.id
    cancel(case, ticket, via)
    case.db.refresh(ticket)
    assert ticket.id == ticket_id and ticket.status == 'closed'
    assert ticket.intake_state == 'ready' and ticket.ticket_number == 50
    assert case.db.get(TicketNumberCounter, 1).last_number == 50


@pytest.mark.parametrize('via', ['command', 'button'])
def test_second_client_close_has_no_active_ticket(case, via):
    ticket = draft(case)
    cancel(case, ticket, via)
    payload = (_message_payload(update_id=2002, contact=case.contact, text='/close') if via == 'command'
               else _callback_payload(update_id=2002, contact=case.contact, data='client_close', message_id=12))
    assert telegram.process_telegram_update(case.db, payload, notify_client=True)['closed'] is False


def test_bulk_cancel_can_skip_client_notification(case):
    ticket = draft(case)
    result = tickets.admin_close_tickets(AdminBulkTicketsPayload(ticket_ids=[ticket.id], skip_client_notification=True),
                                         db=case.db, user=case.admin)
    assert result.closed == 1
    case.send.assert_not_called()


def test_rollback_retains_ticket_and_attachment_file(case, tmp_path):
    ticket = draft(case)
    ticket_id = ticket.id
    message = case.db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id))
    path = tmp_path / 'retained.txt'
    path.write_text('retained', encoding='utf-8')
    case.db.add(TicketAttachment(id=str(uuid.uuid4()), ticket_id=ticket_id, message_id=message.id,
                                 source='telegram', local_path=str(path)))
    case.db.commit()
    delete_preliminary_telegram_ticket(case.db, ticket)
    case.db.flush()
    assert path.exists()
    case.db.rollback()
    assert case.db.get(Ticket, ticket_id) is not None and path.exists()
    case.db.commit()
    assert path.exists()


def test_start_does_not_allocate_permanent_number(case):
    payload = _message_payload(update_id=2000, contact=case.contact, text='/start')
    telegram.process_telegram_update(case.db, payload, notify_client=True)
    ticket = telegram._collecting_ticket_for_client(case.db, case.company.id, case.client.id)
    assert ticket is not None and ticket.status == 'unassigned'
    assert ticket.ticket_number is None and case.db.get(TicketNumberCounter, 1).last_number == 50
