import json
import logging
import re
import shutil
import tempfile
from collections import Counter
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from unicodedata import category
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import String, distinct, func, select, update
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..auth import CurrentUser, get_current_user, is_admin
from ..config import settings
from ..db import get_db
from ..models import Client, ClientCardBlockConfig, ClientCardFieldConfig, ClientProfile, MassNotificationOperation, MassNotificationRecipient, TelegramContact
from ..services.client_card import SYSTEM_PROFILE_FIELD_KEYS, _field_options_out
from ..schemas import MassNotificationRecipientOut
from ..services.mass_notification_delivery import (
    INTERRUPTED_ATTACHMENT_ERROR,
    INTERRUPTED_COMPONENT_ERROR,
    INTERRUPTED_OPERATION_ERROR,
    reconcile_stale_mass_notification_operations,
    run_mass_notification_operation,
)
from ..services.mass_notification_operations import MassNotificationOperationConflict, create_mass_notification_operation
from ..services.mass_notification_staging import DOCUMENT_LIMIT, PHOTO_LIMIT, PHOTO_MIME_TYPES
from ..services.telegram_bot import send_document, send_message, send_photo  # Existing router test patch points.


router = APIRouter(prefix="/api/v1/mass-notifications", tags=["mass-notifications"])

logger = logging.getLogger(__name__)
MAX_FILES = 10
FILE_CHUNK_SIZE = 1024 ** 2
FILTER_PARAMETERS = ("filter_field", "filter_value", "branch_field", "branch_value", "location_type", "destination")
ALLOWED_FILTER_FIELDS = {
    "destination": ClientProfile.destination,
    "sand_owner": ClientProfile.sand_owner,
    "sales_group": ClientProfile.sales_group,
    "department": ClientProfile.department,
    "first_department": ClientProfile.first_department,
    "group_name": ClientProfile.group_name,
    "location_type": ClientProfile.location_type,
    "employee_type": ClientProfile.employee_type,
}


def _error(status_code: int, code: str, message: str):
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


def _admin_only(current_user: CurrentUser) -> None:
    if not is_admin(current_user):
        _error(403, "forbidden", "Только администратор может делать массовую рассылку")


def _trusted_send_origin(request: Request) -> str | None:
    public_url = (settings.app_public_base_url or "").strip()
    if not public_url and settings.is_prod:
        return None

    try:
        url = urlsplit(public_url or str(request.base_url))
        host, port = url.hostname, url.port
    except ValueError:
        return None

    if url.scheme not in {"http", "https"} or not host or "@" in url.netloc:
        return None
    # A Host-derived origin is only suitable for direct local development.
    if not public_url and host not in {"localhost", "127.0.0.1", "::1"}:
        return None

    authority = f"[{host}]" if ":" in host else host
    default_port = 443 if url.scheme == "https" else 80
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{url.scheme}://{authority}"


def _unique_filter_pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        _error(400, "invalid_filters", "Ключи фильтров не должны повторяться")
    return result


def _filter_pairs(
    filters=None, filter_field=None, filter_value=None,
    branch_field=None, branch_value=None, location_type=None, destination=None,
):
    selected = {}
    if filters is not None:
        try:
            selected = json.loads(filters, object_pairs_hook=_unique_filter_pairs)
        except (ValueError, TypeError, RecursionError):
            _error(400, "invalid_filters", "Фильтры должны быть JSON-объектом")
        if not isinstance(selected, dict):
            _error(400, "invalid_filters", "Фильтры должны быть JSON-объектом")

    pairs = list(selected.items())
    for key, value in ((filter_field, filter_value), (branch_field, branch_value)):
        if key is not None and not isinstance(key, str):
            _error(400, "invalid_filter_field", "Неизвестное поле фильтра")
        if key:
            pairs.append((key, value))
        elif value is not None and value != "":
            _error(400, "invalid_filter_field", "Укажите поле фильтра")
    if location_type is not None:
        pairs.append(("location_type", location_type))
    if destination is not None:
        pairs.append(("destination", destination))
    return pairs


def _recipients_query(db: Session, filters: list[tuple[str, str | None]]):
    stmt = (
        select(
            TelegramContact.client_id,
            TelegramContact.external_chat_id,
            TelegramContact.external_username,
            TelegramContact.external_display_name,
            Client.display_name,
            ClientProfile.destination,
        )
        .join(Client, Client.id == TelegramContact.client_id)
        .join(ClientProfile, ClientProfile.client_id == TelegramContact.client_id)
        .where(
            ClientProfile.is_verified.is_(True),
            TelegramContact.is_blocked.is_(False),
            func.trim(TelegramContact.external_chat_id) != "",
        )
    )
    if filters:
        configured_fields = select(ClientCardFieldConfig).join(
            ClientCardBlockConfig, ClientCardBlockConfig.key == ClientCardFieldConfig.block_key
        ).where(
            ClientCardBlockConfig.is_active.is_(True),
            ClientCardFieldConfig.field_type == "select",
        )
        fields = {field.key: field for field in db.scalars(configured_fields)}
        for key, value in filters:
            field = fields.get(key)
            if field is None:
                _error(400, "invalid_filter_field", "Неизвестное поле фильтра")
            if value is None or value == "":
                continue
            if not isinstance(value, str) or value not in (_field_options_out(field) or []):
                _error(400, "invalid_filter_value", "Недопустимое значение фильтра")
            if field.is_system:
                column = ClientProfile.__table__.c.get(key) if key in SYSTEM_PROFILE_FIELD_KEYS else None
                if column is None or not isinstance(column.type, String):
                    _error(400, "invalid_filter_field", "Неизвестное поле профиля")
            else:
                column = ClientProfile.custom_fields[key].as_string()
            stmt = stmt.where(column == value)
    return stmt.order_by(Client.display_name.asc())


@router.get("/recipients", response_model=list[MassNotificationRecipientOut])
def list_recipients(
    filters: str | None = Query(default=None),
    filter_field: str | None = Query(default=None),
    filter_value: str | None = Query(default=None),
    branch_field: str | None = Query(default=None),
    branch_value: str | None = Query(default=None),
    location_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)
    pairs = _filter_pairs(filters, filter_field, filter_value, branch_field, branch_value, location_type)
    rows = db.execute(_recipients_query(db, pairs)).all()
    return [
        MassNotificationRecipientOut(
            client_id=row.client_id,
            client_name=row.display_name or "Клиент",
            chat_id=row.external_chat_id,
            destination=row.destination,
            telegram_username=row.external_username,
            telegram_display_name=row.external_display_name,
        )
        for row in rows
    ]


def _audience_fingerprint(rows) -> str:
    audience = sorted([str(row.client_id), str(row.external_chat_id)] for row in rows)
    serialized = json.dumps(audience, ensure_ascii=False, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


@router.get("/preview")
def preview_recipients(
    filters: str | None = Query(default=None),
    filter_field: str | None = Query(default=None),
    filter_value: str | None = Query(default=None),
    branch_field: str | None = Query(default=None),
    branch_value: str | None = Query(default=None),
    location_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, int | str]:
    _admin_only(current_user)
    pairs = _filter_pairs(filters, filter_field, filter_value, branch_field, branch_value, location_type)
    rows = db.execute(_recipients_query(db, pairs)).all()
    return {"count": len(rows), "audience_fingerprint": _audience_fingerprint(rows)}


@router.get("/filter-options")
def list_filter_options(
    filter_field: str = Query(...),
    destination: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)
    field = ALLOWED_FILTER_FIELDS.get(filter_field)
    if not field:
        _error(400, "invalid_filter_field", "Неизвестное поле фильтра")
    config = db.execute(select(ClientCardFieldConfig).where(ClientCardFieldConfig.key == filter_field)).scalar_one_or_none()
    if config is None or config.field_type == "select":
        return {"field": filter_field, "values": (_field_options_out(config) or []) if config is not None else []}
    stmt = (
        select(distinct(field))
        .select_from(TelegramContact)
        .join(ClientProfile, ClientProfile.client_id == TelegramContact.client_id)
        .where(field.isnot(None))
        .order_by(field.asc())
    )
    if destination:
        stmt = stmt.where(ClientProfile.destination == destination)
    values = [row[0] for row in db.execute(stmt).all() if str(row[0]).strip()]
    return {"field": filter_field, "values": values}


TELEGRAM_TAGS = frozenset("b strong i em u ins s strike del tg-spoiler code pre blockquote a".split())
TELEGRAM_END_TAG = re.compile(r"</[a-zA-Z][a-zA-Z0-9-]*\s*>")
TELEGRAM_TAG_ALIASES = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}


class _TelegramHtml(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.parts = []

    def handle_data(self, data):
        self.parts.append(escape(data, quote=False))

    def handle_starttag(self, tag, attrs):
        if tag not in TELEGRAM_TAGS:
            raise ValueError
        suffix = ""
        if tag == "a":
            if len(attrs) != 1 or attrs[0][0] != "href" or not attrs[0][1]:
                raise ValueError
            href = attrs[0][1].strip()
            if any(ord(char) < 32 or ord(char) == 127 for char in href):
                raise ValueError
            url = urlsplit(href)
            if url.scheme not in {"http", "https", "tg", "mailto"}:
                raise ValueError
            if not (url.netloc if url.scheme in {"http", "https", "tg"} else url.path):
                raise ValueError
            suffix = f' href="{escape(href, quote=True)}"'
        elif tag == "blockquote" and len(attrs) == 1 and attrs[0] in {("expandable", None), ("expandable", "")}:
            suffix = " expandable"
        elif attrs:
            raise ValueError
        tag = TELEGRAM_TAG_ALIASES.get(tag, tag)
        if tag in {"a", "blockquote"} and tag in self.tags:
            raise ValueError
        self.tags.append(tag)
        self.parts.append(f"<{tag}{suffix}>")

    def parse_endtag(self, index):
        if self.rawdata.find(">", index) >= 0 and not TELEGRAM_END_TAG.match(self.rawdata, index):
            raise ValueError
        return super().parse_endtag(index)

    def handle_endtag(self, tag):
        tag = TELEGRAM_TAG_ALIASES.get(tag, tag)
        if not self.tags or self.tags.pop() != tag:
            raise ValueError
        self.parts.append(f"</{tag}>")

    def _invalid_markup(self, *args):
        raise ValueError

    handle_startendtag = handle_comment = handle_decl = handle_pi = unknown_decl = _invalid_markup


def _notification_text(text: str, text_format: str) -> str:
    if text_format == "plain":
        return escape(text.strip(), quote=False)
    if text_format != "telegram_html":
        _error(400, "invalid_text_format", "Недопустимый формат текста")
    parser = _TelegramHtml()
    try:
        parser.feed(text.strip())
        if re.match(r"</?[a-zA-Z]", parser.rawdata):
            raise ValueError
        parser.close()
        if parser.tags:
            raise ValueError
    except (ValueError, AssertionError):
        _error(400, "invalid_telegram_html", "Некорректная разметка Telegram HTML")
    return "".join(parser.parts)


SAFE_JOURNAL_ERRORS = {
    INTERRUPTED_OPERATION_ERROR: INTERRUPTED_OPERATION_ERROR,
    INTERRUPTED_ATTACHMENT_ERROR: INTERRUPTED_ATTACHMENT_ERROR,
    INTERRUPTED_COMPONENT_ERROR: INTERRUPTED_COMPONENT_ERROR,
    "No longer eligible": "Получатель больше не соответствует условиям рассылки",
    "Telegram did not confirm a valid message ID": "Telegram не подтвердил отправку",
    "Telegram did not confirm a valid staging message ID": "Telegram не подтвердил подготовку файла",
    "TELEGRAM_STAGING_CHAT_ID is not configured": "Технический чат не настроен",
    "The notification attachment must be a non-empty regular file": "Вложение пусто или недоступно",
    "The local attachment size differs from its saved size": "Размер вложения изменился",
    "The photo extension does not match its MIME type": "Формат фотографии не соответствует её расширению",
    "Не удалось подготовить каталог вложений рассылки": "Не удалось подготовить каталог вложений рассылки",
    "bot was blocked by the user": "Получатель заблокировал бота",
    "chat not found": "Чат получателя не найден",
    "user is deactivated": "Аккаунт получателя деактивирован",
}
RECIPIENT_STATUSES = ("sent", "failed", "partial", "unknown", "skipped", "pending", "sending")


def _journal_error(error):
    if not error:
        return None
    reason = str(error).removeprefix("Forbidden: ").removeprefix("Bad Request: ")
    if reason.lower().startswith(("too many requests", "retry after")):
        return "Telegram ограничил частоту отправки"
    return SAFE_JOURNAL_ERRORS.get(reason, "Подробности ошибки недоступны")


def _journal_timestamp(date):
    return date.replace(tzinfo=date.tzinfo or timezone.utc).isoformat() if date else None


def _operation_journal(db: Session, operation: MassNotificationOperation):
    recipient = MassNotificationRecipient
    rows = db.scalars(
        select(recipient).where(recipient.operation_id == operation.id).order_by(recipient.created_at, recipient.id)
    ).all()
    counts = Counter(row.status for row in rows)
    return {
        "operation_id": operation.id,
        "status": operation.status,
        "stage": operation.stage,
        "total_count": operation.total_count,
        "processed_count": sum(counts[status] for status in RECIPIENT_STATUSES[:5]),
        **{f"{status}_count": counts[status] for status in RECIPIENT_STATUSES},
        "error_message": _journal_error(operation.error_message),
        "created_at": _journal_timestamp(operation.created_at),
        "finished_at": _journal_timestamp(operation.finished_at),
        "attachments": [
            {
                "index": index, "original_filename": attachment["original_filename"], "file_size": attachment["file_size"],
                "status": attachment["status"], "error": _journal_error(attachment.get("error")),
            }
            for index, attachment in enumerate(operation.attachments_json)
        ],
        "recipients": [
            {
                "client_name": row.client_name, "status": row.status, "error": _journal_error(row.error_message),
                "components": [
                    {
                        "key": component["key"], "kind": component["kind"], "status": component["status"],
                        "error": _journal_error(component.get("error")),
                    }
                    for component in row.components_json
                ],
            }
            for row in rows
        ],
    }


def _current_operation_journal(db: Session, operation: MassNotificationOperation):
    reconcile_stale_mass_notification_operations(db, operation_id=operation.id)
    db.refresh(operation)
    return {"operation": _operation_journal(db, operation)}


@router.get("/operations/latest")
def latest_operation(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    _admin_only(current_user)
    operation = MassNotificationOperation
    latest = db.scalar(
        select(operation).where(operation.requested_by_user_id == current_user.id)
        .order_by(operation.created_at.desc(), operation.id.desc()).limit(1)
    )
    return _current_operation_journal(db, latest) if latest else {"operation": None}


@router.get("/operations/{operation_id}")
def operation_details(operation_id: str, db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    _admin_only(current_user)
    operation = MassNotificationOperation
    match = db.scalar(select(operation).where(
        operation.id == operation_id, operation.requested_by_user_id == current_user.id,
    ))
    if match is None:
        _error(404, "operation_not_found", "Операция не найдена")
    return _current_operation_journal(db, match)


def _send_fields(payload, *, multipart):
    key = payload.get("idempotency_key")
    try:
        canonical = str(UUID(key)) if isinstance(key, str) else None
    except ValueError:
        canonical = None
    if canonical is None or key != canonical:
        _error(400, "invalid_idempotency_key", "Укажите idempotency_key в каноническом формате UUID")
    fingerprint = payload.get("audience_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        _error(400, "invalid_audience_fingerprint", "Укажите корректный отпечаток аудитории")
    raw_filters = payload.get("filters")
    if multipart:
        if not isinstance(raw_filters, str):
            _error(400, "invalid_filters", "Фильтры должны быть JSON-объектом")
    else:
        if not isinstance(raw_filters, dict):
            _error(400, "invalid_filters", "Фильтры должны быть JSON-объектом")
        raw_filters = json.dumps(raw_filters)
    text = payload.get("text")
    if not isinstance(text, str):
        _error(400, "invalid_text", "Укажите текст сообщения")
    pairs = _filter_pairs(raw_filters, **{name: payload.get(name) for name in FILTER_PARAMETERS})
    filters = {}
    for field, value in pairs:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            _error(400, "invalid_filter_value", "Недопустимое значение фильтра")
        if field in filters and filters[field] != value:
            _error(400, "conflicting_filters", "Переданы противоречащие друг другу значения фильтра")
        filters[field] = value
    return key, fingerprint, _notification_text(text, payload.get("text_format", "plain")), pairs, filters


def _request_directory() -> Path:
    root = Path(tempfile.gettempdir()).resolve() / "es-desk-mass-notifications"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.resolve() != root:
        raise OSError("Unsafe notification staging root")
    return Path(tempfile.mkdtemp(prefix="request-", dir=root))


def _attachment_metadata(file: UploadFile) -> tuple[str, str | None, int]:
    cleaned = "".join(char for char in (file.filename or "") if category(char) not in {"Cc", "Cf", "Cs"})
    filename = PureWindowsPath(cleaned).name.strip().replace('"', "_")
    if filename in {"", ".", ".."}:
        filename = "attachment"
    mime_type = (file.content_type or "").partition(";")[0].strip().lower() or None
    if mime_type and not re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+", mime_type):
        _error(400, "invalid_mime_type", "Недопустимый MIME-тип вложения")
    photo_type = PHOTO_MIME_TYPES.get(Path(filename).suffix.lower())
    if photo_type and mime_type not in {None, "application/octet-stream", photo_type}:
        _error(400, "invalid_mime_type", "MIME-тип не соответствует расширению фотографии")
    return filename, mime_type, PHOTO_LIMIT if photo_type else DOCUMENT_LIMIT


async def _save_attachment(file: UploadFile, directory: Path) -> tuple[dict, dict]:
    filename, mime_type, limit = _attachment_metadata(file)
    stored_name = uuid4().hex
    size = 0
    digest = sha256()
    with (directory / stored_name).open("xb") as output:
        while chunk := await file.read(FILE_CHUNK_SIZE):
            size += len(chunk)
            if size > limit:
                _error(400, "attachment_too_large", "Превышен допустимый размер вложения")
            output.write(chunk)
            digest.update(chunk)
    if not size:
        _error(400, "empty_attachment", "Пустое вложение")
    identity = {"original_filename": filename, "mime_type": mime_type, "file_size": size}
    metadata = dict(
        identity, stored_name=stored_name, status="pending", staging_chat_id=None, staging_message_id=None, error=None,
    )
    return metadata, dict(identity, sha256=digest.hexdigest())


def _operation_response(operation: MassNotificationOperation, created: bool) -> dict[str, str | int | bool]:
    return {
        "operation_id": operation.id,
        "status": operation.status,
        "total_count": operation.total_count,
        "created": created,
    }


def _finalize_files(directory: Path, operation_id: str) -> None:
    destination = directory.with_name(operation_id)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Operation directory already exists")
    directory.rename(destination)


def _record_finalization_failure(db: Session, operation_id: str) -> None:
    operation = MassNotificationOperation
    reason = "Не удалось подготовить каталог вложений рассылки"
    try:
        failed = db.execute(
            update(operation)
            .where(operation.id == operation_id, operation.status == "queued")
            .values(status="failed", stage="file_finalization", error_message=reason, finished_at=datetime.now(timezone.utc))
        )
        if failed.rowcount != 1:
            raise RuntimeError("The operation is no longer queued")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not record attachment finalization failure for notification %s", operation_id)
    _error(500, "file_finalization_failed", reason)


async def _queue_notification(db, current_user, payload, files, multipart, background_tasks):
    key, fingerprint, text, pairs, filters = _send_fields(payload, multipart=multipart)
    if len(files) > MAX_FILES or any(not isinstance(file, UploadFile) for file in files):
        _error(400, "invalid_attachments", "Допускается не более 10 файлов")
    if not text and not files:
        _error(400, "empty_payload", "Укажите текст и/или вложения для рассылки")

    directory = None
    try:
        directory = _request_directory()
        attachments, identities = [], []
        for file in files:
            metadata, identity = await _save_attachment(file, directory)
            attachments.append(metadata)
            identities.append(identity)
        data = {"text": text, "filters": filters, "audience_fingerprint": fingerprint, "attachments": identities}
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            request_hash = sha256(canonical.encode("utf-8")).hexdigest()
        except UnicodeError:
            _error(400, "invalid_payload", "Некорректные данные рассылки")

        operation = db.scalar(select(MassNotificationOperation).where(MassNotificationOperation.idempotency_key == key))
        if operation is not None:
            if operation.requested_by_user_id != current_user.id or operation.request_hash != request_hash:
                _error(409, "idempotency_conflict", "Ключ уже использован для другой рассылки")
            return _operation_response(operation, False)

        rows = db.execute(
            _recipients_query(db, pairs).add_columns(ClientProfile.last_name, ClientProfile.contact_name)
        ).all()
        if not rows or _audience_fingerprint(rows) != fingerprint:
            _error(409, "audience_changed", "Аудитория изменилась или пуста. Обновите предпросмотр")
        recipients = [
            {
                "client_id": str(row.client_id),
                "chat_id": str(row.external_chat_id),
                "client_name": " ".join(part.strip() for part in (row.last_name, row.contact_name) if part and part.strip())
                or row.display_name or "Клиент",
            }
            for row in rows
        ]
        try:
            operation, created = create_mass_notification_operation(
                db, current_user.id, key, request_hash, text, filters, attachments, recipients,
            )
        except MassNotificationOperationConflict:
            _error(409, "idempotency_conflict", "Ключ уже использован для другой рассылки")

        if not created:
            return _operation_response(operation, False)
        operation_id = operation.id
        try:
            _finalize_files(directory, operation_id)
        except OSError:
            logger.exception("Could not finalize attachment files for notification %s", operation_id)
            _record_finalization_failure(db, operation_id)
        directory = None
        background_tasks.add_task(run_mass_notification_operation, operation_id)
        return _operation_response(operation, True)
    except OSError:
        logger.exception("Could not save mass notification input files")
        _error(500, "attachment_storage_failed", "Не удалось сохранить вложения рассылки")
    finally:
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError:
                logger.exception("Could not remove the temporary mass notification request directory")


@router.post("/send", status_code=202)
async def send_mass_notification(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _admin_only(current_user)
    expected_origin = _trusted_send_origin(request)
    if expected_origin is None or request.headers.getlist("origin") != [expected_origin]:
        _error(403, "invalid_origin", "Недопустимый источник запроса")

    multipart = "multipart/form-data" in (request.headers.get("content-type") or "").lower()
    async with AsyncExitStack() as stack:
        if multipart:
            payload = await stack.enter_async_context(request.form(max_files=MAX_FILES))
            for name in ("idempotency_key", "audience_fingerprint", "filters", "text", "text_format", *FILTER_PARAMETERS):
                if len(payload.getlist(name)) > 1:
                    _error(400, "invalid_payload", "Поля запроса не должны повторяться")
            files = payload.getlist("files")
        else:
            try:
                payload = json.loads(await request.body(), object_pairs_hook=_unique_filter_pairs)
            except (ValueError, RecursionError):
                _error(400, "invalid_payload", "Некорректные данные рассылки")
            if not isinstance(payload, dict) or "files" in payload:
                _error(400, "invalid_payload", "Некорректные данные рассылки")
            files = []
        return await _queue_notification(db, current_user, payload, files, multipart, background_tasks)
