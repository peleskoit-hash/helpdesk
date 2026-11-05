"""Metadata-only notices for WEB attachments rejected before upload."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, text

from ..models import Client, WebChatMessage
from . import web_auth, web_chat
from .automatic_messages import render_automatic_message_text
from .telegram_attachment_policy import _kb_size
from .telegram_text_format import strip_telegram_html

RECENT_MESSAGE_LIMIT = 20


def attachment_too_large(db, client, file_name, file_size, event_id):
    # Serialize the duplicate check, rate check and insert across API workers.
    if db.get_bind().dialect.name == 'postgresql':
        db.scalar(select(Client.id).where(Client.id == client.id).with_for_update())
    else:
        db.execute(text('BEGIN IMMEDIATE'))
    event_key = f'{web_chat.FILE_SIZE_NOTICE_PREFIX}{event_id}'
    existing = db.scalar(select(WebChatMessage).where(WebChatMessage.client_id == client.id,
                                                    WebChatMessage.event_key == event_key))
    if existing is not None:
        return existing
    now = web_auth.utcnow()
    recent = db.scalar(select(func.count()).select_from(WebChatMessage).where(
        WebChatMessage.client_id == client.id,
        WebChatMessage.event_key.startswith(web_chat.FILE_SIZE_NOTICE_PREFIX, autoescape=True),
        WebChatMessage.created_at >= now - timedelta(minutes=1)))
    if recent >= RECENT_MESSAGE_LIMIT:
        raise HTTPException(429, detail='Слишком много уведомлений. Повторите позже.', headers={'Retry-After': '60'})
    body = render_automatic_message_text(db, 'telegram.attachment.too_large',
        extra={'file_name': file_name, 'file_size': _kb_size(file_size)}, escape_values_for_html=True)
    return web_chat.record_message(db, client.id, message_type='bot', author='Elite Sochi Support',
        text=strip_telegram_html(body), created_at=now, event_key=event_key)
