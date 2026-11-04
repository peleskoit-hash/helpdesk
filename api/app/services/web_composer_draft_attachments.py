"""Client-owned file preparation independent of task intake and public numbering."""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..models import TicketAttachmentPreload, WebClientDraft
from . import telegram_upload_state as upload_state
from .attachment_preload_owners import owned_draft_preload, owner_scope
from .ticket_attachments import _ensure_local_attachment_file, _save_attachment_preload, _error
from .telegram_outbound_delivery import (_discard_preload, _safe_unlink, claim_attachment_preload,
    _actual_info_message_id, _pending_info_message_id)
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
            current = db.scalar(select(TicketAttachmentPreload).where(
                TicketAttachmentPreload.composer_draft_id == draft['id'], TicketAttachmentPreload.ticket_id.is_(None),
                TicketAttachmentPreload.status != 'cancelled').order_by(
                TicketAttachmentPreload.created_at, TicketAttachmentPreload.id).limit(1))
            group_id = current.archive_group_id if current is not None else draft['id']
            row = _save_attachment_preload(db, None, None, upload, archive_group_id=group_id,
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
    # Transfer may have committed while this request waited for the draft lock.
    owned_draft_preload(db, row.id, client.id)
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


def claim_description_preloads(db, client, ticket, preload_ids):
    """Draft -> rows is the same lock order as create/delete. Validate the whole snapshot first."""
    if not preload_ids:
        return [], []
    draft = db.scalar(select(WebClientDraft).where(WebClientDraft.client_id == client.id))
    draft_rows = []
    if draft is not None:
        upload_state.lock_row(db, WebClientDraft, draft.id)
        candidates = db.scalars(select(TicketAttachmentPreload).where(
            TicketAttachmentPreload.composer_draft_id == draft.id,
            TicketAttachmentPreload.ticket_id.is_(None), *owner_scope(client_id=client.id))
            .order_by(TicketAttachmentPreload.created_at, TicketAttachmentPreload.id)).all()
        draft_rows = [upload_state.lock_row(db, TicketAttachmentPreload, row.id) for row in candidates]
    by_id = {row.id: row for row in draft_rows}
    selected = []
    for preload_id in preload_ids:
        row = by_id.get(preload_id)
        if row is None:
            row = claim_attachment_preload(db, preload_id=preload_id, ticket_id=ticket.id, client_id=client.id)
            if row is not None and row.composer_draft_id is not None:
                _error(404, 'attachment_preload_not_found', 'Предзагруженный файл не найден')
        if row is None or row.final_attachment_id:
            _error(404, 'attachment_preload_not_found', 'Предзагруженный файл не найден')
        if row.status != 'ready' or not (row.telegram_file_id or '').strip():
            _error(409, 'attachment_preload_not_ready', 'Файл ещё не подготовлен')
        selected.append(row)
    sync_ids = {row.id for row in selected}
    selected_ids = set(preload_ids)
    for group_id in {row.archive_group_id for row in selected if row.composer_draft_id is not None}:
        group = [row for row in draft_rows if row.archive_group_id == group_id and row.status != 'cancelled']
        info_id = _actual_info_message_id(group)
        if not info_id and _pending_info_message_id(group):
            # Wait for the in-flight summary receipt rather than create a second summary.
            _error(409, 'attachment_preload_not_ready', 'Подготовка сводки файлов ещё не завершена')
        remaining_group = str(uuid4())
        for row in group:
            upload_state.patch(row, archive_reconcile_pending=True)
            sync_ids.add(row.id)
            if row.id in selected_ids:
                row.ticket_id, row.composer_draft_id = ticket.id, None
                row.telegram_staging_info_message_id = info_id
            else:
                # Rotate the batch identity too: in-flight staging must detect the split.
                row.archive_group_id = remaining_group
                row.telegram_staging_info_message_id = None
    for row in selected:
        upload_state.patch(row, archive_reconcile_pending=True)
    return selected, sorted(sync_ids)
