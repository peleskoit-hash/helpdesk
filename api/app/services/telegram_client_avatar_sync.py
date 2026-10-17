"""Best-effort avatar refresh, scheduled only by new Telegram ticket creation."""
from concurrent.futures import ThreadPoolExecutor
import logging
import re
from time import monotonic
from urllib.request import Request, urlopen

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import TelegramContact, Ticket
from .telegram_bot import telegram_request
from .telegram_client_avatars import MAX_IMAGE_BYTES, needs_avatar_download, save_avatar


logger = logging.getLogger(__name__)
_PENDING_KEY = "telegram_client_avatar_refresh"
# ponytail: one worker preserves photo order; use per-contact serialization if throughput requires it.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telegram-client-avatar")
_TIMEOUT = 10


def _result(method: str, payload: dict, token: str) -> dict:
    response = telegram_request(method, payload, timeout=_TIMEOUT, bot_token=token)
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise ValueError("Invalid Telegram avatar response")
    return response["result"]


def _download(file_path: str, token: str) -> bytes:
    # Telegram returns a relative file path, never an arbitrary download URL.
    if (not isinstance(file_path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", file_path)
            or any(part in {"", ".", ".."} for part in file_path.split("/"))):
        raise ValueError("Invalid Telegram avatar file path")
    request = Request(f"https://api.telegram.org/file/bot{token}/{file_path}")
    deadline = monotonic() + 20
    content = bytearray()
    with urlopen(request, timeout=_TIMEOUT) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_IMAGE_BYTES:
            raise ValueError("Telegram avatar exceeds the size limit")
        while True:
            if monotonic() > deadline:
                raise TimeoutError("Telegram avatar download timed out")
            chunk = response.read1(min(64 * 1024, MAX_IMAGE_BYTES + 1 - len(content)))
            if not chunk:
                return bytes(content)
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError("Telegram avatar exceeds the size limit")


def _refresh(ticket_id: str) -> None:
    # Never log exception details: urllib/Telegram errors may contain token URLs or PII.
    try:
        token = settings.telegram_bot_token
        if not token:
            return
        with SessionLocal() as db:
            ticket = db.get(Ticket, ticket_id)
            if ticket is None:
                return
            contact = db.scalar(select(TelegramContact).where(
                TelegramContact.company_id == ticket.company_id,
                TelegramContact.client_id == ticket.client_id,
            ))
            if contact is None or not str(contact.external_user_id).isdigit():
                return
            photos = _result("getUserProfilePhotos", {
                "user_id": int(contact.external_user_id), "offset": 0, "limit": 1,
            }, token)
            if photos.get("total_count") == 0:
                return
            if type(photos.get("total_count")) is not int or photos["total_count"] < 1:
                raise ValueError("Invalid Telegram avatar count")
            group = photos["photos"][0]
            if not isinstance(group, list) or not group or any(
                not isinstance(item, dict)
                or type(item.get("width")) is not int or item["width"] < 1
                or type(item.get("height")) is not int or item["height"] < 1
                or not isinstance(item.get("file_id"), str) or not item["file_id"]
                for item in group
            ):
                raise ValueError("Invalid Telegram avatar sizes")
            photo = max(group, key=lambda item: (item["width"] * item["height"], item.get("file_size", 0)))
            unique_id = photo["file_unique_id"]
            if not isinstance(unique_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", unique_id):
                raise ValueError("Invalid Telegram avatar identity")
            if not needs_avatar_download(contact, unique_id):
                return
            if photo.get("file_size", 0) > MAX_IMAGE_BYTES:
                raise ValueError("Telegram avatar exceeds the size limit")
            file = _result("getFile", {"file_id": photo["file_id"]}, token)
            if file.get("file_size", 0) > MAX_IMAGE_BYTES:
                raise ValueError("Telegram avatar exceeds the size limit")
            content = _download(file["file_path"], token)
            save_avatar(db, contact.id, unique_id, content, require_open_ticket=True)
    except Exception:
        logger.warning("Telegram client avatar refresh failed")


def schedule_avatar_refresh(db: Session, ticket: Ticket) -> None:
    """Queue only at new client Telegram intake, never when creating internal/child tasks."""
    db.info.setdefault(_PENDING_KEY, set()).add(ticket.id)


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    if db.in_nested_transaction():
        return
    pending = db.info.pop(_PENDING_KEY, set())
    if not settings.telegram_bot_token:
        return
    for ticket_id in pending:
        try:
            _executor.submit(_refresh, ticket_id)
        except Exception:
            logger.warning("Telegram client avatar refresh could not be scheduled")


@event.listens_for(Session, "after_soft_rollback")
@event.listens_for(Session, "after_rollback")
def _after_rollback(db: Session, *args) -> None:
    db.info.pop(_PENDING_KEY, None)
