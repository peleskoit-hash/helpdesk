"""Client-owned file preparation independent of task intake and public numbering."""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..models import TicketAttachmentPreload, WebClientDraft
from . import telegram_upload_state as upload_state
from .attachment_preload_owners import owned_draft_preload
from .ticket_attachments import _ensure_local_attachment_file, _save_attachment_preload, _error
from .telegram_outbound_delivery import _discard_preload, _safe_unlink
from .web_composer_draft import get_draft
from .web_task_attachments import file_manifest

UPLOAD_NAMESPACE = UUID('42d11f65-318e-4dc2-99a0-dbe76c4616ab')
BASE = '/api/v1/web/composer-draft/attachment-preloads'


def payload(row):
    return {**upload_state.preload_payload(row), 'original_filename': row.original_filename,
            'mime_type': row.mime_type, 'file_size': row.file_size, 'created_at': row.created_at,
            'content_url': f'{BASE}/{row.id}/content'}


def create_preload(db, client, upload, upload_id):
    manifest, = file_manifest([upload])
    draft = get_draft(db, client.id)
    # Serialize inserts for this draft across API workers, including repeated upload IDs.
    upload_state.lock_row(db, WebClientDraft, draft['id'])
    row_id = str(uuid5(UPLOAD_NAMESPACE, f"{draft['id']}:{upload_id}"))
    row = upload_state.lock_row(db, TicketAttachmentPreload, row_id)
    if row is not None:
        owned_draft_preload(db, row_id, client.id)
        if (row.original_filename != manifest['filename'] or row.mime_type != manifest['mime_type']
                or row.file_size != manifest['size'] or upload_state.state(row).get('content_sha256') != manifest['sha256']):
            _error(409, 'attachment_upload_id_conflict', 'upload_id уже использован для другого файла')
        if row.status == 'cancelled' or row.final_attachment_id:
            _error(409, 'attachment_preload_deleted', 'Предзагруженный файл удалён')
        db.commit()
    else:
        try:
            row = _save_attachment_preload(db, None, None, upload, archive_group_id=draft['id'],
                preload_id=row_id, client_id=client.id, composer_draft_id=draft['id'])
        except OSError as exc:
            raise HTTPException(503, detail='Не удалось сохранить файл') from exc
        path = row.local_path
        try:
            db.commit()
        except Exception:
            db.rollback()
            _safe_unlink(path, db=db)
            raise
    return row, row.status == 'uploading'


def list_preloads(db, client):
    rows = db.scalars(select(TicketAttachmentPreload).join(WebClientDraft,
        TicketAttachmentPreload.composer_draft_id == WebClientDraft.id).where(
        WebClientDraft.client_id == client.id, TicketAttachmentPreload.client_id == client.id,
        TicketAttachmentPreload.user_id.is_(None), TicketAttachmentPreload.ticket_id.is_(None),
        TicketAttachmentPreload.status != 'cancelled').order_by(
        TicketAttachmentPreload.created_at, TicketAttachmentPreload.id)).all()
    return [payload(upload_state.expire_attempt(db, row)) for row in rows]


def status(db, client, preload_id):
    row = owned_draft_preload(db, preload_id, client.id)
    if row.status == 'cancelled':
        raise HTTPException(404, detail='Файл не найден')
    return payload(upload_state.expire_attempt(db, row))


def retry(db, client, preload_id, confirm_uncertain):
    row = owned_draft_preload(db, preload_id, client.id)
    if row.status == 'cancelled' or row.final_attachment_id:
        _error(409, 'attachment_preload_deleted', 'Предзагруженный файл удалён')
    return upload_state.retry_preload(db, row, confirm_uncertain, client_id=client.id)


def delete(db, client, preload_id):
    row = owned_draft_preload(db, preload_id, client.id)
    upload_state.lock_row(db, WebClientDraft, row.composer_draft_id)
    row = upload_state.lock_row(db, TicketAttachmentPreload, row.id)
    if row.final_attachment_id:
        _error(409, 'attachment_preload_already_claimed', 'Вложение уже связано с отправленным сообщением')
    _discard_preload(db, row, delete_row=True, unlink_local=True)
    return {'ok': True}


def content(db, client, preload_id):
    row = owned_draft_preload(db, preload_id, client.id)
    if row.status == 'cancelled':
        raise HTTPException(404, detail='Файл не найден')
    root = Path(settings.attachment_storage_dir).resolve()
    try:
        if not Path(row.local_path).resolve().is_relative_to(root):
            raise ValueError('Outside attachment storage')
        # Reuse Telegram restoration without adding TicketAttachment or persisting a second file entity.
        source = SimpleNamespace(local_path=row.local_path, telegram_file_id=row.telegram_file_id,
                                 telegram_file_path=None)
        path = _ensure_local_attachment_file(source).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Unavailable file')
    except Exception as exc:
        raise HTTPException(404, detail='Файл не найден') from exc
    return path, row.original_filename or 'file'
