"""Read-only customer projection; file access uses the same visibility decision."""
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..models import TelegramContact, TicketAttachment, TicketMessage, WebTaskResult
from .ticket_attachments import _ensure_local_attachment_file
from .ticket_messages import initial_task_message_ids
from .web_history import owned_history_ticket
from .web_task_results import result_detail


def _filename(value):
    name = (value or 'file').replace('\\', '/').rsplit('/', 1)[-1]
    return ''.join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:255] or 'file'


def _preview(source, mime):
    if source == 'sticker':
        return 'sticker' if mime in {'image/webp', 'application/x-tgsticker', 'video/webm'} else None
    return 'image' if mime in {'image/jpeg', 'image/png', 'image/gif', 'image/webp'} else None


def _delivered_file(attachment):
    # "sent" alone can mean archiving; require a client-side message receipt.
    return attachment.telegram_delivery_status == 'sent' and bool((attachment.telegram_message_id or '').strip())


def _visible_content(db, client, ticket_id):
    ticket = owned_history_ticket(db, client, ticket_id)
    messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)
                          .order_by(TicketMessage.created_at, TicketMessage.id)).all()
    attachments = db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id)
                             .order_by(TicketAttachment.created_at, TicketAttachment.id)).all()
    by_message = {}
    for attachment in attachments:
        by_message.setdefault(attachment.message_id, []).append(attachment)

    if ticket.workflow_type == 'task':
        # Telegram close currently discards the final text delivery receipt.
        # Neither closed status nor an archive receipt proves publication; do
        # not expose a guessed last AGENT message (including group-parent copies).
        # Keep the original request, including its Telegram album. Never promote a
        # later client message when the original has been deleted.
        initial = initial_task_message_ids(ticket, messages, by_message)
        first = next((m for m in messages if m.message_type == 'client'), None)
        if not initial and first is not None:
            initial = {first.id}
        visible = [m for m in messages if m.id in initial and m.message_status != 'deleted']
    elif ticket.workflow_type in {None, 'ticket'}:
        # Ordinary WEB replies have no persisted customer publication receipt.
        # A delivered file alone cannot confirm a nonempty message's text.
        visible = [m for m in messages if m.message_status != 'deleted' and (
            m.message_type == 'client' or (m.message_type == 'agent' and ticket.channel == 'telegram' and (
                bool((m.telegram_message_id or '').strip()) or (not (m.body or '').strip() and any(
                    _delivered_file(a)
                    for a in by_message.get(m.id, [])
                ))
            ))
        )]
    else:
        visible = []

    allowed = {}
    for message in visible:
        allowed[message.id] = [a for a in by_message.get(message.id, []) if (
            message.message_type == 'client' or (
                ticket.channel == 'telegram' and _delivered_file(a)
            )
        )]
    return ticket, visible, allowed


def history_messages(db, client, ticket_id):
    ticket, messages, attachments = _visible_content(db, client, ticket_id)
    items = []
    for message in messages:
        files = []
        for attachment in attachments[message.id]:
            mime = attachment.mime_type or 'application/octet-stream'
            files.append({'id': attachment.id, 'filename': _filename(attachment.original_filename),
                'mime_type': mime, 'size': attachment.file_size, 'source': attachment.source,
                'preview_kind': _preview(attachment.source, mime),
                'download_url': f'/api/v1/web/history/{ticket.id}/attachments/{attachment.id}/download'})
        items.append({'id': message.id, 'message_type': message.message_type, 'text': message.body,
                      'created_at': message.created_at, 'files': files})

    if ticket.workflow_type == 'task' and ticket.channel == 'web' and ticket.status == 'closed':
        publication = db.get(WebTaskResult, ticket.id)
        if publication is not None:
            result = result_detail(db, client, ticket.id)
            items.append({'id': result['message_id'], 'message_type': 'agent', 'text': result['text'],
                'created_at': result['closed_at'], 'files': [dict(file, source='web_result',
                    preview_kind=_preview('web_result', file['mime_type'])) for file in result['files']]})
            if result['wrong_problem_hint']:
                items[-1]['wrong_problem_hint'] = result['wrong_problem_hint']
    return {'ticket_id': ticket.id, 'items': items}


def history_attachment(db, client, ticket_id, attachment_id):
    ticket, _, by_message = _visible_content(db, client, ticket_id)
    attachment = next((a for files in by_message.values() for a in files if a.id == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(404, detail='Файл не найден')
    root = Path(settings.attachment_storage_dir).resolve()
    try:
        if not Path(attachment.local_path).resolve().is_relative_to(root):
            raise ValueError('Outside attachment storage')
        contact = db.scalar(select(TelegramContact).where(TelegramContact.client_id == ticket.client_id,
                                                        TelegramContact.company_id == ticket.company_id))
        path = _ensure_local_attachment_file(attachment, contact.external_chat_id if contact else None).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Unavailable attachment')
    except Exception as exc:
        # Never return storage paths, Telegram errors, IDs or bot tokens.
        raise HTTPException(404, detail='Файл не найден') from exc
    return path, _filename(attachment.original_filename)
