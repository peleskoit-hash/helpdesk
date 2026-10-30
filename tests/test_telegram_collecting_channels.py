"""Channel isolation on synthetic SQLite only; no application startup or network."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import Base
from api.app.models import BotButtonSetting, Company, Client, ClientProfile, TelegramContact, Ticket, TicketAttachment, TicketMessage
from api.app.routers import telegram
from api.app.services import telegram_client_origin as origin, telegram_bitrix_phone as phone
from api.app.services import telegram_client_close, telegram_group_automation as group


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'employee_telegram_bot_token', '')
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Company(id='company', name='Synthetic'))
        db.flush()
        db.add(Client(id='client', company_id='company', display_name='Synthetic'))
        db.flush()
        contact = TelegramContact(id='contact', company_id='company', client_id='client', external_user_id='123', external_chat_id='123')
        db.add(contact)
        db.commit()
        yield SimpleNamespace(db=db, contact=contact)
    engine.dispose()


def ticket(case, key, channel, **changes):
    values = dict(id=key, company_id='company', client_id='client', channel=channel, subject='Synthetic',
                  workflow_type='task', intake_state='collecting', status='unassigned',
                  created_at=datetime.utcnow() - timedelta(minutes=11), updated_at=datetime.utcnow() - timedelta(minutes=11))
    values.update(changes)
    result = Ticket(**values)
    case.db.add(result)
    case.db.commit()
    return result


@pytest.mark.parametrize('workflow', ['task', 'ticket'])
def test_collecting_lookup_skips_newer_web_draft(case, workflow):
    web = ticket(case, 'web', 'web', workflow_type=workflow, updated_at=datetime.utcnow())
    assert telegram._collecting_ticket_for_client(case.db, 'company', 'client') is None
    tg = ticket(case, 'tg', 'telegram', workflow_type=workflow)
    assert telegram._collecting_ticket_for_client(case.db, 'company', 'client').id == tg.id
    assert web.intake_state == 'collecting'


@pytest.mark.parametrize('workflow', ['task', 'ticket'])
def test_timeout_cleanup_only_cancels_expired_telegram_draft(case, monkeypatch, workflow):
    web = ticket(case, 'web', 'web', workflow_type=workflow)
    tg = ticket(case, 'tg', 'telegram', workflow_type=workflow)
    fresh = ticket(case, 'fresh', 'telegram', workflow_type=workflow, updated_at=datetime.utcnow())
    send = Mock()
    remove_menu, remove_close = Mock(), Mock()
    monkeypatch.setattr(telegram, 'send_message', send)
    monkeypatch.setattr(telegram, '_remove_menu_keyboard', remove_menu)
    monkeypatch.setattr(telegram, 'remove_client_close_button', remove_close)
    monkeypatch.setattr(telegram, '_automatic_telegram_text', lambda *a, **kw: (kw['ticket'].id, None))
    assert telegram.cleanup_expired_task_intents(case.db) == 1
    assert (tg.status, tg.intake_state) == ('closed', 'cancelled')
    assert (web.status, web.intake_state, web.closed_at) == ('unassigned', 'collecting', None)
    assert (fresh.status, fresh.intake_state) == ('unassigned', 'collecting')
    assert case.db.scalar(select(TicketMessage.id).where(TicketMessage.ticket_id == web.id)) is None
    send.assert_called_once_with('123', 'tg', parse_mode=None)
    assert remove_menu.call_args.args[1].id == remove_close.call_args.args[1].id == 'tg'
    assert telegram.cleanup_expired_task_intents(case.db) == 0


@pytest.mark.parametrize('service,key', [(origin, origin.KEY), (phone, phone.KEY), (phone, phone.PHONE_DEALS_KEY)])
def test_automation_wait_and_timeout_ignore_web_collecting(case, monkeypatch, service, key):
    web = ticket(case, 'web', 'web', quick_action_state_json={key: {'deadline': 0}})
    tg = ticket(case, 'tg', 'telegram', quick_action_state_json={key: {'deadline': 0}})
    assert not service.waiting(web)
    assert service.waiting(tg)
    finish = Mock(return_value={'closed': True})
    monkeypatch.setattr(service, 'finish', finish)
    assert service.cleanup(case.db, {}) == 1
    assert finish.call_count == 1 and finish.call_args.args[1].id == 'tg'
    assert web.intake_state == 'collecting' and web.closed_at is None


@pytest.mark.parametrize('channel,expected', [('web', 0), ('telegram', 1)])
def test_phone_cleanup_recovery_does_not_resume_web_collecting(case, monkeypatch, channel, expected):
    ticket(case, 'draft', channel, quick_action_state_json={phone.KEY: {'deadline': 0, 'finish': 'success'}})
    resume = Mock(return_value={'closed': True})
    monkeypatch.setattr(phone, 'resume', resume)
    assert phone.cleanup(case.db, {}) == expected
    assert resume.call_count == expected


@pytest.mark.parametrize('channel', ['web', 'telegram'])
def test_ready_phone_recovery_is_unchanged(case, monkeypatch, channel):
    ticket(case, 'ready', channel, intake_state='ready', quick_action_state_json={phone.KEY: {'handed_off': True}})
    resume = Mock(return_value={'closed': True})
    monkeypatch.setattr(phone, 'resume', resume)
    assert phone.cleanup(case.db, {}) == 1
    assert resume.call_count == 1


@pytest.mark.parametrize('channel,expected', [('web', 0), ('telegram', 1)])
def test_update_pending_discovery_is_channel_scoped(case, monkeypatch, channel, expected):
    ticket(case, 'draft', channel)
    entered = []
    @contextmanager
    def lock(*args, **kwargs):
        entered.append(True)
        yield True
    monkeypatch.setattr(group, 'chat_lock', lock)
    monkeypatch.setattr(group, 'recover', Mock())
    monkeypatch.setattr(telegram, '_is_group_automation', lambda *a: True)
    monkeypatch.setattr(telegram, '_process_telegram_update', lambda *a: {'ok': True})
    assert telegram.process_telegram_update(case.db, {'update_id': 1, 'message': {'chat': {'id': 123, 'type': 'private'}}}) == {'ok': True}
    assert len(entered) == expected


@pytest.mark.parametrize('channel', ['web', 'telegram'])
def test_active_ticket_limit_remains_cross_channel(case, channel):
    active = ticket(case, 'active', channel, workflow_type='ticket', intake_state='ready')
    assert telegram._active_ticket_for_client(case.db, 'company', 'client').id == active.id
    active.status = 'closed'
    case.db.commit()
    assert telegram._active_ticket_for_client(case.db, 'company', 'client') is None


def test_close_command_cannot_close_web_collecting(case):
    web = ticket(case, 'web', 'web', workflow_type='ticket')
    unused = Mock(side_effect=AssertionError('WEB draft must not be closed by Telegram'))
    assert telegram_client_close.close_active_ticket_for_contact(
        case.db, case.contact, active_ticket_for_client_fn=telegram._active_ticket_for_client,
        collecting_ticket_for_client_fn=telegram._collecting_ticket_for_client,
        remove_menu_keyboard_fn=unused, remove_client_close_button_fn=unused, append_system_message_fn=unused,
    ) is None
    assert (web.status, web.intake_state) == ('unassigned', 'collecting')


@pytest.mark.parametrize('channel,intake,visible', [
    ('web', 'collecting', False), ('telegram', 'collecting', True),
    ('web', 'ready', True), ('telegram', 'ready', True),
])
def test_album_lookup_only_excludes_web_collecting(case, channel, intake, visible):
    draft = ticket(case, 'draft', channel, intake_state=intake)
    case.db.add(TicketMessage(id='message', ticket_id=draft.id, message_type='client', body='Synthetic'))
    case.db.flush()
    case.db.add(TicketAttachment(id='attachment', ticket_id=draft.id, message_id='message', source='synthetic',
                                 local_path='synthetic', telegram_media_group_id='synthetic-album'))
    case.db.commit()
    result = telegram._task_for_client_album(case.db, case.contact, {'media_group_id': 'synthetic-album'})
    assert (result is not None) == visible


@pytest.mark.parametrize('channel,intake,visible', [
    ('web', 'collecting', False), ('telegram', 'collecting', True),
    ('web', 'ready', True), ('telegram', 'ready', True),
])
def test_menu_cleanup_and_close_ownership_only_exclude_web_collecting(case, monkeypatch, channel, intake, visible):
    draft = ticket(case, 'draft', channel, intake_state=intake, menu_message_id='99',
                   quick_action_state_json={origin.KEY: {'close_message_ids': ['99']}})
    assert origin.owns_close_button(case.db, case.contact, 99) == visible
    monkeypatch.setattr(telegram, '_safe_delete_message', Mock(return_value=True))
    telegram._clear_client_menu_keyboards(case.db, case.contact, [99])
    assert draft.menu_message_id == (None if visible else '99')


@pytest.mark.parametrize('active_channel', [None, 'web', 'telegram'])
def test_real_task_selection_preserves_web_draft_and_active_limit(case, monkeypatch, active_channel):
    web = ticket(case, 'web-draft', 'web')
    if active_channel:
        ticket(case, 'active', active_channel, workflow_type='ticket', intake_state='ready')
    case.db.add(ClientProfile(id='profile', client_id='client', is_verified=True))
    case.db.add(BotButtonSetting(id='button', menu_type='Базовое', button_key='Базовое|task|Task',
                                 button_path='Task', button_label='Task', button_kind='terminal', workflow_type='task'))
    case.db.commit()
    monkeypatch.setattr(telegram, 'schedule_avatar_refresh', Mock())
    forbidden = Mock(side_effect=AssertionError('No Telegram delivery expected'))
    monkeypatch.setattr(telegram, 'send_message', forbidden)
    monkeypatch.setattr(telegram, 'answer_callback_query', forbidden)
    result = telegram.process_telegram_update(case.db, {'update_id': 1, 'callback_query': {
        'id': 'synthetic', 'data': 'btnid:button', 'from': {'id': 123},
        'message': {'message_id': 1, 'chat': {'id': 123, 'type': 'private'}},
    }}, notify_client=False)
    if active_channel:
        assert result['already_active'] and result['ticket_id'] == 'active'
        assert len(case.db.scalars(select(Ticket)).all()) == 2
    else:
        assert result['task_pending'] and result['ticket_id'] != web.id
        tg = case.db.get(Ticket, result['ticket_id'])
        assert (tg.channel, tg.intake_state, tg.workflow_type) == ('telegram', 'collecting', 'task')
        completed = telegram.process_telegram_update(case.db, {'update_id': 2, 'message': {
            'message_id': 2, 'text': 'Synthetic task description', 'from': {'id': 123},
            'chat': {'id': 123, 'type': 'private'},
        }}, notify_client=False)
        assert completed['ticket_id'] == tg.id
        assert tg.intake_state == 'ready'
        assert case.db.scalar(select(TicketMessage.body).where(TicketMessage.ticket_id == tg.id,
                              TicketMessage.message_type == 'client')) == 'Synthetic task description'
    assert (web.status, web.intake_state, web.closed_at) == ('unassigned', 'collecting', None)
    assert case.db.scalar(select(TicketMessage.id).where(TicketMessage.ticket_id == web.id)) is None
    forbidden.assert_not_called()
