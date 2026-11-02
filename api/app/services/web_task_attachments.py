"""WEB intake limits and transaction-scoped use of the existing attachment archive."""
from contextlib import contextmanager
import hashlib
import logging
from pathlib import Path

from fastapi import HTTPException

from .ticket_attachments import _safe_filename, _save_upload_attachment
from .telegram_bot import TelegramApiError
from .telegram_outbound_delivery import _delete_archive_message_by_id, _send_staging_document_from_path
from .telegram_attachments import (
    _client_name_for_archive, _inbound_group_archive_info_text,
    _prepare_archive_html_text, _send_message_with_retry, _staging_chat_id, _telegram_message_id,
)

MAX_FILE_SIZE = 20 * 1024 * 1024
logger = logging.getLogger(__name__)


def fail(status, code, message):
    raise HTTPException(status, detail={'error': {'code': code, 'message': message, 'details': {}}})


def file_manifest(uploads):
    files = []
    for upload in uploads:
        if 'sticker' in (upload.content_type or '').lower() or Path(upload.filename or '').suffix.lower() == '.tgs':
            fail(422, 'web_task_sticker_not_supported', 'Стикеры не поддерживаются. Выберите обычный файл.')
        digest, size = hashlib.sha256(), 0
        upload.file.seek(0)
        try:
            for chunk in iter(lambda: upload.file.read(1024 * 1024), b''):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    fail(413, 'web_task_file_too_large', 'Каждый файл должен быть не больше 20 МиБ.')
                digest.update(chunk)
        finally:
            upload.file.seek(0)
        files.append({'filename': _safe_filename(upload.filename), 'mime_type': upload.content_type or 'application/octet-stream',
                      'size': size, 'sha256': digest.hexdigest()})
    return files


@contextmanager
def attach_client_files(db, ticket, message, uploads):
    attachments, archive_ids = [], []
    def receipt(response):
        message_id = str(((response or {}).get('result') or {}).get('message_id') or '')
        if message_id.isdecimal() and int(message_id) > 0:
            archive_ids.append(message_id)
    try:
        try:
            for upload in uploads:
                attachment = _save_upload_attachment(db, ticket, message, upload)
                attachment.source = 'web'
                attachments.append(attachment)
            db.flush()
            for attachment in attachments:
                file_id, unique_id, archive_id = _send_staging_document_from_path(
                    local_path=attachment.local_path, original_filename=attachment.original_filename,
                    mime_type=attachment.mime_type, on_receipt=receipt)
                attachment.telegram_file_id = file_id
                attachment.telegram_file_unique_id = unique_id
                attachment.telegram_archive_message_id = archive_id
                if archive_id not in archive_ids:
                    archive_ids.append(archive_id)
            if attachments:
                text = _inbound_group_archive_info_text(ticket, attachments, _client_name_for_archive(db, ticket))
                payload, parse_mode = _prepare_archive_html_text(text)
                result = _send_message_with_retry(_staging_chat_id(), payload, parse_mode=parse_mode)
                receipt(result)
                info_id = _telegram_message_id(result)
                if not str(info_id or '').isdecimal() or int(info_id) <= 0:
                    raise RuntimeError('Archive summary returned no message_id')
                for attachment in attachments:
                    attachment.telegram_archive_info_message_id = info_id
        except (OSError, TelegramApiError, RuntimeError) as exc:
            raise HTTPException(502, detail={'error': {'code': 'web_task_upload_failed',
                'message': 'Не удалось загрузить файлы. Повторите отправку.', 'details': {}}}) from exc
        yield attachments
    except Exception:
        db.rollback()
        for attachment in attachments:
            try:
                Path(attachment.local_path).unlink(missing_ok=True)
            except OSError:
                logger.warning('Could not remove failed WEB attachment %s', attachment.id)
        for archive_id in archive_ids:
            _delete_archive_message_by_id(archive_id)
        raise
