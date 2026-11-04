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
from ..models import BotButtonSetting, Client, Ticket, TicketAttachmentPreload, TicketMessage
from ..routers import telegram
from . import web_auth, web_chat, telegram_upload_state as upload_state
from .attachment_preload_owners import client_collecting_ticket, owned_client_preload
from .telegram_outbound_delivery import claim_attachment_preload, _discard_preload
from .ticket_attachments import _save_attachment_preload
from .ticket_messages import _attachment_from_preload
from .assignment import append_system_message
from .automatic_messages import get_automatic_message_row, render_automatic_message_text
from .statistics_lifecycle import sync_work_interval_after_close
from .web_menu import read_client_menu
from .web_task_attachments import attach_client_files, file_manifest

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


def chat_event(db, ticket, event, body, *, client=False, system=False):
    web_chat.record_message(db, ticket.client_id, message_type='system' if system else 'client' if client else 'bot',
        author='Вы' if client else 'Elite Sochi Support', text=body, created_at=web_auth.utcnow(),
        event_key=f'web-task:{ticket.id}:{event}')


def close_draft(db, ticket, reason):
    now = web_auth.utcnow()
    ticket.intake_state = 'cancelled'
    ticket.status = 'closed'
    ticket.closed_at = now
    ticket.quick_action_state_json = {**(ticket.quick_action_state_json or {}), STATE_KEY: {**state(ticket), 'reason': reason}}
    sync_work_interval_after_close(db, ticket, now=now)
    append_system_message(db, ticket, 'Создание задачи отменено: клиент не отправил текст вовремя'
                          if reason == 'expired' else 'Создание задачи отменено клиентом через WEB')
    key = 'telegram.intake.task_timeout' if reason == 'expired' else 'telegram.ticket.close.unassigned.client'
    if reason == 'cancelled':
        chat_event(db, ticket, 'cancel-requested', 'Отменить создание задачи', client=True)
    chat_event(db, ticket, reason, message_text(db, key, ticket))


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


def active_task(db, owner):
    candidates = db.scalars(select(Ticket).where(
        Ticket.client_id == owner.id, Ticket.company_id == owner.company_id,
        Ticket.channel == 'web', Ticket.workflow_type == 'task', Ticket.task_kind == 'normal',
        Ticket.intake_state == 'collecting', Ticket.status == 'unassigned', Ticket.assignee_id.is_(None)
    ).order_by(Ticket.created_at.desc(), Ticket.id.desc())).all()
    for ticket in candidates:
        if not state(ticket):
            continue
        ticket = owned_task(db, owner, ticket.id)  # Persist expiration through the existing path.
        if ticket.intake_state == 'collecting':
            return {**response(db, ticket), 'button_id': state(ticket)['button_id']}
    return None


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


def confirm_task(db, owner, client, task_id):
    ticket = owned_task(db, owner, task_id)
    if ticket.intake_state != 'collecting':
        response(db, ticket)  # Preserve the configured timeout error.
        fail(db, 'web_task_already_created' if ticket.intake_state == 'ready' else 'web_task_cancelled')
    available_button(db, owner, state(ticket)['button_id'])
    if not state(ticket)['confirmed']:
        check_active(db, owner)
        ticket.quick_action_state_json = {**ticket.quick_action_state_json, STATE_KEY: {
            **state(ticket), 'confirmed': True,
            'expires_at': (web_auth.utcnow() + timedelta(minutes=telegram.TASK_DESCRIPTION_TIMEOUT_MINUTES)).isoformat()}}
        web_auth.register_web_activity(db, client)
        result = response(db, ticket)
        chat_event(db, ticket, 'confirmed', 'Подтвердить', client=True)
        chat_event(db, ticket, 'description-requested', result['text'])
        db.commit()
        return result
    return response(db, ticket)


def select_task(db, owner, client, button_id, request_id, confirmed=False):
    task_id = str(uuid5(REQUEST_NAMESPACE, f'{owner.company_id}:{owner.id}:{request_id}'))
    existing = db.get(Ticket, task_id)
    if existing is not None:
        ticket = owned_task(db, owner, task_id)
        if state(ticket)['button_id'] != str(button_id):
            fail(db, 'web_task_request_conflict')
        if ticket.intake_state == 'collecting':
            if confirmed:
                return confirm_task(db, owner, client, task_id)
            available_button(db, owner, button_id)
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
    result = response(db, ticket)
    chat_event(db, ticket, 'selected', f'Выбран пункт «{button.label}»', system=True)
    chat_event(db, ticket, 'confirmation-requested' if confirmation else 'description-requested', result['text'])
    db.commit()
    return result


def describe_task(db, owner, client, task_id, body, uploads=(), preload_ids=()):
    ticket = owned_task(db, owner, task_id)
    body = body.strip()
    if uploads and preload_ids:
        fail(db, 'web_task_upload_methods_mixed', 422)
    if len(set(preload_ids)) != len(preload_ids):
        fail(db, 'web_task_duplicate_preloads', 422)
    preload_ids = sorted(preload_ids)
    files = file_manifest(uploads)
    if ticket.intake_state == 'ready':
        original = db.scalar(select(TicketMessage.body).where(TicketMessage.ticket_id == ticket.id,
                              TicketMessage.message_type == 'client').order_by(TicketMessage.created_at).limit(1))
        if (original != body or state(ticket).get('description_files', []) != files
                or state(ticket).get('description_preload_ids', []) != preload_ids):
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
    preloads = []
    for preload_id in preload_ids:
        row = claim_attachment_preload(db, preload_id=preload_id, ticket_id=ticket.id, client_id=owner.id)
        if row is None:
            fail(db, 'attachment_preload_not_found', 404)
        if row.status != 'ready' or not (row.telegram_file_id or '').strip():
            fail(db, 'attachment_preload_not_ready')
        preloads.append(row)
    message = telegram._append_client_message(db, ticket, body)
    for row in preloads:
        attachment = _attachment_from_preload(db=db, ticket=ticket, message=message, preload=row, delivery_status='ready')
        attachment.source = 'web'
        attachment.telegram_archive_message_id = row.telegram_staging_file_message_id
        attachment.telegram_archive_info_message_id = row.telegram_staging_info_message_id
    if preload_ids:
        ticket.quick_action_state_json = {**ticket.quick_action_state_json,
            STATE_KEY: {**state(ticket), 'description_preload_ids': preload_ids}}
    with attach_client_files(db, ticket, message, uploads):
        if files:
            ticket.quick_action_state_json = {**ticket.quick_action_state_json,
                STATE_KEY: {**state(ticket), 'description_files': files}}
        telegram._store_button_system_text(db, ticket, button, client_message=body)
        telegram._ready_ticket_for_distribution(db, ticket, button)
        web_auth.register_web_activity(db, client)
        db.flush()
        result = response(db, ticket)
        chat_event(db, ticket, 'description', body, client=True)
        chat_event(db, ticket, 'created', result['text'])
    # Retain files if the commit outcome is uncertain: a retry checks persisted
    # intake state and fingerprints before it can save or archive anything again.
    db.commit()
    return result


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


def collecting_preload_task(db, owner, task_id):
    ticket = owned_task(db, owner, task_id)
    if client_collecting_ticket(db, ticket.id, owner.id) is None:
        fail(db, 'web_task_no_longer_collecting', 409)
    if not state(ticket)['confirmed']:
        fail(db, 'web_task_confirmation_required', ticket=ticket, key='telegram.task.request_text')
    return ticket


def create_preload(db, owner, task_id, upload, upload_id):
    ticket = collecting_preload_task(db, owner, task_id)
    manifest, = file_manifest([upload])
    # Scope browser upload IDs to the actual session owner and the existing task.
    row_id = str(uuid5(REQUEST_NAMESPACE, f'preload:{owner.company_id}:{owner.id}:{ticket.id}:{upload_id}'))
    if row_id in state(ticket).get('deleted_preload_ids', []):
        fail(db, 'attachment_preload_deleted', 409)
    row = upload_state.lock_row(db, TicketAttachmentPreload, row_id)
    if row is not None:
        if (row.ticket_id != ticket.id or row.client_id != owner.id or row.user_id is not None
                or row.original_filename != manifest['filename'] or row.mime_type != manifest['mime_type']
                or row.file_size != manifest['size'] or upload_state.state(row).get('content_sha256') != manifest['sha256']):
            fail(db, 'attachment_upload_id_conflict', 409)
        if row.status == 'cancelled' or row.final_attachment_id:
            fail(db, 'attachment_preload_deleted', 409)
        db.commit()
    else:
        try:
            row = _save_attachment_preload(db, ticket, None, upload, archive_group_id=ticket.id,
                preload_id=row_id, client_id=owner.id)
        except OSError:
            fail(db, 'web_task_upload_failed', 503)
        cleanup_path = row.local_path
        try:
            db.commit()
        except Exception:
            db.rollback()
            from .telegram_outbound_delivery import _safe_unlink
            _safe_unlink(cleanup_path, db=db)
            raise
    return row, row.status == 'uploading'


def preload_status(db, owner, task_id, preload_id):
    owned_task(db, owner, task_id)
    row = owned_client_preload(db, str(preload_id), str(task_id), owner.id, allow_bound=True)
    row = upload_state.expire_attempt(db, row)
    if row is None:
        fail(db, 'attachment_preload_not_found', 404)
    # Polling must not count as client activity.
    return upload_state.preload_payload(row)


def retry_preload(db, owner, task_id, preload_id, confirm_uncertain=False):
    collecting_preload_task(db, owner, task_id)
    row = owned_client_preload(db, str(preload_id), str(task_id), owner.id)
    if row.final_attachment_id or row.status == 'cancelled':
        fail(db, 'attachment_preload_deleted', 409)
    return upload_state.retry_preload(db, row, confirm_uncertain, client_id=owner.id)


def delete_preload(db, owner, task_id, preload_id):
    ticket = collecting_preload_task(db, owner, task_id)
    deleted = state(ticket).get('deleted_preload_ids', [])
    row = upload_state.lock_row(db, TicketAttachmentPreload, str(preload_id))
    if row is None and str(preload_id) in deleted:
        return {'ok': True}
    row = owned_client_preload(db, str(preload_id), ticket.id, owner.id)
    if row.final_attachment_id:
        fail(db, 'attachment_preload_already_claimed', 409)
    ticket.quick_action_state_json = {**ticket.quick_action_state_json,
        STATE_KEY: {**state(ticket), 'deleted_preload_ids': sorted(set(deleted) | {row.id})}}
    _discard_preload(db, row, delete_row=True, unlink_local=True)
    return {'ok': True}
