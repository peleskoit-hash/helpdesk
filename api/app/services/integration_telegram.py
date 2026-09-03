from __future__ import annotations

import html
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

from sqlalchemy import and_, desc, select, text
from sqlalchemy.orm import Session

from ..models import Client, ClientProfile, IntegrationLog, TelegramContact
from .integration_tasks import INTEGRATION_SOURCE_EXTERNAL_API
from .telegram_bot import TelegramApiError, send_message

INTEGRATION_EVENT_TELEGRAM_SEND = "telegram_send"


@dataclass(frozen=True)
class IntegrationTelegramResult:
    result: str
    code: str
    user_id: str
    call_time: datetime
    telegram_message_id: str | None = None
    description: str | None = None


class IntegrationTelegramError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        user_id: str,
        call_time: datetime,
        description: str,
    ) -> None:
        super().__init__(description)
        self.status_code = status_code
        self.code = code
        self.user_id = user_id
        self.call_time = call_time
        self.description = description


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _call_time_out(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _acquire_telegram_lock(db: Session, *, bitrix_id: str, call_time: datetime) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    lock_key = f"integration_telegram:{bitrix_id}:{_call_time_out(call_time)}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})


def _telegram_message_id(response: dict | None) -> str | None:
    message_id = ((response or {}).get("result") or {}).get("message_id")
    if message_id is None:
        return None
    return str(message_id)


def _log(
    db: Session,
    *,
    result: str,
    code: str,
    description: str,
    bitrix_id: str | None = None,
    call_time: datetime | None = None,
    message_text: str | None = None,
    telegram_message_id: str | None = None,
) -> IntegrationLog:
    row = IntegrationLog(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        source=INTEGRATION_SOURCE_EXTERNAL_API,
        event_type=INTEGRATION_EVENT_TELEGRAM_SEND,
        result=result,
        code=code,
        description=description,
        deal_id=None,
        task_id=None,
        bitrix_id=bitrix_id,
        call_time=_as_utc(call_time) if call_time is not None else None,
        message_text=message_text,
        telegram_message_id=telegram_message_id,
    )
    db.add(row)
    db.flush()
    return row


def log_invalid_payload(
    db: Session,
    *,
    bitrix_id: str | None = None,
    call_time: datetime | None = None,
    message_text: str | None = None,
) -> None:
    _log(
        db,
        result="error",
        code="invalid_payload",
        description="Некорректный payload Telegram Integration API.",
        bitrix_id=bitrix_id,
        call_time=call_time,
        message_text=message_text,
    )
    db.commit()


def _previous_success(db: Session, *, bitrix_id: str, call_time: datetime) -> IntegrationLog | None:
    return db.execute(
        select(IntegrationLog)
        .where(
            and_(
                IntegrationLog.source == INTEGRATION_SOURCE_EXTERNAL_API,
                IntegrationLog.event_type == INTEGRATION_EVENT_TELEGRAM_SEND,
                IntegrationLog.result == "success",
                IntegrationLog.bitrix_id == bitrix_id,
                IntegrationLog.call_time == _as_utc(call_time),
            )
        )
        .order_by(desc(IntegrationLog.created_at))
        .limit(1)
    ).scalar_one_or_none()


def _client_for_bitrix_id(db: Session, bitrix_id: str) -> Client | None:
    rows = db.execute(
        select(Client)
        .join(ClientProfile, ClientProfile.client_id == Client.id)
        .where(ClientProfile.bitrix_id == bitrix_id)
        .order_by(Client.id.asc())
    ).scalars().all()
    distinct_by_id = {client.id: client for client in rows}
    if len(distinct_by_id) > 1:
        raise ValueError("ambiguous")
    return next(iter(distinct_by_id.values()), None)


def _telegram_contact_for_client(db: Session, client_id: str) -> TelegramContact | None:
    return db.execute(
        select(TelegramContact)
        .where(TelegramContact.client_id == client_id)
        .order_by(TelegramContact.updated_at.desc(), TelegramContact.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


class _StrictIntegrationHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = (tag or "").lower()
        if normalized == "br":
            self.parts.append("\n")
            return
        if normalized in {"b", "i"}:
            self.parts.append(f"<{normalized}>")
            self.stack.append(normalized)
            return
        self.parts.append(html.escape(self.get_starttag_text() or f"<{normalized}>", quote=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = (tag or "").lower()
        if normalized == "br":
            self.parts.append("\n")
            return
        self.parts.append(html.escape(self.get_starttag_text() or f"<{normalized}/>", quote=False))

    def handle_endtag(self, tag: str) -> None:
        normalized = (tag or "").lower()
        if normalized in {"b", "i"} and self.stack and self.stack[-1] == normalized:
            self.stack.pop()
            self.parts.append(f"</{normalized}>")
            return
        self.parts.append(html.escape(f"</{normalized}>", quote=False))

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data or "", quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def getvalue(self) -> str:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts)


def prepare_integration_telegram_html_message(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parser = _StrictIntegrationHtmlSanitizer()
    parser.feed(raw)
    parser.close()
    return parser.getvalue()


def send_integration_telegram_message(
    db: Session,
    *,
    bitrix_id: str,
    call_time: datetime,
    message_text: str,
) -> IntegrationTelegramResult:
    normalized_call_time = _as_utc(call_time)
    _acquire_telegram_lock(db, bitrix_id=bitrix_id, call_time=normalized_call_time)

    previous_success = _previous_success(db, bitrix_id=bitrix_id, call_time=normalized_call_time)
    if previous_success is not None:
        _log(
            db,
            result="duplicate",
            code="duplicate_call_time",
            description="Telegram-сообщение для этого клиента и call_time уже было успешно отправлено.",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
            telegram_message_id=previous_success.telegram_message_id,
        )
        db.commit()
        return IntegrationTelegramResult(
            result="duplicate",
            code="duplicate_call_time",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            telegram_message_id=previous_success.telegram_message_id,
        )

    try:
        client = _client_for_bitrix_id(db, bitrix_id)
    except ValueError:
        _log(
            db,
            result="error",
            code="bitrix_client_ambiguous",
            description="По Bitrix ID найдено несколько разных клиентов.",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
        )
        db.commit()
        raise IntegrationTelegramError(
            status_code=409,
            code="bitrix_client_ambiguous",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            description="По Bitrix ID найдено несколько разных клиентов.",
        )

    if client is None:
        _log(
            db,
            result="error",
            code="bitrix_client_not_found",
            description="Клиент с таким Bitrix ID не найден.",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
        )
        db.commit()
        raise IntegrationTelegramError(
            status_code=404,
            code="bitrix_client_not_found",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            description="Клиент с таким Bitrix ID не найден.",
        )

    contact = _telegram_contact_for_client(db, client.id)
    if contact is None or not str(contact.external_chat_id or "").strip():
        _log(
            db,
            result="error",
            code="telegram_contact_not_found",
            description="Telegram-контакт клиента не найден.",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
        )
        db.commit()
        raise IntegrationTelegramError(
            status_code=409,
            code="telegram_contact_not_found",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            description="Telegram-контакт клиента не найден.",
        )

    if contact.is_blocked:
        _log(
            db,
            result="error",
            code="telegram_contact_blocked",
            description="Telegram-контакт клиента заблокирован.",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
        )
        db.commit()
        raise IntegrationTelegramError(
            status_code=409,
            code="telegram_contact_blocked",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            description="Telegram-контакт клиента заблокирован.",
        )

    prepared_message = prepare_integration_telegram_html_message(message_text)
    try:
        response = send_message(str(contact.external_chat_id).strip(), prepared_message, parse_mode="HTML")
    except TelegramApiError as exc:
        db.rollback()
        _log(
            db,
            result="error",
            code="telegram_send_failed",
            description=f"Не удалось отправить Telegram-сообщение: {exc}",
            bitrix_id=bitrix_id,
            call_time=normalized_call_time,
            message_text=message_text,
        )
        db.commit()
        raise IntegrationTelegramError(
            status_code=502,
            code="telegram_send_failed",
            user_id=bitrix_id,
            call_time=normalized_call_time,
            description="Не удалось отправить Telegram-сообщение.",
        ) from exc

    telegram_message_id = _telegram_message_id(response)
    _log(
        db,
        result="success",
        code="telegram_sent",
        description="Telegram-сообщение отправлено.",
        bitrix_id=bitrix_id,
        call_time=normalized_call_time,
        message_text=message_text,
        telegram_message_id=telegram_message_id,
    )
    db.commit()
    return IntegrationTelegramResult(
        result="success",
        code="telegram_sent",
        user_id=bitrix_id,
        call_time=normalized_call_time,
        telegram_message_id=telegram_message_id,
    )
