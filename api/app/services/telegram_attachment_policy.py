from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import MessageType, Ticket, TicketMessage
from .assignment import append_system_message
from .automatic_messages import render_automatic_message_for_telegram
from .telegram_bot import send_message

FORBIDDEN_ATTACHMENT_TEXT = (
    "Голосовые и видеосообщения технически не поддерживаются в данном сервисе и не будут доставлены сотруднику тех.поддержки.\n"
    "Пожалуйста, опишите проблему текстом и/или прикрепите файл/скриншот."
)
TELEGRAM_ATTACHMENT_SIZE_LIMIT_BYTES = 20 * 1024 * 1024
TOO_LARGE_ATTACHMENT_WARNING_MARKER = "Клиент уведомлен об ограничении размера файлов Telegram Bot API"


def attachment_is_too_large(attachment_data: dict | None) -> bool:
    if not attachment_data:
        return False
    size = attachment_data.get("file_size")
    try:
        size_value = int(size)
    except Exception:
        return False
    return size_value > TELEGRAM_ATTACHMENT_SIZE_LIMIT_BYTES


def too_large_attachment_system_text(attachment_data: dict | None) -> str:
    if not attachment_data:
        return "Клиент отправил файл больше 20 МБ, скачивание через Telegram Bot API недоступно"
    name = (attachment_data.get("original_filename") or attachment_data.get("source") or "Файл").strip()
    size = attachment_data.get("file_size")
    size_mb = None
    try:
        size_mb = int(size) / (1024 * 1024)
    except Exception:
        size_mb = None
    if size_mb is None:
        return f"Клиент отправил файл «{name}»: больше 20 МБ, скачивание недоступно"
    return f"Клиент отправил файл «{name}» ({size_mb:.1f} МБ): больше 20 МБ, скачивание недоступно"


def forbidden_attachment_system_text(forbidden_attachment_type: str) -> str:
    return f"Клиент отправил запрещённый тип сообщения: {forbidden_attachment_type}"


def _kb_size(value: object) -> int | None:
    try:
        size = int(value)
    except Exception:
        return None
    if size <= 0:
        return 0
    return (size + 1023) // 1024


def _too_large_attachment_client_notice(attachment_data: dict | None) -> str:
    name = ((attachment_data or {}).get("original_filename") or (attachment_data or {}).get("source") or "Файл").strip()
    size_kb = _kb_size((attachment_data or {}).get("file_size"))
    if size_kb is None:
        return f"Файл «{name}» имеет размер больше 20 МБ и не будет доставлен."
    return f"Файл «{name}» имеет размер {size_kb} КБ и не будет доставлен."


def send_too_large_attachment_client_messages(
    db: Session,
    ticket: Ticket,
    external_chat_id: str,
    attachment_data: dict | None,
    *,
    notify_client: bool,
) -> None:
    if not notify_client:
        return
    already_warned = db.execute(
        select(TicketMessage.id).where(
            and_(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.message_type == MessageType.SYSTEM.value,
                TicketMessage.body == TOO_LARGE_ATTACHMENT_WARNING_MARKER,
            )
        ).limit(1)
    ).first() is not None
    if not already_warned:
        limit_kb = (TELEGRAM_ATTACHMENT_SIZE_LIMIT_BYTES + 1023) // 1024
        send_message(
            external_chat_id,
            f"Обратите внимание, что файлы больше 20 МБ ({limit_kb} КБ) не будут доставлены в связи с ограничениями Telegram Bot API.",
        )
        append_system_message(db, ticket, TOO_LARGE_ATTACHMENT_WARNING_MARKER, agent_visible=False)
    text, parse_mode = render_automatic_message_for_telegram(
        db,
        "telegram.attachment.too_large",
        _too_large_attachment_client_notice(attachment_data),
        ticket=ticket,
        extra={"file_name": ((attachment_data or {}).get("original_filename") or (attachment_data or {}).get("source") or "файл"), "file_size": _kb_size((attachment_data or {}).get("file_size"))},
    )
    send_message(external_chat_id, text, parse_mode=parse_mode)
