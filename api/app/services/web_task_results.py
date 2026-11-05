"""Immutable customer publications, inserted in the staff close transaction."""
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..config import settings
from ..models import Client, Ticket, WebTaskResult, WebTaskResultFile
from . import web_auth
from .ticket_attachments import _ensure_local_attachment_file
from .telegram_text_format import strip_telegram_html
from .web_history import history_scope


def fail(status, code, message):
    raise HTTPException(status, detail={'error': {'code': code, 'message': message, 'details': {}}})


def _snapshot_file(attachment):
    root = Path(settings.attachment_storage_dir).resolve()
    path = Path(attachment.local_path).resolve()
    if not path.is_relative_to(root):
        fail(409, 'web_result_file_unavailable', 'Не удалось сохранить файл окончательного ответа')
    try:
        path = _ensure_local_attachment_file(attachment).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise OSError('Unavailable attachment')
        content = path.read_bytes()
    except Exception as exc:
        # Do not disclose paths, bot tokens or remote storage details.
        raise HTTPException(409, detail={'error': {'code': 'web_result_file_unavailable',
            'message': 'Не удалось сохранить файл окончательного ответа', 'details': {}}}) from exc
    name = (attachment.original_filename or 'file').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:255] or 'file'
    return {'id': str(uuid4()), 'filename': name, 'mime_type': attachment.mime_type or 'application/octet-stream',
            'size': len(content), 'data': content}


def publish_result(db, ticket, message, attachments):
    """Never commit here. Unique task_id makes concurrent/repeated publication inert."""
    if (ticket.channel != 'web' or ticket.workflow_type != 'task' or ticket.task_kind != 'normal'
            or ticket.status != 'closed' or ticket.closed_at is None or ticket.suppress_client_answer
            or message.ticket_id != ticket.id or message.message_type != 'agent'
            or message.message_status == 'deleted'):
        return
    if db.get(WebTaskResult, ticket.id) is not None:
        return
    final_files = [attachment for attachment in attachments
                   if attachment.ticket_id == ticket.id and attachment.message_id == message.id]
    if not (message.body or '').strip() and not final_files:
        return
    files = [_snapshot_file(attachment) for attachment in final_files]
    insert = sqlite_insert if db.get_bind().dialect.name == 'sqlite' else pg_insert
    created = db.scalar(insert(WebTaskResult).values(task_id=ticket.id, company_id=ticket.company_id,
        client_id=ticket.client_id, message_id=message.id, text=message.body or '', closed_at=ticket.closed_at,
        read_at=None).on_conflict_do_nothing(index_elements=['task_id']).returning(WebTaskResult.task_id))
    if created is not None:
        for position, file in enumerate(files):
            db.add(WebTaskResultFile(result_task_id=ticket.id, position=position, **file))


def owner_filter(db, client):
    owner = db.get(Client, client.id)
    if owner is None:
        fail(401, 'web_unauthorized', 'Требуется WEB-авторизация')
    return (WebTaskResult.client_id == owner.id, WebTaskResult.company_id == owner.company_id,
            select(Ticket.id).where(Ticket.id == WebTaskResult.task_id,
                *history_scope(db, client)).exists())


def owned_result(db, client, task_id):
    result = db.scalar(select(WebTaskResult).where(WebTaskResult.task_id == str(task_id), *owner_filter(db, client)))
    if result is None:
        fail(404, 'web_task_result_not_found', 'Результат не найден')
    return result


def list_results(db, client, limit, offset):
    scope = owner_filter(db, client)
    total = db.scalar(select(func.count()).select_from(WebTaskResult).where(*scope))
    unread = db.scalar(select(func.count()).select_from(WebTaskResult).where(*scope, WebTaskResult.read_at.is_(None)))
    rows = db.execute(select(WebTaskResult.task_id, WebTaskResult.message_id, WebTaskResult.closed_at, WebTaskResult.read_at)
        .where(*scope).order_by(WebTaskResult.closed_at.desc(), WebTaskResult.task_id).limit(limit).offset(offset)).mappings().all()
    return {'items': [dict(row) for row in rows], 'total': total, 'unread_count': unread}


def result_detail(db, client, task_id):
    result = owned_result(db, client, task_id)
    ticket = db.get(Ticket, result.task_id)
    # Read-only projection also serves existing publications. Never expose a
    # pending hint independently of a successfully published normal WEB result.
    hint = None
    if (ticket.channel == 'web' and ticket.workflow_type == 'task' and ticket.task_kind == 'normal'
            and ticket.status == 'closed' and not ticket.suppress_client_answer):
        hint = strip_telegram_html(ticket.wrong_problem_hint or '').strip() or None
    files = db.scalars(select(WebTaskResultFile).where(WebTaskResultFile.result_task_id == result.task_id)
                       .order_by(WebTaskResultFile.position, WebTaskResultFile.id)).all()
    return {'task_id': result.task_id, 'message_id': result.message_id, 'text': result.text,
            'wrong_problem_hint': hint,
            'closed_at': result.closed_at, 'read_at': result.read_at,
            'files': [{'id': file.id, 'filename': file.filename, 'mime_type': file.mime_type, 'size': file.size,
                       'download_url': f'/api/v1/web/task-results/{result.task_id}/files/{file.id}'} for file in files]}


def mark_read(db, client, task_id):
    result = owned_result(db, client, task_id)
    changed = db.execute(update(WebTaskResult).where(WebTaskResult.task_id == result.task_id,
        *owner_filter(db, client), WebTaskResult.read_at.is_(None)).values(read_at=web_auth.utcnow())).rowcount
    if changed:
        web_auth.register_web_activity(db, client)
    db.commit()
    db.refresh(result)
    return {'task_id': result.task_id, 'read_at': result.read_at}


def result_file(db, client, task_id, file_id):
    result = owned_result(db, client, task_id)
    file = db.scalar(select(WebTaskResultFile).where(WebTaskResultFile.id == str(file_id),
                                                  WebTaskResultFile.result_task_id == result.task_id))
    if file is None:
        fail(404, 'web_task_result_file_not_found', 'Файл не найден')
    return file
