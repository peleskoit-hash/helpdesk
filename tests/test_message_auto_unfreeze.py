"""Automatic unfreeze follows confirmed client delivery and the actual assignee."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from api.app.models import Client, Company, MessageType, StatsWorkInterval, TelegramContact, Ticket, TicketAttachment, TicketAttachmentPreload, TicketMessage, User
from api.app.routers import telegram, tickets
from api.app.schemas import FreezePayload, TextPayload
from api.app.services import ticket_messages as messages, ticket_problem_flow as problems, telegram_outbound_delivery as outbound
from api.app.services.ticket_lifecycle import unfreeze_ticket_on_message
from test_ticket_messages_outbound import _JsonRequest, _MultipartRequest, _FormData
from test_web_auth import engine


@pytest.fixture
def case(engine, monkeypatch, tmp_path):
    factory = sessionmaker(bind=engine,autoflush=False)
    db = factory()
    company=Company(id='company',name='Isolated')
    db.add(company);db.flush()
    client=Client(id='client',company_id=company.id,display_name='Client')
    db.add(client);db.flush()
    db.add(TelegramContact(id='contact',company_id=company.id,client_id=client.id,
        external_user_id='123',external_chat_id='chat',external_username='client'))
    db.flush()
    ticket=Ticket(id='ticket',company_id=company.id,client_id=client.id,subject='Test',status='pending')
    db.add(ticket);db.flush()
    users = {}
    for role in ['agent','trainee','admin','chiki_piki']:
        user = User(id='actor-'+role,company_id=ticket.company_id,full_name=role,role=role,
                    is_active=True,availability_status='online')
        db.add(user)
        users[role] = SimpleNamespace(id=user.id,role=role)
    db.add(User(id='different-assignee',company_id=ticket.company_id,full_name='Other',role='agent',is_active=True))
    db.flush()
    ticket.assignee_id = users['agent'].id
    ticket.ticket_number = 1
    ticket.is_frozen = True
    ticket.freeze_until = datetime.utcnow()+timedelta(days=1)
    db.commit()
    scheduled, sent = [], []

    def send(*args,**kwargs):
        sent.append('text')
        return {'result':{'message_id':100}}

    kwargs = dict(db=db,ticket=ticket,user=users['agent'],send_to_telegram_client=send,
        telegram_message_id_from_response=tickets._telegram_message_id,
        save_upload_attachment=lambda *a,**k: pytest.fail('Unexpected raw upload'),
        send_attachment_to_telegram_client=lambda *a,**k: pytest.fail('Unexpected synchronous file delivery'),
        claim_attachment_preload=lambda session,pid,tid,uid: outbound.claim_attachment_preload(session,preload_id=pid,ticket_id=tid,user_id=uid),
        schedule_attachment_delivery=lambda **payload: scheduled.append(payload),
        unfreeze_ticket_on_message=unfreeze_ticket_on_message,
        remove_client_close_button=lambda *a,**k: None,
        refresh_ticket_auto_close=lambda *a,**k: None)
    monkeypatch.setattr(tickets,'_send_to_telegram_client',send)
    monkeypatch.setattr(tickets,'_remove_client_close_button',lambda *a,**k: None)
    monkeypatch.setattr(tickets,'refresh_ticket_auto_close',lambda *a,**k: None)
    monkeypatch.setattr(outbound,'SessionLocal',factory)
    monkeypatch.setattr(outbound,'_best_effort_edit_group_archive_message',lambda **k: None)
    monkeypatch.setattr(outbound,'_finalize_claimed_preload_archive',lambda **k: None)
    monkeypatch.setattr(outbound,'send_message',send)
    monkeypatch.setattr(outbound,'send_document_by_file_id',lambda *a,**k: {'result':{'message_id':200}})
    try:
        yield SimpleNamespace(db=db,ticket=ticket,users=users,kwargs=kwargs,scheduled=scheduled,
                              sent=sent,send=send,tmp=tmp_path)
    finally:
        db.close()


def reply(c,kind=MessageType.AGENT,request=None):
    return asyncio.run(messages.reply_to_ticket(request=request or _JsonRequest({'text':'Text'}),message_type=kind,**c.kwargs))


def preload(c):
    path=c.tmp/'file.pdf'
    path.write_bytes(b'pdf')
    row=TicketAttachmentPreload(id=str(uuid4()),ticket_id=c.ticket.id,user_id=c.kwargs['user'].id,
        original_filename='file.pdf',local_path=str(path),mime_type='application/pdf',file_size=3,
        status='ready',telegram_file_id='archived-file')
    c.db.add(row);c.db.commit()
    return row.id


@pytest.mark.parametrize('role',['agent','trainee','admin'])
@pytest.mark.parametrize('owner',[True,False])
@pytest.mark.parametrize('channel',['telegram','web'])
def test_only_actual_assignee_reply_unfreezes(case,role,owner,channel):
    c=case;c.kwargs['user']=c.users[role];c.ticket.channel=channel
    c.ticket.assignee_id=c.users[role].id if owner else 'different-assignee'
    c.db.commit()
    assert reply(c)=={'ok':True}
    c.db.refresh(c.ticket)
    assert c.ticket.is_frozen is not owner
    assert (c.ticket.freeze_until is None) is owner


@pytest.mark.parametrize('role',['agent','trainee','admin','chiki_piki'])
@pytest.mark.parametrize('owner',[True,False])
@pytest.mark.parametrize('with_file',[True,False])
def test_internal_comments_never_unfreeze(case,role,owner,with_file):
    c=case;c.kwargs['user']=c.users[role]
    c.ticket.assignee_id=c.users[role].id if owner else 'different-assignee'
    c.db.commit()
    deadline=c.ticket.freeze_until
    request=None
    if with_file:
        request=_MultipartRequest(_FormData(text='Private',files=[],preloaded_attachment_ids=[preload(c)]))
    assert reply(c,MessageType.INTERNAL,request)=={'ok':True}
    c.db.refresh(c.ticket)
    assert c.ticket.is_frozen and c.ticket.freeze_until==deadline
    assert not c.sent and not c.scheduled
    assert c.db.scalar(select(StatsWorkInterval)) is None


@pytest.mark.parametrize('role',['agent','trainee','admin','chiki_piki'])
def test_legacy_comment_entry_point_does_not_unfreeze(case,role):
    c=case;c.ticket.assignee_id=c.users[role].id;c.db.commit()
    assert messages.comment_on_ticket(db=c.db,user=c.users[role],ticket=c.ticket,
        payload=TextPayload(text='Private'),unfreeze_ticket_on_message=unfreeze_ticket_on_message)=={'ok':True}
    assert c.ticket.is_frozen
    assert c.db.scalar(select(StatsWorkInterval)) is None


@pytest.mark.parametrize('failure',['exception','missing_ack'])
def test_failed_text_send_does_not_unfreeze(case,failure):
    def send(*a,**k):
        if failure=='exception':
            raise HTTPException(502,detail={'error':{'code':'telegram_send_failed'}})
        return None
    case.kwargs['send_to_telegram_client']=send
    if failure=='exception':
        with pytest.raises(HTTPException):reply(case)
    else:reply(case)
    case.db.refresh(case.ticket)
    assert case.ticket.is_frozen


def test_incoming_client_message_keeps_existing_unfreeze(case):
    telegram._append_client_message(case.db,case.ticket,'Client message',schedule_notification=False)
    case.db.commit()
    assert not case.ticket.is_frozen and case.ticket.freeze_until is None


def test_system_message_does_not_unfreeze(case):
    messages.add_message(db=case.db,ticket=case.ticket,message_type=MessageType.SYSTEM,
                         body='System',author_user_id=case.users['agent'].id)
    case.db.commit()
    assert case.ticket.is_frozen


@pytest.mark.parametrize('role',['agent','trainee','admin'])
@pytest.mark.parametrize('owner',[True,False])
@pytest.mark.parametrize('text',['Text',''])
@pytest.mark.parametrize('channel',['telegram','web'])
def test_queued_attachment_waits_for_confirmed_delivery(case,role,owner,text,channel):
    c=case;c.kwargs['user']=c.users[role];c.ticket.channel=channel
    c.ticket.assignee_id=c.users[role].id if owner else 'different-assignee'
    c.db.commit()
    request=_MultipartRequest(_FormData(text=text,files=[],preloaded_attachment_ids=[preload(c)]))
    assert reply(c,request=request)=={'ok':True}
    assert c.ticket.is_frozen  # A queued or archived file is not a delivered message.
    assert len(c.scheduled)==1
    outbound.deliver_ticket_attachment(**c.scheduled[0])
    c.db.expire_all()
    attachment=c.db.scalar(select(TicketAttachment))
    assert attachment.telegram_delivery_status=='sent'
    assert c.ticket.is_frozen is not owner
    assert (c.ticket.auto_close_due_at is not None) is owner


@pytest.mark.parametrize('failure',['exception','missing_ack'])
def test_failed_or_partial_attachment_send_does_not_unfreeze(case,monkeypatch,failure):
    c=case
    def send_file(*a,**k):
        if failure=='exception':
            from api.app.services.telegram_bot import TelegramApiError
            raise TelegramApiError('not delivered')
        return None
    monkeypatch.setattr(outbound,'send_document_by_file_id',send_file)
    assert reply(c,request=_MultipartRequest(_FormData(text='Text before file',files=[],preloaded_attachment_ids=[preload(c)])))=={'ok':True}
    outbound.deliver_ticket_attachment(**c.scheduled[0])
    c.db.expire_all()
    assert c.db.scalar(select(TicketAttachment)).telegram_delivery_status=='failed'
    assert c.ticket.is_frozen


def test_current_assignee_is_checked_after_asynchronous_delivery(case):
    c=case
    reply(c,request=_MultipartRequest(_FormData(text='',files=[],preloaded_attachment_ids=[preload(c)])))
    c.ticket.assignee_id=c.users['admin'].id;c.db.commit()
    outbound.deliver_ticket_attachment(**c.scheduled[0]);c.db.expire_all()
    assert c.ticket.is_frozen


@pytest.mark.parametrize('owner',[True,False])
@pytest.mark.parametrize('ack',[True,False])
def test_greeting_uses_actual_actor_and_ack(case,monkeypatch,owner,ack):
    c=case;actor=c.users['admin']
    c.ticket.assignee_id=actor.id if owner else c.users['agent'].id;c.db.commit()
    monkeypatch.setattr(tickets,'_send_to_telegram_client',lambda *a,**k: {'result':{'message_id':100}} if ack else None)
    tickets.greeting(c.ticket.id,db=c.db,user=actor)
    assert c.ticket.is_frozen is not (owner and ack)


@pytest.mark.parametrize('owner',[True,False])
@pytest.mark.parametrize('ack',[True,False])
@pytest.mark.parametrize('workflow',['ticket','task'])
def test_hint_uses_actual_actor_ack_and_real_client_send(case,monkeypatch,owner,ack,workflow):
    c=case;actor=c.users['admin']
    c.ticket.assignee_id=actor.id if owner else c.users['agent'].id
    c.ticket.workflow_type=workflow;c.db.commit()
    button=SimpleNamespace(key='target',label='Correct',kind='ticket')
    monkeypatch.setattr(problems,'menu_name_for_ticket_client',lambda *a,**k:'Menu')
    monkeypatch.setattr(problems,'_db_button_config',lambda *a:SimpleNamespace(buttons={'target':button}))
    monkeypatch.setattr(problems,'resolve_button_row',lambda *a:SimpleNamespace(button_key='target'))
    monkeypatch.setattr(problems,'button_full_path_label',lambda *a:'Correct')
    monkeypatch.setattr(problems,'render_automatic_message_text',lambda db,key,fallback,**kw:fallback)
    problems.apply_wrong_problem(c.db,c.ticket,SimpleNamespace(button_key='target'),
        actor_user_id=actor.id,actor_role=actor.role,actor_display_name='Admin',
        wrong_problem_text_template='Select {label}',get_or_create_client_profile_fn=lambda *a:None,
        send_to_telegram_client_fn=lambda *a: {'result':{'message_id':100}} if ack else None,
        telegram_message_id_from_response_fn=tickets._telegram_message_id,
        add_message_fn=tickets._add_message,unfreeze_ticket_on_message_fn=unfreeze_ticket_on_message,
        remove_client_close_button_fn=lambda *a:None)
    c.db.commit()
    assert c.ticket.is_frozen is not (workflow=='ticket' and owner and ack)


@pytest.mark.parametrize('role',['agent','trainee','admin'])
def test_manual_actions_remain_available_under_existing_permissions(case,role):
    c=case;actor=c.users[role]
    c.ticket.assignee_id=c.users['agent'].id if role=='admin' else actor.id;c.db.commit()
    tickets.unfreeze(c.ticket.id,db=c.db,user=actor)
    assert not c.ticket.is_frozen
    tickets.freeze(c.ticket.id,FreezePayload(freeze_until=datetime.utcnow()+timedelta(hours=1)),db=c.db,user=actor)
    assert c.ticket.is_frozen



@pytest.mark.parametrize("second_ok",[True,False])
def test_attachment_message_waits_for_all_files_and_replay_cannot_unfreeze(case,monkeypatch,second_ok):
    c=case
    reply(c,request=_MultipartRequest(_FormData(text='',files=[],preloaded_attachment_ids=[preload(c)])))
    first=c.scheduled[0]
    message_id=first['message_id']
    second=TicketAttachment(id=str(uuid4()),ticket_id=c.ticket.id,message_id=message_id,source='helpdesk',
        original_filename='second.pdf',mime_type='application/pdf',file_size=3,local_path=str(c.tmp/'second.pdf'),
        telegram_file_id='second-archived-file',telegram_delivery_status='sending')
    c.db.add(second);c.db.commit()
    second_id=second.id
    monkeypatch.setattr(outbound,'send_document_by_file_id',lambda chat,file_id,**kw: {'result':{'message_id':200}} if second_ok or file_id!='second-archived-file' else None)
    outbound.deliver_ticket_attachment(**first);c.db.expire_all()
    assert c.ticket.is_frozen
    outbound.deliver_ticket_attachment(attachment_id=second_id,message_id=message_id,ticket_id=c.ticket.id)
    c.db.expire_all()
    assert c.ticket.is_frozen is not second_ok
    assert len(list(c.db.scalars(select(StatsWorkInterval)))) == (1 if second_ok else 0)
    if not second_ok:
        return
    c.ticket.is_frozen=True;c.ticket.freeze_until=datetime.utcnow()+timedelta(hours=1);c.db.commit()
    outbound.deliver_ticket_attachment(**first);c.db.expire_all()
    assert c.ticket.is_frozen


def test_task_reply_remains_deferred_and_does_not_unfreeze(case):
    c=case;c.ticket.workflow_type='task';c.db.commit()
    reply(c,request=_MultipartRequest(_FormData(text='Task',files=[],preloaded_attachment_ids=[preload(c)])))
    assert c.ticket.is_frozen and not c.sent and not c.scheduled


@pytest.mark.parametrize('action',['greeting','hint'])
def test_special_action_failure_does_not_unfreeze(case,monkeypatch,action):
    c=case;c.ticket.assignee_id=c.users['admin'].id;c.db.commit()
    def fail(*a,**k):
        raise HTTPException(502,detail={'error':{'code':'telegram_send_failed'}})
    if action=='greeting':
        monkeypatch.setattr(tickets,'_send_to_telegram_client',fail)
        with pytest.raises(HTTPException):tickets.greeting(c.ticket.id,db=c.db,user=c.users['admin'])
    else:
        button=SimpleNamespace(key='target',label='Correct',kind='ticket')
        monkeypatch.setattr(problems,'menu_name_for_ticket_client',lambda *a,**k:'Menu')
        monkeypatch.setattr(problems,'_db_button_config',lambda *a:SimpleNamespace(buttons={'target':button}))
        monkeypatch.setattr(problems,'resolve_button_row',lambda *a:SimpleNamespace(button_key='target'))
        monkeypatch.setattr(problems,'button_full_path_label',lambda *a:'Correct')
        monkeypatch.setattr(problems,'render_automatic_message_text',lambda db,key,fallback,**kw:fallback)
        with pytest.raises(HTTPException):
            problems.apply_wrong_problem(c.db,c.ticket,SimpleNamespace(button_key='target'),
                actor_user_id=c.users['admin'].id,actor_role='admin',actor_display_name='Admin',
                wrong_problem_text_template='Select {label}',get_or_create_client_profile_fn=lambda *a:None,
                send_to_telegram_client_fn=fail,telegram_message_id_from_response_fn=tickets._telegram_message_id,
                add_message_fn=tickets._add_message,unfreeze_ticket_on_message_fn=unfreeze_ticket_on_message,
                remove_client_close_button_fn=lambda *a:None)
    c.db.refresh(c.ticket)
    assert c.ticket.is_frozen



@pytest.mark.parametrize('kind',['internal','system'])
def test_attachment_status_cannot_unfreeze_non_client_message(case,monkeypatch,kind):
    c=case
    message=TicketMessage(id=str(uuid4()),ticket_id=c.ticket.id,message_type=kind,
        author_user_id=c.users['agent'].id,body='Not a client reply')
    attachment=TicketAttachment(id=str(uuid4()),ticket_id=c.ticket.id,message_id=message.id,
        source='helpdesk',original_filename='file.pdf',local_path=str(c.tmp/'file.pdf'),telegram_delivery_status='sending')
    c.db.add(message);c.db.flush();c.db.add(attachment);c.db.commit()
    attachment_id,message_id,ticket_id=attachment.id,message.id,c.ticket.id
    def finish(**kw):
        with outbound.SessionLocal() as db:
            db.get(TicketAttachment,attachment_id).telegram_delivery_status='sent'
            db.commit()
    monkeypatch.setattr(outbound,'_deliver_ticket_attachment',finish)
    outbound.deliver_ticket_attachment(attachment_id=attachment_id,message_id=message_id,ticket_id=ticket_id)
    c.db.expire_all()
    assert c.ticket.is_frozen



def test_parallel_confirmed_files_open_one_work_interval(case,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    c=case
    reply(c,request=_MultipartRequest(_FormData(text='',files=[],preloaded_attachment_ids=[preload(c)])))
    first=dict(c.scheduled[0])
    second=TicketAttachment(id=str(uuid4()),ticket_id=c.ticket.id,message_id=first['message_id'],source='helpdesk',
        original_filename='second.pdf',mime_type='application/pdf',file_size=3,local_path=str(c.tmp/'second.pdf'),
        telegram_file_id='second-file',telegram_delivery_status='sending')
    c.db.add(second);c.db.commit()
    jobs=[first,dict(attachment_id=second.id,message_id=first['message_id'],ticket_id=c.ticket.id)]
    c.db.commit()
    barrier=Barrier(2)
    load=outbound._load_entities
    def synchronized_load(*args,**kwargs):
        entities=load(*args,**kwargs)
        if entities[0] is not None and entities[0].telegram_delivery_status=='sent':
            barrier.wait(timeout=10)
        return entities
    monkeypatch.setattr(outbound,'_load_entities',synchronized_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda job:outbound.deliver_ticket_attachment(**job),jobs))
    c.db.expire_all()
    assert not c.ticket.is_frozen
    assert len(list(c.db.scalars(select(StatsWorkInterval))))==1
