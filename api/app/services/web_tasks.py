"""Ordinary WEB task intake, serialized by client until the operation commits.

Selection requires a stable request UUID; its scoped UUID5 is the persisted task
identity, including after cancellation. Retrying must reuse that request UUID.
Only WEB intake metadata uses quick_action_state_json[STATE_KEY]. No Telegram
updates, chat IDs, keyboards, automation dispatch or customer delivery are used.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import BotButtonSetting, Client, Ticket, TicketMessage
from ..routers import telegram
from . import web_auth
from .assignment import append_system_message
from .automatic_messages import get_automatic_message_row, render_automatic_message_text
from .statistics_lifecycle import sync_work_interval_after_close
from .web_menu import read_client_menu

STATE_KEY = 'web_task_intake'
REQUEST_NAMESPACE = UUID('9f1eb726-545d-4f8d-8cbc-a20eb97113f1')
logger = logging.getLogger(__name__)


def lock_client(db: Session, client_id: str):
    """Database locks cover multiple workers and remain held until commit/rollback."""
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': f'web-task:{client_id}'})
    elif db.get_bind().dialect.name == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
    else:
        raise RuntimeError('WEB task intake requires PostgreSQL or SQLite')
    db.expire_all()


def message_text(db, key, ticket=None, **extra):
    return render_automatic_message_text(db, key, ticket=ticket,
        extra={'timeout_minutes': telegram.TASK_DESCRIPTION_TIMEOUT_MINUTES, **extra})


def fail(db, code, status=409, ticket=None, key='telegram.menu.problem_not_selected'):
    raise HTTPException(status, detail={'error': {'code': code, 'message': message_text(db, key, ticket),
                                                 'details': {}}})


def state(ticket):
    return dict((ticket.quick_action_state_json or {}).get(STATE_KEY) or {})


def deadline(ticket):
    return datetime.fromisoformat(state(ticket)['expires_at'])


def close_draft(db, ticket, reason):
    now = web_auth.utcnow()
    ticket.intake_state = 'cancelled'
    ticket.status = 'closed'
    ticket.closed_at = now
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), STATE_KEY: {**state(ticket), 'reason': reason}}
    sync_work_interval_after_close(db, ticket, now=now)
    append_system_message(db, ticket, 'Создание задачи отменено: клиент не отправил текст вовремя'
                          if reason == 'expired' else 'Создание задачи отменено клиентом через WEB')


def expire(db, ticket):
    if (ticket.intake_state == 'collecting' and ticket.status == 'unassigned'
            and ticket.assignee_id is None and deadline(ticket) <= web_auth.utcnow()):
        close_draft(db, ticket, 'expired')
        return True
    return False


def response(db, ticket):
    meta = state(ticket)
    result = {'task_id': ticket.id, 'state': ticket.intake_state, 'status': ticket.status,
              'expires_at': meta['expires_at'] if ticket.intake_state == 'collecting' else None}
    if ticket.intake_state == 'ready':
        result['text'] = message_text(db, 'telegram.task.created', ticket)
    elif ticket.intake_state == 'cancelled':
        if meta.get('reason') == 'expired':
            fail(db, 'web_task_expired', 410, ticket, 'telegram.intake.task_timeout')
        result['text'] = message_text(db, 'telegram.ticket.close.unassigned.client', ticket)
    elif not meta['confirmed']:
        result['state'] = 'confirmation_required'
        result['text'] = telegram._render_confirmation_text_for_telegram(
            db, ticket, meta.get('confirmation_text'), 'Подтвердите, пожалуйста, создание обращения')[0]
    else:
        row = get_automatic_message_row(db, 'telegram.task.request_text')
        fallback = message_text(db, 'telegram.task.request_text', ticket, button_label=ticket.subject)
        button = telegram._button_from_db_row(db, db.get(BotButtonSetting, meta['button_id']))
        result['text'] = fallback if row is not None and row.is_overridden else telegram._render_client_text(db, ticket, button, fallback)
    return result


def owned_task(db, owner: Client, task_id):
    ticket = db.scalar(select(Ticket).where(Ticket.id == str(task_id), Ticket.client_id == owner.id,
        Ticket.company_id == owner.company_id, Ticket.channel == 'web', Ticket.workflow_type == 'task', Ticket.task_kind == 'normal'))
    if ticket is None or not state(ticket):
        fail(db, 'web_task_not_found', 404)
    if ticket.intake_state == 'collecting' and (ticket.status != 'unassigned' or ticket.assignee_id is not None):
        fail(db, 'web_task_no_longer_collecting', ticket=ticket, key='telegram.ticket.close.unassigned.client')
    if expire(db, ticket):
        db.commit()
    return ticket


def available_button(db, owner, button_id):
    menu = read_client_menu(db, owner.id)
    def visible(rows):
        return any(item['id'] == str(button_id) and item['children'] is None
                   or item['children'] is not None and visible(item['children'])
                   for row in rows for item in row)
    if not visible(menu['rows']):
        fail(db, 'web_task_button_unavailable')
    row = db.get(BotButtonSetting, str(button_id))
    if (row is None or (row.workflow_type or '').strip().lower() != 'task'
            or (row.button_kind or 'terminal').strip().lower() != 'terminal'):
        fail(db, 'web_task_button_not_ordinary_task')
    # Automations require their own WEB flow; never dispatch them as plain tasks.
    if (row.automation_key or '').strip():
        fail(db, 'web_task_automation_not_supported')
    return row, telegram._button_from_db_row(db, row), menu['menu_type']


def check_active(db, owner):
    active = telegram._active_ticket_for_client(db, owner.company_id, owner.id)
    if active is not None:
        fail(db, 'client_has_active_ticket', ticket=active, key='telegram.start.active_ticket.client')


def select_task(db, owner, client, button_id, request_id, confirmed=False):
    task_id = str(uuid5(REQUEST_NAMESPACE, f'{owner.company_id}:{owner.id}:{request_id}'))
    existing = db.get(Ticket, task_id)
    if existing is not None:
        ticket = owned_task(db, owner, task_id)
        if state(ticket)['button_id'] != str(button_id):
            fail(db, 'web_task_request_conflict')
        if ticket.intake_state == 'collecting':
            available_button(db, owner, button_id)
            if confirmed and not state(ticket)['confirmed']:
                check_active(db, owner)
                ticket.quick_action_state_json = {**ticket.quick_action_state_json, STATE_KEY: {
                    **state(ticket), 'confirmed': True,
                    'expires_at': (web_auth.utcnow() + timedelta(minutes=telegram.TASK_DESCRIPTION_TIMEOUT_MINUTES)).isoformat()}}
                web_auth.register_web_activity(db, client)
                db.commit()
        return response(db, ticket)
    row, button, menu_name = available_button(db, owner, button_id)
    check_active(db, owner)
    for pending in db.scalars(select(Ticket).where(Ticket.client_id == owner.id, Ticket.company_id == owner.company_id,
            Ticket.channel == 'web', Ticket.workflow_type == 'task', Ticket.intake_state == 'collecting', Ticket.status != 'closed')):
        if state(pending) and expire(db, pending):
            continue
        fail(db, 'web_task_already_collecting', ticket=pending, key='telegram.task.request_text')
    now = web_auth.utcnow()
    ticket = telegram._create_collecting_ticket(db, owner.company_id, owner.id, button.label,
                                                channel='web', workflow_type='task', ticket_id=task_id)
    telegram._prepare_collecting_ticket_for_button(db, ticket, button, menu_name)
    confirmation, confirmation_text = telegram._resolve_effective_confirmation(ticket, button, db)
    ticket.quick_action_state_json = {STATE_KEY: {'button_id': row.id, 'confirmed': not confirmation,
        'confirmation_text': confirmation_text,
        'expires_at': (now + timedelta(minutes=telegram.TASK_DESCRIPTION_TIMEOUT_MINUTES)).isoformat()}}
    web_auth.register_web_activity(db, client)
    db.commit()
    return response(db, ticket)


def describe_task(db, owner, client, task_id, body):
    ticket = owned_task(db, owner, task_id)
    body = body.strip()
    if ticket.intake_state == 'ready':
        original = db.scalar(select(TicketMessage.body).where(TicketMessage.ticket_id == ticket.id,
                              TicketMessage.message_type == 'client').order_by(TicketMessage.created_at).limit(1))
        if original != body:
            fail(db, 'web_task_description_already_sent')
        return response(db, ticket)
    if ticket.intake_state != 'collecting':
        response(db, ticket)  # Preserve the configured timeout response.
        fail(db, 'web_task_cancelled')
    _, button, _ = available_button(db, owner, state(ticket)['button_id'])
    check_active(db, owner)
    if not state(ticket)['confirmed']:
        fail(db, 'web_task_confirmation_required', ticket=ticket, key='telegram.task.request_text')
    if not body:
        fail(db, 'web_task_description_required', 422, ticket, 'telegram.task.request_text')
    telegram._append_client_message(db, ticket, body)
    telegram._store_button_system_text(db, ticket, button, client_message=body)
    telegram._ready_ticket_for_distribution(db, ticket, button)
    web_auth.register_web_activity(db, client)
    db.commit()
    return response(db, ticket)


def cancel_task(db, owner, client, task_id):
    ticket = owned_task(db, owner, task_id)
    if ticket.intake_state == 'ready':
        fail(db, 'web_task_already_created')
    if ticket.intake_state == 'collecting':
        close_draft(db, ticket, 'cancelled')
        web_auth.register_web_activity(db, client)
        db.commit()
    return response(db, ticket)


def cleanup_expired_web_tasks(db: Session):
    candidates = db.execute(select(Ticket.id, Ticket.client_id).where(Ticket.channel == 'web',
        Ticket.workflow_type == 'task', Ticket.task_kind == 'normal', Ticket.intake_state == 'collecting',
        Ticket.status == 'unassigned', Ticket.assignee_id.is_(None))).all()
    closed = 0
    for task_id, client_id in candidates:
        lock_client(db, client_id)
        ticket = db.get(Ticket, task_id)
        if ticket is not None and state(ticket) and expire(db, ticket):
            closed += 1
        db.commit()
    return closed


def _cleanup_once():
    with SessionLocal() as db:
        cleanup_expired_web_tasks(db)


async def web_task_cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(_cleanup_once)
        except Exception:
            logger.exception('WEB task timeout cleanup failed')
        await asyncio.sleep(30)
